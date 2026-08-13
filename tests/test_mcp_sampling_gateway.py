from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from types import SimpleNamespace

import pytest
from mcp.types import CreateMessageResult, TextContent

from aar.broker_models import (
    BrokerContext,
    ModelRequest,
    ModelRouteBinding,
    ModelRouteCatalog,
    ModelRouteProfile,
)
from aar.providers.gateway import (
    GatewayCallOutcomeUnknown,
    GatewayProviderCall,
    GatewayProviderFailure,
)
from aar.providers.mcp_sampling import McpSamplingGatewayTransport
from aar.schemas import OperationRef


def _binding() -> ModelRouteBinding:
    profile = ModelRouteProfile(
        profile_id="gateway-luna-max-v1",
        provider_driver="hermes-mcp-sampling-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
        fallback_policy="none",
    )
    return ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)


def _call(binding: ModelRouteBinding) -> tuple[GatewayProviderCall, ModelRequest]:
    request = ModelRequest(prompt="Return exactly QUALIFIED.")
    context = BrokerContext(
        parent_operation=OperationRef(value="operation-sampling-qualification"),
        grant_id="grant-model-request",
        deadline_unix_ms=4_102_444_800_000,
        idempotency_key="sampling-call-1",
    )
    return GatewayProviderCall.issue(request, context, binding), request


def _result(call: GatewayProviderCall) -> CreateMessageResult:
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text="QUALIFIED"),
        model="gpt-5.6-luna",
        stopReason="endTurn",
        _meta={
            "aar.model-receipt.v1": {
                "schema_version": "aar.model-receipt.v1",
                "provider_request_id": call.provider_request_id,
                "provider": "openai-codex",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "finish_reason": "stop",
                "input_tokens": 9,
                "output_tokens": 1,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": 10,
                "retry_count": 0,
                "fallback_chain": [],
            }
        },
    )


