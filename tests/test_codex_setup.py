from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aar.compat import codex_setup
from aar.compat.codex_setup import configure_codex, run_setup_preflight
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
            "[mcp_servers.aar]\n" f"command = {json.dumps(command)}\n", encoding="utf-8"
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


def _assert_structured_rollback_containment(error: BaseException) -> None:
    details = getattr(error, "details", None)
    assert isinstance(details, dict), (
        "transaction drift/unreadability must raise a structured containment error with "
        "a details mapping"
    )
    assert {
        "original_error",
        "completed_mutations",
        "rollback_status",
        "current_state",
    } <= set(details)
    assert details["rollback_status"] in {"contained", "rollback_failed"}


def _is_mutating_codex_call(call: tuple[str, ...]) -> bool:
    return (
        call[:3] == ("plugin", "marketplace", "add")
        or call == ("plugin", "marketplace", "remove", codex_setup.MARKETPLACE_NAME)
        or call[:2] in {
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
    report = asyncio.run(
        run_setup_preflight(command, runtime_home=tmp_path / "runtime")
    )
    assert report["declared_command"] == os.fspath(command.resolve(strict=True))
    assert report["declared_args"] == []
    assert report["server_name"] == "aar-mcp"
    assert report["tool_count"] == 30
    assert report["supervisor"]["mode"] == "attached-supervisor"
    assert report["supervisor"]["frontend_ephemeral"] is True
    assert report["supervisor"]["process_identity_digest"].startswith("sha256:")
    assert report["calculation"] == {"answer": 18, "rows": 3}
    assert set(report["dependencies"]) == {"ipython", "numpy", "pandas"}
    assert report["workspace_closed"] is True
    assert report["supervisor_stopped"] is True
    assert not (tmp_path / "runtime/supervisor/discovery.json").exists()


def test_resolve_aar_mcp_uses_active_environment_launcher(monkeypatch) -> None:
    name = "aar-mcp.exe" if os.name == "nt" else "aar-mcp"
    local = Path(sys.executable).with_name(name)
    monkeypatch.setattr(codex_setup.shutil, "which", lambda _name: None)

    assert codex_setup.resolve_aar_mcp() == local.resolve(strict=True)


def test_configure_codex_adds_plugin_and_removes_matching_global_mcp_override(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    global_mcp_configured = True
    marketplace_root: Path | None = None
    plugin: dict[str, object] | None = None
    codex_config = tmp_path / "config.toml"
    codex_config.write_text(
        "[mcp_servers.aar]\ncommand = 'C:\\tools\\aar-mcp.exe'\n",
        encoding="utf-8",
    )
    marketplace = ROOT / "profiles" / "codex"
    manifest = json.loads(
        (
            marketplace
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal global_mcp_configured, marketplace_root, plugin
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            marketplaces = (
                []
                if marketplace_root is None
                else [{"name": "aar-local", "root": str(marketplace_root)}]
            )
            return json.dumps({"marketplaces": marketplaces})
        if arguments[:3] == ("plugin", "marketplace", "add"):
            marketplace_root = Path(arguments[3]).resolve(strict=False)
            return '{"name": "aar-local"}'
        if arguments == ("plugin", "list", "--json"):
            return json.dumps({"installed": [] if plugin is None else [plugin]})
        if arguments[:2] == ("plugin", "add"):
            plugin = {
                "pluginId": "adaptive-agent-runtime@aar-local",
                "version": manifest["version"],
                "enabled": True,
            }
            return json.dumps(plugin)
        if arguments == ("mcp", "remove", "aar"):
            global_mcp_configured = False
            codex_config.write_text("", encoding="utf-8")
            return "Removed global MCP server 'aar'."
        raise AssertionError(arguments)

    report = configure_codex(
        Path(r"C:\tools\codex.cmd"),
        aar_mcp,
        marketplace,
        invoke=invoke,
        codex_config=codex_config,
    )
    assert report["marketplace_added"] is True
    assert report["plugin_version"] == manifest["version"]
    assert report["mcp_authority"] == "plugin"
    assert report["preflight_launcher"] == os.fspath(aar_mcp)
    assert report["legacy_global_mcp_removed"] is True
    assert report["configuration_changed"] is True
    assert ("mcp", "remove", "aar") in calls
    assert not any(arguments[:3] == ("mcp", "add", "aar") for arguments in calls)


def test_configure_codex_reuses_existing_aar_marketplace(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    version = json.loads(
        (
            marketplace
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )["version"]
    aar_mcp = Path("/tools/aar-mcp")
    calls: list[tuple[str, ...]] = []
    plugin: dict[str, object] | None = None

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal plugin
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(marketplace)}]}
            )
        if arguments == ("plugin", "list", "--json"):
            return json.dumps({"installed": [] if plugin is None else [plugin]})
        if arguments[:2] == ("plugin", "add"):
            plugin = {
                "pluginId": "adaptive-agent-runtime@aar-local",
                "version": version,
                "enabled": True,
            }
            return json.dumps(plugin)
        raise AssertionError(arguments)

    report = configure_codex(
        Path("/tools/codex"),
        aar_mcp,
        marketplace,
        invoke=invoke,
        codex_config=tmp_path / "config.toml",
    )
    assert report["marketplace_added"] is False
    assert report["marketplace_path"] == os.fspath(marketplace)
    assert not any(arguments[:3] == ("plugin", "marketplace", "add") for arguments in calls)


def test_configure_codex_replaces_same_name_marketplace_at_other_root(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    stale_marketplace = tmp_path / "stale-marketplace"
    stale_marketplace.mkdir()
    version = json.loads(
        (
            marketplace
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )["version"]
    calls: list[tuple[str, ...]] = []
    active_root: Path | None = stale_marketplace
    plugin: dict[str, object] | None = {
        "pluginId": "adaptive-agent-runtime@aar-local",
        "version": version,
        "enabled": True,
    }

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal active_root, plugin
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            marketplaces = (
                []
                if active_root is None
                else [{"name": "aar-local", "root": str(active_root)}]
            )
            return json.dumps({"marketplaces": marketplaces})
        if arguments == ("plugin", "marketplace", "remove", "aar-local"):
            active_root = None
            return "Removed marketplace aar-local"
        if arguments[:3] == ("plugin", "marketplace", "add"):
            active_root = Path(arguments[3]).resolve(strict=False)
            return '{"name": "aar-local"}'
        if arguments == ("plugin", "list", "--json"):
            return json.dumps({"installed": [] if plugin is None else [plugin]})
        if arguments[:2] == ("plugin", "add"):
            plugin = {
                "pluginId": "adaptive-agent-runtime@aar-local",
                "version": version,
                "enabled": True,
            }
            return json.dumps(plugin)
        raise AssertionError(arguments)

    report = configure_codex(
        Path("/tools/codex"),
        Path("/tools/aar-mcp"),
        marketplace,
        invoke=invoke,
        codex_config=tmp_path / "config.toml",
    )

    assert report["marketplace_replaced"] is True
    assert Path(report["marketplace_path"]) == marketplace.resolve(strict=True)
    assert ("plugin", "marketplace", "remove", "aar-local") in calls
    assert any(arguments[:3] == ("plugin", "marketplace", "add") for arguments in calls)
    assert any(arguments[:2] == ("plugin", "add") for arguments in calls)
    assert report["plugin_changed"] is True


def test_configure_codex_restores_prior_marketplace_when_replacement_fails(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    stale_marketplace = tmp_path / "stale-marketplace"
    stale_marketplace.mkdir()
    calls: list[tuple[str, ...]] = []
    active_root: Path | None = stale_marketplace

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal active_root
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            marketplaces = (
                []
                if active_root is None
                else [{"name": "aar-local", "root": str(active_root)}]
            )
            return json.dumps({"marketplaces": marketplaces})
        if arguments == ("plugin", "list", "--json"):
            return '{"installed": []}'
        if arguments == ("plugin", "marketplace", "remove", "aar-local"):
            active_root = None
            return "Removed marketplace aar-local"
        if arguments[:3] == ("plugin", "marketplace", "add"):
            if Path(arguments[3]) == marketplace.resolve(strict=True):
                raise RuntimeError("injected candidate marketplace failure")
            assert Path(arguments[3]) == stale_marketplace.resolve(strict=True)
            active_root = stale_marketplace.resolve(strict=True)
            return '{"name":"aar-local"}'
        raise AssertionError(arguments)

    with pytest.raises(RuntimeError, match="prior root was restored"):
        configure_codex(
            Path("/tools/codex"),
            Path("/tools/aar-mcp"),
            marketplace,
            invoke=invoke,
            codex_config=tmp_path / "config.toml",
        )

    restored = [call for call in calls if call[:3] == ("plugin", "marketplace", "add")]
    assert Path(restored[-1][3]) == stale_marketplace.resolve(strict=True)
    assert active_root == stale_marketplace.resolve(strict=True)


def test_configure_codex_restores_prior_binding_when_plugin_install_fails(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    stale_marketplace = tmp_path / "stale-marketplace"
    stale_marketplace.mkdir()
    version = json.loads(
        (
            marketplace
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )["version"]
    calls: list[tuple[str, ...]] = []
    active_root: Path | None = stale_marketplace
    plugin: dict[str, object] | None = {
        "pluginId": "adaptive-agent-runtime@aar-local",
        "version": version,
        "enabled": True,
    }

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal active_root, plugin
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            marketplaces = (
                []
                if active_root is None
                else [{"name": "aar-local", "root": str(active_root)}]
            )
            return json.dumps({"marketplaces": marketplaces})
        if arguments == ("plugin", "marketplace", "remove", "aar-local"):
            active_root = None
            return "Removed marketplace aar-local"
        if arguments[:3] == ("plugin", "marketplace", "add"):
            active_root = Path(arguments[3]).resolve(strict=False)
            return '{"name":"aar-local"}'
        if arguments == ("plugin", "list", "--json"):
            return json.dumps({"installed": [] if plugin is None else [plugin]})
        if arguments[:2] == ("plugin", "remove"):
            plugin = None
            return "Removed plugin"
        if arguments[:2] == ("plugin", "add"):
            if active_root == marketplace.resolve(strict=True):
                raise RuntimeError("injected plugin install failure")
            assert active_root == stale_marketplace.resolve(strict=True)
            plugin = {
                "pluginId": "adaptive-agent-runtime@aar-local",
                "version": version,
                "enabled": True,
            }
            return json.dumps(plugin)
        raise AssertionError(arguments)

    with pytest.raises(RuntimeError, match="prior marketplace binding was restored"):
        configure_codex(
            Path("/tools/codex"),
            Path("/tools/aar-mcp"),
            marketplace,
            invoke=invoke,
            codex_config=tmp_path / "config.toml",
        )

    assert active_root == stale_marketplace.resolve(strict=True)
    assert plugin is not None


def test_configure_codex_rolls_back_first_plugin_add_failure(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    state.plugin_add_error_before_mutation = RuntimeError(
        "injected first plugin add failure"
    )
    before = state.snapshot()

    with pytest.raises(RuntimeError, match="first plugin add failure"):
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    assert state.snapshot() == before


@pytest.mark.parametrize("post_add_failure", ["wrong-version", "disabled"])
def test_configure_codex_post_add_validation_failure_restores_or_contains_transaction(
    tmp_path: Path, post_add_failure: str
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    if post_add_failure == "wrong-version":
        state.plugin_add_version = "0.0.0-wrong"
    else:
        state.plugin_add_enabled = False
    before = state.snapshot()

    with pytest.raises(Exception) as raised:
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    if state.snapshot() != before:
        _assert_structured_rollback_containment(raised.value)


def test_configure_codex_unexpected_plugin_readback_is_not_claimed_for_undo(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(config, marketplace)
    state.plugin_add_version = "9.9.9-foreign-or-untrusted"

    with pytest.raises(Exception) as raised:
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    add_call = next(
        index
        for index, call in enumerate(state.calls)
        if call[:2] == ("plugin", "add")
    )
    assert not any(
        call[:2] == ("plugin", "remove") for call in state.calls[add_call + 1 :]
    )
    assert state.plugin is not None
    assert state.plugin["version"] == "9.9.9-foreign-or-untrusted"
    _assert_structured_rollback_containment(raised.value)


def test_configure_codex_mcp_remove_command_failure_restores_prior_mutations(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    state.mcp_remove_error = RuntimeError("injected global MCP remove failure")
    before = state.snapshot()

    with pytest.raises(RuntimeError, match="global MCP remove failure"):
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    assert state.snapshot() == before


def test_configure_codex_mcp_remove_readback_failure_restores_prior_mutations(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    state.mcp_remove_leaves_global = True
    before = state.snapshot()

    with pytest.raises(RuntimeError, match="global AAR MCP server remained"):
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    assert state.snapshot() == before


def test_configure_codex_final_sole_authority_readback_failure_is_not_success(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(config, marketplace)
    state.fail_marketplace_read_on = 2
    state.fail_plugin_read_on = 2
    before = state.snapshot()

    with pytest.raises(Exception) as raised:
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    assert state.read_failure_call is not None
    if state.snapshot() != before:
        _assert_structured_rollback_containment(raised.value)


def test_configure_codex_concurrent_drift_is_contained_without_destructive_undo(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    foreign_marketplace = tmp_path / "foreign-marketplace"
    foreign_marketplace.mkdir()
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    state.plugin_add_error = RuntimeError("injected plugin failure before rollback")
    drift_call: list[int] = []

    def inject_concurrent_drift(current: _FakeCodexState) -> None:
        current.marketplace_root = foreign_marketplace.resolve(strict=False)
        current.plugin = {
            "pluginId": codex_setup.PLUGIN_SELECTOR,
            "version": "9.9.9-foreign",
            "enabled": True,
        }
        current._write_global(r"C:\example\foreign-aar-mcp.exe")
        drift_call.append(len(current.calls))

    state.set_post_hook(
        ("plugin", "add", codex_setup.PLUGIN_SELECTOR, "--json"),
        inject_concurrent_drift,
    )

    with pytest.raises(Exception) as raised:
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    assert drift_call
    undo_calls = state.calls[drift_call[0] :]
    assert not any(_is_mutating_codex_call(call) for call in undo_calls)
    _assert_structured_rollback_containment(raised.value)


def test_configure_codex_failed_command_does_not_claim_predicted_state_for_undo(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(config, marketplace)
    state.marketplace_add_error = RuntimeError(
        "injected command failure after an indistinguishable target state appeared"
    )

    with pytest.raises(Exception) as raised:
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    add_call = next(
        index
        for index, call in enumerate(state.calls)
        if call[:3] == ("plugin", "marketplace", "add")
    )
    assert not any(
        call == ("plugin", "marketplace", "remove", codex_setup.MARKETPLACE_NAME)
        for call in state.calls[add_call + 1 :]
    )
    assert state.marketplace_root == marketplace.resolve(strict=True)
    _assert_structured_rollback_containment(raised.value)


def test_configure_codex_unreadable_current_state_stops_before_each_undo(
    tmp_path: Path,
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    state.plugin_add_error = RuntimeError("injected plugin failure before unreadable undo")
    state.fail_marketplace_read_on = 2
    state.fail_plugin_read_on = 2

    with pytest.raises(Exception) as raised:
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    assert state.read_failure_call is not None
    failed_read = state.read_failure_call
    assert not any(
        _is_mutating_codex_call(call) for call in state.calls[failed_read:]
    )
    _assert_structured_rollback_containment(raised.value)


def test_configure_codex_unreadable_global_state_stops_before_global_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    state.mcp_remove_leaves_global = True
    global_reads = 0
    unreadable_read_call: list[int] = []
    original_global_entry = codex_setup._global_aar_entry

    def fail_on_fresh_global_read(path: Path):
        nonlocal global_reads
        global_reads += 1
        if global_reads == 3:
            unreadable_read_call.append(len(state.calls))
            raise RuntimeError("injected unreadable current global MCP state")
        return original_global_entry(path)

    monkeypatch.setattr(codex_setup, "_global_aar_entry", fail_on_fresh_global_read)

    with pytest.raises(Exception) as raised:
        configure_codex(
            Path(r"C:\tools\codex.cmd"),
            aar_mcp,
            marketplace,
            invoke=state.invoke,
            codex_config=config,
        )

    assert global_reads >= 3
    assert unreadable_read_call
    assert not any(
        _is_mutating_codex_call(call)
        for call in state.calls[unreadable_read_call[0] :]
    )
    _assert_structured_rollback_containment(raised.value)


def test_configure_codex_success_performs_final_target_and_authority_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marketplace = ROOT / "profiles" / "codex"
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    config = tmp_path / "config.toml"
    state = _FakeCodexState(
        config,
        marketplace,
        global_command=os.fspath(aar_mcp),
    )
    global_reads: list[Path] = []
    original_global_entry = codex_setup._global_aar_entry

    def observe_global_read(path: Path):
        global_reads.append(path)
        return original_global_entry(path)

    monkeypatch.setattr(codex_setup, "_global_aar_entry", observe_global_read)
    report = configure_codex(
        Path(r"C:\tools\codex.cmd"),
        aar_mcp,
        marketplace,
        invoke=state.invoke,
        codex_config=config,
    )

    assert report["marketplace_path"] == os.fspath(marketplace.resolve(strict=True))
    assert report["plugin_version"] == codex_setup._plugin_version(marketplace)
    assert state.marketplace_reads >= 2
    assert state.plugin_reads >= 2
    assert len(global_reads) >= 2
    assert state.snapshot()["global_config"] == b""


def test_configure_codex_is_a_noop_when_plugin_and_mcp_are_current(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    version = json.loads(
        (
            marketplace
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )["version"]
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    calls: list[tuple[str, ...]] = []

    def invoke(arguments: tuple[str, ...]) -> str:
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(marketplace)}]}
            )
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
        if arguments == ("mcp", "get", "aar"):
            return "aar\n  command: aar-mcp\n"
        raise AssertionError(arguments)

    report = configure_codex(
        Path(r"C:\tools\codex.cmd"),
        aar_mcp,
        marketplace,
        invoke=invoke,
        codex_config=tmp_path / "config.toml",
    )

    assert report["configuration_changed"] is False
    assert report["plugin_changed"] is False
    assert report["legacy_global_mcp_removed"] is False
    assert report["mcp_authority"] == "plugin"
    assert not any(arguments[:2] == ("plugin", "add") for arguments in calls)
    assert not any(arguments[:3] == ("mcp", "add", "aar") for arguments in calls)
    assert ("mcp", "get", "aar") not in calls


def test_configure_codex_upgrades_previous_plugin_cachebuster(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    expected_version = json.loads(
        (
            marketplace
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )["version"]
    previous_version = "0.3.0+codex.20260812160000"
    assert expected_version != previous_version
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    calls: list[tuple[str, ...]] = []
    installed_version = previous_version

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal installed_version
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(marketplace)}]}
            )
        if arguments == ("plugin", "list", "--json"):
            return json.dumps(
                {
                    "installed": [
                        {
                            "pluginId": "adaptive-agent-runtime@aar-local",
                            "version": installed_version,
                            "enabled": True,
                        }
                    ]
                }
            )
        if arguments[:2] == ("plugin", "add"):
            installed_version = expected_version
            return json.dumps(
                {
                    "pluginId": "adaptive-agent-runtime@aar-local",
                    "version": expected_version,
                    "enabled": True,
                }
            )
        raise AssertionError(arguments)

    report = configure_codex(
        Path(r"C:\tools\codex.cmd"),
        aar_mcp,
        marketplace,
        invoke=invoke,
        codex_config=tmp_path / "config.toml",
    )

    assert report["configuration_changed"] is True
    assert report["plugin_changed"] is True
    assert report["plugin_version"] == expected_version
    assert any(arguments[:2] == ("plugin", "add") for arguments in calls)


def test_configure_codex_rejects_conflicting_global_mcp_override(tmp_path: Path) -> None:
    marketplace = ROOT / "profiles" / "codex"
    version = json.loads(
        (
            marketplace
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )["version"]

    def invoke(arguments: tuple[str, ...]) -> str:
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(marketplace)}]}
            )
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
    monkeypatch.setattr(
        codex_setup, "ensure_codex_supervisor", ensure, raising=False
    )

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
        codex_setup.main(
            ["--stop-runtime", "--codex-runtime-home", os.fspath(runtime_home)]
        )
        == 0
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
        "plugin_version": "0.4.0a5",
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
