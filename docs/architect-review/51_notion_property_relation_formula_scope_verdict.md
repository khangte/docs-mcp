# 51. Notion 속성 색인 범위 — relation/formula 판정

- 상태: 판정 완료 (부분 승인)
- 발단: reviewer 리뷰 지적 — `property_plain_text` 가 `relation`/`formula` 를 빈
  문자열로 버리는데, `docs/architect-review/50` §3 P0-3 스펙은 둘 다 지원
  타입으로 명시했고 축소에 대한 이탈 표시·승인 기록이 없다.
- 관련: `50`(Notion nested block 색인 갭 및 설계안),
  `docs/superpowers/plans/2026-08-15-notion-nested-block-indexing.md` Task 7

## 1. 지적의 타당성

**타당하다.** 그리고 이탈을 만든 쪽은 developer 가 아니라 **architect(나)** 다.

`50` §3 P0-3 은 지원 타입으로 `relation(id 만)`·`formula(평문 결과)` 를 적었는데,
구현 계획(`2026-08-15-notion-nested-block-indexing.md`) Task 7 을 쓰면서 내가
"비용 대비 이득이 작다"는 사유로 두 타입을 조용히 뺐다. developer 는 계획서를
그대로 따랐을 뿐이므로 developer 측 설계 이탈이 아니다. 계획서가 설계 문서를
축소할 때 그 사실을 표시하지 않은 것이 절차 결함이다.

reviewer 의 기술적 지적도 맞다 — **둘 다 추가 API 호출이 필요 없다.** 두 값 모두
이미 `GET /pages/{id}` 응답의 `properties` 안에 들어 있다. 계획서에 적힌
"비용 대비 이득" 사유는 `relation` 에 대해서만 절반 맞고(대상 페이지 **제목**을
얻으려면 추가 호출이 필요하다), `formula` 에 대해서는 **틀렸다**.

## 2. 판정

### 2.1 `formula` — 반영 (스펙대로)

승인한다. 결과값이 페이지 응답 안에 이미 있어 추가 호출이 0회이고, "경과일",
"우선순위 점수" 같은 계산 결과는 실제 검색 신호가 된다. 값이
`{"type": "string"|"number"|"boolean"|"date", ...}` 로 한 겹 더 감싸여 있으므로
`property_plain_text` 를 그 안쪽에 재귀 적용하면 기존 분기를 그대로 재사용할 수
있다(신규 로직 최소).

### 2.2 `relation` — 반려 (스펙 쪽을 정정한다)

`50` §3 P0-3 의 `relation(id 만)` 은 **스펙이 틀렸다.** 구현이 아니라 스펙을
고친다. 사유는 "비용"이 아니라 **색인 품질 훼손**이다.

1. `relation` 값은 `[{"id": "<uuid>"}]` 뿐이다. 사람이 검색하는 문자열은 대상
   페이지의 **제목**이지 UUID 가 아니다 — UUID 로 검색하는 이용자는 없다.
2. `chunk.text_tsv` 생성식(`app/models/chunk.py:27`)은 영숫자·한글이 아닌 문자를
   공백으로 치환한다. UUID 는 하이픈에서 쪼개져 `8f3a`, `4b2c` 같은 의미 없는
   lexeme 5개로 색인된다. **recall 이득 0, 인덱스 부피와 노이즈만 증가.**
3. 임베딩 쪽은 더 나쁘다. 의미 없는 hex 토큰이 청크 벡터를 희석해 같은 청크의
   실제 본문 신호를 약화시킨다.
4. 유용한 형태(대상 페이지 제목)를 얻으려면 relation 개수만큼 추가 호출이 필요한데,
   이는 `50` 이 명시적으로 범위 밖으로 둔 항목이다.

즉 `relation` 은 "지금 넣기엔 비싸다"가 아니라 **id 형태로는 넣지 않는 것이
맞다.** 후속으로 넣는다면 반드시 대상 페이지 제목 해소(추가 호출 + 캐시)를
동반해야 하며, 그때 별도 설계 판단 대상이다.

### 2.3 `rollup` — 현행 유지 (범위 밖)

`50` 스펙에 없었고 이번 지적 대상도 아니다. 값이 중첩 배열이라 평문화 규칙을
따로 정해야 하므로 실제 수요가 확인되면 그때 다룬다.

## 3. developer 조치 지시

`app/services/documents/sources/notion_blocks.py` `property_plain_text` 만 수정한다.

1. `formula` 분기를 추가한다 — 값 안쪽에 자기 자신을 재귀 적용한다.
2. 재귀가 닿는 스칼라 타입을 받게 한다: `"string"` 을 기존 `number/url/email/
   phone_number` 튜플에 추가하고, `"boolean"` 을 `checkbox` 분기에서 함께 처리한다.
   이때 값이 `None` 이면 `"false"` 가 아니라 `""` 를 낸다(수식 결과 없음을
   `false` 로 색인하면 없는 신호가 생긴다).
3. docstring 을 판정에 맞게 고친다 — `relation`/`rollup` 제외 사유를 "비용 대비
   이득"이 아니라 **"UUID 는 검색 신호가 아니고 tsvector·임베딩을 오염시킨다"**
   로 정확히 적고 이 문서를 참조한다.
4. 테스트를 추가한다: formula string/number/date/boolean 각 1건, formula 결과가
   비었을 때 `""`, `relation` 은 계속 `""`(의도적 제외를 고정하는 가드).

## 4. 절차 개선 (architect 자책 항목)

구현 계획이 설계 문서의 범위를 **좁힐 때**는 계획서에 그 사실과 사유를 명시하고,
설계 문서 쪽도 같이 고쳐 두 문서가 어긋난 채로 남지 않게 한다. 이번처럼 계획서만
조용히 줄이면 리뷰 단계에서야 불일치가 드러나고, 그 사이 구현은 이미 끝나 있다.

이 판정에 따라 `50` §3 P0-3 의 지원 타입 목록에서 `relation(id 만)` 을 제거하고
이 문서를 참조하도록 갱신했다.
