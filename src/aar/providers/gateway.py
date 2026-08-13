"""Owner-controlled inference gateway driver with ephemeral credential resolution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal, Protocol

from pydantic import Field, ValidationError

from aar.broker_models import (
    BrokerContext,
    EffectiveModelRoute,
    ModelRequest,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_sha256
from aar.runtime.model_broker import (
    ModelProviderFailure,
    ModelProviderOutcomeUnknown,
    ModelReceiptLookupFailed,
    ModelReceiptLookupUnavailable,
    ModelRouteDrift,
)
from aar.schemas import BudgetCounter, StrictModel

DriverValue = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+~-]{0,127}$",
        strict=True,
    ),
]


class GatewayDriverManifest(StrictModel):
    driver_id: DriverValue
    driver_version: DriverValue
    lookup_supported: bool
    cancellation_supported: bool


class GatewayProviderCall(StrictModel):
    provider_request_id: DriverValue
    operation_id: DriverValue
    idempotency_key: DriverValue
    request_digest: str
    profile_digest: str
    deadline_unix_ms: int

    @classmethod
    def issue(
        cls,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> GatewayProviderCall:
        identity = canonical_sha256(
            {
                "operation": context.parent_operation,
                "idempotency_key": context.idempotency_key,
                "request": request,
                "route": binding,
            }
        )
        return cls(
            provider_request_id="aar-" + identity.removeprefix("sha256:")[:48],
            operation_id=context.parent_operation.value,
            idempotency_key=context.idempotency_key,
            request_digest=canonical_sha256(request),
            profile_digest=binding.profile_digest,
            deadline_unix_ms=context.deadline_unix_ms,
        )


class GatewayModelResult(StrictModel):
    provider_request_id: DriverValue
    output_text: Annotated[str, Field(max_length=1_048_576, strict=True)]
    provider: DriverValue
    model: DriverValue
    reasoning_effort: DriverValue | None = None
    finish_reason: DriverValue
    input_tokens: BudgetCounter
    output_tokens: BudgetCounter
    cache_read_tokens: BudgetCounter | None = None
    cache_write_tokens: BudgetCounter | None = None
    reasoning_tokens: BudgetCounter | None = None
    total_tokens: BudgetCounter
    retry_ordinal: BudgetCounter = 0


class SamplingFallbackRoute(StrictModel):
    provider: DriverValue
    model: DriverValue
    reasoning_effort: DriverValue | None = None


class SamplingModelReceipt(StrictModel):
    schema_version: Literal["aar.model-receipt.v1"]
    provider_request_id: DriverValue
    provider: DriverValue
    model: DriverValue
    reasoning_effort: DriverValue | None = None
    finish_reason: DriverValue
    input_tokens: BudgetCounter
    output_tokens: BudgetCounter
    cache_read_tokens: BudgetCounter | None = None
    cache_write_tokens: BudgetCounter | None = None
    reasoning_tokens: BudgetCounter | None = None
    total_tokens: BudgetCounter
    retry_count: BudgetCounter
    fallback_chain: list[SamplingFallbackRoute]


def parse_sampling_gateway_result(
    call: GatewayProviderCall,
    binding: ModelRouteBinding,
    result: object,
) -> GatewayModelResult:
    role = getattr(result, "role", None)
    content = getattr(result, "content", None)
    if role != "assistant" or getattr(content, "type", None) != "text":
        raise ModelRouteDrift("sampling result must be one assistant text response")
    output_text = getattr(content, "text", None)
    if not isinstance(output_text, str):
        raise ModelRouteDrift("sampling result text is invalid")
    meta = getattr(result, "meta", None)
    if not isinstance(meta, dict):
        raise ModelRouteDrift("sampling result has no AAR receipt metadata")
    raw_receipt = meta.get("aar.model-receipt.v1")
    try:
        receipt = SamplingModelReceipt.model_validate(raw_receipt, strict=True)
    except ValidationError as error:
        raise ModelRouteDrift("sampling receipt schema is invalid") from error
    if receipt.provider_request_id != call.provider_request_id:
        raise ModelRouteDrift("sampling receipt identity does not match the provider call")
    if getattr(result, "model", None) != receipt.model:
        raise ModelRouteDrift("sampling result model does not match its receipt")
    expected_route = (binding.provider, binding.model, binding.reasoning_effort)
    actual_route = (receipt.provider, receipt.model, receipt.reasoning_effort)
    if actual_route != expected_route:
        raise ModelRouteDrift("sampling receipt route drifted from the bound profile")
    if binding.fallback_policy == "none" and receipt.fallback_chain:
        raise ModelRouteDrift("sampling receipt reports forbidden fallback")
    if binding.cache_policy == "disabled" and any(
        value not in (None, 0)
        for value in (receipt.cache_read_tokens, receipt.cache_write_tokens)
    ):
        raise ModelRouteDrift("sampling receipt violates disabled cache policy")
    if receipt.total_tokens != receipt.input_tokens + receipt.output_tokens:
        raise ModelRouteDrift("sampling receipt usage total is invalid")
    return GatewayModelResult(
        provider_request_id=receipt.provider_request_id,
        output_text=output_text,
        provider=receipt.provider,
        model=receipt.model,
        reasoning_effort=receipt.reasoning_effort,
        finish_reason=receipt.finish_reason,
        input_tokens=receipt.input_tokens,
        output_tokens=receipt.output_tokens,
        cache_read_tokens=receipt.cache_read_tokens,
        cache_write_tokens=receipt.cache_write_tokens,
        reasoning_tokens=receipt.reasoning_tokens,
        total_tokens=receipt.total_tokens,
        retry_ordinal=receipt.retry_count,
    )


class OwnerGatewayTransport(Protocol):
    def send(
        self,
        call: GatewayProviderCall,
        request: ModelRequest,
        binding: ModelRouteBinding,
        credential: bytes,
    ) -> GatewayModelResult: ...

    def lookup(
        self,
        call: GatewayProviderCall,
        binding: ModelRouteBinding,
        credential: bytes,
    ) -> GatewayModelResult | None: ...

    def close(self) -> None: ...


CredentialResolver = Callable[[ModelRouteBinding], bytes]


class GatewayCallOutcomeUnknown(ModelProviderOutcomeUnknown):
    """The gateway accepted a call but its terminal response was not observed."""

    def __init__(self, provider_request_id: str) -> None:
        self.provider_request_id = provider_request_id
        super().__init__("gateway call outcome is unknown; receipt lookup is required")


class GatewayProviderFailure(ModelProviderFailure):
    """A certain gateway failure whose retained message contains no provider details."""


class OwnerGatewayModelBroker:
    """Translate generic model calls through an owner-supplied gateway transport."""

    def __init__(
        self,
        *,
        manifest: GatewayDriverManifest,
        transport: OwnerGatewayTransport,
        credential_resolver: CredentialResolver,
    ) -> None:
        self.manifest = manifest
        self._transport = transport
        self._credential_resolver = credential_resolver
        self._closed = False

    def request(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse:
        if self._closed:
            raise RuntimeError("owner gateway model broker is closed")
        if binding.provider_driver != self.manifest.driver_id:
            raise ValueError("bound route selects a different provider driver")
        call = GatewayProviderCall.issue(request, context, binding)
        failure: GatewayProviderFailure | None = None
        outcome_unknown = False
        result: GatewayModelResult | None = None
        try:
            credential = self._resolve_credential(binding)
            result = self._transport.send(call, request, binding, credential)
        except GatewayCallOutcomeUnknown:
            outcome_unknown = True
        except ModelRouteDrift:
            raise
        except Exception:
            failure = GatewayProviderFailure("owner gateway provider call failed")
        if outcome_unknown:
            raise GatewayCallOutcomeUnknown(call.provider_request_id)
        if failure is not None:
            raise failure
        assert result is not None
        return self._response(call, binding, result)

    def reconcile(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse | None:
        if self._closed:
            raise RuntimeError("owner gateway model broker is closed")
        if binding.provider_driver != self.manifest.driver_id:
            raise ValueError("bound route selects a different provider driver")
        if not self.manifest.lookup_supported:
            raise ModelReceiptLookupUnavailable(
                "owner gateway driver does not support provider receipt lookup"
            )
        call = GatewayProviderCall.issue(request, context, binding)
        failure: ModelReceiptLookupFailed | None = None
        result: GatewayModelResult | None = None
        try:
            credential = self._resolve_credential(binding)
            result = self._transport.lookup(call, binding, credential)
        except ModelRouteDrift:
            raise
        except Exception:
            failure = ModelReceiptLookupFailed("owner gateway receipt lookup failed")
        if failure is not None:
            raise failure
        return None if result is None else self._response(call, binding, result)

    def _resolve_credential(self, binding: ModelRouteBinding) -> bytes:
        credential = self._credential_resolver(binding)
        if not isinstance(credential, bytes) or not credential:
            raise RuntimeError("owner credential resolver returned no ephemeral credential")
        return credential

    def _response(
        self,
        call: GatewayProviderCall,
        binding: ModelRouteBinding,
        result: GatewayModelResult,
    ) -> ModelResponse:
        if result.provider_request_id != call.provider_request_id:
            raise ValueError("gateway response identity does not match the provider call")
        expected_route = (
            binding.provider,
            binding.model,
            binding.reasoning_effort,
        )
        effective_route = (
            result.provider,
            result.model,
            result.reasoning_effort,
        )
        if effective_route != expected_route:
            raise ModelRouteDrift(
                "gateway effective provider, model, or reasoning effort drifted"
            )
        effective = EffectiveModelRoute(
            provider_driver=binding.provider_driver,
            provider=result.provider,
            model=result.model,
            reasoning_effort=result.reasoning_effort,
        )
        receipt = ModelRouteReceipt.issue(
            requested=binding,
            effective=effective,
            finish_reason=result.finish_reason,
            provider_response_id=result.provider_request_id,
            lookup_supported=self.manifest.lookup_supported,
        )
        usage = ModelUsageRecord(
            accounting_source="provider_reported",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            reasoning_tokens=result.reasoning_tokens,
            total_tokens=result.total_tokens,
            retry_ordinal=result.retry_ordinal,
        )
        return ModelResponse(
            output_text=result.output_text,
            route_receipt=receipt,
            usage=usage,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._transport.close()
