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

DIGEST = canonical_sha256({"fixture": "provider-ready-generation-restart"})


def _grant_set(runtime_generation: int) -> WorkbenchGrantSet:
    return WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=runtime_generation,
        activation_generation=3,
        profile_id="profile-primary",
        profile_digest=DIGEST,
        activation_authority_digest=DIGEST,
        capability_digest=DIGEST,
        route_catalog_digest=DIGEST,
        principal_ids=("principal-a",),
        session_binding_policy="bind_exact_request_session",
        capabilities=("rlm.workbench.execute",),
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


def _issue_session_grant(store: ProviderReadyActivationStore, grant_id: str):
    return store.issue_session_grant(
        principal_id="principal-a",
        session_id="session-a",
        capability="rlm.workbench.execute",
        issued_at_unix_ms=1_000,
        ttl_ms=10_000,
        policy_approved=True,
        grant_id=grant_id,
    )


def _accept_session_grant(store: ProviderReadyActivationStore, grant):
    return store.accept_session_grant(
        grant,
        principal_id="principal-a",
        session_id="session-a",
        capability="rlm.workbench.execute",
        runtime_generation=grant.runtime_generation,
        now_unix_ms=2_000,
    )


def test_restart_fences_stale_replay_at_highest_durable_generation(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    first_process = ProviderReadyActivationStore(authority)
    generation_7 = _grant_set(7)
    generation_8 = _grant_set(8)
    first_process.publish(generation_7)
    first_process.publish(generation_8)

    restarted = ProviderReadyActivationStore(authority)
    with pytest.raises(ActivationGenerationConflict, match="older than current generation 8"):
        restarted.publish(generation_7)

    assert restarted.current_runtime_generation == 8
    issued = _issue_session_grant(restarted, "grant-after-restart")
    assert issued.runtime_generation == 8
    assert _accept_session_grant(restarted, issued) == issued

    replayed = restarted.publish(generation_8)
    assert replayed.replayed is True
    assert restarted.current_runtime_generation == 8


def test_restart_allows_successor_after_a_generation_gap_but_not_gap_replay(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    first_process = ProviderReadyActivationStore(authority)
    first_process.publish(_grant_set(7))
    first_process.publish(_grant_set(9))

    restarted = ProviderReadyActivationStore(authority)
    with pytest.raises(ActivationGenerationConflict, match="older than current generation 9"):
        restarted.publish(_grant_set(8))
    assert restarted.current_runtime_generation == 9

    successor = restarted.publish(_grant_set(10))
    assert successor.replayed is False
    assert restarted.current_runtime_generation == 10


def test_successor_publication_invalidates_prior_memory_only_sessions(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    store = ProviderReadyActivationStore(authority)
    store.publish(_grant_set(7))
    prior = _issue_session_grant(store, "grant-prior")
    assert _accept_session_grant(store, prior) == prior

    store.publish(_grant_set(8))
    with pytest.raises(GrantDenied, match="not owned by this activation process"):
        _accept_session_grant(store, prior)

    successor = _issue_session_grant(store, "grant-successor")
    assert successor.runtime_generation == 8
    assert _accept_session_grant(store, successor) == successor


def test_external_successor_fences_previous_process_sessions(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    first_process = ProviderReadyActivationStore(authority)
    first_process.publish(_grant_set(7))
    prior = _issue_session_grant(first_process, "grant-external-successor")

    successor_process = ProviderReadyActivationStore(authority)
    successor_process.publish(_grant_set(8))

    with pytest.raises(GrantDenied, match="authority cannot be read back"):
        _accept_session_grant(first_process, prior)

    successor = _issue_session_grant(first_process, "grant-external-successor")
    assert successor.runtime_generation == 8
    assert _accept_session_grant(first_process, successor) == successor


def test_exact_same_generation_replay_preserves_live_session(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    store = ProviderReadyActivationStore(authority)
    generation_7 = _grant_set(7)
    store.publish(generation_7)
    prior = _issue_session_grant(store, "grant-exact-replay")

    replayed = store.publish(generation_7)
    assert replayed.replayed is True
    assert _accept_session_grant(store, prior) == prior


def test_restart_rejects_unknown_generation_root_sibling(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    first_process = ProviderReadyActivationStore(authority)
    first_process.publish(_grant_set(7))
    runtime_root = authority / "runtime-generations"
    (runtime_root / "not-a-generation").mkdir(mode=0o700)

    restarted = ProviderReadyActivationStore(authority)
    with pytest.raises(ActivationStoreError, match="unknown runtime-generations sibling"):
        restarted.publish(_grant_set(8))
    assert restarted.current_runtime_generation is None


def test_restart_rejects_malformed_extra_generation_entry(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    first_process = ProviderReadyActivationStore(authority)
    first_process.publish(_grant_set(7))
    runtime_root = authority / "runtime-generations"
    malformed_generation = runtime_root / "00000000000000000008"
    malformed_generation.mkdir(mode=0o700)
    malformed_file = malformed_generation / "workbench-grant-set.json"
    malformed_file.write_bytes(b"{}")
    malformed_file.chmod(0o600)

    restarted = ProviderReadyActivationStore(authority)
    with pytest.raises(ActivationStoreError, match="canonical grant-set file is malformed"):
        restarted.publish(_grant_set(7))
    assert restarted.current_runtime_generation is None
