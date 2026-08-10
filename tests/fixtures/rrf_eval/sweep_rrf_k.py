"""RRF `K` 그리드 인메모리 스윕 실험 스크립트.

`docs/exec_plans/search-p4-rrf-k-sweep-plan.md` 근거. 평가 픽스처(`openapi.json`)가
오퍼레이션 20개뿐이라 후보폭 N은 이미 전 코퍼스를 포함(포화) — N은 스윕 불가하고
K만 관측 가능하다(계획서 0절).

**프로덕션 코드(`app/`)는 전혀 바꾸지 않는다.** 두 arm(키워드·벡터)의 순위 리스트는
질의당 딱 한 번만 실행해서 얻고, `reciprocal_rank_fuse`의 `k` 인자만 그리드로
바꿔가며 순수 in-memory 로 재융합한다 — DB·임베딩 재호출 없이 정확한(근사 아닌)
결과를 얻는다.

사용법(로컬 postgres 필요, `docker compose up -d postgres`):
    uv run python tests/fixtures/rrf_eval/sweep_rrf_k.py

`compare_strategies.py`와 마찬가지로 pytest 수집 대상이 아니다(실모델 로딩·전용
임시 DB 필요 — 회귀 의심 시 수동 재실행 용도).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.composition import AppState, build_services
from app.core.db import create_db_engine
from app.models.openapi import create_all
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from app.services.search.rrf import RRF_K, reciprocal_rank_fuse
from compare_strategies import (  # type: ignore[import-not-found]
    RECALL_KS,
    EvalQuery,
    EvalSummary,
    _drop_temp_db,
    _load_queries,
    _load_valid_endpoints,
    _make_temp_db,
    _rank_of_answer,
    _summarize,
    _validate_labels,
)

_DIR = Path(__file__).parent
TOP_K = 10  # compare_strategies.TOP_K 와 동일 해상도(평가 일관성)

#: 후보 폭. `EndpointCandidateSearch._search_rrf`(운영 코드)와 동일 계산식.
_CANDIDATE_WIDTH = max(TOP_K * 4, 50)

K_GRID = (10, 20, 30, 40, 60, 80, 120)

#: 결과 해석 가드(계획서 4절) 임계값. "잡음 수준"을 넘는지 판정하는 최소 기준.
_SIGNIFICANT_RECALL_DELTA = 0.005  # 0.5pt
_SIGNIFICANT_SCALAR_DELTA = 0.01  # MRR/nDCG
_MIN_CHANGED_QUERIES = 3  # "1~2개 질의의 우연"을 넘는 최소 변경 질의 수


@dataclass
class _RankedEndpoint:
    """`_rank_of_answer` 재사용을 위한 최소 (method, path) 래퍼."""

    method: str
    path: str


def _collect_arm_rankings(cs, queries: list[EvalQuery]) -> dict[str, tuple[list[str], list[str]]]:
    """질의별 키워드/벡터 arm 순위 리스트(ref_id)를 1회씩 수집한다.

    `cs` 내부 속성(`_keyword_search` 등) 접근은 "프로덕션과 바이트 동일한 arm"을
    쓰기 위한 이 실험 스크립트 한정 지름길이다(운영 코드에서는 금지 — private
    속성 접근으로 캡슐화를 깨는 대신, arm 로직을 손으로 복제해 프로덕션과 어긋날
    위험을 없앤다).
    """
    rankings: dict[str, tuple[list[str], list[str]]] = {}
    for eq in queries:
        keyword_hits = cs._keyword_search.search(
            eq.query, top_k=_CANDIDATE_WIDTH, document_id=None, project=None
        )
        keyword_ref_ids = [h.ref_id for h in keyword_hits]

        candidate_ids = cs._chunk_repo.list_endpoint_chunk_ids(document_id=None, project=None)
        vector_hits = cs._vector_search.search(
            eq.query, top_k=_CANDIDATE_WIDTH, candidates=candidate_ids
        )
        vector_ref_ids = [h.ref_id for h in vector_hits if h.score > 0.0]

        rankings[eq.query] = (keyword_ref_ids, vector_ref_ids)
    return rankings


def _rank_for_k(
    cs,
    keyword_ref_ids: list[str],
    vector_ref_ids: list[str],
    k: int,
    accepted: list[tuple[str, str]],
) -> int | None:
    """주어진 K로 in-memory 재융합해 정답의 1-based 순위를 구한다(없으면 None)."""
    fused = reciprocal_rank_fuse(keyword_ref_ids, vector_ref_ids, top_k=TOP_K, k=k)
    ranked: list[_RankedEndpoint] = []
    for f in fused:
        endpoint = cs._endpoint_repo.get(f.ref_id)
        if endpoint is None:
            continue
        ranked.append(_RankedEndpoint(method=endpoint.method, path=endpoint.path))
    return _rank_of_answer(ranked, accepted)


def _metric_values(summary: EvalSummary) -> dict[str, float]:
    values = {f"Recall@{k}": summary.recall[k] for k in RECALL_KS}
    values["MRR"] = summary.mrr
    values["nDCG@10"] = summary.ndcg10
    return values


def _passes_overfitting_guard(
    baseline: EvalSummary,
    candidate: EvalSummary,
    changed_query_count: int,
    neighbor_summaries: list[EvalSummary],
) -> tuple[bool, list[str]]:
    """계획서 4절 4개 기준(유의미크기·지표일관성·소수질의비의존·K이웃안정성)을 검사한다."""
    reasons: list[str] = []
    base_vals = _metric_values(baseline)
    cand_vals = _metric_values(candidate)
    scalar_names = {"MRR", "nDCG@10"}
    deltas = {name: cand_vals[name] - base_vals[name] for name in base_vals}

    significant = any(
        abs(d) >= (_SIGNIFICANT_SCALAR_DELTA if name in scalar_names else _SIGNIFICANT_RECALL_DELTA)
        for name, d in deltas.items()
    )
    if not significant:
        reasons.append("(1) 유의미크기 미달: 모든 지표 델타가 잡음 수준")

    consistent = all(d >= 0 for d in deltas.values()) and any(d > 0 for d in deltas.values())
    if not consistent:
        reasons.append("(2) 지표 일관성 미달: 개선/악화 방향이 혼재")

    if changed_query_count < _MIN_CHANGED_QUERIES:
        reasons.append(
            f"(3) 소수 질의 의존: 순위 변경 질의 {changed_query_count}건 < {_MIN_CHANGED_QUERIES}"
        )

    neighbor_ok = all(_metric_values(nb)["MRR"] >= base_vals["MRR"] for nb in neighbor_summaries)
    if not neighbor_ok:
        reasons.append("(4) K 이웃 안정성 미달: 인접 K가 기준(K=60)보다 못함")

    return (len(reasons) == 0, reasons)


def _print_report(
    summaries: dict[int, EvalSummary], ranks_by_k: dict[int, list[int | None]], n: int
) -> None:
    best_k = max(K_GRID, key=lambda k: summaries[k].mrr)

    print(f"\n### RRF K 스윕 결과 (n={n}, TOP_K={TOP_K}, 후보폭={_CANDIDATE_WIDTH})")
    print("| K | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@10 | 비고 |")
    print("|---|---|---|---|---|---|---|---|")
    for k in K_GRID:
        s = summaries[k]
        recall_cols = " | ".join(f"{s.recall[rk]:.0%}" for rk in RECALL_KS)
        notes = []
        if k == RRF_K:
            notes.append("기준")
        if k == best_k:
            notes.append("최고 MRR")
        print(f"| {k} | {recall_cols} | {s.mrr:.3f} | {s.ndcg10:.3f} | {', '.join(notes)} |")

    baseline = summaries[RRF_K]
    print(f"\n### K={RRF_K} 대비 델타")
    for k in K_GRID:
        if k == RRF_K:
            continue
        s = summaries[k]
        deltas = _metric_values(s)
        base_vals = _metric_values(baseline)
        delta_str = " ".join(f"{name} {deltas[name] - base_vals[name]:+.3f}" for name in deltas)
        changed = sum(
            1
            for a, b in zip(ranks_by_k[RRF_K], ranks_by_k[k], strict=True)
            if a != b
        )
        print(f"- K={k}: {delta_str} (순위 변경 질의 {changed}건)")

    print(f"\n### 과적합 가드 판정 (기준 K={RRF_K}, 최고 MRR K={best_k})")
    if best_k == RRF_K:
        print(f"- 최고 MRR K가 기준(K={RRF_K})과 동일 → K 변경 검토 불필요. **결론: K={RRF_K} 유지.**")
        return

    idx = K_GRID.index(best_k)
    neighbor_ks = [K_GRID[i] for i in (idx - 1, idx + 1) if 0 <= i < len(K_GRID) and K_GRID[i] != best_k]
    neighbor_summaries = [summaries[k] for k in neighbor_ks]
    changed_count = sum(
        1
        for a, b in zip(ranks_by_k[RRF_K], ranks_by_k[best_k], strict=True)
        if a != b
    )
    passed, reasons = _passes_overfitting_guard(
        baseline, summaries[best_k], changed_count, neighbor_summaries
    )
    if passed:
        print(f"- 4개 기준 모두 충족 → **결론: K={best_k}로 변경을 검토할 근거 있음**(후속 태스크에서 판단).")
    else:
        print(f"- 기준 미달 항목: {'; '.join(reasons)}")
        print(f"- **결론: K={RRF_K} 유지**(null result도 유효한 결론 — 표준값을 근거 없이 흔들지 않는다).")


def main() -> None:
    queries = _load_queries()
    openapi_doc = (_DIR / "openapi.json").read_text()
    _validate_labels(queries, _load_valid_endpoints(openapi_doc))

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)
        state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
        state.search_strategy = "rrf"
        if not state.embedding_provider.is_semantic:
            raise RuntimeError(
                "벡터 arm이 비의미론적(is_semantic=False)이라 K 스윕이 무의미함 — 임베딩 백엔드 확인 필요"
            )
        bundle = next(build_services(state))
        bundle.sync_service.register(project="default", source_url=None, raw_document=openapi_doc)

        cs = bundle.candidate_search
        arm_rankings = _collect_arm_rankings(cs, queries)

        summaries: dict[int, EvalSummary] = {}
        ranks_by_k: dict[int, list[int | None]] = {}
        for k in K_GRID:
            ranks = [
                _rank_for_k(cs, *arm_rankings[eq.query], k, eq.accepted) for eq in queries
            ]
            ranks_by_k[k] = ranks
            summaries[k] = _summarize(ranks)

        _print_report(summaries, ranks_by_k, len(queries))
    finally:
        _drop_temp_db(admin_url, dbname)


if __name__ == "__main__":
    main()
