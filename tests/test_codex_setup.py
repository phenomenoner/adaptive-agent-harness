from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from aar.compat import codex_setup
from aar.compat.codex_setup import configure_codex, run_setup_preflight

ROOT = Path(__file__).resolve().parents[1]


def test_setup_preflight_uses_real_mcp_worker_and_analysis_dependencies(tmp_path: Path) -> None:
    name = "aar-mcp.exe" if os.name == "nt" else "aar-mcp"
    command = Path(sys.executable).with_name(name)
    report = asyncio.run(
        run_setup_preflight(command, database=tmp_path / "setup-preflight.sqlite3")
    )
    assert report["server_name"] == "aar-mcp"
    assert report["tool_count"] == 30
    assert report["calculation"] == {"answer": 18, "rows": 3}
    assert set(report["dependencies"]) == {"ipython", "numpy", "pandas"}
    assert report["workspace_closed"] is True


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
    monkeypatch, capsys
) -> None:
    aar_mcp = Path(r"C:\tools\aar-mcp.exe")
    monkeypatch.setattr(codex_setup, "resolve_aar_mcp", lambda command: aar_mcp)
    monkeypatch.setattr(codex_setup, "resolve_codex", lambda command: Path("codex"))
    monkeypatch.setattr(codex_setup, "bundled_marketplace", lambda: Path("marketplace"))
    monkeypatch.setattr(
        codex_setup,
        "configure_codex",
        lambda *args, **kwargs: {"configuration_changed": False},
    )

    assert codex_setup.main(["--skip-preflight"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["restart_required"] is False
