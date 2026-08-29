"""Bounded, single-runtime durable dispatch for accepted RLM operations.

The dispatcher deliberately owns no transport or process-supervisor state.  A
registry persists dispatch intent, claims an attempt with a lease, and commits
an attempt result.  The host remains responsible for reconstructing and
running the persisted RLM request.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from aar.canonical import canonical_sha256
from aar.runtime.models import OperationRecord
from aar.schemas import OperationRef, OperationState


class DispatcherError(RuntimeError):
    """Base class for dispatcher lifecycle errors."""


class DispatcherClosed(DispatcherError):
    """The dispatcher has stopped accepting new dispatch requests."""


class DispatcherDrainTimeout(DispatcherError):
    """One or more worker callbacks remained live after bounded shutdown."""

    def __init__(self, worker_names: tuple[str, ...]) -> None:
        self.worker_names = worker_names
        joined = ", ".join(worker_names)
        super().__init__(
            f"{len(worker_names)} dispatcher worker(s) still running after drain timeout: {joined}"
        )


@dataclass(frozen=True, slots=True)
class AttemptFence:
    """The complete owner fence supplied to one claimed attempt."""

    dispatcher_generation: int
    lease_epoch: int
    owner_digest: str


@dataclass(frozen=True, slots=True)
class DispatchClaim:
    """The bounded claim shape returned by a dispatch registry.

    Registries may return an equivalent object with ``operation``, ``attempt``,
    and ``lease_epoch`` attributes.  ``DispatchClaim`` is provided as the
    transport-neutral shape for small registry implementations and tests.
    """

    operation: OperationRef
    attempt: Any
    lease_epoch: int
    kind: str = "rlm.execute"


@runtime_checkable
class DispatchRegistry(Protocol):
    """The narrow durable registry seam used by :class:`DurableDispatcher`."""

    def request_dispatch(self, operation: OperationRef, *, kind: str) -> Any:
        """Persist or idempotently request dispatch of an accepted operation."""
        ...

    def claim_next(
        self,
        *,
        runtime_generation: int,
        dispatcher_generation: int,
        owner_digest: str,
        lease_duration_ms: int,
    ) -> DispatchClaim | None:
        """Atomically claim one eligible attempt, or return ``None``."""
        ...

    def finish_attempt(
        self,
        attempt: Any,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
    ) -> Any:
        """Commit the attempt result only when ``fence`` is still current."""
        ...

    def get(self, operation: OperationRef) -> OperationRecord:
        """Read the current logical operation record without changing it."""
        ...

    def park_attempt(
        self,
        attempt: Any,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
        *,
        note: str,
    ) -> OperationRecord:
        """Fence and park one current attempt without inferring success or failure."""
        ...


class DispatchHost(Protocol):
    """Host authority needed by a single-runtime durable dispatcher."""

    @property
    def registry(self) -> DispatchRegistry: ...

    @property
    def runtime_generation(self) -> int: ...

    def run_claimed_rlm(
        self,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
    ) -> OperationRecord:
        """Run one claimed RLM attempt under the supplied fence."""
        ...

    def recover_durable_rlm(self) -> None:
        """Fence expirations and apply bounded RLM recovery decisions."""
        ...


_TERMINAL_STATES = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.TIMED_OUT,
        OperationState.INDETERMINATE,
    }
)
_TERMINAL_VALUES = frozenset(state.value for state in _TERMINAL_STATES)


class DurableDispatcher:
    """Run persisted ``rlm.execute`` work with bounded daemon workers.

    LT1 has one runtime generation, so the dispatcher generation is exactly
    ``host.runtime_generation``.  A fresh opaque owner is generated for every
    dispatcher instance, but only its canonical digest is retained and sent to
    the registry.  The raw owner is never stored on the dispatcher.
    """

    dispatch_kind = "rlm.execute"

    def __init__(
        self,
        host: DispatchHost,
        *,
        concurrency: int = 2,
        lease_duration_ms: int = 30_000,
        idle_poll_ms: int = 100,
    ) -> None:
        if isinstance(concurrency, bool) or concurrency < 1:
            raise ValueError("concurrency must be a positive integer")
        if isinstance(lease_duration_ms, bool) or lease_duration_ms < 1:
            raise ValueError("lease_duration_ms must be a positive integer")
        if isinstance(idle_poll_ms, bool) or idle_poll_ms < 1:
            raise ValueError("idle_poll_ms must be a positive integer")
        if isinstance(host.runtime_generation, bool) or host.runtime_generation < 1:
            raise ValueError("host.runtime_generation must be a positive integer")

        self.host = host
        self.registry = host.registry
        self.concurrency = concurrency
        self.lease_duration_ms = lease_duration_ms
        self.idle_poll_ms = idle_poll_ms
        self.dispatcher_generation = host.runtime_generation
        self._owner_digest = canonical_sha256(secrets.token_urlsafe(32))

        self._condition = threading.Condition()
        self._workers: list[threading.Thread] = []
        self._started = False
        self._closing = False
        self._closed = False
        self._claims_in_flight = 0

    @property
    def owner_digest(self) -> str:
        """Return the opaque owner's canonical digest, never the raw owner."""

        return self._owner_digest

    @property
    def closed(self) -> bool:
        """Whether this dispatcher has been closed to new submissions."""

        with self._condition:
            return self._closed

    def start(self) -> None:
        """Launch at most ``concurrency`` daemon worker threads."""

        with self._condition:
            if self._closed:
                raise DispatcherClosed("dispatcher is closed")
            if self._started:
                return
            self._started = True
            self._workers = [
                threading.Thread(
                    target=self._worker_main,
                    name=f"aar-durable-dispatcher-{index}",
                    daemon=True,
                )
                for index in range(self.concurrency)
            ]
            workers = tuple(self._workers)

        for worker in workers:
            worker.start()

    def notify(self, operation: OperationRef, *, kind: str | None = None) -> Any:
        """Persist a dispatch request and wake idle workers."""

        dispatch_kind = self.dispatch_kind if kind is None else kind
        if not dispatch_kind:
            raise ValueError("dispatch kind must be non-empty")
        with self._condition:
            self._ensure_open_for_submit()
            result = self.registry.request_dispatch(operation, kind=dispatch_kind)
            self._condition.notify_all()
            return result

    def submit(self, operation: OperationRef, *, kind: str | None = None) -> Any:
        """Explicit submit alias for callers that use submit terminology."""

        return self.notify(operation, kind=kind)

    def wait(self, operation: OperationRef, timeout_s: float | None = None) -> OperationRecord:
        """Read until terminal/indeterminate, or return the current record on timeout.

        Waiting never requests cancellation and never changes registry state.
        The condition is only a local wake-up optimization; bounded polling
        keeps this method correct when another runtime component changes the
        record without notifying this dispatcher.
        """

        if timeout_s is not None and timeout_s < 0:
            raise ValueError("timeout_s must be non-negative or None")
        deadline = None if timeout_s is None else time.monotonic() + timeout_s

        while True:
            record = self.registry.get(operation)
            if _is_terminal(record):
                return record
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return record
                wait_s = min(remaining, self.idle_poll_ms / 1_000)
            else:
                wait_s = self.idle_poll_ms / 1_000
            with self._condition:
                self._condition.wait(wait_s)

    def close(self, drain_timeout_s: float = 5) -> None:
        """Stop claiming, wake workers, and join them for a bounded interval.

        Closing does not cancel active handler calls and does not synthesize a
        successful or failed result for work whose outcome is uncertain.  If
        any worker remains live after the timeout, fail closed so the owner
        cannot tear down stores that the callback may still use.  The caller
        may retry after the handler returns; any late commit still passes
        through the registry's normal lease/fence checks.
        """

        if drain_timeout_s < 0:
            raise ValueError("drain_timeout_s must be non-negative")

        with self._condition:
            self._closing = True
            self._closed = True
            self._condition.notify_all()
            workers = tuple(self._workers)

        deadline = time.monotonic() + drain_timeout_s
        current = threading.current_thread()
        for worker in workers:
            if worker is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)

        with self._condition:
            self._condition.notify_all()

        alive = tuple(worker.name for worker in workers if worker.is_alive())
        if alive:
            raise DispatcherDrainTimeout(alive)

    def _ensure_open_for_submit(self) -> None:
        if self._closed:
            raise DispatcherClosed("dispatcher is closed; cannot submit operation")

    def _worker_main(self) -> None:
        while True:
            if self._should_stop_claiming():
                return

            try:
                claim = self._claim_next()
            except BaseException:
                # No operation identity is available for a claim failure.  Do
                # not invent an outcome; retain the worker and retry after a
                # bounded wait so a transient registry failure cannot kill the
                # dispatcher thread.
                self._wait_for_work()
                continue

            if claim is None:
                self._wait_for_work()
                continue

            try:
                operation, attempt, lease_epoch, kind = _claim_parts(claim)
            except BaseException:
                # A malformed claim cannot be safely attributed to an outer
                # operation.  Never turn it into success or failure.
                self._signal_workers()
                continue

            try:
                self._run_claim(operation, attempt, lease_epoch, kind)
            except BaseException as error:
                # _run_claim is intentionally defensive, but keep the daemon
                # alive if a registry implementation violates the seam.
                self._park_indeterminate(operation, attempt, self._fence(lease_epoch), error)
            finally:
                self._signal_workers()

    def _claim_next(self) -> Any:
        with self._condition:
            if self._closing:
                return None
            self._claims_in_flight += 1
        try:
            recover = getattr(self.host, "recover_durable", None)
            if not callable(recover):
                recover = getattr(self.host, "recover_durable_rlm", None)
            if callable(recover):
                recover()
            return self.registry.claim_next(
                runtime_generation=self.dispatcher_generation,
                dispatcher_generation=self.dispatcher_generation,
                owner_digest=self.owner_digest,
                lease_duration_ms=self.lease_duration_ms,
            )
        finally:
            with self._condition:
                self._claims_in_flight -= 1

    def _run_claim(
        self,
        operation: OperationRef,
        attempt: Any,
        lease_epoch: int,
        kind: str,
    ) -> None:
        fence = self._fence(lease_epoch)
        try:
            if kind == self.dispatch_kind:
                record = self.host.run_claimed_rlm(operation, attempt, fence)
            else:
                run_claimed = getattr(self.host, "run_claimed", None)
                if not callable(run_claimed):
                    raise TypeError(f"host does not support dispatch kind {kind!r}")
                record = cast(
                    OperationRecord,
                    run_claimed(kind, operation, attempt, fence),
                )
        except BaseException as error:
            self._handle_handler_exception(kind, operation, attempt, fence, error)
            return

        try:
            self._finish_attempt(operation, attempt, fence, record)
        except BaseException as error:
            # The handler outcome is not enough to prove that the outer
            # terminal receipt was durably fenced and committed.
            self._park_indeterminate(operation, attempt, fence, error)

    def _handle_handler_exception(
        self,
        kind: str,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
        error: BaseException,
    ) -> None:
        marker = getattr(self.host, "mark_dispatch_failure_for_kind", None)
        marker_args: tuple[Any, ...] = (kind, operation, attempt, fence, error)
        if not callable(marker) and kind == self.dispatch_kind:
            marker = getattr(self.host, "mark_dispatch_failure", None)
            marker_args = (operation, attempt, fence, error)
        if callable(marker):
            try:
                record = cast(OperationRecord | None, marker(*marker_args))
            except BaseException:
                record = None
            if record is not None:
                try:
                    self._finish_attempt(operation, attempt, fence, record)
                    return
                except BaseException as finish_error:
                    self._park_indeterminate(operation, attempt, fence, finish_error)
                    return

        self._park_indeterminate(operation, attempt, fence, error)

    def _park_indeterminate(
        self,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
        error: BaseException,
    ) -> None:
        note = f"dispatcher_worker_exception:{type(error).__name__}"
        park = getattr(self.registry, "park_attempt", None)
        if callable(park):
            with suppress(BaseException):
                park(
                    attempt,
                    self.dispatcher_generation,
                    fence.dispatcher_generation,
                    fence.lease_epoch,
                    fence.owner_digest,
                    note=note,
                )

    def _finish_attempt(
        self,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
        record: OperationRecord | None,
    ) -> Any:
        del operation, record
        return self.registry.finish_attempt(
            attempt,
            self.dispatcher_generation,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
        )

    def _fence(self, lease_epoch: int) -> AttemptFence:
        return AttemptFence(
            dispatcher_generation=self.dispatcher_generation,
            lease_epoch=lease_epoch,
            owner_digest=self.owner_digest,
        )

    def _should_stop_claiming(self) -> bool:
        with self._condition:
            return self._closing

    def _wait_for_work(self) -> None:
        with self._condition:
            if not self._closing:
                self._condition.wait(self.idle_poll_ms / 1_000)

    def _signal_workers(self) -> None:
        with self._condition:
            self._condition.notify_all()


