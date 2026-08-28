# 검색 품질 평가 2026-08-28 — 색인 시점 구조 신호(가중 tsvector) · p02 개발 회귀 게이트

`docs/architect-review/78_endpoint_index_structure_signal_design.md` §8.2 의 p02
개발 회귀 게이트. 계획 79 Task 11.

## 대상 상태

- 측정 대상 commit SHA: `29d4534` (branch `weighted-tsvector-track` HEAD, 워킹트리
  clean — `git status --porcelain` 비어 있음). 계획 79 Task 1~10 커밋 완료 상태.
- 계통: `9d6fac3`(구조 파생) → `6fba42a`(BuiltChunk) → `d3fd1fb`(가중 `search_tsv`
  + `lexical_field` 분기) → `dd2cc2f`(색인 경로 영속화) → `f438218`(롤백 스위치)
  → `914e453`(write-back 가드) → `25296bc`(백필 스크립트) → `34ebc2a`(러너 축)
  → `29d4534`(ADR).
- 임베딩: `intfloat/multilingual-e5-small` (dim 384), is_semantic: true (4회 실행 모두).
- 코퍼스 content_sha256: stripe=`3653ad45bbec`, github=`80850db290cd`
  (full: stripe `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5`,
  github `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d`)

## 공유 인덱스 (Task 11 Step 1 preflight)

- shared DB: `rrfeval_56b1a4d1`
- endpoint 수 / endpoint chunk 수: github=1220, stripe=589 (합계 1809)
- `(doc, method, path, chunk_id)` sorted SHA-256:
  `7b794a65eca626f7428134cafc2a841e89e2e9063166f4480901d72fe805f20a`
- query SHA-256: `6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8`
  (`queries_gate_v1.json`)
- fixture commit: `29d4534e63c784510c2626ec327a0f307cbc401a`
- 4회 실행(text/structured × variants OFF/ON) 모두 위 세 지문(`(doc,method,path,
  chunk_id)` SHA · query SHA · fixture commit)이 동일 — 같은 물리 인덱스 위에서
  lexical field 만 바꾼 짝 실행임이 확인된다.

## 백필 (Task 11 Step 2)

명령:
```
DOCS_MCP_DATABASE_URL='postgresql+psycopg://.../rrfeval_56b1a4d1' \
  uv run python -m app.scripts.backfill_endpoint_structure
```
결과 로그: `구조 신호 백필 완료: 총 1809개 청크` (github 1220 + stripe 589, 기대치 일치).

## 벡터 arm 불변 확인 (Task 11 Step 3)

`app.chunk` 대상, 백필 직전/직후 동일 쿼리.

| 쿼리 | 백필 전 | 백필 후 | 기대 |
|---|---|---|---|
| `md5(string_agg(id\|\|':'\|\|text ORDER BY id))` | `5dc075e98a930aa02fc576f7e5c31466` | `5dc075e98a930aa02fc576f7e5c31466` | 동일 (text 불변) |
| `count(*) endpoint AND embedding IS NULL` | 0 | 0 | 0 |
| `count(*) endpoint AND leaf_text = ''` | 1 | 1 | 1 (architect 재해석 — 아래) |
| `count(*) chunk_type<>'endpoint' AND search_tsv IS NOT NULL` | 0 | 0 | 0 |
| `count(*) endpoint AND search_tsv IS NULL` | — | 0 | 0 |

- `text` md5 백필 전후 완전 일치 → `text`/`embedding` 무변경 확인.
- `leaf_text = ''` 1건은 `GET /` (github "GitHub API Root", `operation_id=meta/root`,
  `tags=[meta]`). path `/` 에 leaf 리터럴이 없어 결정적 파생 결과가 빈 문자열이다.
  백필 전(색인 경로 산출)과 후(백필 재계산)가 같은 1건 — 백필 결함이 아니라 root
  path 의 구조적 특성이다. `context_text`·`intent_text`·`search_tsv` 및 `text`·
  `embedding` 은 정상/불변.
