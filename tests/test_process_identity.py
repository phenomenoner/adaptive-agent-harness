from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.runtime.process_identity import (
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SUPERVISOR_VERSION,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
    current_process_identity,
    process_identity,
    process_identity_matches,
)

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


def test_unsupported_platform_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
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
