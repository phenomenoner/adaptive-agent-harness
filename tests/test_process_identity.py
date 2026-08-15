from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

import aar.runtime.process_identity as process_identity_module
from aar.canonical import canonical_sha256
from aar.runtime.process_identity import (
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SUPERVISOR_VERSION,
    ExactChild,
    ExactChildState,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
    bind_exact_child,
    current_process_identity,
    process_identity,
    process_identity_matches,
)
from aar.runtime.python_child import exact_module_command

DIGEST = canonical_sha256({"fixture": "process-identity"})
OTHER_DIGEST = canonical_sha256({"fixture": "other"})


def test_current_process_identity_is_native_and_repeatable() -> None:
    identity = current_process_identity()
    observed = process_identity(os.getpid())

    assert identity == observed
    assert identity.pid == os.getpid()
    assert identity.platform in {"linux", "windows"}
    assert identity.start_time > 0
    if identity.platform == "linux":
        assert identity.boot_id
        assert identity.native_identity.startswith("linux:")
    else:
        assert identity.boot_id is None
        assert identity.native_identity.startswith("windows:")
    assert process_identity_matches(identity)


def test_dead_or_missing_pid_fails_closed() -> None:
    # This is outside every normal Linux/Windows PID range and must not be reduced to PID-only
    # identity.  The platform implementation is still required to fail closed if it is absent.
    with pytest.raises(ProcessIdentityUnavailable):
        process_identity(2**31 - 1)


def test_process_identity_matches_propagates_native_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = current_process_identity()

    def unavailable(_pid: int) -> ProcessStartIdentity:
        raise ProcessIdentityUnavailable("transient native observation failure")

    monkeypatch.setattr(process_identity_module, "process_identity", unavailable)

    with pytest.raises(ProcessIdentityUnavailable, match="transient native observation"):
        process_identity_matches(identity)