def _claim_parts(claim: Any) -> tuple[OperationRef, Any, int, str]:
    """Normalize the small set of equivalent registry claim projections."""

    if isinstance(claim, DispatchClaim):
        operation, attempt, lease_epoch = claim.operation, claim.attempt, claim.lease_epoch
        kind = claim.kind
    elif isinstance(claim, Mapping):
        operation = claim["operation"]
        attempt = claim.get("attempt", claim)
        lease_epoch = claim.get("lease_epoch")
        kind = claim.get("kind", "rlm.execute")
    elif isinstance(claim, tuple):
        if len(claim) == 4:
            operation, attempt, lease_epoch, kind = claim
        elif len(claim) == 3:
            operation, attempt, lease_epoch = claim
            kind = "rlm.execute"
        elif len(claim) == 2:
            operation, attempt = claim
            lease_epoch = getattr(attempt, "lease_epoch", None)
            kind = getattr(attempt, "kind", "rlm.execute")
        else:
            raise TypeError("claim tuple must contain operation, attempt, lease epoch, and kind")
    else:
        operation = claim.operation
        attempt = getattr(claim, "attempt", claim)
        lease_epoch = getattr(claim, "lease_epoch", None)
        kind = getattr(claim, "kind", "rlm.execute")
        if lease_epoch is None:
            lease_epoch = getattr(attempt, "lease_epoch", None)

    if lease_epoch is None:
        raise TypeError("claimed attempt does not expose lease_epoch")
    if isinstance(lease_epoch, bool) or not isinstance(lease_epoch, int) or lease_epoch < 1:
        raise ValueError("lease_epoch must be a positive integer")
    if not isinstance(kind, str) or not kind:
        raise ValueError("claimed dispatch kind must be a non-empty string")
    return operation, attempt, lease_epoch, kind


def _is_terminal(record: Any) -> bool:
    state = getattr(record, "state", None)
    if state in _TERMINAL_STATES:
        return True
    return getattr(state, "value", state) in _TERMINAL_VALUES


__all__ = [
    "AttemptFence",
    "DispatchClaim",
    "DispatchHost",
    "DispatchRegistry",
    "DispatcherClosed",
    "DispatcherDrainTimeout",
    "DispatcherError",
    "DurableDispatcher",
]
