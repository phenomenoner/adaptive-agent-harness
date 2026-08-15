from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest
from pydantic import ValidationError

import aar.runtime.process_identity as process_identity_module
from aar.canonical import canonical_sha256
from aar.runtime.process_identity import (
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SUPERVISOR_VERSION,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
    current_process_identity,
    open_exact_process,
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


def test_exact_process_handle_signals_and_observes_one_real_python_child() -> None:
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
        identity = process_identity(process.pid)
        with open_exact_process(identity, terminate=True) as target:
            assert target.send_signal(signal.SIGTERM)
            assert target.wait(5)
        process.wait(timeout=5)
        assert process.returncode is not None
        assert not process_identity_matches(identity)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


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
    record = SupervisorDiscoveryRecord.issue(
        endpoint_kind="unix",
        endpoint_ref="opaque-socket-ref",
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
    record = SupervisorDiscoveryRecord.issue(
        endpoint_kind="tcp",
        endpoint_ref="127.0.0.1:opaque",
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


@pytest.mark.skipif(os.name != "nt", reason="Windows GetProcessTimes path is platform guarded")
def test_windows_identity_uses_current_process_start_time() -> None:
    identity = current_process_identity()
    assert identity.platform == "windows"
    assert identity.boot_id is None
    assert identity.start_time > 0
