"""Server-owned provider eligibility. Requests/documents cannot override this policy."""

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum

from src.config import settings


class DataClassification(StrEnum):
    PUBLIC = "public"
    SYNTHETIC = "synthetic"
    CONFIDENTIAL = "confidential"
    MEDICAL = "medical"


class ProviderError(RuntimeError):
    code = "provider_unavailable"
    retryable = True


class ProviderNotApprovedError(ProviderError):
    code = "provider_not_approved"
    retryable = False


class ProviderQuotaExceededError(ProviderError):
    code = "provider_quota"
    retryable = False


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"


class ProviderRateLimitError(ProviderError):
    code = "provider_rate_limit"


class ProviderInvalidResponseError(ProviderError):
    code = "provider_invalid_response"
    retryable = False


@dataclass(frozen=True)
class InferenceScope:
    classification: DataClassification
    client_id: str
    tenant_id: str
    loop: asyncio.AbstractEventLoop
    validator: Callable[[], Awaitable[None]] | None = None
    token_counts: dict[str, int] = field(default_factory=dict, repr=False)


_scope: ContextVar[InferenceScope | None] = ContextVar("inference_scope", default=None)


@contextmanager
def inference_scope(classification: DataClassification, client_id: str, tenant_id: str, *, validator=None):
    """Enter only after deriving classification and resource scope from PostgreSQL."""
    if not client_id.strip() or not tenant_id.strip():
        raise ProviderNotApprovedError("A client and tenant scope are required.")
    value = InferenceScope(
        DataClassification(classification),
        client_id,
        tenant_id,
        asyncio.get_running_loop(),
        validator,
    )
    token = _scope.set(value)
    try:
        yield value
    finally:
        _scope.reset(token)


def authorize_cohere(api_key: str) -> InferenceScope:
    scope = _scope.get()
    if scope is None:
        raise ProviderNotApprovedError(
            "An authorized data scope is required for model inference."
        )
    if settings.ENVIRONMENT == "production" and scope.validator is None:
        raise ProviderNotApprovedError("PostgreSQL authorization is required for production inference.")
    fingerprint = hashlib.sha256(api_key.encode()).hexdigest()
    approved_key = settings.COHERE_TRIAL_KEY_SHA256
    if (
        not api_key
        or not approved_key
        or not hmac.compare_digest(fingerprint, approved_key)
    ):
        raise ProviderNotApprovedError(
            "The configured Cohere key has no verified free-trial approval."
        )
    allowed = settings.COHERE_APPROVED_DATA_CLASSIFICATIONS
    if scope.classification.value not in allowed:
        raise ProviderNotApprovedError(
            "No approved provider for this data classification."
        )
    if (
        scope.classification
        in {DataClassification.CONFIDENTIAL, DataClassification.MEDICAL}
        and not settings.COHERE_SENSITIVE_APPROVAL_REF
    ):
        raise ProviderNotApprovedError(
            "Sensitive-content provider approval is not recorded."
        )
    return scope


def current_inference_scope() -> InferenceScope:
    value = _scope.get()
    if value is None:
        raise ProviderNotApprovedError("An authorized inference scope is required.")
    return value
