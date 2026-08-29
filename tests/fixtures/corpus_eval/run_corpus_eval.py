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
import unicodedata
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


def _norm_query_strict(q: str) -> str:
    """v2 신규성 계약(§3)용 정규화: Unicode NFKC → trim → whitespace collapse → casefold."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", q).strip().casefold())


#: --queries-file basename → 그 데이터셋 버전의 frozen manifest. 러너 하드코딩을
#: 없애고(§6) 각 버전이 자기 manifest·quota·SHA 를 검증하게 한다.
_MANIFEST_BY_QUERY_FILE = {
    "queries_gate_v1.json": "gate_manifest_v1.json",
    "queries_gate_v2.json": "gate_manifest_v2.json",
}


def _validate_v2_novelty(raw_items: list[dict]) -> None:
    """§3 v2 신규성 계약. legacy queries.json 과 v1 queries_gate_v1.json 을 함께 읽어
    ID/정규화 query/variant/accepted label/endpoint·query pair/route pair/C6 를 전부
    대조한다. DB 검색을 시작하기 전 로더 단계에서 돈다(§6). 하나라도 위반하면 죽는다.
    """
    errs: list[str] = []

    def bad(msg: str) -> None:
        errs.append(msg)

    legacy = json.loads((_DIR / "queries.json").read_text())
    v1 = json.loads((_DIR / "queries_gate_v1.json").read_text())

    def acc_tuples(r: dict) -> set[tuple[str, str, str]]:
        return {(a["doc"], a["method"], a["path"]) for a in r["accepted"]}

    def route_family(path: str) -> str:
        segs = [s for s in path.split("/") if s and not s.startswith("{")]
        return "/".join(segs[:2])

    # 1) ID 패턴/번호/불교집합
    ids = [r["id"] for r in raw_items]
    for r in raw_items:
        if not re.fullmatch(r"v2g\d{3}", r["id"]):
            bad(f"{r['id']}: v2 id 패턴(v2gNNN) 아님")
    nums = sorted(int(x[3:]) for x in ids if re.fullmatch(r"v2g\d{3}", x))
    if nums != list(range(1, 125)):
        bad("v2 id 번호가 v2g001~v2g124 연속 아님")
    if {r["id"] for r in v1} & set(ids):
        bad(f"v2 id 가 v1 과 겹침: {sorted({r['id'] for r in v1} & set(ids))}")
    v2_pair_ids = {r["pair_id"] for r in raw_items if r.get("pair_id")}
    for pid in v2_pair_ids:
        if not re.fullmatch(r"v2p\d{2}", pid):
            bad(f"{pid}: v2 pair id 패턴(v2pNN) 아님")

    # 2) 정규화 query/variant 가 legacy/v1 및 v2 내부와 불일치 (NFKC strict)
    prior_q: set[str] = set()
    for src in (legacy, v1):
        for r in src:
            prior_q.add(_norm_query_strict(r["query"]))
            for v in r.get("variants", []) or []:
                prior_q.add(_norm_query_strict(v))
    seen: set[str] = set()
    for r in raw_items:
        n = _norm_query_strict(r["query"])
        if n in prior_q:
            bad(f"{r['id']}: 정규화 query 가 legacy/v1 과 중복 {r['query']!r}")
        if n in seen:
            bad(f"{r['id']}: 정규화 query 가 v2 내부에서 중복 {r['query']!r}")
        seen.add(n)
        for v in r.get("variants", []) or []:
            nv = _norm_query_strict(v)
            if nv in prior_q:
                bad(f"{r['id']}: 정규화 variant 가 legacy/v1 과 중복 {v!r}")
            if nv in seen:
                bad(f"{r['id']}: 정규화 variant 가 다른 v2 query/variant 와 중복 {v!r}")
            seen.add(nv)

    # 3) accepted label(전량) 이 v1 scored+diagnostic accepted tuple 에 없음
    v1_acc: set[tuple[str, str, str]] = set()
    for r in v1:
        v1_acc |= acc_tuples(r)
    for r in raw_items:
        for t in acc_tuples(r):
            if t in v1_acc:
                bad(f"{r['id']}: accepted tuple 이 v1 재사용 {t}")

    # 4) v2 내부에서 scored 레코드끼리 accepted tuple 공유 금지(C6 두 endpoint 포함)
    owner: dict[tuple[str, str, str], str] = {}
    for r in raw_items:
        for t in acc_tuples(r):
            if t in owner:
                bad(f"{r['id']}: accepted tuple {t} 가 {owner[t]} 과 중복")
            else:
                owner[t] = r["id"]

    # 5) endpoint/query pair 조합(정규화 query + 정렬된 accepted tuple 집합) 이 legacy/v1 에 없음
    def combo(r: dict) -> tuple:
        return (_norm_query_strict(r["query"]),
                tuple(sorted((a["doc"], a["method"], a["path"]) for a in r["accepted"])))

    prior_combo = {combo(r) for r in legacy} | {combo(r) for r in v1}
    for r in raw_items:
        if combo(r) in prior_combo:
            bad(f"{r['id']}: endpoint/query 조합이 legacy/v1 재사용")

    # 6) route pair: v1 pair id / 두 accepted endpoint / route family 재사용 금지
    v1_pairs: dict[str, list[dict]] = {}
    for r in v1:
        if r.get("pair_id"):
            v1_pairs.setdefault(r["pair_id"], []).append(r)
    v1_pair_endpoints: set[tuple[str, str, str]] = set()
    v1_pair_families: set[tuple[str, str]] = set()
    for prs in v1_pairs.values():
        for r in prs:
            a = r["accepted"][0]
            v1_pair_endpoints.add((a["doc"], a["method"], a["path"]))
            v1_pair_families.add((r["domain"], route_family(a["path"])))
    v2_pairs: dict[str, list[dict]] = {}
    for r in raw_items:
        if r.get("pair_id"):
            v2_pairs.setdefault(r["pair_id"], []).append(r)
    if sorted(v2_pairs) != [f"v2p{i:02d}" for i in range(1, 13)]:
        bad(f"v2 pair id 집합이 v2p01~v2p12 정확히 아님: {sorted(v2_pairs)}")
    for pid, prs in v2_pairs.items():
        if len(prs) != 2:
            bad(f"{pid}: pair 멤버 2건 아님 ({len(prs)})")
    for pid, prs in v2_pairs.items():
        if pid in v1_pairs:
            bad(f"{pid}: pair id 가 v1 재사용")
        fam: set[tuple[str, str]] = set()
        for r in prs:
            a = r["accepted"][0]
            if (a["doc"], a["method"], a["path"]) in v1_pair_endpoints:
                bad(f"{pid}: pair accepted endpoint 가 v1 pair 재사용 {a['method']} {a['path']}")
            fam.add((r["domain"], route_family(a["path"])))
        if fam & v1_pair_families:
            bad(f"{pid}: route family 가 v1 pair 재사용 {sorted(fam & v1_pair_families)}")

    # 7) C6: 두 endpoint 모두 v1 accepted 와 불일치 + v2 내부 C6 끼리 중복 없음
    c6_sets: list[tuple] = []
    for r in raw_items:
        if r["category"] != _C6:
            continue
        s = tuple(sorted((a["doc"], a["method"], a["path"]) for a in r["accepted"]))
        if s in c6_sets:
            bad(f"{r['id']}: C6 endpoint 집합이 다른 C6 와 중복")
        c6_sets.append(s)
        for t in acc_tuples(r):
            if t in v1_acc:
                bad(f"{r['id']}: C6 endpoint 가 v1 accepted {t}")

    if errs:
        raise ValueError("§3 v2 신규성 검증 실패:\n  - " + "\n  - ".join(errs))


def _verify_manifest_shas(queries_file: Path, raw_items: list[dict], manifest_path: Path) -> None:
    """manifest 의 query_sha256 / split_sha256 을 파일 실제값과 대조한다(§5·§6).

    프리즈된 fixture 와 manifest 가 어긋난 채로 평가가 도는 것을 로더 단계에서 막는다.
    split_sha256 은 §5.4 직렬화(scored 를 id 오름차순, `<id><TAB><split><LF>`)로 재계산한다.
    """
    if not manifest_path.exists():
        raise ValueError(f"manifest 없음: {manifest_path.name}")
    man = json.loads(manifest_path.read_text())
    errs: list[str] = []
    want_q = man.get("query_sha256")
    if want_q:
        got_q = hashlib.sha256(queries_file.read_bytes()).hexdigest()
        if got_q != want_q:
            errs.append(f"query_sha256 불일치: 파일 {got_q} != manifest {want_q}")
    want_s = man.get("split_sha256")
    if want_s:
        scored = sorted(
            (r for r in raw_items if r.get("evaluation_role") == "scored"),
            key=lambda r: r["id"],
        )
        blob = "".join(f"{r['id']}\t{r['split']}\n" for r in scored)
        got_s = hashlib.sha256(blob.encode()).hexdigest()
        if got_s != want_s:
            errs.append(f"split_sha256 불일치: 산출 {got_s} != manifest {want_s}")
    if errs:
        raise ValueError(f"{manifest_path.name} SHA 검증 실패:\n  - " + "\n  - ".join(errs))


def _validate_gate_schema(
    raw_items: list[dict],
    valid_by_doc: dict[str, set[tuple[str, str]]],
    corpus_sha: dict[str, str],
    manifest_path: Path,
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
    mf = manifest_path
    if mf.exists():
        man = json.loads(mf.read_text())
        if man.get("corpus_sha256", {}).get("stripe") != corpus_sha.get("stripe"):
            bad(f"{mf.name} corpus_sha256.stripe 불일치")
        if man.get("corpus_sha256", {}).get("github") != corpus_sha.get("github"):
            bad(f"{mf.name} corpus_sha256.github 불일치")

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
        if queries_file.name not in _MANIFEST_BY_QUERY_FILE:
            raise ValueError(
                f"등록되지 않은 gate 스키마 질의 파일: {queries_file.name} "
                f"(허용: {sorted(_MANIFEST_BY_QUERY_FILE)})"
            )
        manifest_path = _DIR / _MANIFEST_BY_QUERY_FILE[queries_file.name]
        _validate_gate_schema(raw_items, valid_by_doc, corpus_sha, manifest_path)
        _verify_manifest_shas(queries_file, raw_items, manifest_path)
        if queries_file.name == "queries_gate_v2.json":
            _validate_v2_novelty(raw_items)
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
        "--mode",
        choices=("full", "preflight", "eval", "determinism", "cleanup"),
        default="full",
        help="full=자체 임시DB 생성·색인·drop(기본, 기존 동작). "
        "preflight=공유 임시DB 1회 생성+corpus 색인 후 유지(DB URL·fingerprint 출력). "
        "eval=--db-url의 공유 인덱스에 read-only 평가(색인·drop 생략). "
        "determinism=§4.4 결정성 검증(OFF 2회 동일 + variants 없는 질의 OFF/ON 동일). "
        "cleanup=--db-url 임시DB drop.",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="eval/determinism/cleanup 모드에서 쓸 공유 임시 DB 접속 URL(preflight 출력값).",
    )
    parser.add_argument(
        "--lexical-field",
        choices=("text", "structured"),
        default="text",
        help="키워드 arm 이 쓸 lexical 벡터. text=현행 chunk.text_tsv(baseline), "
        "structured=가중 chunk.search_tsv(78번 설계 candidate). 같은 공유 인덱스 위에서 "
        "이 값만 바꿔 baseline/candidate 를 비교한다(78번 §8.1).",
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


def _capped(rank: int | None, top_k: int) -> int:
    """미검출·top-k 밖을 (top_k+1)로 cap 한 per-query 순위(결정성 비교용)."""
    return rank if rank is not None and rank <= top_k else top_k + 1


def _fixture_commit() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _doc_key_by_id(engine) -> dict[str, str]:
    """document_id → 소스 키. endpoint 수(stripe 589 / github 1220)로 식별한다."""
    from sqlalchemy import text as _sql

    with engine.connect() as conn:
        rows = conn.execute(
            _sql("SELECT document_id, count(*) FROM app.api_endpoint GROUP BY document_id")
        ).all()
    known = {589: "stripe", 1220: "github"}
    return {doc_id: known.get(int(n), f"doc:{doc_id[:8]}") for doc_id, n in rows}


def _print_shared_index_fingerprint(
    engine, queries_file: Path, lexical_field: str = "text"
) -> None:
    """§4.3 shared-index 지문. 네 실행이 같은 물리 인덱스를 읽는지 대조하는 SELECT 전용 요약."""
    from sqlalchemy import text as _sql

    key_by_id = _doc_key_by_id(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            _sql(
                "SELECT e.document_id, e.method, e.path, ch.id "
                "FROM app.chunk ch JOIN app.api_endpoint e ON ch.ref_id = e.id "
                "WHERE ch.chunk_type = 'endpoint'"
            )
        ).all()
    triples = sorted(
        (key_by_id.get(doc_id, doc_id), method, path, chunk_id)
        for doc_id, method, path, chunk_id in rows
    )
    fp = hashlib.sha256("\n".join("\t".join(t) for t in triples).encode()).hexdigest()
    # endpoint 수는 chunk join 이 아니라 api_endpoint 원본으로 센다.
    with engine.connect() as conn:
        ep_rows = conn.execute(
            _sql("SELECT document_id, count(*) FROM app.api_endpoint GROUP BY document_id")
        ).all()
    endpoints = {key_by_id.get(d, d): int(n) for d, n in ep_rows}
    chunk_by_key: dict[str, int] = {}
    for k, _m, _p, _c in triples:
        chunk_by_key[k] = chunk_by_key.get(k, 0) + 1
    qsha = hashlib.sha256(queries_file.read_bytes()).hexdigest()
    print("\n### shared-index fingerprint")
    print("- endpoint 수: " + ", ".join(f"{k}={endpoints.get(k, 0)}" for k in sorted(endpoints)))
    print("- endpoint chunk 수: " + ", ".join(f"{k}={chunk_by_key[k]}" for k in sorted(chunk_by_key)))
    print(f"- (doc, method, path, chunk_id) sorted SHA-256: {fp}")
    print(f"- query SHA-256: {qsha}")
    print(f"- fixture commit: {_fixture_commit()}")
    print(f"- lexical field: {lexical_field}")


def _evaluate_and_report(
    state, queries: list[EvalQuery], strategies: tuple[str, ...], args: argparse.Namespace,
    indexed_rss: tuple[int, int] | None,
) -> None:
    """전략별 검색 실행 + 리포트. indexed_rss=None 이면 색인은 별도(preflight) 프로세스."""
    ranks_by_strategy: dict[str, StrategyRun] = {}
    cpu_before = resource.getrusage(resource.RUSAGE_SELF)
    for strategy in strategies:
        state.search_strategy = strategy
        state.search_lexical_field = args.lexical_field
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
    if indexed_rss is not None:
        rss_before_kb, rss_after_index_kb = indexed_rss
        print(f"- Memory: 색인 전 {rss_before_kb / 1024:.1f}MB -> 색인 후 {rss_after_index_kb / 1024:.1f}MB "
              f"-> 검색 종료 시점 peak RSS {rss_peak_kb / 1024:.1f}MB (ru_maxrss, 프로세스 누적 peak)")
    else:
        print(f"- Memory: 검색 종료 시점 peak RSS {rss_peak_kb / 1024:.1f}MB "
              f"(ru_maxrss; 색인은 별도 preflight 프로세스라 색인 전/후 표기 없음)")
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


def _cmd_preflight(args: argparse.Namespace) -> None:
    """§4.3 (1)(2): 임시 DB 1회 생성 + frozen corpus 색인, drop 하지 않고 지문 출력."""
    manifest = _load_manifest()
    texts = _load_corpus_texts(manifest)
    doc_type_by_key = {e["source_key"]: e["doc_type"] for e in manifest}
    content_sha = {e["source_key"]: e["content_sha256"] for e in manifest}

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    engine = create_db_engine(test_url)
    create_all(engine)
    state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
    print("is_semantic:", state.embedding_provider.is_semantic)
    bundle = next(build_services(state))
    doc_id_by_key: dict[str, str] = {}
    for source_key, raw in texts.items():
        result = bundle.sync_service.register(
            project="default", source_url=None,
            raw_document=raw, doc_type=doc_type_by_key[source_key],
        )
        doc_id_by_key[source_key] = result.document.id
        print(f"등록: {source_key} -> document_id={result.document.id} endpoints={result.endpoints_count}")

    print("\n### shared-index preflight")
    print(f"- DB 식별자: {dbname}")
    print(f"- DB URL: {test_url}")
    for k in sorted(content_sha):
        print(f"- {k}: content_sha256={content_sha[k]} document_id={doc_id_by_key.get(k, '?')}")
    _print_shared_index_fingerprint(engine, Path(args.queries_file), args.lexical_field)
    print(
        f"\n임시 DB 유지(drop 안 함). "
        f"평가:  --mode eval --db-url '{test_url}'  |  "
        f"결정성:  --mode determinism --db-url '{test_url}'  |  "
        f"정리:  --mode cleanup --db-url '{test_url}'"
    )


def _cmd_cleanup(args: argparse.Namespace) -> None:
    """§4.3 (4): 네 실행이 끝난 뒤 명시적으로 임시 DB 정리."""
    if not args.db_url:
        raise SystemExit("--mode cleanup 에는 --db-url 필요")
    dbname = args.db_url.rsplit("/", 1)[1]
    admin_url = args.db_url.rsplit("/", 1)[0] + "/docs_mcp"
    _drop_temp_db(admin_url, dbname)
    print(f"임시 DB drop 완료: {dbname}")


def _load_shared_queries(args: argparse.Namespace) -> list[EvalQuery]:
    manifest = _load_manifest()
    texts = _load_corpus_texts(manifest)
    corpus_sha = {e["source_key"]: e["content_sha256"] for e in manifest}
    return _load_and_validate_queries(
        _valid_endpoints_by_doc(texts), Path(args.queries_file), args.split, corpus_sha
    )


def _cmd_determinism(args: argparse.Namespace) -> None:
    """§4.4: 같은 shared index에서 OFF 2회 동일 + variants 없는 질의 OFF/ON 동일 검증."""
    if not args.db_url:
        raise SystemExit("--mode determinism 에는 --db-url 필요")
    queries = _load_shared_queries(args)
    engine = create_db_engine(args.db_url)
    state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
    strategies = ("fallback", "rrf")

    def run(with_variants: bool) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for s in strategies:
            state.search_strategy = s
            b = next(build_services(state))
            r = _run_strategy(b, queries, args.top_k, with_variants, 1)
            out[s] = [_capped(x, args.top_k) for x in r.ranks]
        return out

    off1, off2, on1 = run(False), run(False), run(True)

    problems: list[str] = []
    for s in strategies:
        for i, (a, b) in enumerate(zip(off1[s], off2[s], strict=True)):
            if a != b:
                problems.append(f"{s} {queries[i].id}: OFF 재실행 capped rank 불일치 {a} != {b}")
        for i, (a, b) in enumerate(zip(off1[s], on1[s], strict=True)):
            if not queries[i].variants and a != b:
                problems.append(
                    f"{s} {queries[i].id}: variants 없는 질의인데 OFF/ON capped rank 불일치 {a} != {b}"
                )

    _print_shared_index_fingerprint(engine, Path(args.queries_file), args.lexical_field)
    print("\n### 결정성 preflight (§4.4)")
    if problems:
        print("FAIL — 하네스/검색 결정성 문제. gate 실행 금지, 재회부.")
        for p in problems:
            print(f"- {p}")
        raise SystemExit(1)
    print("PASS — OFF 2회 per-query capped rank 완전 동일, variants 없는 질의 OFF/ON 동일.")


def main() -> None:
    args = _parse_args()

    if args.mode == "preflight":
        _cmd_preflight(args)
        return
    if args.mode == "cleanup":
        _cmd_cleanup(args)
        return
    if args.mode == "determinism":
        _cmd_determinism(args)
        return

    strategies = ("fallback", "rrf") if args.strategy == "both" else (args.strategy,)

    manifest = _load_manifest()
    texts = _load_corpus_texts(manifest)
    corpus_sha = {e["source_key"]: e["content_sha256"] for e in manifest}
    queries = _load_and_validate_queries(
        _valid_endpoints_by_doc(texts), Path(args.queries_file), args.split, corpus_sha
    )
    doc_type_by_key = {e["source_key"]: e["doc_type"] for e in manifest}

    if args.mode == "eval":
        if not args.db_url:
            raise SystemExit("--mode eval 에는 --db-url 필요 (preflight가 출력한 DB URL)")
        engine = create_db_engine(args.db_url)
        state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
        print("is_semantic:", state.embedding_provider.is_semantic)
        print("with_variants:", args.with_variants)
        print(f"shared-index 재사용: {args.db_url} (등록·재색인·drop 생략, read-only)")
        _print_shared_index_fingerprint(engine, Path(args.queries_file), args.lexical_field)
        _evaluate_and_report(state, queries, strategies, args, indexed_rss=None)
        return

    # mode == full: 기존 동작(자체 임시 DB 생성 → 색인 → 평가 → drop) 그대로.
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

        _evaluate_and_report(
            state, queries, strategies, args,
            indexed_rss=(rss_before_kb, rss_after_index_kb),
        )
    finally:
        _drop_temp_db(admin_url, dbname)


if __name__ == "__main__":
    main()
