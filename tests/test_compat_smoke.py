from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp.client.stdio import StdioServerParameters

from aar.compat.smoke import _resolve_scenario_id, _run

ROOT = Path(__file__).resolve().parents[1]


def test_reusable_smoke_pack_exercises_only_real_stdio_tools(tmp_path: Path) -> None:
    report = asyncio.run(
        _run(
            StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "aar.mcp.server",
                    "--database",
                    str(tmp_path / "compat-smoke.sqlite3"),
                ],
                cwd=ROOT,
            ),
            "pytest",
        )
    )
    assert report["status"] == "passed"
    assert report["schema_version"] == "aar.compat-smoke.v3"
    assert report["observed"]["server_name"] == "aar-mcp"
    assert set(report["checks"].values()) == {"passed"}
    assert report["checks"]["analysis_dependencies"] == "passed"
    assert report["observed"]["operation_skill_version"] == "0.9.4"
    assert report["observed"]["client_protocol_version"] == "2026-07-28"
    assert report["observed"]["server_protocol_version"] == "2026-07-28"


def test_explicit_compat_scenario_id_is_preserved() -> None:
    assert _resolve_scenario_id("replay-known-case") == "replay-known-case"


def test_default_compat_scenario_ids_are_unique() -> None:
    first = _resolve_scenario_id(None)
    second = _resolve_scenario_id(None)

    assert first.startswith("black-box-")
    assert second.startswith("black-box-")
    assert first != second