def test_transport_schedules_sampling_on_the_mcp_session_owner_loop() -> None:
    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)
        observed = SimpleNamespace(kwargs=None)

        class Session:
            async def create_message(self, **kwargs):
                observed.kwargs = kwargs
                return _result(call)

        transport = McpSamplingGatewayTransport(now_ms=lambda: 0)
        transport.bind(
            OperationRef(value=call.operation_id),
            session=Session(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-1",
        )

        result = await asyncio.to_thread(
            transport.send,
            call,
            request,
            binding,
            b"mcp-client-owned-authority",
        )

        assert result.output_text == "QUALIFIED"
        assert result.provider == "openai-codex"
        assert observed.kwargs["max_tokens"] == 64
        assert observed.kwargs["metadata"] == {
            "aar.model-request.v1": {
                "provider_request_id": call.provider_request_id,
            }
        }
        assert observed.kwargs["related_request_id"] == "mcp-request-1"
        assert observed.kwargs["include_context"] is None

    asyncio.run(scenario())


def test_transport_timeout_cancels_inflight_sampling_and_marks_outcome_unknown() -> None:
    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)
        entered = threading.Event()
        cancelled = threading.Event()
        cleanup_finished = threading.Event()

        class Session:
            async def create_message(self, **kwargs):
                del kwargs
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await asyncio.sleep(0.01)
                    cleanup_finished.set()
                    raise

        transport = McpSamplingGatewayTransport(
            now_ms=lambda: call.deadline_unix_ms - 100,
            cancellation_cleanup_ms=50,
        )
        transport.bind(
            OperationRef(value=call.operation_id),
            session=Session(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-timeout",
        )

        with pytest.raises(GatewayCallOutcomeUnknown) as captured:
            await asyncio.to_thread(
                transport.send,
                call,
                request,
                binding,
                b"mcp-client-owned-authority",
            )

        assert captured.value.provider_request_id == call.provider_request_id
        assert entered.is_set()
        assert cancelled.is_set()
        assert cleanup_finished.is_set()

    asyncio.run(scenario())


def test_transport_close_after_sampling_starts_marks_outcome_unknown() -> None:
    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)
        entered = threading.Event()

        class Session:
            async def create_message(self, **kwargs):
                del kwargs
                entered.set()
                await asyncio.Event().wait()

        transport = McpSamplingGatewayTransport(now_ms=lambda: 0)
        transport.bind(
            OperationRef(value=call.operation_id),
            session=Session(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-close-race",
        )
        send_task = asyncio.create_task(
            asyncio.to_thread(
                transport.send,
                call,
                request,
                binding,
                b"mcp-client-owned-authority",
            )
        )
        assert await asyncio.to_thread(entered.wait, 1.0)

        transport.close()

        with pytest.raises(GatewayCallOutcomeUnknown) as captured:
            await send_task
        assert captured.value.provider_request_id == call.provider_request_id

    asyncio.run(scenario())


def test_transport_close_cannot_miss_scheduled_future_before_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aar.providers.mcp_sampling as sampling_module

    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)
        scheduled = threading.Event()
        release_registration = threading.Event()
        captured: dict[str, concurrent.futures.Future[object]] = {}

        class Session:
            async def create_message(self, **kwargs):
                del kwargs
                await asyncio.Event().wait()

        real_schedule = asyncio.run_coroutine_threadsafe

        def schedule_then_pause(coroutine, loop):
            future = real_schedule(coroutine, loop)
            captured["future"] = future
            scheduled.set()
            release_registration.wait(1.0)
            return future

        monkeypatch.setattr(
            sampling_module.asyncio,
            "run_coroutine_threadsafe",
            schedule_then_pause,
        )
        transport = McpSamplingGatewayTransport(
            now_ms=lambda: 0,
            cancellation_cleanup_ms=50,
        )
        transport.bind(
            OperationRef(value=call.operation_id),
            session=Session(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-schedule-registration-race",
        )
        send_task = asyncio.create_task(
            asyncio.to_thread(
                transport.send,
                call,
                request,
                binding,
                b"mcp-client-owned-authority",
            )
        )
        assert await asyncio.to_thread(scheduled.wait, 1.0)
        future = captured["future"]
        try:
            transport.close()
            release_registration.set()
            with pytest.raises(GatewayCallOutcomeUnknown):
                await asyncio.wait_for(send_task, timeout=0.25)
            assert future.cancelled()
        finally:
            future.cancel()
            release_registration.set()
        assert transport._futures == set()
        transport.close()

    asyncio.run(scenario())


def test_transport_timeout_cleanup_wait_is_bounded_by_total_deadline() -> None:
    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)
        cleanup_started = threading.Event()
        release_cleanup = asyncio.Event()

        class Session:
            async def create_message(self, **kwargs):
                del kwargs
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup_started.set()
                    await release_cleanup.wait()
                    raise

        transport = McpSamplingGatewayTransport(
            now_ms=lambda: call.deadline_unix_ms - 100,
            cancellation_cleanup_ms=40,
        )
        transport.bind(
            OperationRef(value=call.operation_id),
            session=Session(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-bounded-cleanup",
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            with pytest.raises(GatewayCallOutcomeUnknown):
                await asyncio.to_thread(
                    transport.send,
                    call,
                    request,
                    binding,
                    b"mcp-client-owned-authority",
                )
            elapsed = loop.time() - started
            assert cleanup_started.is_set()
            assert elapsed < 0.2
        finally:
            release_cleanup.set()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_transport_disconnect_retires_operation_generation() -> None:
    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)

        class FirstSession:
            async def create_message(self, **kwargs):
                del kwargs
                return _result(call)

        class ReplacementSession:
            async def create_message(self, **kwargs):
                del kwargs
                raise AssertionError("replacement session must never inherit the operation")

        transport = McpSamplingGatewayTransport(now_ms=lambda: 0)
        operation = OperationRef(value=call.operation_id)
        generation = transport.bind(
            operation,
            session=FirstSession(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-first-generation",
        )
        transport.unbind(operation, generation)

        with pytest.raises(RuntimeError, match="generation is retired"):
            transport.bind(
                operation,
                session=ReplacementSession(),
                loop=asyncio.get_running_loop(),
                related_request_id="mcp-request-replacement-generation",
            )
        with pytest.raises(GatewayProviderFailure, match="unavailable before send"):
            await asyncio.to_thread(
                transport.send,
                call,
                request,
                binding,
                b"mcp-client-owned-authority",
            )

    asyncio.run(scenario())


def test_transport_stale_generation_cannot_unbind_owner_session() -> None:
    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)

        class Session:
            async def create_message(self, **kwargs):
                del kwargs
                return _result(call)

        transport = McpSamplingGatewayTransport(now_ms=lambda: 0)
        operation = OperationRef(value=call.operation_id)
        generation = transport.bind(
            operation,
            session=Session(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-owner-generation",
        )

        with pytest.raises(RuntimeError, match="generation mismatch"):
            transport.unbind(operation, object())

        result = await asyncio.to_thread(
            transport.send,
            call,
            request,
            binding,
            b"mcp-client-owned-authority",
        )
        assert result.output_text == "QUALIFIED"
        transport.unbind(operation, generation)

    asyncio.run(scenario())


def test_transport_rejects_expired_deadline_before_sampling_send() -> None:
    async def scenario() -> None:
        binding = _binding()
        call, request = _call(binding)

        class Session:
            async def create_message(self, **kwargs):
                del kwargs
                raise AssertionError("expired request must not reach MCP sampling")

        transport = McpSamplingGatewayTransport(now_ms=lambda: call.deadline_unix_ms)
        transport.bind(
            OperationRef(value=call.operation_id),
            session=Session(),
            loop=asyncio.get_running_loop(),
            related_request_id="mcp-request-expired",
        )

        with pytest.raises(GatewayProviderFailure, match="deadline expired before send"):
            await asyncio.to_thread(
                transport.send,
                call,
                request,
                binding,
                b"mcp-client-owned-authority",
            )

    asyncio.run(scenario())
