"""비즈니스 메타데이터 생성 오케스트레이션 — 대상 선별 → 호출 → 검증/절단 → upsert.

docs/architect-review/55 §3,§4: skip 규칙 4분기(행없음/해시불일치/모델불일치/
`--force`)로 재생성 대상을 정하고, 저장 직전 길이 상한을 강제 절단한다.
LLM 호출은 네트워크 바운드라 `ThreadPoolExecutor` 로 병렬화하되(§4), DB
세션은 스레드 안전하지 않으므로 쓰기는 메인 스레드에서만 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.models import ApiEndpoint, Document, EndpointBusinessMetadata
from app.services.metadata.llm_client import AnthropicClient
from app.services.metadata.prompt import SYSTEM_PROMPT, build_user_prompt
from app.services.metadata.spec_payload import (
    build_endpoint_input,
    build_payload_json,
    compute_source_hash,
)
from app.services.metadata.validation import sanitize_and_clip

_LOG = get_logger("docs_mcp.metadata.generator")

_COMMIT_EVERY = 20


@dataclass
class GenerationSummary:
    """실행 1회 결과 집계."""

    total: int = 0
    generated: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Target:
    """생성 대상 엔드포인트 1건 + 미리 계산된 payload/해시/기존 행."""

    endpoint: ApiEndpoint
    payload_json: str
    source_hash: str
    existing: EndpointBusinessMetadata | None


def select_targets(
    session: Session,
    *,
    document_ids: Sequence[str] | None,
    project: str | None,
    model: str,
    force: bool,
) -> list[_Target]:
    """skip 규칙(55 §3)을 적용해 생성 대상 엔드포인트 목록을 만든다.

    `--force` 가 아니면 (행없음 / source_hash 불일치 / model 불일치) 중
    하나라도 참인 엔드포인트만 남긴다. `generated_at` 은 판단에 쓰지 않는다.
    """
    endpoints = _scope_endpoints(session, document_ids, project)
    existing_rows = _load_existing(session, endpoints)
    targets: list[_Target] = []
    for endpoint in endpoints:
        payload_json = build_payload_json(build_endpoint_input(endpoint))
        source_hash = compute_source_hash(payload_json)
        existing = existing_rows.get((endpoint.document_id, endpoint.method, endpoint.path))
        if force or _needs_generation(existing, source_hash, model):
            targets.append(_Target(endpoint, payload_json, source_hash, existing))
    return targets


def generate_business_metadata(
    session: Session,
    llm_client: AnthropicClient,
    *,
    document_ids: Sequence[str] | None = None,
    project: str | None = None,
    force: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    concurrency: int = 4,
) -> GenerationSummary:
    """대상을 선별하고, dry-run 이 아니면 병렬 호출·절단·upsert 를 수행한다."""
    targets = select_targets(
        session,
        document_ids=document_ids,
        project=project,
        model=llm_client.model,
        force=force,
    )
    if limit is not None:
        targets = targets[:limit]
    summary = GenerationSummary(total=len(targets))

    if dry_run:
        for target in targets:
            _LOG.info(
                "dry-run 대상: %s %s payload=%s",
                target.endpoint.method,
                target.endpoint.path,
                target.payload_json,
            )
        return summary

    processed = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        future_to_target = {
            pool.submit(
                llm_client.generate_json, SYSTEM_PROMPT, build_user_prompt(target.payload_json)
            ): target
            for target in targets
        }
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                data = future.result()
            except IntegrationError as exc:
                _LOG.error(
                    "메타데이터 생성 실패: %s %s: %s",
                    target.endpoint.method,
                    target.endpoint.path,
                    exc,
                )
                summary.failed.append(f"{target.endpoint.method} {target.endpoint.path}")
                continue
            sanitized = sanitize_and_clip(
                data.get("business_description"),
                data.get("keywords"),
                data.get("user_phrases"),
            )
            if sanitized.truncated:
                _LOG.warning(
                    "메타데이터 상한 초과로 절단됨: %s %s",
                    target.endpoint.method,
                    target.endpoint.path,
                )
            _upsert(
                session,
                target,
                sanitized.business_description,
                sanitized.keywords,
                sanitized.user_phrases,
                llm_client.model,
            )
            summary.generated += 1
            processed += 1
            if processed % _COMMIT_EVERY == 0:
                session.commit()
            _LOG.info("생성 진행: %d/%d", processed, summary.total)
    session.commit()
    return summary


def _scope_endpoints(
    session: Session, document_ids: Sequence[str] | None, project: str | None
) -> list[ApiEndpoint]:
    """`--document-id`/`--project` 스코프로 대상 엔드포인트를 조회한다(기본: 전체)."""
    stmt = select(ApiEndpoint)
    if document_ids:
        stmt = stmt.where(ApiEndpoint.document_id.in_(document_ids))
    if project is not None:
        stmt = stmt.join(Document, ApiEndpoint.document_id == Document.id).where(
            Document.project == project
        )
    stmt = stmt.order_by(ApiEndpoint.document_id, ApiEndpoint.method, ApiEndpoint.path)
    return list(session.execute(stmt).scalars().all())


def _load_existing(
    session: Session, endpoints: Iterable[ApiEndpoint]
) -> dict[tuple[str, str, str], EndpointBusinessMetadata]:
    """스코프에 걸린 문서들의 기존 메타데이터를 (document_id, method, path) 로 매핑한다."""
    document_ids = {ep.document_id for ep in endpoints}
    if not document_ids:
        return {}
    stmt = select(EndpointBusinessMetadata).where(
        EndpointBusinessMetadata.document_id.in_(document_ids)
    )
    rows = session.execute(stmt).scalars().all()
    return {(row.document_id, row.method, row.path): row for row in rows}


def _needs_generation(
    existing: EndpointBusinessMetadata | None, source_hash: str, model: str
) -> bool:
    """skip 규칙 4분기 중 `--force` 를 뺀 3개(행없음/해시불일치/모델불일치)."""
    if existing is None:
        return True
    if existing.source_hash != source_hash:
        return True
    return existing.model != model


def _upsert(
    session: Session,
    target: _Target,
    description: str,
    keywords: list[str],
    phrases: list[str],
    model: str,
) -> None:
    """대상 행을 새로 만들거나 갱신한다(document_id, method, path 가 키)."""
    row = target.existing
    if row is None:
        row = EndpointBusinessMetadata(
            document_id=target.endpoint.document_id,
            method=target.endpoint.method,
            path=target.endpoint.path,
        )
        session.add(row)
    row.business_description = description
    row.keywords = keywords
    row.user_phrases = phrases
    row.source_hash = target.source_hash
    row.model = model
    row.generated_at = datetime.now(UTC)
