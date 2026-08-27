"""실 코퍼스(Stripe/GitHub OpenAPI) 기반 검색 품질 평가 스크립트.

`docs/architect-review/27_search_quality_eval_real_corpus_design.md` §7 계약의
구현이다. `tests/fixtures/rrf_eval/compare_strategies.py`(synthetic 20-엔드포인트
하네스)의 DB·순위·지표·요약 로직을 그대로 재사용하고(§7.1), 이 스크립트가
새로 갖는 것은 (1) 코퍼스 매니페스트 로더 (2) 다-문서 라벨 검증 게이트뿐이다.

pytest로 수집되지 않는 독립 스크립트다(대형 스펙 색인 + 실 임베딩 모델
로딩이 무거워 CI 상시 실행용이 아니라 수동 회귀 재실행 용도).

사용법(로컬 postgres 필요, `docker compose up -d postgres`):
    uv run python tests/fixtures/corpus_eval/run_corpus_eval.py [--strategy rrf|fallback|both] [--top-k 10]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).parent

# compare_strategies.py는 스크립트 직접 실행(같은 디렉터리가 sys.path[0])을
# 전제로 `from metrics import ...`(flat import)한다. 같은 방식으로 재사용하려고
# rrf_eval 디렉터리를 sys.path에 얹고 flat import한다(패키지 경로 임포트는
# "tests"가 sys.path에 없는 직접 스크립트 실행에서 깨진다).
sys.path.insert(0, str(_DIR.parent / "rrf_eval"))

from compare_strategies import (  # noqa: E402  type: ignore[import-not-found]
    TOP_K,
    _drop_temp_db,
    _format_summary_line,
    _make_temp_db,
    _rank_of_answer,
    _summarize,
)

from app.composition import AppState, build_services
from app.core.db import create_db_engine
from app.models import create_all
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from app.services.search.endpoint_candidate_search import CandidateSearchOptions


@dataclass
class EvalQuery:
    id: str
    query: str
    category: str
    accepted: list[tuple[str, str]]  # (method, path) — doc은 §3.3 검증에만 쓰고 채점에는 무관
    #: 클라 LLM이 함께 제공했을 영문 변형(query_variants). --with-variants 일 때만 사용.
    variants: list[str]
    #: 아래는 queries_gate_v1.json(§2.2 확장 스키마)에만 존재. 레거시 queries.json은 기본값.
    domain: str = ""
    language: str = ""
    evaluation_role: str = "scored"
    split: str = ""
    answer_mode: str = "any"
    pair_id: str | None = None
    pair_role: str | None = None


def _load_manifest() -> list[dict]:
    return json.loads((_DIR / "corpus_manifest.json").read_text())


def _load_corpus_texts(manifest: list[dict]) -> dict[str, str]:
    """소스 키 → 원문. 프리즈된 파일이 매니페스트의 content_sha256과 일치하는지 검증한다."""
    texts: dict[str, str] = {}
    for entry in manifest:
        raw = (_DIR / entry["file"]).read_text()
        actual = hashlib.sha256(raw.encode()).hexdigest()
        if actual != entry["content_sha256"]:
            raise ValueError(
                f"content_sha256 불일치: {entry['source_key']}({entry['file']}) "
                f"— 스펙이 재수집되었거나 변조되었을 수 있음"
            )
        texts[entry["source_key"]] = raw
    return texts


def _valid_endpoints_by_doc(texts: dict[str, str]) -> dict[str, set[tuple[str, str]]]:
    result: dict[str, set[tuple[str, str]]] = {}
    for source_key, raw in texts.items():
        paths = json.loads(raw)["paths"]
        result[source_key] = {(m.upper(), p) for p, methods in paths.items() for m in methods}
    return result


_KNOWN_CATEGORIES = {
    "C1-직접키워드", "C2-한글패러프레이즈", "C3-영문의역", "C4-흔한토큰범람",
    "C5-decoy구분", "C6-다개념", "C7-대형엔드포인트세부",
}
_KNOWN_TAGS = {
    "route_family_pair", "root_target", "child_target", "lexical_control",
    "common_token", "cross_language", "multi_intent", "detail_field",
}
_C6 = "C6-다개념"


def _norm_query(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().casefold())


def _validate_gate_schema(
    raw_items: list[dict],
    valid_by_doc: dict[str, set[tuple[str, str]]],
    corpus_sha: dict[str, str],
) -> None:
    """§4.1 정적 검증. 검색 실행 전에 전부 통과해야 한다(하나라도 실패하면 죽는다).

    확장 스키마(queries_gate_v1.json)에만 적용된다. 레거시 queries.json은 대상 아님.
    """
    errs: list[str] = []

    def bad(msg: str) -> None:
        errs.append(msg)

    scored = [r for r in raw_items if r.get("evaluation_role") == "scored"]
    diag = [r for r in raw_items if r.get("evaluation_role") == "diagnostic"]

    # 1) schema: 필수 필드/enum/타입
    required = {"id", "query", "category", "domain", "language", "evaluation_role",
                "split", "answer_mode", "accepted"}
    for r in raw_items:
        miss = required - r.keys()
        if miss:
            bad(f"{r.get('id', '?')}: 필수 필드 누락 {sorted(miss)}")
            continue
        if r["category"] not in _KNOWN_CATEGORIES:
            bad(f"{r['id']}: 알 수 없는 category {r['category']!r}")
        if r["domain"] not in ("stripe", "github"):
            bad(f"{r['id']}: domain {r['domain']!r}")
        if r["language"] not in ("ko", "en", "code"):
            bad(f"{r['id']}: language {r['language']!r}")
        if r["evaluation_role"] not in ("scored", "diagnostic"):
            bad(f"{r['id']}: evaluation_role {r['evaluation_role']!r}")
        exp_split = ("gate", "holdout") if r["evaluation_role"] == "scored" else ("diagnostic",)
        if r["split"] not in exp_split:
            bad(f"{r['id']}: split {r['split']!r} (evaluation_role={r['evaluation_role']})")
        if r["answer_mode"] not in ("any", "all"):
            bad(f"{r['id']}: answer_mode {r['answer_mode']!r}")
        if not isinstance(r["accepted"], list) or not r["accepted"]:
            bad(f"{r['id']}: accepted 비어있음")
        for t in r.get("diagnostic_tags", []):
            if t not in _KNOWN_TAGS:
                bad(f"{r['id']}: 알 수 없는 diagnostic_tag {t!r}")

    # 2) id / 정규화 query 중복 없음 + 레거시 20건과도 중복 없음
    ids = [r["id"] for r in raw_items]
    if len(set(ids)) != len(ids):
        bad("id 중복 존재")
    legacy = {_norm_query(x["query"]) for x in json.loads((_DIR / "queries.json").read_text())}
    seen: set[str] = set()
    for r in raw_items:
        n = _norm_query(r["query"])
        if n in seen:
            bad(f"{r['id']}: query 정규화 중복 {r['query']!r}")
        if n in legacy:
            bad(f"{r['id']}: query가 레거시 queries.json과 중복 {r['query']!r}")
        seen.add(n)

    # 3) 레코드 수 / split 분포
    if len(scored) != 120:
        bad(f"scored {len(scored)} != 120")
    if len(diag) != 4:
        bad(f"diagnostic {len(diag)} != 4")
    n_gate = sum(r["split"] == "gate" for r in scored)
    n_hold = sum(r["split"] == "holdout" for r in scored)
    if (n_gate, n_hold, len(diag)) != (96, 24, 4):
        bad(f"split 분포 {(n_gate, n_hold, len(diag))} != (96, 24, 4)")

    # 4) §5.2/§5.3/§5.4 quota 정확 일치
    cat_want = {"C1-직접키워드": 12, "C2-한글패러프레이즈": 24, "C3-영문의역": 18,
                "C4-흔한토큰범람": 12, "C5-decoy구분": 24, "C6-다개념": 12,
                "C7-대형엔드포인트세부": 18}
    for cat, want in cat_want.items():
        got = sum(r["category"] == cat for r in scored)
        if got != want:
            bad(f"category quota {cat}: {got} != {want}")
        rs = [r for r in scored if r["category"] == cat]
        if rs and sum(r["domain"] == "stripe" for r in rs) != want // 2:
            bad(f"category {cat} domain 50/50 아님")
    lang = {k: sum(r["language"] == k for r in scored) for k in ("ko", "en", "code")}
    if (lang["ko"], lang["en"], lang["code"]) != (58, 58, 4):
        bad(f"언어 quota {lang} != ko58/en58/code4")
    for dom in ("stripe", "github"):
        dl = {k: sum(r["language"] == k for r in scored if r["domain"] == dom) for k in ("ko", "en", "code")}
        if (dl["ko"], dl["en"], dl["code"]) != (29, 29, 2):
            bad(f"{dom} 언어 quota {dl} != ko29/en29/code2")
    split_want = {"C1-직접키워드": (10, 2), "C2-한글패러프레이즈": (19, 5), "C3-영문의역": (14, 4),
                  "C4-흔한토큰범람": (10, 2), "C5-decoy구분": (19, 5), "C6-다개념": (10, 2),
                  "C7-대형엔드포인트세부": (14, 4)}
    for cat, (wg, wh) in split_want.items():
        rs = [r for r in scored if r["category"] == cat]
        got = (sum(r["split"] == "gate" for r in rs), sum(r["split"] == "holdout" for r in rs))
        if got != (wg, wh):
            bad(f"gate/holdout split {cat}: {got} != {(wg, wh)}")
    hold = [r for r in scored if r["split"] == "holdout"]
    if sum(r["domain"] == "stripe" for r in hold) != 12:
        bad("holdout stripe != 12")
    hl = {k: sum(r["language"] == k for r in hold) for k in ("ko", "en", "code")}
    if (hl["ko"], hl["en"], hl["code"]) != (11, 11, 2):
        bad(f"holdout 언어 {hl} != ko11/en11/code2")

    # 5) corpus manifest SHA
    if corpus_sha.get("stripe", "").split(":")[-1][:12] != "3653ad45bbec":
        bad(f"stripe corpus SHA 불일치: {corpus_sha.get('stripe')}")
    if corpus_sha.get("github", "").split(":")[-1][:12] != "80850db290cd":
        bad(f"github corpus SHA 불일치: {corpus_sha.get('github')}")
    mf = _DIR / "gate_manifest_v1.json"
    if mf.exists():
        man = json.loads(mf.read_text())
        if man.get("corpus_sha256", {}).get("stripe") != corpus_sha.get("stripe"):
            bad("gate_manifest_v1.json corpus_sha256.stripe 불일치")
        if man.get("corpus_sha256", {}).get("github") != corpus_sha.get("github"):
            bad("gate_manifest_v1.json corpus_sha256.github 불일치")

    # 6) accepted 실재 (전량)
    for r in raw_items:
        for acc in r["accepted"]:
            if (acc["method"], acc["path"]) not in valid_by_doc.get(acc["doc"], set()):
                bad(f"{r['id']}: accepted 미존재 {acc['doc']} {acc['method']} {acc['path']}")

    # 7) answer_mode 계약
    for r in raw_items:
        if r["answer_mode"] == "all":
            if r["category"] != _C6 or len(r["accepted"]) != 2:
                bad(f"{r['id']}: answer_mode=all 은 C6·accepted 2건이어야 함")
        elif not (1 <= len(r["accepted"]) <= 3):
            bad(f"{r['id']}: any accepted 수 {len(r['accepted'])} (1~3 허용)")

    # 8) variants: ko 정확히 1건, en/code 없음, blank/중복/원문동일 거부
    all_q = {_norm_query(r["query"]) for r in raw_items} | legacy
    for r in raw_items:
        v = r.get("variants")
        if r["language"] == "ko":
            if not v or len(v) != 1:
                bad(f"{r['id']}: ko variants 정확히 1건 필요")
            elif not v[0].strip():
                bad(f"{r['id']}: blank variant")
            elif _norm_query(v[0]) == _norm_query(r["query"]):
                bad(f"{r['id']}: variant가 원문과 동일")
            elif _norm_query(v[0]) in all_q:
                bad(f"{r['id']}: variant가 다른 query와 중복 {v[0]!r}")
        elif v is not None:
            bad(f"{r['id']}: {r['language']} 레코드에 variants 존재")

    # 9) pair_id: 정확히 두 레코드(root/child), 동일 domain/language, accepted 1건씩,
    #    root path 가 child path 의 세그먼트 경계 prefix, endpoint 서로 다름
    pairs: dict[str, list[dict]] = {}
    for r in raw_items:
        if "pair_id" in r:
            if r.get("pair_role") not in ("root", "child"):
                bad(f"{r['id']}: pair_role {r.get('pair_role')!r}")
            pairs.setdefault(r["pair_id"], []).append(r)
    for pid, prs in pairs.items():
        if len(prs) != 2 or {x["pair_role"] for x in prs} != {"root", "child"}:
            bad(f"pair {pid}: root/child 정확히 1건씩 아님")
            continue
        root = next(x for x in prs if x["pair_role"] == "root")
        child = next(x for x in prs if x["pair_role"] == "child")
        if root["domain"] != child["domain"] or root["language"] != child["language"]:
            bad(f"pair {pid}: domain/language 불일치")
        if root["split"] != child["split"]:
            bad(f"pair {pid}: split 불일치")
        if len(root["accepted"]) != 1 or len(child["accepted"]) != 1:
            bad(f"pair {pid}: accepted 각 1건 아님")
            continue
        rp, cp = root["accepted"][0]["path"], child["accepted"][0]["path"]
        if not cp.startswith(rp + "/"):
            bad(f"pair {pid}: root path가 child path의 세그먼트 prefix 아님 ({rp} !< {cp})")
        if (root["accepted"][0]["method"], rp) == (child["accepted"][0]["method"], cp):
            bad(f"pair {pid}: root/child endpoint 동일")

    if errs:
        raise ValueError("§4.1 정적 검증 실패:\n  - " + "\n  - ".join(errs))


def _load_and_validate_queries(
    valid_by_doc: dict[str, set[tuple[str, str]]],
    queries_file: Path,
    split: str | None,
    corpus_sha: dict[str, str],
) -> list[EvalQuery]:
    """질의 파일을 읽고 라벨 검증 게이트를 통과시킨다(§3.3 / §4.1).

    오타/추정 라벨이 조용히 미검출(rank=None)로 집계되는 것을 막기 위해,
    실행 초입에 명확한 에러로 죽인다. 확장 스키마 파일이면 §4.1 정적 검증도 돈다.
    """
    raw_items = json.loads(queries_file.read_text())
    is_gate_schema = bool(raw_items) and "evaluation_role" in raw_items[0]

    if is_gate_schema:
        _validate_gate_schema(raw_items, valid_by_doc, corpus_sha)
        # 확장 스키마인데 --split 미지정이면 diagnostic 4건이 headline·category 집계에
        # 조용히 섞인다(§4.1-10). 기본을 gate+holdout 로 잡고, diagnostic 은 명시할 때만.
        if split is None:
            split = "all"
    else:
        if split is not None:
            raise ValueError("--split 은 확장 스키마(queries_gate_v1.json)에서만 쓴다")
        bad = [
            (item["query"], acc["doc"], acc["method"], acc["path"])
            for item in raw_items
            for acc in item["accepted"]
            if (acc["method"], acc["path"]) not in valid_by_doc.get(acc["doc"], set())
        ]
        if bad:
            raise ValueError(f"미존재 라벨(프리즈 코퍼스에 없는 accepted 엔드포인트): {bad}")

    if split == "gate":
        raw_items = [r for r in raw_items if r.get("split") == "gate"]
    elif split == "holdout":
        raw_items = [r for r in raw_items if r.get("split") == "holdout"]
    elif split == "diagnostic":
        raw_items = [r for r in raw_items if r.get("split") == "diagnostic"]
    elif split == "all":
        raw_items = [r for r in raw_items if r.get("split") in ("gate", "holdout")]

    return [
        EvalQuery(
            id=item["id"],
            query=item["query"],
            category=item["category"],
            accepted=[(acc["method"], acc["path"]) for acc in item["accepted"]],
            variants=item.get("variants", []),
            domain=item.get("domain", ""),
            language=item.get("language", ""),
            evaluation_role=item.get("evaluation_role", "scored"),
            split=item.get("split", ""),
            answer_mode=item.get("answer_mode", "any"),
            pair_id=item.get("pair_id"),
            pair_role=item.get("pair_role"),
        )
        for item in raw_items
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("rrf", "fallback", "both"), default="both")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument(
        "--queries-file",
        default=str(_DIR / "queries.json"),
        help="질의셋 경로. 기본값은 레거시 queries.json 전체. 확장 게이트셋은 queries_gate_v1.json.",
    )
    parser.add_argument(
        "--split",
        choices=("gate", "holdout", "all", "diagnostic"),
        default=None,
        help="확장 스키마 전용. scored를 split으로 거른다(all=gate+holdout). 미지정 시 파일 전체.",
    )
    parser.add_argument(
        "--with-variants",
        action="store_true",
        help="queries.json의 variants(클라 LLM이 제공했을 영문 변형)를 query_variants로 함께 넘겨 재측정한다(doc/30 §7.3).",
    )
    parser.add_argument(
        "--latency-reps",
        type=int,
        default=5,
        help="질의당 반복 검색 횟수. n=20 질의 그대로는 p99 표본이 1건(=max)이라 해상도가 없어, "
        "기본 5회 반복으로 전략당 표본을 100건까지 확보한다(정확도 순위는 1회차만 채점).",
    )
    return parser.parse_args()


@dataclass
class StrategyRun:
    ranks: list[int | None]
    latencies_ms: list[float]  # 반복 포함 전체 표본(percentile 계산용)
    #: 질의별 accepted 각 항목의 개별 순위(정렬 = eq.accepted). C6 coverage/complete·pair 표에 쓴다.
    per_accepted_ranks: list[list[int | None]]
    #: 검색 반환 list 자체가 빈 질의 수(§3.5 empty_result_rate)
    empty_count: int


def _rank_of_one(candidates, method: str, path: str) -> int | None:
    for i, c in enumerate(candidates, start=1):
        if (c.method, c.path) == (method, path):
            return i
    return None


def _run_strategy(
    bundle, queries: list[EvalQuery], top_k: int, with_variants: bool, latency_reps: int
) -> StrategyRun:
    ranks: list[int | None] = []
    per_accepted_ranks: list[list[int | None]] = []
    latencies_ms: list[float] = []
    empty_count = 0
    for eq in queries:
        options = CandidateSearchOptions(
            top_k=top_k,
            query_variants=eq.variants if with_variants and eq.variants else None,
        )
        start = time.perf_counter()
        candidates = bundle.candidate_search.search(eq.query, options)
        latencies_ms.append((time.perf_counter() - start) * 1000)
        ranks.append(_rank_of_answer(candidates, eq.accepted))
        per_accepted_ranks.append([_rank_of_one(candidates, m, p) for m, p in eq.accepted])
        if not candidates:
            empty_count += 1
        for _ in range(latency_reps - 1):
            start = time.perf_counter()
            bundle.candidate_search.search(eq.query, options)
            latencies_ms.append((time.perf_counter() - start) * 1000)
    return StrategyRun(
        ranks=ranks,
        latencies_ms=latencies_ms,
        per_accepted_ranks=per_accepted_ranks,
        empty_count=empty_count,
    )


def _percentile(values: list[float], p: float) -> float:
    """`p`(0~100) 백분위수. `statistics.quantiles`는 n=1일 때 죽으므로 직접 처리한다."""
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]


def _print_latency_summary(label: str, latencies_ms: list[float]) -> None:
    p50 = _percentile(latencies_ms, 50)
    p95 = _percentile(latencies_ms, 95)
    p99 = _percentile(latencies_ms, 99)
    print(f"- {label}: n={len(latencies_ms)} | p50 {p50:.1f}ms | p95 {p95:.1f}ms | p99 {p99:.1f}ms")


def _print_category_breakdown(queries: list[EvalQuery], ranks_by_strategy: dict[str, list[int | None]]) -> None:
    print("\n### 카테고리별 분해(Recall@3 / MRR)")
    categories = sorted({eq.category for eq in queries})
    strategies = list(ranks_by_strategy)
    header = " | ".join(f"{s} Recall@3 | {s} MRR" for s in strategies)
    print(f"| 카테고리 | n | {header} |")
    print("|---|---|" + "---|" * (2 * len(strategies)))
    for cat in categories:
        idxs = [i for i, eq in enumerate(queries) if eq.category == cat]
        cells = []
        for s in strategies:
            summary = _summarize([ranks_by_strategy[s][i] for i in idxs])
            cells.append(f"{summary.recall[3]:.0%} | {summary.mrr:.3f}")
        print(f"| {cat} | {len(idxs)} | " + " | ".join(cells) + " |")


def _print_pair_table(
    queries: list[EvalQuery], runs: dict[str, StrategyRun], top_k: int
) -> None:
    """§3.4 route pair 보조 표. 미검출/top-k 밖은 (top_k+1)로 cap 한 순위를 찍는다.

    baseline vs candidate delta·non-regression 판정은 두 worktree 실행 결과를 lead가 대조한다.
    """
    pids = sorted({eq.pair_id for eq in queries if eq.pair_id})
    if not pids:
        return
    cap = top_k + 1
    idx_by_id = {eq.id: i for i, eq in enumerate(queries)}
    strategies = list(runs)
    print(f"\n### route pair 순위 (미검출·top{top_k} 밖 = {cap}로 cap)")
    print("| pair | split | domain | role | accepted | " + " | ".join(f"{s} r_s" for s in strategies) + " |")
    print("|---|---|---|---|---|" + "---|" * len(strategies))
    for pid in pids:
        members = [eq for eq in queries if eq.pair_id == pid]
        for role in ("root", "child"):
            eq = next((m for m in members if m.pair_role == role), None)
            if eq is None:
                continue
            i = idx_by_id[eq.id]
            m, p = eq.accepted[0]
            cells = []
            for s in strategies:
                r = runs[s].ranks[i]
                cells.append(str(r if (r is not None and r <= top_k) else cap))
            print(f"| {pid} | {eq.split} | {eq.domain} | {role} | {m} {p} | " + " | ".join(cells) + " |")


def _print_c6_aux(
    queries: list[EvalQuery], runs: dict[str, StrategyRun], top_k: int
) -> None:
    """§3.3 C6 보조 게이트: coverage@k = top-k에서 찾은 accepted 수 / 2, complete@k = 둘 다 존재."""
    c6_idx = [i for i, eq in enumerate(queries) if eq.answer_mode == "all"]
    if not c6_idx:
        return
    strategies = list(runs)
    print(f"\n### C6 all-of 보조 지표 (coverage@{top_k} / complete@{top_k})")
    print("| id | " + " | ".join(f"{s} cov | {s} complete" for s in strategies) + " |")
    print("|---|" + "---|---|" * len(strategies))
    agg: dict[str, list[tuple[float, int]]] = {s: [] for s in strategies}
    for i in c6_idx:
        eq = queries[i]
        cells = []
        for s in strategies:
            per = runs[s].per_accepted_ranks[i]
            found = sum(1 for r in per if r is not None and r <= top_k)
            cov = found / len(per)
            complete = 1 if found == len(per) else 0
            agg[s].append((cov, complete))
            cells.append(f"{cov:.2f} | {complete}")
        print(f"| {eq.id} | " + " | ".join(cells) + " |")
    print("\n| 전략 | 평균 coverage | complete 비율 |")
    print("|---|---|---|")
    for s in strategies:
        rows = agg[s]
        mean_cov = sum(c for c, _ in rows) / len(rows)
        comp_ratio = sum(k for _, k in rows) / len(rows)
        print(f"| {s} | {mean_cov:.3f} | {comp_ratio:.1%} |")


def main() -> None:
    args = _parse_args()
    strategies = ("fallback", "rrf") if args.strategy == "both" else (args.strategy,)

    manifest = _load_manifest()
    texts = _load_corpus_texts(manifest)
    corpus_sha = {e["source_key"]: e["content_sha256"] for e in manifest}
    queries = _load_and_validate_queries(
        _valid_endpoints_by_doc(texts), Path(args.queries_file), args.split, corpus_sha
    )
    doc_type_by_key = {e["source_key"]: e["doc_type"] for e in manifest}

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)
        state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
        print("is_semantic:", state.embedding_provider.is_semantic)
        print("with_variants:", args.with_variants)
        bundle = next(build_services(state))
        rss_before_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        for source_key, raw in texts.items():
            result = bundle.sync_service.register(
                project="default",
                source_url=None,
                raw_document=raw,
                doc_type=doc_type_by_key[source_key],
            )
            print(f"등록: {source_key} -> document_id={result.document.id} endpoints={result.endpoints_count}")
        rss_after_index_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        ranks_by_strategy: dict[str, StrategyRun] = {}
        cpu_before = resource.getrusage(resource.RUSAGE_SELF)
        for strategy in strategies:
            state.search_strategy = strategy
            b = next(build_services(state))
            ranks_by_strategy[strategy] = _run_strategy(
                b, queries, args.top_k, args.with_variants, args.latency_reps
            )
        cpu_after = resource.getrusage(resource.RUSAGE_SELF)
        rss_peak_kb = cpu_after.ru_maxrss

        print(f"\n| # | 질의 | 카테고리 | 정답 | " + " | ".join(f"{s} 순위" for s in strategies) + " |")
        print("|---|---|---|---|" + "---|" * len(strategies))
        for i, eq in enumerate(queries):
            accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)
            cells = []
            for s in strategies:
                r = ranks_by_strategy[s].ranks[i]
                cells.append(str(r) if r is not None else "미검출")
            print(f"| {eq.id} | {eq.query} | {eq.category} | {accepted_str} | " + " | ".join(cells) + " |")

        print("\n### 지표 요약")
        print(f"(n={len(queries)}, top_k={args.top_k})")
        for s in strategies:
            run = ranks_by_strategy[s]
            ranks = run.ranks
            print(_format_summary_line(s, _summarize(ranks)))
            # §3.5: answer_miss@10 = 정답을 top-10 안에서 못 찾음 (1 - Recall@10)
            miss = sum(1 for r in ranks if r is None or r > 10)
            print(f"  - {s} answer_miss@10: {miss}/{len(ranks)} ({miss / len(ranks):.1%})")
            # §3.5: empty_result_rate = 검색이 빈 결과를 반환 (miss와 별개 지표)
            print(f"  - {s} empty_result_rate: {run.empty_count}/{len(ranks)} "
                  f"({run.empty_count / len(ranks):.1%})")

        print(f"\n### Latency (질의당 {args.latency_reps}회 반복, 콜드 1회차 포함)")
        for s in strategies:
            _print_latency_summary(s, ranks_by_strategy[s].latencies_ms)

        print("\n### Resource")
        print(f"- Memory: 색인 전 {rss_before_kb / 1024:.1f}MB -> 색인 후 {rss_after_index_kb / 1024:.1f}MB "
              f"-> 검색 종료 시점 peak RSS {rss_peak_kb / 1024:.1f}MB (ru_maxrss, 프로세스 누적 peak)")
        print(f"- CPU: 검색 루프 구간 사용자 {cpu_after.ru_utime - cpu_before.ru_utime:.3f}s "
              f"+ 시스템 {cpu_after.ru_stime - cpu_before.ru_stime:.3f}s "
              f"(질의 {sum(len(r.latencies_ms) for r in ranks_by_strategy.values())}건 합산)")
        print("- Search cost: $0 (로컬 CPU 임베딩·자체 호스팅 Postgres, 외부 과금 API 미호출 — 측정이 아닌 구조상 선언)")

        _print_category_breakdown(queries, {s: r.ranks for s, r in ranks_by_strategy.items()})
        _print_pair_table(queries, ranks_by_strategy, args.top_k)
        _print_c6_aux(queries, ranks_by_strategy, args.top_k)

        if args.strategy == "both":
            print("\n### 회귀(rrf가 fallback보다 나빠진 케이스, MRR 기준 병행 표기)")
            fb_ranks, rrf_ranks = ranks_by_strategy["fallback"].ranks, ranks_by_strategy["rrf"].ranks
            regressions = [
                (eq.query, fb, rr)
                for eq, fb, rr in zip(queries, fb_ranks, rrf_ranks, strict=True)
                if fb != rr and not (fb is None or (rr is not None and rr < fb))
            ]
            if regressions:
                from metrics import reciprocal_rank  # type: ignore[import-not-found]

                for q, fb, rr in regressions:
                    mrr_delta = reciprocal_rank(rr) - reciprocal_rank(fb)
                    print(f"- {q!r}: fallback={fb} -> rrf={rr} (MRR delta {mrr_delta:+.3f})")
            else:
                print("- 없음")
    finally:
        _drop_temp_db(admin_url, dbname)


if __name__ == "__main__":
    main()
