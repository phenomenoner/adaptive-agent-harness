"""Durable worker ownership and exact orphan fencing for AR-LT2."""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime.process_identity import (
    ProcessIdentityMismatch,
    ProcessIdentityState,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    observe_process_identity,
    open_exact_process,
    process_identity,
)
from aar.runtime.registry import OperationRegistry, WorkerBindingRecord
from aar.schemas import Digest, OperationRef, StrictModel

WORKER_RECEIPT_SCHEMA_VERSION = "aar.worker-termination.v1"
WorkerTerminalState = Literal["lost", "quarantined", "terminated"]


class WorkerTerminationReceipt(StrictModel):
    schema_version: Literal["aar.worker-termination.v1"] = WORKER_RECEIPT_SCHEMA_VERSION
    worker_id: str
    runtime_generation: int
    process_identity: ProcessStartIdentity
    disposition: WorkerTerminalState
    reason: str
    at_unix_ms: int
    receipt_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        worker_id: str,
        runtime_generation: int,
        process_identity: ProcessStartIdentity,
        disposition: WorkerTerminalState,
        reason: str,
        at_unix_ms: int,
    ) -> Self:
        payload = {
            "schema_version": WORKER_RECEIPT_SCHEMA_VERSION,
            "worker_id": worker_id,
            "runtime_generation": runtime_generation,
            "process_identity": process_identity.model_dump(mode="json"),
            "disposition": disposition,
            "reason": reason,
            "at_unix_ms": at_unix_ms,
        }
        return cls(**payload, receipt_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"receipt_digest"})
        if self.receipt_digest != canonical_sha256(payload):
            raise ValueError("worker termination receipt digest mismatch")
        return self


@dataclass(frozen=True)
class ManagedWorker:
    worker_id: str
    process_identity: ProcessStartIdentity