- **architect 판정(2026-08-28)**: 설계 78 §4.1 · 계획 79 Task 1 이 literal 세그먼트가
  없는 `/` 의 `leaf_text=''` 를 명시하므로 `GET /` 1건은 정상 계약이다. Task 11 Step 3
  의 일괄 기대 0 이 계획 오류이며, frozen corpus 기대를 1건(`GET /`)으로 해석한다.
  해당 쿼리는 진단값으로 둔다. 코드·alias 조정 없이 p02 PASS 승인, Task 12 진행.

## p02 route pair (Task 11 Step 4·5)

- 명령: `uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --mode eval
  --db-url <...>/rrfeval_56b1a4d1 --queries-file
  tests/fixtures/corpus_eval/queries_gate_v1.json --split all --strategy rrf
  --top-k 10 --lexical-field {text|structured} [--with-variants]`
- g003 = p02 root (`GET /repos/{owner}/{repo}`, 질의 "저장소 기본 정보를 가져와줘", ko)
- g004 = p02 child (`GET /repos/{owner}/{repo}/topics`, 질의 "저장소에 달린 토픽만
  따로 조회해줘", ko), split=holdout
- 미검출·top10 밖 = cap 11. 값은 각 실행 `### route pair 순위` 표에서 전사.
- `delta(q) = rank_structured(q) - rank_text(q)`

| variants | text root | structured root | delta(root) | text child | structured child | delta(child) |
|---|--:|--:|--:|--:|--:|--:|
| OFF (`--with-variants` 없음) | 4 | 4 | 0 | 11 | 11 | 0 |
| ON (`--with-variants`) | 11 | 11 | 0 | 6 | 6 | 0 |

```
PASS  <=>  OFF/ON 각각에서 delta(root) <= 0 이고 delta(child) <= 0
```

**판정: PASS.** OFF·ON 네 delta 전부 0 — structured lexical field 로 바꿔도 p02
route pair 순위가 악화되지 않는다.

g003/g004 는 한글 전용 질의라 키워드 arm 이 영문 파생 leaf/intent/context 토큰과
직접 매칭되지 않고(ADR-0005 한계 절), variants ON 에서 영문 변형이 들어와도 이
pair 의 RRF 최종 순위는 움직이지 않았다 — 순위는 벡터 arm 이 지배한다.

## 부수 관찰 — 전체 route pair (판정 아님, 회귀 스캔)

같은 4회 실행의 `### route pair 순위` 표 전량 대조. cap 11.

- variants OFF: structured 가 text 대비 **악화된 pair-member 0건**, 개선 7건
  (p03 root 10→2, p03 child 2→1, p04 root 11→3, p09 root 3→2, p10 child 11→2,
  p12 root 3→1, p12 child 2→1).
- variants ON: 악화 0건, 개선 7건 (p03 root 10→2, p03 child 2→1, p04 root 11→3,
  p09 root 3→2, p10 child 11→2, p12 root 3→1, p12 child 2→1).

## 부수 관찰 — aggregate headline (판정 아님)

`run_corpus_eval.py` headline (scored n=120). structured 가 네 지표에서 text 를
전 구간 상회, empty_result_rate 는 4회 모두 0/120.

| 실행 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@10 |
|---|--:|--:|--:|--:|--:|--:|
| text / OFF | 23% | 39% | 45% | 60% | 0.336 | 0.398 |
| text / ON | 23% | 42% | 53% | 73% | 0.372 | 0.457 |
| structured / OFF | 30% | 47% | 52% | 64% | 0.401 | 0.458 |
| structured / ON | 30% | 50% | 61% | 78% | 0.438 | 0.520 |

v1 은 노출된 개발 코퍼스이므로 이 수치로 승급하지 않는다(verdict 74 §6.2).

## 산출물

- preflight 로그: `scratchpad/t11_preflight.log`
- 4회 eval 로그: `scratchpad/t11_eval_{text,structured}_{off,on}.log`
- 재현: 위 §공유 인덱스 / §p02 route pair 명령 그대로. shared DB 는 Task 12 Step 5
  (`--mode cleanup`)에서 정리.
