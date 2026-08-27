"""67번 판정 §1 진단: variants off/on 짝지은 재측정 + route-family trace.

`docs/architect-review/67_search_quality_2026_08_27_next_step_verdict.md` §1이
요구한 질의별 기록을 찍는다. headline 재출력이 아니라 실패 유형 분류가 목적이다.

질의별 기록:
  1. accepted endpoint의 variants off / on 순위 (top-k 채점 폭)
  2. top-10 결과의 (method, path) 목록 — 같은 family child 점유 여부
  3. 넓은 후보군(top-N_WIDE)에 accepted 존재 여부 — 후보 생성 실패 vs 최종 융합 실패 분리
  4. accepted 와 top-10 오답의 route-family(첫 path 세그먼트) 관계

대상: variants 필드가 있는 질의(C2 q04~q07, C7 q18·q19) + variants 없는
C3(q08~q10)·C4(q11·q12)는 off 만 같은 형식으로 기록(67번 §1 마지막 문단).

사용법(로컬 postgres 필요):
    uv run python tests/fixtures/corpus_eval/diagnose_variants.py [--top-k 10] [--wide 50]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).parent
sys.path.insert(0, str(_DIR.parent / "rrf_eval"))

from compare_strategies import (  # noqa: E402  type: ignore[import-not-found]
    _drop_temp_db,
    _make_temp_db,
)

from app.composition import AppState, build_services  # noqa: E402
from app.core.db import create_db_engine  # noqa: E402
from app.models import create_all  # noqa: E402
from app.services.ingestor.openapi_fetcher import InMemoryFetcher  # noqa: E402
from app.services.search.endpoint_candidate_search import CandidateSearchOptions  # noqa: E402

# 진단 대상: 27번 재측정에서 Recall@3 0% 였던 카테고리 전부.
DIAG_IDS = {"q04", "q05", "q06", "q07", "q08", "q09", "q10", "q11", "q12"}


@dataclass
class DiagQuery:
    id: str
    query: str
    category: str
    accepted: list[tuple[str, str]]
    variants: list[str]


def _route_family(path: str) -> str:
    """path 첫 두 세그먼트를 route family 키로 쓴다(/v1/customers, /repos/{owner}/{repo})."""
    parts = [p for p in path.split("/") if p]
    return "/" + "/".join(parts[:2]) if parts else "/"


def _load_queries() -> list[DiagQuery]:
    raw = json.loads((_DIR / "queries.json").read_text())
    out = []
    for item in raw:
        if item["id"] not in DIAG_IDS:
            continue
        out.append(
            DiagQuery(
                id=item["id"],
                query=item["query"],
                category=item["category"],
                accepted=[(a["method"], a["path"]) for a in item["accepted"]],
                variants=item.get("variants", []),
            )
        )
    return out


def _load_corpus() -> tuple[dict[str, str], dict[str, str]]:
    manifest = json.loads((_DIR / "corpus_manifest.json").read_text())
    texts: dict[str, str] = {}
    for e in manifest:
        raw = (_DIR / e["file"]).read_text()
        actual = hashlib.sha256(raw.encode()).hexdigest()
        if actual != e["content_sha256"]:
            raise ValueError(f"content_sha256 불일치: {e['source_key']}")
        texts[e["source_key"]] = raw
    doc_type_by_key = {e["source_key"]: e["doc_type"] for e in manifest}
    return texts, doc_type_by_key


def _rank_of(cands, accepted: list[tuple[str, str]]) -> int | None:
    acc = set(accepted)
    for i, c in enumerate(cands, 1):
        if (c.method.upper(), c.path) in acc:
            return i
    return None


def _search(bundle, query: str, variants: list[str] | None, top_k: int):
    opts = CandidateSearchOptions(top_k=top_k, query_variants=variants or None)
    return bundle.candidate_search.search(query, opts)


def _emit_query(bundle, q: DiagQuery, top_k: int, wide: int) -> None:
    acc_families = {_route_family(p) for _, p in q.accepted}
    print(f"\n## {q.id} [{q.category}] {q.query!r}")
    print("- accepted: " + " or ".join(f"{m} {p}" for m, p in q.accepted)
          + f"  (family: {', '.join(sorted(acc_families))})")

    off = _search(bundle, q.query, None, top_k)
    off_rank = _rank_of(off, q.accepted)
    off_wide = _search(bundle, q.query, None, wide)
    off_wide_rank = _rank_of(off_wide, q.accepted)

    if q.variants:
        on = _search(bundle, q.query, q.variants, top_k)
        on_rank = _rank_of(on, q.accepted)
        on_wide = _search(bundle, q.query, q.variants, wide)
        on_wide_rank = _rank_of(on_wide, q.accepted)
        print(f"- variants: {q.variants}")
        print(f"- accepted 순위: off top{top_k}={off_rank}  on top{top_k}={on_rank}  "
              f"| off top{wide}={off_wide_rank}  on top{wide}={on_wide_rank}")
    else:
        on = None
        print("- variants: (없음 — off 만 기록)")
        print(f"- accepted 순위: off top{top_k}={off_rank}  | off top{wide}={off_wide_rank}")

    def dump(label: str, cands) -> None:
        print(f"- {label} top-{top_k}:")
        for i, c in enumerate(cands[:top_k], 1):
            fam = _route_family(c.path)
            same = "  <same-family>" if fam in acc_families else ""
            hit = "  ★ACCEPTED" if (c.method.upper(), c.path) in set(q.accepted) else ""
            print(f"  {i:2} {c.method.upper():6} {c.path}  [{c.match_type}] fam={fam}{same}{hit}")
        if not cands:
            print("   (빈 결과)")

    dump("off", off)
    if on is not None:
        dump("on", on)

    # 실패 유형 분류(67번 §1 해석 규칙)
    best_wide = on_wide_rank if q.variants else off_wide_rank
    best_topk = on_rank if q.variants else off_rank
    if best_topk is not None and best_topk <= 3:
        verdict = "OK — top-3 회복"
    elif best_wide is not None:
        verdict = (f"FAMILY-RERANK 후보 — 넓은 후보군 top{wide}엔 있음(순위 {best_wide}), "
                   f"top{top_k}에서만 밀림")
    else:
        verdict = f"CANDIDATE-GEN 실패 — top{wide} 넓은 후보군에도 accepted 없음"
    print(f"- 분류: {verdict}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--wide", type=int, default=50)
    args = ap.parse_args()

    texts, doc_type_by_key = _load_corpus()
    queries = _load_queries()

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)
        state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
        print("is_semantic:", state.embedding_provider.is_semantic)
        print(f"top_k={args.top_k}  wide={args.wide}")
        bundle = next(build_services(state))
        for key, raw in texts.items():
            r = bundle.sync_service.register(
                project="default", source_url=None, raw_document=raw,
                doc_type=doc_type_by_key[key],
            )
            print(f"등록: {key} -> endpoints={r.endpoints_count}")

        state.search_strategy = "rrf"
        b = next(build_services(state))
        for q in queries:
            _emit_query(b, q, args.top_k, args.wide)
    finally:
        _drop_temp_db(admin_url, dbname)


if __name__ == "__main__":
    main()
