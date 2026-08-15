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
        nonlocal global_mcp_configured
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return '{"marketplaces": []}'
        if arguments[:3] == ("plugin", "marketplace", "add"):
            return '{"name": "aar-local"}'
        if arguments == ("plugin", "list", "--json"):
            return '{"installed": []}'
        if arguments[:2] == ("plugin", "add"):
            return json.dumps(
                {
                    "pluginId": "adaptive-agent-runtime@aar-local",
                    "version": manifest["version"],
                }
            )
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

    def invoke(arguments: tuple[str, ...]) -> str:
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(marketplace)}]}
            )
        if arguments == ("plugin", "list", "--json"):
            return '{"installed": []}'
        if arguments[:2] == ("plugin", "add"):
            return json.dumps({"pluginId": "adaptive-agent-runtime@aar-local", "version": version})
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
    active_root = stale_marketplace

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal active_root
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(active_root)}]}
            )
        if arguments == ("plugin", "marketplace", "remove", "aar-local"):
            active_root = Path()
            return "Removed marketplace aar-local"
        if arguments[:3] == ("plugin", "marketplace", "add"):
            active_root = marketplace
            return '{"name": "aar-local"}'
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
        if arguments[:2] == ("plugin", "add"):
            return json.dumps(
                {
                    "pluginId": "adaptive-agent-runtime@aar-local",
                    "version": version,
                }
            )
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

    def invoke(arguments: tuple[str, ...]) -> str:
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(stale_marketplace)}]}
            )
        if arguments == ("plugin", "marketplace", "remove", "aar-local"):
            return "Removed marketplace aar-local"
        if arguments[:3] == ("plugin", "marketplace", "add"):
            if Path(arguments[3]) == marketplace.resolve(strict=True):
                raise RuntimeError("injected candidate marketplace failure")
            assert Path(arguments[3]) == stale_marketplace.resolve(strict=True)
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

    assert calls[-1][0:3] == ("plugin", "marketplace", "add")
    assert Path(calls[-1][3]) == stale_marketplace.resolve(strict=True)


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

    def invoke(arguments: tuple[str, ...]) -> str:
        nonlocal active_root
        calls.append(arguments)
        if arguments == ("plugin", "marketplace", "list", "--json"):
            return json.dumps(
                {"marketplaces": [{"name": "aar-local", "root": str(active_root)}]}
            )
        if arguments == ("plugin", "marketplace", "remove", "aar-local"):
            active_root = None
            return "Removed marketplace aar-local"
        if arguments[:3] == ("plugin", "marketplace", "add"):
            active_root = Path(arguments[3]).resolve(strict=False)
            return '{"name":"aar-local"}'
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
        if arguments[:2] == ("plugin", "add"):
            if active_root == marketplace.resolve(strict=True):
                raise RuntimeError("injected plugin install failure")
            assert active_root == stale_marketplace.resolve(strict=True)
            return json.dumps(
                {
                    "pluginId": "adaptive-agent-runtime@aar-local",
                    "version": version,
                }
            )
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
    assert calls[-1][:2] == ("plugin", "add")


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
                            "version": previous_version,
                            "enabled": True,
                        }
                    ]
                }
            )
        if arguments[:2] == ("plugin", "add"):
            return json.dumps(
                {
                    "pluginId": "adaptive-agent-runtime@aar-local",
                    "version": expected_version,
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