class WorkerManager:
    """Persist worker bindings and fence or classify prior-generation workers."""

    def __init__(
        self,
        registry: OperationRegistry,
        *,
        runtime_generation: int,
        now_ms,
        termination_timeout_s: float = 2.0,
    ) -> None:
        if runtime_generation < 1:
            raise ValueError("runtime_generation must be positive")
        if termination_timeout_s <= 0:
            raise ValueError("termination_timeout_s must be positive")
        self.registry = registry
        self.runtime_generation = runtime_generation
        self._now_ms = now_ms
        self._termination_timeout_s = termination_timeout_s

    def recover_orphans(self) -> tuple[WorkerBindingRecord, ...]:
        """Fence every active binding from a predecessor generation before new workers start."""

        recovered: list[WorkerBindingRecord] = []
        for binding in self.registry.active_worker_bindings():
            if binding.runtime_generation >= self.runtime_generation:
                continue
            recovered.append(self._recover_orphan(binding))
        return tuple(recovered)

    def register(
        self,
        *,
        worker_kind: str,
        workspace_id: str,
        workspace_generation: int,
        pid: int,
        capability_digest: str,
        environment_digest: str,
    ) -> ManagedWorker:
        conflicting = next(
            (
                binding
                for binding in self.registry.active_worker_bindings()
                if binding.worker_kind == worker_kind
                and binding.workspace_id == workspace_id
            ),
            None,
        )
        if conflicting is not None:
            raise RuntimeError(
                "an active or unresolved worker still owns this workspace; "
                "successor registration is blocked"
            )
        identity = process_identity(pid)
        material = {
            "worker_kind": worker_kind,
            "workspace_id": workspace_id,
            "workspace_generation": workspace_generation,
            "runtime_generation": self.runtime_generation,
            "process_identity": identity.model_dump(mode="json"),
        }
        worker_id = f"worker-{canonical_sha256(material).removeprefix('sha256:')[:32]}"
        self.registry.register_worker_binding(
            worker_id=worker_id,
            runtime_generation=self.runtime_generation,
            worker_kind=worker_kind,
            workspace_id=workspace_id,
            workspace_generation=workspace_generation,
            pid=pid,
            process_start_identity=identity.model_dump_json(),
            capability_digest=capability_digest,
            environment_digest=environment_digest,
        )
        return ManagedWorker(worker_id=worker_id, process_identity=identity)

    def heartbeat(
        self,
        managed: ManagedWorker,
        *,
        operation: OperationRef | None = None,
        last_event_sequence: int | None = None,
    ) -> WorkerBindingRecord:
        observation = observe_process_identity(managed.process_identity)
        if observation.state is ProcessIdentityState.UNAVAILABLE:
            assert observation.error is not None
            raise observation.error
        if observation.state is ProcessIdentityState.MISMATCH:
            return self.finish(managed, disposition="lost", reason="worker_identity_missing")
        return self.registry.heartbeat_worker_binding(
            worker_id=managed.worker_id,
            runtime_generation=self.runtime_generation,
            process_start_identity=managed.process_identity.model_dump_json(),
            state="busy" if operation is not None else "ready",
            operation_id=None if operation is None else operation.value,
            last_event_sequence=last_event_sequence,
        )

    def mark_terminating(self, managed: ManagedWorker) -> WorkerBindingRecord:
        return self.registry.heartbeat_worker_binding(
            worker_id=managed.worker_id,
            runtime_generation=self.runtime_generation,
            process_start_identity=managed.process_identity.model_dump_json(),
            state="terminating",
        )

    def finish(
        self,
        managed: ManagedWorker,
        *,
        disposition: WorkerTerminalState,
        reason: str,
    ) -> WorkerBindingRecord:
        receipt = WorkerTerminationReceipt.issue(
            worker_id=managed.worker_id,
            runtime_generation=self.runtime_generation,
            process_identity=managed.process_identity,
            disposition=disposition,
            reason=reason,
            at_unix_ms=self._now_ms(),
        )
        return self.registry.finish_worker_binding(
            worker_id=managed.worker_id,
            runtime_generation=self.runtime_generation,
            process_start_identity=managed.process_identity.model_dump_json(),
            state=disposition,
            termination_receipt_json=canonical_json_bytes(receipt).decode(),
        )

    def _recover_orphan(self, binding: WorkerBindingRecord) -> WorkerBindingRecord:
        try:
            expected = ProcessStartIdentity.model_validate_json(
                binding.process_start_identity, strict=True
            )
        except ValueError:
            return self._finish_prior(
                binding,
                None,
                disposition="quarantined",
                reason="malformed_process_identity",
            )
        observation = observe_process_identity(expected)
        if observation.state is ProcessIdentityState.UNAVAILABLE:
            return binding
        if observation.state is ProcessIdentityState.MISMATCH:
            return self._finish_prior(
                binding,
                expected,
                disposition="quarantined",
                reason=(
                    "orphan_process_absent"
                    if observation.observed is None
                    else "pid_reused_identity_mismatch"
                ),
            )
        assert observation.observed is not None
        observed = observation.observed
        if observed != expected:
            return self._finish_prior(
                binding,
                expected,
                disposition="quarantined",
                reason="pid_reused_identity_mismatch",
            )
        if binding.pid == os.getpid():
            return self._finish_prior(
                binding,
                expected,
                disposition="quarantined",
                reason="refused_to_terminate_supervisor_process",
            )
        try:
            terminated = self._terminate_exact(expected)
        except ProcessIdentityMismatch:
            return self._finish_prior(
                binding,
                expected,
                disposition="quarantined",
                reason="orphan_identity_changed_before_exact_signal",
            )
        except ProcessIdentityUnavailable:
            return binding
        if not terminated:
            return binding
        return self._finish_prior(
            binding,
            expected,
            disposition="terminated",
            reason="orphan_fenced",
        )

    def _finish_prior(
        self,
        binding: WorkerBindingRecord,
        identity: ProcessStartIdentity | None,
        *,
        disposition: WorkerTerminalState,
        reason: str,
    ) -> WorkerBindingRecord:
        if identity is None:
            payload = {
                "schema_version": WORKER_RECEIPT_SCHEMA_VERSION,
                "worker_id": binding.worker_id,
                "runtime_generation": binding.runtime_generation,
                "process_identity": None,
                "recorded_process_identity": binding.process_start_identity,
                "disposition": disposition,
                "reason": reason,
                "at_unix_ms": self._now_ms(),
            }
            receipt_json = canonical_json_bytes(
                {**payload, "receipt_digest": canonical_sha256(payload)}
            ).decode()
        else:
            receipt_json = canonical_json_bytes(
                WorkerTerminationReceipt.issue(
                    worker_id=binding.worker_id,
                    runtime_generation=binding.runtime_generation,
                    process_identity=identity,
                    disposition=disposition,
                    reason=reason,
                    at_unix_ms=self._now_ms(),
                )
            ).decode()
        return self.registry.finish_worker_binding(
            worker_id=binding.worker_id,
            runtime_generation=binding.runtime_generation,
            process_start_identity=binding.process_start_identity,
            state=disposition,
            termination_receipt_json=receipt_json,
        )

    def _terminate_exact(self, identity: ProcessStartIdentity) -> bool:
        target = open_exact_process(identity, terminate=True)
        with target:
            if os.name == "nt":
                sent = target.send_signal(signal.SIGTERM)
                if not sent and target.wait(0):
                    return True
                return target.wait(self._termination_timeout_s)
            sent = target.send_signal(signal.SIGTERM)
            if not sent:
                return target.wait(0)
            if target.wait(self._termination_timeout_s):
                return True
            sent = target.send_signal(signal.SIGKILL)
            if not sent:
                return target.wait(0)
            return target.wait(self._termination_timeout_s)


__all__ = [
    "WORKER_RECEIPT_SCHEMA_VERSION",
    "ManagedWorker",
    "WorkerManager",
    "WorkerTerminationReceipt",
]
