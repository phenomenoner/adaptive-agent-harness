from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

from aar.broker_models import ModelRouteCatalog, ModelRouteProfile
from aar.mcp.server import build_server
from aar.rlm_models import RlmJobSpec
from aar.runtime.dispatcher import DispatcherDrainTimeout
from aar.runtime.model_broker import ReferenceModelBroker, StaticModelBrokerRegistry
from aar.runtime.ownership import RuntimeOwnershipConflict, RuntimeOwnershipLock
from aar.runtime.reference_host import ReferenceHostError
from aar.schemas import Budget, PrincipalRef, SessionRef


def test_windows_runtime_ownership_uses_process_owned_first_instance_pipe() -> None:
    assert RuntimeOwnershipLock._WINDOWS_PIPE_PREFIX == r"\\.\pipe\AAR.RuntimeOwnership."
    assert not hasattr(RuntimeOwnershipLock, "_WINDOWS_MUTEX_PREFIX")


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows pipe semantics")
def test_windows_runtime_ownership_is_not_same_thread_recursive(tmp_path: Path) -> None:
    database = tmp_path / "reference.sqlite3"
    first = RuntimeOwnershipLock(database)
    try:
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            RuntimeOwnershipLock(database)
    finally:
        first.close()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows pipe semantics")
def test_windows_runtime_ownership_can_close_from_another_thread(tmp_path: Path) -> None:
    database = tmp_path / "reference.sqlite3"
    first = RuntimeOwnershipLock(database)
    errors: list[BaseException] = []

    def close_owner() -> None:
        try:
            first.close()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=close_owner)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors == []
    with RuntimeOwnershipLock(database):
        pass


def test_runtime_ownership_uses_database_inode_without_auxiliary_artifact(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mode.sqlite3"
    previous = os.umask(0)
    try:
        ownership = RuntimeOwnershipLock(database)
    finally:
        os.umask(previous)
    try:
        assert stat.S_IMODE(ownership.path.stat().st_mode) == 0o600
        assert ownership.path == database.resolve()
        assert not Path(f"{database}.runtime.lock").exists()
        assert not (tmp_path / "supervisor").exists()
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE probe(value INTEGER NOT NULL)")
            connection.execute("INSERT INTO probe(value) VALUES (42)")
            connection.commit()
            assert connection.execute("SELECT value FROM probe").fetchone() == (42,)
    finally:
        ownership.close()


def test_runtime_ownership_rejects_non_file_database_path(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    database.mkdir()

    with pytest.raises(RuntimeOwnershipConflict, match="not a regular file"):
        RuntimeOwnershipLock(database)


def current_generation(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT generation FROM runtime_meta WHERE singleton = 1"
        ).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def test_mcp_composition_root_rejects_a_second_live_database_owner(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    first = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    try:
        generation = first.host.runtime_generation
        assert current_generation(database) == generation
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            build_server(
                database,
                programmable_backend="plain",
                enable_durable_dispatch=False,
            )
        assert current_generation(database) == generation
    finally:
        first.close()

    replacement = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    try:
        assert replacement.host.runtime_generation == generation + 1
    finally:
        replacement.close()


def test_failed_application_close_retains_runtime_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "failed-close.sqlite3"
    application = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    real_close = application.host.close

    def fail_close() -> None:
        raise RuntimeError("host resources remain live")

    monkeypatch.setattr(application.host, "close", fail_close)
    with pytest.raises(RuntimeError, match="resources remain live"):
        application.close()
    with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
        RuntimeOwnershipLock(database)

    monkeypatch.setattr(application.host, "close", real_close)
    application.close()
    with RuntimeOwnershipLock(database):
        pass


def test_partial_model_broker_close_must_finish_before_ownership_release(
    tmp_path: Path,
) -> None:
    class FailOnceClosingBroker(ReferenceModelBroker):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("model broker close failed")
            super().close()

    database = tmp_path / "partial-broker-close.sqlite3"
    profile = ModelRouteProfile(
        profile_id="partial-close-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="partial-close",
        max_output_tokens=32,
    )
    broker = FailOnceClosingBroker()
    registry = StaticModelBrokerRegistry(
        ModelRouteCatalog.issue((profile,)),
        brokers={profile.profile_id: broker},
    )
    application = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )

    with pytest.raises(RuntimeError, match="model broker close failed"):
        application.close()
    assert broker.close_calls == 1
    assert broker._closed is False
    with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
        RuntimeOwnershipLock(database)

    application.close()
    assert broker.close_calls == 2
    assert broker._closed is True
    with RuntimeOwnershipLock(database):
        pass


def test_real_dispatch_timeout_retains_owner_and_blocks_new_durable_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "real-dispatch-timeout.sqlite3"
    application = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    host = application.host
    callback_entered = Event()
    release_callback = Event()
    real_run_claimed = host.run_claimed_rlm

    def blocked_run_claimed(*args: object, **kwargs: object) -> object:
        callback_entered.set()
        assert release_callback.wait(10)
        return real_run_claimed(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(host, "run_claimed_rlm", blocked_run_claimed)
    dispatcher = host.start_durable_dispatch()
    real_dispatcher_close = dispatcher.close

    def fast_dispatcher_close(_drain_timeout_s: float = 5) -> None:
        real_dispatcher_close(0.01)

    monkeypatch.setattr(dispatcher, "close", fast_dispatcher_close)
    spec = RlmJobSpec(query="block real callback", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-real-dispatch-timeout",
        idempotency_key="idempotency-real-dispatch-timeout",
        principal=PrincipalRef(value="principal-real-dispatch-timeout"),
        session=SessionRef(value="session-real-dispatch-timeout"),
        spec=spec,
        deadline_unix_ms=host.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    real_accept = host.registry.accept
    accept_calls = 0

    def fail_if_accepted(*_args: object, **_kwargs: object) -> None:
        nonlocal accept_calls
        accept_calls += 1
        raise AssertionError("closing host must reject before registry.accept")

    try:
        submitted = host.submit_rlm_durable(envelope, spec)
        assert callback_entered.wait(2)
        assert submitted.state.value == "accepted"

        with pytest.raises(DispatcherDrainTimeout, match="still running"):
            application.close()
        assert host._closing is True
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            RuntimeOwnershipLock(database)

        monkeypatch.setattr(host.registry, "accept", fail_if_accepted)
        with pytest.raises(ReferenceHostError, match="closing"):
            host.submit_rlm_durable(envelope, spec)
        assert accept_calls == 0

        monkeypatch.setattr(host.registry, "accept", real_accept)
        monkeypatch.setattr(dispatcher, "close", real_dispatcher_close)
        release_callback.set()
        application.close()
        with RuntimeOwnershipLock(database):
            pass
    finally:
        release_callback.set()
        monkeypatch.setattr(host.registry, "accept", real_accept)
        monkeypatch.setattr(dispatcher, "close", real_dispatcher_close)
        application.close()


def test_runtime_ownership_is_released_by_hard_process_exit(tmp_path: Path) -> None:
    database = tmp_path / "subprocess.sqlite3"
    script = (
        "from pathlib import Path; import time; "
        "from aar.runtime.ownership import RuntimeOwnershipLock; "
        f"lock=RuntimeOwnershipLock(Path({str(database)!r})); "
        "print('locked', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            RuntimeOwnershipLock(database)
    finally:
        process.kill()
        process.wait(timeout=10)

    with RuntimeOwnershipLock(database) as ownership:
        assert ownership.path == database.resolve()
        assert ownership.path.is_file()
        assert not Path(f"{database}.runtime.lock").exists()
        assert not (tmp_path / "supervisor").exists()
