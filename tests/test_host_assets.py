from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aar.compat.assets import (
    CODEX_ROOT,
    HERMES_ROOT,
    OPTIONAL_SKILL_FILES,
    SKILL_FILES,
    verify_profiles,
)

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_host_profiles_match_generator() -> None:
    assert verify_profiles(ROOT) == []


def test_bundles_copy_every_canonical_skill_byte() -> None:
    canonical = ROOT / "skills" / "aar-operations"
    for relative in SKILL_FILES:
        expected = (canonical / relative).read_bytes()
        assert (ROOT / CODEX_ROOT / "skills/aar-operations" / relative).read_bytes() == expected
        assert (ROOT / HERMES_ROOT / "skills/aar-operations" / relative).read_bytes() == expected
    optional = ROOT / "skills" / "aar-ipython-codegraph"
    for relative in OPTIONAL_SKILL_FILES:
        expected = (optional / relative).read_bytes()
        assert (
            ROOT / CODEX_ROOT / "skills/aar-ipython-codegraph" / relative
        ).read_bytes() == expected
        assert (
            ROOT / HERMES_ROOT / "skills/aar-ipython-codegraph" / relative
        ).read_bytes() == expected


def test_profile_contract_binds_runtime_configs_and_skill() -> None:
    contract = json.loads((ROOT / "profiles/host-profiles-v1.json").read_text(encoding="utf-8"))
    metadata = json.loads(
        (ROOT / "skills/aar-operations/metadata.json").read_text(encoding="utf-8")
    )
    assert contract["package"]["python_requires"] == ">=3.11,<3.15"
    assert contract["operation_skill"]["digest"] == metadata["skill_digest"]
    assert contract["operation_skill"]["version"] == "0.8.0"
    for host in ("codex", "hermes"):
        profile = contract["profiles"][host]
        content = (ROOT / profile["bundle"] / profile["config_file"]).read_bytes()
        assert profile["config_digest"] == f"sha256:{hashlib.sha256(content).hexdigest()}"


def test_codex_plugin_uses_single_development_cachebuster() -> None:
    manifest = json.loads(
        (
            ROOT
            / "profiles/codex/plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["version"].startswith("0.1.0+codex.")
    assert manifest["version"].count("+codex.") == 1
    interface_text = json.dumps(manifest["interface"]).lower()
    assert "tool use or analysis" in interface_text
    assert "software planning, development, testing, and troubleshooting" in interface_text


def test_hermes_runtime_config_and_review_map_are_equivalent() -> None:
    root = ROOT / HERMES_ROOT
    review_map = json.loads((root / "mcp.json").read_text(encoding="utf-8"))["aar"]
    config = (root / "config.yaml").read_text(encoding="utf-8")
    assert f"command: {review_map['command']}" in config
    assert f"connect_timeout: {review_map['connect_timeout']}" in config
    assert f"timeout: {review_map['timeout']}" in config