def test_spawn_bound_exact_child_signals_waits_and_reaps_one_real_python_child() -> None:
    process = subprocess.Popen(
        exact_module_command("aar.runtime.ipython_worker", isolated=True),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdout is not None
        assert '"ready":true' in process.stdout.readline()
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "command": "execute",
                    "code": "__import__('os').getpid()",
                    "max_events": 8,
                    "max_output_chars": 4_096,
                    "artifact_enabled": False,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        assert response["ok"] is True
        assert response["status"] == "succeeded"
        assert response["result"] == process.pid
        child = bind_exact_child(
            process,
            owner_kind="process-identity-test",
            owner_generation="generation-1",
        )
        identity = child.identity
        receipt = child.terminalize(5)
        assert receipt.identity == identity
        assert receipt.signal_count == 1
        assert receipt.exact_wait_count >= 2
        assert receipt.returncode == process.returncode
        assert child.state is ExactChildState.TERMINAL
        assert process.returncode is not None
        assert not process_identity_matches(identity)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_exact_child_serializes_terminalizers_on_one_retained_handle() -> None:
    identity = ProcessStartIdentity(
        pid=12_345,
        platform="linux",
        start_time=99,
        boot_id="test-boot",
    )

    class FakeProcess:
        pid = identity.pid
        returncode: int | None = None
        wait_count = 0
        unsafe_signal_count = 0

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.wait_count += 1
            self.returncode = -15
            return self.returncode

        def terminate(self) -> None:
            self.unsafe_signal_count += 1

        def kill(self) -> None:
            self.unsafe_signal_count += 1

    class FakeHandle:
        kind = "linux-pidfd"

        def __init__(self) -> None:
            self.identity = identity
            self.close_count = 0
            self.signal_calls: list[int] = []
            self.wait_calls: list[float] = []

        def send_signal(self, signum: int) -> bool:
            self.signal_calls.append(signum)
            return True

        def wait(self, timeout_sec: float) -> bool:
            self.wait_calls.append(timeout_sec)
            return len(self.wait_calls) > 1

        def close(self) -> None:
            self.close_count += 1

    process = FakeProcess()
    target = FakeHandle()
    child = ExactChild(
        process,  # type: ignore[arg-type]
        identity,
        target,  # type: ignore[arg-type]
        owner_kind="ipython-worker",
        owner_generation="workspace-1:generation-1",
        spawn_nonce="nonce-1",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = tuple(pool.map(lambda _index: child.terminalize(1), range(2)))

    assert receipts[0] is receipts[1]
    assert target.signal_calls == [signal.SIGTERM]
    assert target.wait_calls == [0, 1]
    assert target.close_count == 1
    assert process.wait_count == 1
    assert process.unsafe_signal_count == 0
    assert child.terminal_receipt is receipts[0]
    assert child.state is ExactChildState.TERMINAL


def test_exact_child_contains_unavailable_signal_without_popen_fallback() -> None:
    identity = ProcessStartIdentity(
        pid=12_346,
        platform="linux",
        start_time=100,
        boot_id="test-boot",
    )

    class FakeProcess:
        pid = identity.pid
        unsafe_signal_count = 0

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            raise AssertionError("unconfirmed child must not be reaped as terminal")

        def terminate(self) -> None:
            self.unsafe_signal_count += 1

        def kill(self) -> None:
            self.unsafe_signal_count += 1

    class UnavailableHandle:
        kind = "linux-pidfd"

        def __init__(self) -> None:
            self.identity = identity

        def wait(self, _timeout_sec: float) -> bool:
            return False

        def send_signal(self, _signum: int) -> bool:
            raise ProcessIdentityUnavailable("injected exact-handle signal gap")

        def close(self) -> None:
            raise AssertionError("unresolved exact handle must remain retained")

    process = FakeProcess()
    child = ExactChild(
        process,  # type: ignore[arg-type]
        identity,
        UnavailableHandle(),  # type: ignore[arg-type]
        owner_kind="launcher",
        owner_generation="runtime-1",
    )

    with pytest.raises(ProcessIdentityUnavailable, match="signal gap"):
        child.terminalize(1)
    with pytest.raises(ProcessIdentityUnavailable, match="unresolved"):
        child.terminalize(1)

    assert process.unsafe_signal_count == 0
    assert child.state is ExactChildState.UNRESOLVED


def test_unsupported_platform_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_identity_module.os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "plan9")
    with pytest.raises(ProcessIdentityUnavailable, match="unsupported"):
        process_identity(os.getpid())


def test_process_identity_model_is_strict() -> None:
    identity = current_process_identity()
    document = identity.model_dump(mode="json")
    document["unexpected"] = True

    with pytest.raises(ValidationError):
        ProcessStartIdentity.model_validate(document, strict=True)


def test_discovery_record_round_trip_and_self_digest() -> None:
    identity = current_process_identity()
    publication_id = "1" * 32
    record = SupervisorDiscoveryRecord.issue(
        publication_id=publication_id,
        endpoint_kind="unix",
        endpoint_ref="opaque-socket-ref",
        credential_file=f"attachment-{publication_id}.key",
        shutdown_request_file=f"shutdown-{publication_id}.request",
        process_identity=identity,
        runtime_generation=3,
        dispatcher_generation=5,
        capability_digest=DIGEST,
        runtime_home_digest=OTHER_DIGEST,
        ready_at_unix_ms=1_000,
        attachment_credential_digest=DIGEST,
        supervisor_version=DEFAULT_SUPERVISOR_VERSION,
        protocol_version=DEFAULT_PROTOCOL_VERSION,
    )

    restored = SupervisorDiscoveryRecord.model_validate_json(
        record.model_dump_json(), strict=True
    )
    assert restored == record
    assert restored.validate_discovery_digest() == record
    assert restored.pid == identity.pid
    assert "super-secret" not in repr(restored)
    assert "super-secret" not in restored.model_dump_json()


def test_discovery_rejects_pid_mismatch_and_wrong_digest() -> None:
    identity = current_process_identity()
    publication_id = "2" * 32
    record = SupervisorDiscoveryRecord.issue(
        publication_id=publication_id,
        endpoint_kind="tcp",
        endpoint_ref="127.0.0.1:opaque",
        credential_file=f"attachment-{publication_id}.key",
        shutdown_request_file=f"shutdown-{publication_id}.request",
        process_identity=identity,
        runtime_generation=1,
        dispatcher_generation=1,
        capability_digest=DIGEST,
        runtime_home_digest=DIGEST,
        ready_at_unix_ms=1,
        attachment_credential_digest=DIGEST,
    )

    wrong_digest = record.model_dump(mode="json")
    wrong_digest["discovery_digest"] = OTHER_DIGEST
    with pytest.raises(ValidationError, match="discovery digest"):
        SupervisorDiscoveryRecord.model_validate(wrong_digest, strict=True)

    wrong_pid = record.model_dump(mode="json")
    wrong_pid["pid"] = identity.pid + 1
    # The old digest is intentionally retained, so either the binding or self-digest check must
    # reject the record before it can be treated as an owner.
    with pytest.raises(ValidationError, match="discovery"):
        SupervisorDiscoveryRecord.model_validate(wrong_pid, strict=True)

    wrong_control_name = record.model_dump(mode="json")
    wrong_control_name["credential_file"] = "attachment-wrong.key"
    wrong_control_name["discovery_digest"] = canonical_sha256(
        {key: value for key, value in wrong_control_name.items() if key != "discovery_digest"}
    )
    with pytest.raises(ValidationError, match="publication id"):
        SupervisorDiscoveryRecord.model_validate(wrong_control_name, strict=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows GetProcessTimes path is platform guarded")
def test_windows_identity_uses_current_process_start_time() -> None:
    identity = current_process_identity()
    assert identity.platform == "windows"
    assert identity.boot_id is None
    assert identity.start_time > 0
