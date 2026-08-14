"""도메인·리포지토리·통합·API 예외 계층.

services 레이어는 domain/repository 만 raise 한다.
api 레이어에서 HTTP 로 변환(상태코드 매핑)하며 스택트레이스는 외부 노출 금지.
"""

from __future__ import annotations


class DomainError(Exception):
    """비즈니스 규칙 위반."""

    def __init__(self, message: str, code: str = "domain_error") -> None:
        """예외 메시지와 코드 식별자를 보관한다."""
        super().__init__(message)
        self.code = code


class DocumentNotFoundError(DomainError):
    """주어진 ID 의 문서를 찾을 수 없을 때 발생."""

    def __init__(self, document_id: str) -> None:
        """문서 ID 를 메시지·코드와 함께 보관한다."""
        super().__init__(f"document not found: {document_id}", code="document_not_found")


class EndpointNotFoundError(DomainError):
    """주어진 ID 의 엔드포인트를 찾을 수 없을 때 발생."""

    def __init__(self, endpoint_id: str) -> None:
        """엔드포인트 ID 를 메시지·코드와 함께 보관한다."""
        super().__init__(f"endpoint not found: {endpoint_id}", code="endpoint_not_found")


class DuplicateDocumentError(DomainError):
    """동일 source_url 의 문서가 이미 등록돼 있을 때 발생."""

    def __init__(self, source_url: str) -> None:
        """중복된 source_url 을 메시지·코드와 함께 보관한다."""
        super().__init__(f"document already registered: {source_url}", code="duplicate_document")


class ParserError(DomainError):
    """OpenAPI/Swagger 문서 파싱 실패."""

    def __init__(self, message: str) -> None:
        """파서 오류 메시지를 보관한다."""
        super().__init__(message, code="parser_error")


class ValidationError(DomainError):
    """입력값 검증 실패."""

    def __init__(self, message: str) -> None:
        """검증 오류 메시지를 보관한다."""
        super().__init__(message, code="validation_error")


class IntegrationError(Exception):
    """외부 HTTP/LLM/임베딩 실패."""
