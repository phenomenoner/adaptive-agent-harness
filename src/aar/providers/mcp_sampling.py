"""MCP Sampling compatibility transport for owner-controlled model gateways.

The generic broker core does not depend on MCP. This adapter is the only boundary
that schedules a provider request back onto the MCP client session that owns model
policy and credentials.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.types import SamplingMessage, TextContent

from aar.broker_models import ModelRequest, ModelRouteBinding
from aar.providers.gateway import (
    GatewayCallOutcomeUnknown,
    GatewayModelResult,
    GatewayProviderCall,
    GatewayProviderFailure,
    parse_sampling_gateway_result,
)
from aar.schemas import OperationRef


@dataclass(frozen=True)
class _SessionBinding:
    session: Any
    loop: asyncio.AbstractEventLoop
    related_request_id: str
    generation: object


class McpSamplingGatewayTransport:
    """Deprecated compatibility transport for an operation's owning MCP session.

    New public integrations use caller-delegated RLM and must not depend on MCP Sampling.
    """

    def __init__(
        self,
        *,
        now_ms: Callable[[], int],
        cancellation_cleanup_ms: int = 1_000,
    ) -> None:
        if not isinstance(cancellation_cleanup_ms, int) or isinstance(
            cancellation_cleanup_ms, bool
        ):
            raise TypeError("MCP sampling cancellation cleanup budget must be an integer")
        if cancellation_cleanup_ms < 1 or cancellation_cleanup_ms > 10_000:
            raise ValueError(
                "MCP sampling cancellation cleanup budget must be between 1 and 10000 ms"
            )
        self._now_ms = now_ms
        self._cancellation_cleanup_ms = cancellation_cleanup_ms
        self._lock = threading.RLock()
        self._bindings: dict[str, _SessionBinding] = {}
        self._retired_operations: set[str] = set()
        self._futures: set[concurrent.futures.Future[Any]] = set()
        self._closed = False

    def bind(
        self,
        operation: OperationRef,
        *,
        session: Any,
        loop: asyncio.AbstractEventLoop,
        related_request_id: str,
    ) -> object:
        if not related_request_id:
            raise ValueError("MCP related request identity must be non-empty")
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP sampling transport is closed")
            if operation.value in self._retired_operations:
                raise RuntimeError("operation sampling generation is retired")
            current = self._bindings.get(operation.value)
            if current is not None:
                if (
                    current.session is not session
                    or current.loop is not loop
                    or current.related_request_id != related_request_id
                ):
                    raise RuntimeError(
                        "operation is already bound to a different MCP session"
                    )
                return current.generation
            generation = object()
            candidate = _SessionBinding(session, loop, related_request_id, generation)
            self._bindings[operation.value] = candidate
            return generation

    def unbind(self, operation: OperationRef, generation: object) -> None:
        with self._lock:
            current = self._bindings.get(operation.value)
            if current is None or current.generation is not generation:
                raise RuntimeError("operation sampling generation mismatch")
            self._bindings.pop(operation.value)
            self._retired_operations.add(operation.value)

    def send(
        self,
        call: GatewayProviderCall,
        request: ModelRequest,
        binding: ModelRouteBinding,
        credential: bytes,
    ) -> GatewayModelResult:
        if not credential:
            raise GatewayProviderFailure("MCP owner authority is unavailable")
        with self._lock:
            if self._closed:
                raise GatewayProviderFailure("MCP sampling transport is closed")
            session_binding = self._bindings.get(call.operation_id)
        if session_binding is None or session_binding.loop.is_closed():
            raise GatewayProviderFailure("MCP sampling session is unavailable before send")
        remaining_ms = call.deadline_unix_ms - self._now_ms()
        if remaining_ms <= 0:
            raise GatewayProviderFailure("MCP sampling deadline expired before send")
        provider_wait_ms = remaining_ms - self._cancellation_cleanup_ms
        if provider_wait_ms <= 0:
            raise GatewayProviderFailure(
                "MCP sampling deadline cannot reserve bounded cancellation cleanup"
            )

        coroutine = session_binding.session.create_message(
            messages=[
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text=request.prompt),
                )
            ],
            max_tokens=binding.max_output_tokens,
            include_context=None,
            metadata={
                "aar.model-request.v1": {
                    "provider_request_id": call.provider_request_id,
                }
            },
            related_request_id=session_binding.related_request_id,
        )
        cleanup_finished = threading.Event()

        async def run_sampling() -> Any:
            try:
                return await coroutine
            finally:
                cleanup_finished.set()

        total_deadline = time.monotonic() + (remaining_ms / 1_000)
        future = asyncio.run_coroutine_threadsafe(
            run_sampling(), session_binding.loop
        )
        with self._lock:
            current_binding = self._bindings.get(call.operation_id)
            ownership_lost = (
                self._closed
                or current_binding is None
                or current_binding.generation is not session_binding.generation
            )
            if not ownership_lost:
                self._futures.add(future)
        if ownership_lost:
            future.cancel()
            cleanup_finished.wait(
                timeout=min(
                    self._cancellation_cleanup_ms / 1_000,
                    max(0.0, total_deadline - time.monotonic()),
                )
            )
            raise GatewayCallOutcomeUnknown(call.provider_request_id)
        try:
            result = future.result(timeout=provider_wait_ms / 1_000)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            cleanup_finished.wait(timeout=max(0.0, total_deadline - time.monotonic()))
            raise GatewayCallOutcomeUnknown(call.provider_request_id) from error
        except Exception as error:
            # Scheduling onto the session owner loop is the last point where
            # AAR can prove no MCP Sampling request was sent. Once scheduled,
            # cancellation, disconnect, or a transport exception can race an
            # already-written request, so the terminal provider outcome is
            # unknown until an authoritative receipt can be reconciled.
            raise GatewayCallOutcomeUnknown(call.provider_request_id) from error
        finally:
            with self._lock:
                self._futures.discard(future)
        return parse_sampling_gateway_result(call, binding, result)

    def lookup(
        self,
        call: GatewayProviderCall,
        binding: ModelRouteBinding,
        credential: bytes,
    ) -> GatewayModelResult | None:
        del call, binding, credential
        raise GatewayProviderFailure("MCP sampling has no provider receipt lookup")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            futures = tuple(self._futures)
            self._bindings.clear()
        for future in futures:
            future.cancel()
