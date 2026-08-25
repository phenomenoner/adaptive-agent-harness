from __future__ import annotations

from pathlib import Path

import pytest

from aar.canonical import canonical_sha256
from aar.provider_ready_models import GrantBudgetCeiling
from aar.provider_ready_runtime_models import (
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    WorkbenchGrantSet,
)
from aar.runtime.provider_ready_activation import (
    ActivationGenerationConflict,
    ActivationStoreError,
    GrantDenied,
    ProviderReadyActivationStore,
)

DIGEST = canonical_sha256({"fixture": "provider-ready-activation"})


def _grant_set(
    *,
    principal_ids: tuple[str, ...] = ("principal-a",),
    capabilities: tuple[str, ...] = ("rlm.workbench.execute",),
) -> WorkbenchGrantSet:
    return WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=7,
        activation_generation=3,
        profile_id="profile-primary",
        profile_digest=DIGEST,
        activation_authority_digest=DIGEST,
        capability_digest=DIGEST,
        route_catalog_digest=DIGEST,
        principal_ids=principal_ids,  # type: ignore[arg-type]
        session_binding_policy="bind_exact_request_session",
        capabilities=capabilities,  # type: ignore[arg-type]
        budget_ceiling=GrantBudgetCeiling(
            wall_time_ms=900_000,
            model_requests=128,
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            child_operations=64,
            artifact_bytes=33_554_432,
        ),
        max_ttl_ms=900_000,
    )


def _authority(tmp_path: Path) -> Path:
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    return authority


def test_publish_refuses_missing_authority_without_creating_it(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    store = ProviderReadyActivationStore(authority)

    with pytest.raises(ActivationStoreError, match=r"authority path component.*is missing"):
        store.publish(_grant_set())

    assert not authority.exists()


def test_publish_is_durable_replayable_and_generation_conflict_is_fail_closed(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    store = ProviderReadyActivationStore(authority)
    grant_set = _grant_set()

    published = store.publish(grant_set)
    assert published.replayed is False
    assert published.path.read_bytes()
    assert published.path.stat().st_mode & 0o777 == 0o600
    replayed = store.publish(grant_set)
    assert replayed.replayed is True
    assert replayed.path == published.path
    assert store.read(7) == grant_set

    different = _grant_set(capabilities=("rlm.workbench.execute", "rlm.workbench.status"))
    with pytest.raises(ActivationGenerationConflict, match="different grant-set bytes"):
        store.publish(different)
    assert store.read(7) == grant_set


def test_session_grants_are_memory_only_and_bound_to_current_generation(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    grant_set = _grant_set()
    first_process = ProviderReadyActivationStore(authority)
    first_process.publish(grant_set)
    issued = first_process.issue_session_grant(
        principal_id="principal-a",
        session_id="session-a",
        capability="rlm.workbench.execute",
        issued_at_unix_ms=1_000,
        ttl_ms=10_000,
        policy_approved=True,
        grant_id="grant-a",
    )
    assert (
        first_process.accept_session_grant(
            issued,
            principal_id="principal-a",
            session_id="session-a",
            capability="rlm.workbench.execute",
            runtime_generation=7,
            now_unix_ms=2_000,
        )
        == issued
    )
    first_process.revoke_session_grant(issued)
    with pytest.raises(GrantDenied, match="revoked"):
        first_process.accept_session_grant(
            "grant-a",
            principal_id="principal-a",
            session_id="session-a",
            capability="rlm.workbench.execute",
            runtime_generation=7,
            now_unix_ms=2_000,
        )

    restarted = ProviderReadyActivationStore(authority)
    assert restarted.publish(grant_set).replayed is True
    with pytest.raises(GrantDenied, match="not owned by this activation process"):
        restarted.accept_session_grant(
            "grant-a",
            principal_id="principal-a",
            session_id="session-a",
            capability="rlm.workbench.execute",
            runtime_generation=7,
            now_unix_ms=2_000,
        )
