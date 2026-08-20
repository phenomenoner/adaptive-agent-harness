from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aar.canonical import canonical_sha256
from aar.compat import codex_setup
from aar.compat.codex_setup import configure_codex, run_setup_preflight
from aar.runtime.process_identity import (
    SupervisorDiscoveryRecord,
    process_identity_matches,
)
from aar.versions import PACKAGE_VERSION

ROOT = Path(__file__).resolve().parents[1]


class _FakeCodexState:
    """Deterministic in-memory Codex CLI/config state for transaction regressions."""

    def __init__(
        self,
        config: Path,
        marketplace: Path,
        *,
        marketplace_root: Path | None = None,
        plugin: dict[str, object] | None = None,
        global_command: str | None = None,
    ) -> None:
        self.config = config
        self.marketplace = marketplace.resolve(strict=True)
        self.marketplace_root = (
            marketplace_root.resolve(strict=False) if marketplace_root is not None else None
        )
        self.plugin = dict(plugin) if plugin is not None else None
        self.calls: list[tuple[str, ...]] = []
        self.marketplace_reads = 0
        self.plugin_reads = 0
        self.read_failure_call: int | None = None
        self.fail_marketplace_read_on: int | None = None
        self.fail_plugin_read_on: int | None = None
        self.plugin_add_error_before_mutation: BaseException | None = None
        self.plugin_add_error: BaseException | None = None
        self.plugin_add_version: str | None = None
        self.plugin_add_enabled = True
        self.marketplace_add_error: BaseException | None = None
        self.mcp_remove_error: BaseException | None = None
        self.mcp_remove_leaves_global = False
        self._hooks: dict[tuple[str, ...], object] = {}
        self._post_hooks: dict[tuple[str, ...], object] = {}
        self._initial_config_bytes = config.read_bytes() if config.is_file() else None
        if global_command is not None:
            self._write_global(global_command)
            self._initial_config_bytes = config.read_bytes()

    def _write_global(self, command: str | None) -> None:
        if command is None:
            if self.config.exists():
                self.config.write_text("", encoding="utf-8")
            return
        self.config.write_text(
            f"[mcp_servers.aar]\ncommand = {json.dumps(command)}\n", encoding="utf-8"
        )

    def restore_initial_global(self) -> None:
        if self._initial_config_bytes is None:
            if self.config.exists():
                self.config.unlink()
            return
        self.config.write_bytes(self._initial_config_bytes)

    def snapshot(self) -> dict[str, object]:
        return {
            "marketplace_root": (
                os.fspath(self.marketplace_root) if self.marketplace_root is not None else None
            ),
            "plugin": dict(self.plugin) if self.plugin is not None else None,
            "global_config": self.config.read_bytes() if self.config.is_file() else None,
        }

    def set_hook(self, arguments: tuple[str, ...], callback) -> None:
        self._hooks[arguments] = callback

    def set_post_hook(self, arguments: tuple[str, ...], callback) -> None:
        self._post_hooks[arguments] = callback

    def _run_hook(self, hooks: dict[tuple[str, ...], object], arguments: tuple[str, ...]) -> None:
        callback = hooks.pop(arguments, None)
        if callback is not None:
            callback(self)

    def invoke(self, arguments: tuple[str, ...]) -> str:
        arguments = tuple(arguments)
        self.calls.append(arguments)
        self._run_hook(self._hooks, arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            self.marketplace_reads += 1
            if self.fail_marketplace_read_on == self.marketplace_reads:
                self.read_failure_call = len(self.calls)
                raise RuntimeError("injected current marketplace read failure")
            marketplaces = []
            if self.marketplace_root is not None:
                marketplaces.append(
                    {
                        "name": codex_setup.MARKETPLACE_NAME,
                        "root": os.fspath(self.marketplace_root),
                    }
                )
            return json.dumps({"marketplaces": marketplaces})
        if arguments == ("plugin", "marketplace", "remove", codex_setup.MARKETPLACE_NAME):
            self.marketplace_root = None
            return "Removed marketplace aar-local"
        if arguments[:3] == ("plugin", "marketplace", "add"):
            self.marketplace_root = Path(arguments[3]).resolve(strict=False)
            self._run_hook(self._post_hooks, arguments)
            if self.marketplace_add_error is not None:
                raise self.marketplace_add_error
            return '{"name":"aar-local"}'
        if arguments == ("plugin", "list", "--json"):
            self.plugin_reads += 1
            if self.fail_plugin_read_on == self.plugin_reads:
                self.read_failure_call = len(self.calls)
                raise RuntimeError("injected current plugin read failure")
            installed = [dict(self.plugin)] if self.plugin is not None else []
            return json.dumps({"installed": installed})
        if arguments[:2] == ("plugin", "add"):
            if self.plugin_add_error_before_mutation is not None:
                raise self.plugin_add_error_before_mutation
            expected = self.plugin_add_version or codex_setup._plugin_version(self.marketplace)
            self.plugin = {
                "pluginId": codex_setup.PLUGIN_SELECTOR,
                "version": expected,
                "enabled": self.plugin_add_enabled,
            }
            self._run_hook(self._post_hooks, arguments)
            if self.plugin_add_error is not None:
                raise self.plugin_add_error
            return json.dumps(self.plugin)
        if arguments[:2] == ("plugin", "remove"):
            self.plugin = None
            return "Removed plugin"
        if arguments == ("mcp", "remove", "aar"):
            if self.mcp_remove_error is not None:
                raise self.mcp_remove_error
            if not self.mcp_remove_leaves_global:
                self._write_global(None)
            return "Removed global MCP server 'aar'."
        if arguments[:2] == ("mcp", "add"):
            self.restore_initial_global()
            return "Added global MCP server 'aar'."
        raise AssertionError(arguments)


def _is_mutating_codex_call(call: tuple[str, ...]) -> bool:
    return (
        call[:3] == ("plugin", "marketplace", "add")
        or call == ("plugin", "marketplace", "remove", codex_setup.MARKETPLACE_NAME)
        or call[:2]
        in {
            ("plugin", "add"),
            ("plugin", "disable"),
            ("plugin", "remove"),
            ("mcp", "add"),
            ("mcp", "remove"),
        }
    )


def test_setup_preflight_uses_declared_codex_launcher_and_attached_supervisor(
    tmp_path: Path,
) -> None:
    name = "aar-codex-mcp.exe" if os.name == "nt" else "aar-codex-mcp"
    command = Path(sys.executable).with_name(name)
    report = asyncio.run(run_setup_preflight(command, runtime_home=tmp_path / "runtime"))
    assert report["declared_command"] == os.fspath(command.resolve(strict=True))
    assert report["declared_args"] == []
    assert report["server_name"] == "aar-mcp"
    assert report["tool_count"] == 38
    assert report["supervisor"]["mode"] == "attached-supervisor"
    assert report["supervisor"]["frontend_ephemeral"] is True
    assert report["supervisor"]["process_identity_digest"].startswith("sha256:")
    assert report["calculation"] == {"answer": 18, "rows": 3}
    assert set(report["dependencies"]) == {"ipython", "numpy", "pandas"}
    assert report["workspace_closed"] is True
    assert report["supervisor_stopped"] is True
    discovery_path = tmp_path / "runtime/supervisor/discovery.json"
    retained = SupervisorDiscoveryRecord.model_validate_json(
        discovery_path.read_bytes(), strict=True
    )
    assert (
        canonical_sha256(retained.process_identity)
        == report["supervisor"]["process_identity_digest"]
    )
    assert not process_identity_matches(retained.process_identity)


def test_resolve_aar_mcp_uses_active_environment_launcher(monkeypatch) -> None:
    name = "aar-mcp.exe" if os.name == "nt" else "aar-mcp"
    local = Path(sys.executable).with_name(name)
    monkeypatch.setattr(codex_setup.shutil, "which", lambda _name: None)

    assert codex_setup.resolve_aar_mcp() == local.resolve(strict=True)


def _manual_report(
    state: _FakeCodexState,
    marketplace: Path,
    aar_mcp: Path,
    config: Path,
) -> dict[str, object]:
    before = state.snapshot()
    report = configure_codex(
        Path(r"C:\tools\codex.cmd"),
        aar_mcp,
        marketplace,
        invoke=state.invoke,
        codex_config=config,
    )
    assert report["authority_disposition"] == "NO_ATOMIC_AUTHORITY"
    assert report["status"] == "manual_authority_required"
    assert report["configuration_changed"] is False
    assert report["completed_mutations"] == []
    assert report["restart_required_after_manual_apply"] is True
    assert state.snapshot() == before
    assert not any(_is_mutating_codex_call(call) for call in state.calls)
    return report


def _plan_stages(report: dict[str, object]) -> list[str]:
    return [item["stage"] for item in report["manual_plan"]]  # type: ignore[index]


def test_configure_codex_plans_plugin_and_matching_global_mcp_transition_without_effect(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(config, marketplace, global_command=os.fspath(aar_mcp))

    report = _manual_report(state, marketplace, aar_mcp, config)

    assert _plan_stages(report) == ["marketplace-add", "plugin-add", "global-mcp-remove"]
    assert report["legacy_global_mcp_present"] is True
    assert report["legacy_global_mcp_removed"] is False
    assert report["preflight_launcher"] == os.fspath(aar_mcp)


def test_configure_codex_reuses_existing_aar_marketplace_in_manual_plan(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path("/tools/aar-mcp")
    state = _FakeCodexState(tmp_path / "config.toml", marketplace, marketplace_root=marketplace)

    report = _manual_report(state, marketplace, aar_mcp, state.config)

    assert _plan_stages(report) == ["plugin-add"]
    assert report["marketplace_add_required"] is False
    assert report["marketplace_replace_required"] is False


def test_configure_codex_plans_stale_marketplace_replacement_without_effect(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    stale = tmp_path / "stale-marketplace"
    stale.mkdir()
    plugin = {
        "pluginId": codex_setup.PLUGIN_SELECTOR,
        "version": codex_setup._plugin_version(marketplace),
        "enabled": True,
    }
    state = _FakeCodexState(
        tmp_path / "config.toml", marketplace, marketplace_root=stale, plugin=plugin
    )

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert _plan_stages(report) == [
        "marketplace-remove-prior",
        "marketplace-add-target",
        "plugin-add",
    ]
    assert report["marketplace_replace_required"] is True
    assert report["marketplace_replaced"] is False


def test_configure_codex_does_not_enter_marketplace_failure_or_rollback(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    stale = tmp_path / "stale-marketplace"
    stale.mkdir()
    state = _FakeCodexState(tmp_path / "config.toml", marketplace, marketplace_root=stale)
    state.marketplace_add_error = RuntimeError("must remain unreachable")

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert _plan_stages(report)[:2] == ["marketplace-remove-prior", "marketplace-add-target"]


def test_configure_codex_does_not_enter_plugin_failure_or_rollback(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    state.plugin_add_error = RuntimeError("must remain unreachable")

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert "plugin-add" in _plan_stages(report)


def test_configure_codex_stops_before_first_plugin_add_failure(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    state.plugin_add_error_before_mutation = RuntimeError("must remain unreachable")

    _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)


@pytest.mark.parametrize("post_add_failure", ["wrong-version", "disabled"])
def test_configure_codex_does_not_depend_on_post_add_validation(
    tmp_path: Path, post_add_failure: str
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    if post_add_failure == "wrong-version":
        state.plugin_add_version = "0.0.0-wrong"
    else:
        state.plugin_add_enabled = False

    _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)


def test_configure_codex_never_claims_unobserved_plugin_state_for_undo(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    state.plugin_add_version = "9.9.9-foreign-or-untrusted"

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert report["completed_mutations"] == []


def test_configure_codex_does_not_enter_mcp_remove_command_failure(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    state = _FakeCodexState(
        tmp_path / "config.toml", marketplace, global_command=os.fspath(aar_mcp)
    )
    state.mcp_remove_error = RuntimeError("must remain unreachable")

    report = _manual_report(state, marketplace, aar_mcp, state.config)

    assert _plan_stages(report)[-1] == "global-mcp-remove"


def test_configure_codex_does_not_enter_mcp_remove_readback_failure(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    state = _FakeCodexState(
        tmp_path / "config.toml", marketplace, global_command=os.fspath(aar_mcp)
    )
    state.mcp_remove_leaves_global = True

    _manual_report(state, marketplace, aar_mcp, state.config)


def test_configure_codex_requires_only_one_read_only_snapshot(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    state.fail_marketplace_read_on = 2
    state.fail_plugin_read_on = 2

    _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert state.marketplace_reads == 1
    assert state.plugin_reads == 1


@pytest.mark.parametrize("surface", ["marketplace", "plugin"])
def test_configure_codex_unreadable_initial_inventory_fails_before_any_effect(
    tmp_path: Path, surface: str
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    if surface == "marketplace":
        state.fail_marketplace_read_on = 1
    else:
        state.fail_plugin_read_on = 1
    before = state.snapshot()

    with pytest.raises(RuntimeError, match=r"injected current .* read failure"):
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            Path("/tools/aar-mcp"),
            marketplace,
            invoke=state.invoke,
            codex_config=state.config,
        )

    assert state.snapshot() == before
    assert not any(_is_mutating_codex_call(call) for call in state.calls)


def test_configure_codex_never_runs_mutation_hook_that_would_publish_drift(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    hook_ran = False

    def publish_drift(_state: _FakeCodexState) -> None:
        nonlocal hook_ran
        hook_ran = True

    state.set_hook(("plugin", "add", codex_setup.PLUGIN_SELECTOR, "--json"), publish_drift)

    _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert hook_ran is False


def test_configure_codex_never_claims_predicted_marketplace_state(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    state.marketplace_add_error = RuntimeError("must remain unreachable")

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert report["current_state"]["marketplace_root"] is None  # type: ignore[index]


def test_configure_codex_has_no_automatic_undo_read_or_effect_path(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    state.fail_marketplace_read_on = 2
    state.fail_plugin_read_on = 2

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert report["completed_mutations"] == []


def test_configure_codex_reads_global_state_once_before_returning_manual_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)
    reads: list[Path] = []
    original = codex_setup._global_aar_entry

    def observe(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(codex_setup, "_global_aar_entry", observe)
    _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert reads == [state.config.resolve(strict=False)]


def test_configure_codex_manual_plan_reports_exact_target_without_claiming_success(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    state = _FakeCodexState(tmp_path / "config.toml", marketplace)

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert report["marketplace_path"] == os.fspath(marketplace.resolve(strict=True))
    assert report["plugin_version"] == codex_setup._plugin_version(marketplace)
    assert report["mcp_authority"] == "unconfigured"
    assert report["desired_mcp_authority"] == "plugin"
    assert report["provider_mutation_authority"] == "provider_or_operator_required"


def test_configure_codex_is_a_noop_when_plugin_and_mcp_are_current(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    plugin = {
        "pluginId": codex_setup.PLUGIN_SELECTOR,
        "version": codex_setup._plugin_version(marketplace),
        "enabled": True,
    }
    state = _FakeCodexState(
        tmp_path / "config.toml", marketplace, marketplace_root=marketplace, plugin=plugin
    )
    before = state.snapshot()

    report = configure_codex(
        Path(r"C:\tools\codex.cmd"),
        Path(r"C:\tools\aar-mcp.exe"),
        marketplace,
        invoke=state.invoke,
        codex_config=state.config,
    )

    assert report["status"] == "already_configured"
    assert report["configuration_required"] is False
    assert report["manual_plan"] == []
    assert report["restart_required_after_manual_apply"] is False
    assert report["mcp_authority"] == "plugin"
    assert state.snapshot() == before
    assert not any(_is_mutating_codex_call(call) for call in state.calls)


def test_configure_codex_plans_previous_plugin_cachebuster_upgrade(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    previous = {
        "pluginId": codex_setup.PLUGIN_SELECTOR,
        "version": "0.3.0+codex.20260812160000",
        "enabled": True,
    }
    state = _FakeCodexState(
        tmp_path / "config.toml", marketplace, marketplace_root=marketplace, plugin=previous
    )

    report = _manual_report(state, marketplace, Path("/tools/aar-mcp"), state.config)

    assert _plan_stages(report) == ["plugin-add"]
    assert report["plugin_change_required"] is True
    assert report["plugin_changed"] is False


def test_manual_plan_receipt_preserves_restart_handoff_without_persistent_state(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    prior = {
        "pluginId": codex_setup.PLUGIN_SELECTOR,
        "version": "0.3.0+codex.20260812160000",
        "enabled": True,
    }
    planned_state = _FakeCodexState(
        tmp_path / "planned.toml",
        marketplace,
        marketplace_root=marketplace,
        plugin=prior,
    )

    manual = _manual_report(
        planned_state,
        marketplace,
        Path("/tools/aar-mcp"),
        planned_state.config,
    )

    assert _plan_stages(manual) == ["plugin-add"]
    assert manual["restart_required_after_manual_apply"] is True

    current = {
        "pluginId": codex_setup.PLUGIN_SELECTOR,
        "version": codex_setup._plugin_version(marketplace),
        "enabled": True,
    }
    applied_state = _FakeCodexState(
        tmp_path / "applied.toml",
        marketplace,
        marketplace_root=marketplace,
        plugin=current,
    )
    observed = configure_codex(
        Path(r"C:\tools\codex.cmd"),
        Path(r"C:\tools\aar-mcp.exe"),
        marketplace,
        invoke=applied_state.invoke,
        codex_config=applied_state.config,
    )

    assert observed["status"] == "already_configured"
    assert observed["restart_required_after_manual_apply"] is False
    assert manual["restart_required_after_manual_apply"] is True


def test_configure_codex_rejects_conflicting_global_mcp_override(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    version = json.loads(
        (marketplace / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )["version"]

    def invoke(arguments: tuple[str, ...]) -> str:
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps({"marketplaces": [{"name": "aar-local", "root": str(marketplace)}]})
        if arguments == ("plugin", "list", "--json"):
            return json.dumps(
                {
                    "installed": [
                        {
                            "pluginId": "adaptive-agent-runtime@aar-local",
                            "version": version,
                            "enabled": True,
                        }
                    ]
                }
            )
        raise AssertionError(arguments)

    codex_config = tmp_path / "config.toml"
    codex_config.write_text(
        "[mcp_servers.aar]\ncommand = 'C:\\other\\aar-mcp.exe'\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="conflicting global MCP server"):
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            Path(r"C:\tools\aar-mcp.exe"),
            marketplace,
            invoke=invoke,
            codex_config=codex_config,
        )


def test_main_requests_restart_only_when_configuration_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    codex_mcp = Path(r"C:\tools\aar-codex-mcp.exe")
    monkeypatch.setattr(codex_setup, "resolve_aar_mcp", lambda command: aar_mcp)
    monkeypatch.setattr(codex_setup, "resolve_codex_mcp", lambda command: codex_mcp)
    monkeypatch.setattr(codex_setup, "resolve_codex", lambda command: Path("codex"))
    monkeypatch.setattr(codex_setup, "bundled_marketplace", lambda: Path("marketplace"))
    monkeypatch.setattr(
        codex_setup,
        "_plugin_mcp_spec",
        lambda _marketplace: ("aar-codex-mcp", []),
    )
    monkeypatch.setattr(
        codex_setup,
        "configure_codex",
        lambda *args, **kwargs: {"configuration_changed": False},
    )
    runtime_home = tmp_path / "production-runtime"
    observed: list[Path] = []

    def ensure(selected: Path) -> SimpleNamespace:
        observed.append(selected)
        return SimpleNamespace(
            discovery_digest="sha256:" + "1" * 64,
            dispatcher_generation=4,
            runtime_generation=3,
            supervisor_version=f"aar-supervisor/{PACKAGE_VERSION}",
        )

    monkeypatch.setattr(codex_setup, "default_runtime_home", lambda: runtime_home)
    monkeypatch.setattr(codex_setup, "ensure_codex_supervisor", ensure, raising=False)

    assert codex_setup.main(["--skip-preflight"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["restart_required"] is False
    assert observed == [runtime_home]
    assert report["codex_runtime"] == {
        "discovery_digest": "sha256:" + "1" * 64,
        "dispatcher_generation": 4,
        "runtime_generation": 3,
        "runtime_home": os.fspath(runtime_home),
        "status": "ready",
        "supervisor_version": f"aar-supervisor/{PACKAGE_VERSION}",
    }


def test_main_returns_manual_authority_required_without_starting_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    codex_mcp = Path(r"C:\tools\aar-codex-mcp.exe")
    configured = {
        "authority_disposition": "NO_ATOMIC_AUTHORITY",
        "configuration_changed": False,
        "manual_plan": [{"stage": "plugin-add"}],
        "restart_required_after_manual_apply": True,
        "status": "manual_authority_required",
    }
    monkeypatch.setattr(codex_setup, "resolve_aar_mcp", lambda command: aar_mcp)
    monkeypatch.setattr(codex_setup, "resolve_codex_mcp", lambda command: codex_mcp)
    monkeypatch.setattr(codex_setup, "resolve_codex", lambda command: Path("codex"))
    monkeypatch.setattr(codex_setup, "bundled_marketplace", lambda: Path("marketplace"))
    monkeypatch.setattr(
        codex_setup,
        "_plugin_mcp_spec",
        lambda _marketplace: ("aar-codex-mcp", []),
    )
    monkeypatch.setattr(codex_setup, "configure_codex", lambda *args, **kwargs: configured)
    monkeypatch.setattr(
        codex_setup,
        "ensure_codex_supervisor",
        lambda _runtime_home: pytest.fail("runtime must not start before manual configuration"),
    )

    assert codex_setup.main(["--skip-preflight"]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["status"] == "manual_authority_required"
    assert report["codex"] == configured
    assert report["codex_runtime"] is None
    assert report["restart_required"] is False
    assert report["restart_required_after_manual_apply"] is True


def test_main_stop_runtime_uses_exact_selected_runtime_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime_home = tmp_path / "codex-runtime"
    observed: list[Path] = []

    def stop(selected: Path) -> bool:
        observed.append(selected)
        return True

    monkeypatch.setattr(codex_setup, "stop_codex_supervisor", stop)

    assert (
        codex_setup.main(["--stop-runtime", "--codex-runtime-home", os.fspath(runtime_home)]) == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert observed == [runtime_home.resolve(strict=False)]
    assert report["action"] == "stop-runtime"
    assert report["stopped"] is True
    assert "restart_required" not in report


def test_main_distinguishes_committed_configuration_from_unready_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    codex_mcp = Path(r"C:\tools\aar-codex-mcp.exe")
    runtime_home = tmp_path / "production-runtime"
    configured = {
        "configuration_changed": True,
        "marketplace_path": r"C:\tools\marketplace",
        "plugin_version": "0.4.0a6",
        "mcp_authority": "plugin",
    }
    runtime_error = RuntimeError("injected production supervisor readiness failure")
    monkeypatch.setattr(codex_setup, "resolve_aar_mcp", lambda command: aar_mcp)
    monkeypatch.setattr(codex_setup, "resolve_codex_mcp", lambda command: codex_mcp)
    monkeypatch.setattr(codex_setup, "resolve_codex", lambda command: Path("codex"))
    monkeypatch.setattr(codex_setup, "bundled_marketplace", lambda: Path("marketplace"))
    monkeypatch.setattr(
        codex_setup,
        "_plugin_mcp_spec",
        lambda _marketplace: ("aar-codex-mcp", []),
    )
    monkeypatch.setattr(codex_setup, "configure_codex", lambda *args, **kwargs: configured)
    monkeypatch.setattr(codex_setup, "default_runtime_home", lambda: runtime_home)

    def fail_runtime(_runtime_home: Path):
        raise runtime_error

    monkeypatch.setattr(codex_setup, "ensure_codex_supervisor", fail_runtime)

    assert codex_setup.main(["--skip-preflight"]) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.err)
    assert report["status"] == "configuration_committed_runtime_unready"
    assert report["configuration"]["marketplace_path"] == configured["marketplace_path"]
    assert report["runtime_failure"]["error"] == str(runtime_error)
    assert report["status"] != "passed"


def _provider_revision_red_case(
    tmp_path: Path,
    *,
    stage: str,
    variant: str,
) -> None:
    """Expose the named baseline CLI mutation after a provider revision conflict."""

    marketplace = ROOT / "profiles" / "codex"
    version = codex_setup._plugin_version(marketplace)
    target_plugin = {
        "pluginId": codex_setup.PLUGIN_SELECTOR,
        "version": version,
        "enabled": True,
    }
    stale_marketplace = tmp_path / "stale-marketplace"
    stale_marketplace.mkdir()
    config = tmp_path / "config.toml"
    aar_mcp = Path("/tools/aar-mcp")
    prior_plugin: dict[str, object] | None = None
    marketplace_root: Path | None = marketplace
    global_command: str | None = None

    if stage == "marketplace-add":
        marketplace_root = None
        prior_plugin = target_plugin
    elif stage in {"marketplace-remove-prior", "marketplace-add-target"}:
        marketplace_root = stale_marketplace
        prior_plugin = target_plugin
    elif stage == "plugin-add":
        prior_plugin = None
    elif stage in {"global-mcp-remove", "restore-global-mcp"}:
        prior_plugin = target_plugin
        global_command = os.fspath(aar_mcp)
    elif stage == "remove-transaction-plugin":
        prior_plugin = None
        global_command = os.fspath(aar_mcp)
    elif stage == "remove-transaction-marketplace":
        marketplace_root = None
        prior_plugin = target_plugin
        global_command = os.fspath(aar_mcp)
    elif stage == "restore-prior-marketplace":
        marketplace_root = stale_marketplace
        prior_plugin = target_plugin
    elif stage in {"restore-prior-plugin", "restore-prior-plugin-disabled-state"}:
        marketplace_root = stale_marketplace
        prior_plugin = {
            "pluginId": codex_setup.PLUGIN_SELECTOR,
            "version": "0.4.0a4",
            "enabled": stage != "restore-prior-plugin-disabled-state",
        }
        global_command = os.fspath(aar_mcp)
    else:  # pragma: no cover - helper is called only by matrix rows
        raise AssertionError(stage)

    class RevisionedFake(_FakeCodexState):
        def __init__(self) -> None:
            super().__init__(
                config,
                marketplace,
                marketplace_root=marketplace_root,
                plugin=prior_plugin,
                global_command=global_command,
            )
            self.provider_revision = 1

        def invoke(self, arguments: tuple[str, ...]) -> str:
            arguments = tuple(arguments)
            if arguments[:2] == ("plugin", "add"):
                self.calls.append(arguments)
                self._run_hook(self._hooks, arguments)
                if self.plugin_add_error_before_mutation is not None:
                    raise self.plugin_add_error_before_mutation
                if (
                    self.marketplace_root == stale_marketplace.resolve(strict=False)
                    and prior_plugin
                ):
                    self.plugin = {**prior_plugin, "enabled": True}
                else:
                    self.plugin = {
                        "pluginId": codex_setup.PLUGIN_SELECTOR,
                        "version": self.plugin_add_version or version,
                        "enabled": self.plugin_add_enabled,
                    }
                self._run_hook(self._post_hooks, arguments)
                if self.plugin_add_error is not None:
                    raise self.plugin_add_error
                return json.dumps(self.plugin)
            if arguments[:2] == ("plugin", "disable"):
                self.calls.append(arguments)
                self._run_hook(self._hooks, arguments)
                assert self.plugin is not None
                self.plugin["enabled"] = False
                return "Disabled plugin"
            return super().invoke(arguments)

    fake = RevisionedFake()
    before = fake.snapshot()
    commands = {
        "marketplace-add": ("plugin", "marketplace", "add", os.fspath(marketplace), "--json"),
        "marketplace-remove-prior": (
            "plugin",
            "marketplace",
            "remove",
            codex_setup.MARKETPLACE_NAME,
        ),
        "marketplace-add-target": (
            "plugin",
            "marketplace",
            "add",
            os.fspath(marketplace),
            "--json",
        ),
        "plugin-add": ("plugin", "add", codex_setup.PLUGIN_SELECTOR, "--json"),
        "global-mcp-remove": ("mcp", "remove", "aar"),
        "restore-global-mcp": ("mcp", "add", "aar", "--", os.fspath(aar_mcp)),
        "remove-transaction-plugin": ("plugin", "remove", codex_setup.PLUGIN_SELECTOR),
        "remove-transaction-marketplace": (
            "plugin",
            "marketplace",
            "remove",
            codex_setup.MARKETPLACE_NAME,
        ),
        "restore-prior-marketplace": (
            "plugin",
            "marketplace",
            "add",
            os.path.normcase(os.fspath(stale_marketplace.resolve(strict=False))),
            "--json",
        ),
        "restore-prior-plugin": ("plugin", "add", codex_setup.PLUGIN_SELECTOR, "--json"),
        "restore-prior-plugin-disabled-state": ("plugin", "disable", codex_setup.PLUGIN_SELECTOR),
    }
    target_command = commands[stage]
    expected_revision = fake.provider_revision
    observed_revision = expected_revision + 2

    def conflict_at_final_seam(state: RevisionedFake) -> None:
        state.provider_revision = observed_revision
        if variant == "drift":
            if "marketplace" in stage:
                state.marketplace_root = tmp_path / "foreign-marketplace"
            elif "plugin" in stage:
                state.plugin = {
                    "pluginId": codex_setup.PLUGIN_SELECTOR,
                    "version": "foreign-version",
                    "enabled": True,
                }
            else:
                state._write_global("/foreign/aar-mcp")

    if stage == "restore-prior-plugin":

        def delayed_conflict(state: RevisionedFake) -> None:
            if state.marketplace_root == stale_marketplace.resolve(strict=False):
                conflict_at_final_seam(state)
            else:
                state.set_hook(target_command, delayed_conflict)

        fake.set_hook(target_command, delayed_conflict)
    else:
        fake.set_hook(target_command, conflict_at_final_seam)

    if stage == "restore-global-mcp":
        fake.fail_marketplace_read_on = 3
    elif stage == "remove-transaction-plugin":
        fake.mcp_remove_error = RuntimeError("injected post-plugin forward failure")
    elif stage == "remove-transaction-marketplace":
        fake.mcp_remove_error = RuntimeError("injected post-marketplace forward failure")
    elif stage == "restore-prior-marketplace":
        fake.plugin_add_error_before_mutation = RuntimeError(
            "injected post-target-marketplace forward failure"
        )
    elif stage in {"restore-prior-plugin", "restore-prior-plugin-disabled-state"}:
        fake.mcp_remove_error = RuntimeError("injected post-plugin forward failure")

    report = configure_codex(
        Path("/tools/codex"),
        aar_mcp,
        marketplace,
        invoke=fake.invoke,
        codex_config=config,
    )

    target_attempts = sum(call == target_command for call in fake.calls)
    assert report["authority_disposition"] == "NO_ATOMIC_AUTHORITY"
    assert report["status"] == "manual_authority_required"
    assert report["completed_mutations"] == []
    assert fake.snapshot() == before
    assert fake.provider_revision == expected_revision
    assert not any(_is_mutating_codex_call(call) for call in fake.calls)
    assert target_attempts == 0, (
        f"{stage} ignored provider revision conflict "
        f"expected={expected_revision} observed={observed_revision} variant={variant}"
    )


def test_marketplace_add_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="marketplace-add", variant="aba")


def test_marketplace_add_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="marketplace-add", variant="drift")


def test_marketplace_remove_prior_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="marketplace-remove-prior", variant="aba")


def test_marketplace_remove_prior_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="marketplace-remove-prior", variant="drift")


def test_marketplace_add_target_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="marketplace-add-target", variant="aba")


def test_marketplace_add_target_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="marketplace-add-target", variant="drift")


def test_plugin_add_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="plugin-add", variant="aba")


def test_plugin_add_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="plugin-add", variant="drift")


def test_global_mcp_remove_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="global-mcp-remove", variant="aba")


def test_global_mcp_remove_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="global-mcp-remove", variant="drift")


def test_restore_global_mcp_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="restore-global-mcp", variant="aba")


def test_restore_global_mcp_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="restore-global-mcp", variant="drift")


def test_remove_transaction_plugin_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="remove-transaction-plugin", variant="aba")


def test_remove_transaction_plugin_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="remove-transaction-plugin", variant="drift")


def test_remove_transaction_marketplace_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="remove-transaction-marketplace", variant="aba")


def test_remove_transaction_marketplace_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="remove-transaction-marketplace", variant="drift")


def test_restore_prior_marketplace_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="restore-prior-marketplace", variant="aba")


def test_restore_prior_marketplace_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="restore-prior-marketplace", variant="drift")


def test_restore_prior_plugin_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="restore-prior-plugin", variant="aba")


def test_restore_prior_plugin_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(tmp_path, stage="restore-prior-plugin", variant="drift")


def test_restore_plugin_disabled_state_rejects_equal_value_aba(tmp_path: Path) -> None:
    _provider_revision_red_case(
        tmp_path, stage="restore-prior-plugin-disabled-state", variant="aba"
    )


def test_restore_plugin_disabled_state_rejects_post_read_drift(tmp_path: Path) -> None:
    _provider_revision_red_case(
        tmp_path, stage="restore-prior-plugin-disabled-state", variant="drift"
    )
