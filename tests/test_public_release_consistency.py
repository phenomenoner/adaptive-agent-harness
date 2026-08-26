from __future__ import annotations

import json
from pathlib import Path

import pytest

from aar.compat.assets import (
    CODEX_PROFILE_VERSION,
    RELEASE_RECEIPT_ASSET,
    host_documents,
)
from aar.mcp.server import MCP_TOOL_SURFACE_VERSION, OPERATION_SKILL_VERSION
from aar.versions import PACKAGE_VERSION

ROOT = Path(__file__).resolve().parents[1]
SUCCESSOR = PACKAGE_VERSION
TAG = f"v{SUCCESSOR}"
MATRIX_ROWS = 20
TOOL_MANIFEST_PATH = ROOT / "schemas" / "aar-mcp-tools-v8-combined.json"
STATUS_PATH = ROOT / "profiles" / "release-status-v1.json"
HERMES_PROFILE_README = "profiles/hermes/adaptive-agent-runtime/README.md"

PUBLIC_STATUS_CONSUMERS = (
    "README.md",
    "CHANGELOG.md",
    "TECHNICAL-STATUS.md",
    "HOST-COMPATIBILITY.md",
    f"docs/RELEASE-v{SUCCESSOR}.md",
    "profiles/codex/plugins/adaptive-agent-runtime/README.md",
    HERMES_PROFILE_README,
    "docs/CODEX-INSTALL.md",
    "docs/PUBLIC-PLUGIN.md",
    "src/aar/compat/assets.py",
    "tests/test_public_release_consistency.py",
)

CURRENT_PUBLIC_SURFACES = (
    "README.md",
    "CHANGELOG.md",
    "TECHNICAL-STATUS.md",
    "HOST-COMPATIBILITY.md",
    f"docs/RELEASE-v{SUCCESSOR}.md",
    "profiles/codex/plugins/adaptive-agent-runtime/README.md",
    HERMES_PROFILE_README,
    "docs/CODEX-INSTALL.md",
    "docs/PUBLIC-PLUGIN.md",
)

CURRENT_INSTALL_GUIDES = (
    "README.md",
    "HOST-COMPATIBILITY.md",
    "docs/CODEX-INSTALL.md",
    "profiles/codex/plugins/adaptive-agent-runtime/README.md",
)

OPERATION_SKILL_COPIES = (
    "skills/aar-operations/SKILL.md",
    "profiles/codex/plugins/adaptive-agent-runtime/skills/aar-operations/SKILL.md",
    "profiles/hermes/adaptive-agent-runtime/skills/aar-operations/SKILL.md",
)

TRANSLATED_QUICK_STARTS = (
    "docs/i18n/README.ar.md",
    "docs/i18n/README.cs.md",
    "docs/i18n/README.de.md",
    "docs/i18n/README.es.md",
    "docs/i18n/README.fi.md",
    "docs/i18n/README.fr.md",
    "docs/i18n/README.it.md",
    "docs/i18n/README.ja.md",
    "docs/i18n/README.ko.md",
    "docs/i18n/README.lt.md",
    "docs/i18n/README.no.md",
    "docs/i18n/README.pt-BR.md",
    "docs/i18n/README.ru.md",
    "docs/i18n/README.th.md",
    "docs/i18n/README.vi.md",
    "docs/i18n/README.zh-CN.md",
    "docs/i18n/README.zh-TW.md",
)

REQUIRED_EXTERNAL_RECEIPT_FIELDS = (
    "source.commit",
    "source.tree",
    "artifacts.wheel.name",
    "artifacts.wheel.sha256",
    "artifacts.wheel.length",
    "artifacts.wheel_sidecar.name",
    "artifacts.wheel_sidecar.sha256",
    "artifacts.sdist.name",
    "artifacts.sdist.sha256",
    "artifacts.install_candidate_receipt.name",
    "artifacts.install_candidate_receipt.sha256",
    "ci.head_sha",
    "ci.run_id",
    "ci.conclusion",
    "qualification.clean_install_receipt_sha256",
    "qualification.activation_intent_sha256",
    "qualification.native_capability_receipt_sha256",
    "qualification.caller_provider_receipt_sha256",
    "qualification.private_grant_lifecycle_receipt_sha256",
    "independent_review.formal_sha256",
    "independent_review.claude_cli_sha256",
    "independent_review.verdict",
    "release.tag",
    "release.tag_target",
    "release.asset_readback",
)


def _surface(path: str) -> str:
    target = ROOT / path
    assert target.is_file(), f"required {SUCCESSOR} public release surface is missing: {path}"
    return target.read_text(encoding="utf-8")


def _status() -> dict:
    assert STATUS_PATH.is_file(), "central release-status snapshot is missing"
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def _tool_manifest() -> dict:
    assert TOOL_MANIFEST_PATH.is_file(), "combined MCP v8 tool manifest is missing"
    return json.loads(TOOL_MANIFEST_PATH.read_text(encoding="utf-8"))


def _assert_restart_handoff(text: str, *, surface: str) -> None:
    assert "restart_required: true" in text, f"{surface} omits the direct setup restart path"
    assert "restart_required_after_manual_apply: true" in text, (
        f"{surface} omits the preserved manual-apply restart path"
    )
    assert "restart_required: false" in text, (
        f"{surface} does not explain that a later no-op receipt cannot erase the handoff"
    )


def _markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        pytest.fail(f"missing Markdown section: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    body: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                break
        body.append(line)
    return "\n".join(body)


def test_release_status_is_timeless_release_contract() -> None:
    status = _status()
    tools = _tool_manifest()["tools"]
    assert status["schema_id"] == "aar.release-status.v1"
    assert status["snapshot"] == {
        "scope": "release_contract_snapshot",
        "publication_status": "not_established_by_source",
        "publication_authority": "external_readback_only",
        "version": SUCCESSOR,
        "tag": TAG,
        "operation_skill_version": OPERATION_SKILL_VERSION,
        "codex_profile_version": CODEX_PROFILE_VERSION,
        "mcp_surface": MCP_TOOL_SURFACE_VERSION,
        "mcp_tool_count": len(tools),
    }
    assert MCP_TOOL_SURFACE_VERSION == "aar.mcp-tools.v8"
    assert len(tools) == 38
    serialized = json.dumps(status, sort_keys=True)
    assert "immutable_release_snapshot" not in serialized
    assert "published_from_reviewed_source" not in serialized


def test_release_status_uses_non_self_referential_external_receipt() -> None:
    binding = _status()["evidence_binding"]
    assert binding["mode"] == "post_freeze_external_receipt"
    assert binding["self_hashes_embedded"] is False
    assert "self-refer" in binding["reason"].casefold()
    assert binding["receipt"] == {
        "schema_id": "aar.release-receipt.v1",
        "asset_name": RELEASE_RECEIPT_ASSET,
        "required_fields": list(REQUIRED_EXTERNAL_RECEIPT_FIELDS),
    }


def test_release_status_separates_frozen_and_post_freeze_evidence() -> None:
    verification = _status()["verification"]
    assert verification["frozen_before_release"] == {
        "provider_ready_host_acceptance": {
            "required_rows": MATRIX_ROWS,
            "status": "frozen",
        },
        "public_mcp_v8_surface": {
            "required_tools": len(_tool_manifest()["tools"]),
            "status": "frozen",
        },
    }
    assert verification["post_freeze"] == {
        "authority": RELEASE_RECEIPT_ASSET,
        "required_fields_source": "evidence_binding.receipt.required_fields",
    }


def test_release_status_consumer_inventory_is_explicit() -> None:
    assert _status()["public_consumers"] == list(PUBLIC_STATUS_CONSUMERS)


def _assert_no_stale_current_authority(text: str, *, surface: str) -> None:
    lowered = text.casefold()
    for stale in (
        "this page is the host-facing part of the **`v0.4.0a6` release snapshot**",
        "this page is the codex installation entrypoint for the **`0.4.0a6` release snapshot**",
        "repository status:** the `0.4.0a6` release snapshot",
        "adaptive_agent_runtime-0.4.0a6-py3-none-any.whl",
        ".git@v0.4.0a6",
        "immutable release snapshot",
        "latest published release snapshot",
        "current public alpha is",
        "published from the reviewed source tree",
        "this github prerelease publishes",
    ):
        assert stale not in lowered, f"{surface} contains stale current authority: {stale}"


@pytest.mark.parametrize("path", CURRENT_PUBLIC_SURFACES)
def test_current_public_surfaces_use_timeless_release_contract_wording(path: str) -> None:
    text = _surface(path)
    lowered = text.casefold()
    assert SUCCESSOR in text
    assert "release contract" in lowered
    assert "profiles/release-status-v1.json" in text
    assert RELEASE_RECEIPT_ASSET in text
    _assert_no_stale_current_authority(text, surface=path)
    assert "plugin directory" in lowered
    assert any(
        phrase in lowered
        for phrase in (
            "does not establish that the target tag",
            "does not establish publication",
            "does not establish that publication",
            "does not establish",
            "does not constitute",
            "requires separate",
            "not official",
        )
    ), f"{path} omits the separate Plugin Directory authority boundary"


def test_readme_hero_does_not_claim_target_release_already_exists() -> None:
    hero = "\n".join(_surface("README.md").splitlines()[:20])
    assert "release-contract target" in hero
    assert "source text does not establish publication" in hero
    assert "releases/tag/v0.6.0a1" not in hero


@pytest.mark.parametrize(
    "fixture",
    (
        "This page is the host-facing part of the **`v0.4.0a6` release snapshot**.",
        "This page is the Codex installation entrypoint for the **`0.4.0a6` release snapshot**.",
        "**Repository status:** the `0.4.0a6` release snapshot is current.",
    ),
)
def test_stale_current_version_negative_fixtures_are_rejected(fixture: str) -> None:
    with pytest.raises(AssertionError, match="stale current authority"):
        _assert_no_stale_current_authority(fixture, surface="synthetic fixture")


@pytest.mark.parametrize("path", CURRENT_INSTALL_GUIDES)
def test_current_install_guidance_preserves_both_restart_receipt_paths(path: str) -> None:
    _assert_restart_handoff(_surface(path), surface=path)


def test_readme_codex_quick_start_preserves_manual_restart_handoff_locally() -> None:
    section = _markdown_section(_surface("README.md"), "### Codex App setup")
    _assert_restart_handoff(section, surface="README.md Codex App setup")


@pytest.mark.parametrize("path", OPERATION_SKILL_COPIES)
def test_bundled_operation_skills_preserve_manual_restart_handoff(path: str) -> None:
    _assert_restart_handoff(_surface(path), surface=path)


@pytest.mark.parametrize("path", TRANSLATED_QUICK_STARTS)
def test_translated_quick_starts_preserve_current_release_boundary(path: str) -> None:
    text = _surface(path)
    _assert_restart_handoff(text, surface=path)
    assert SUCCESSOR in text
    assert "0.6.0a0" not in text
    assert "release-contract target" in text[:1000]
    assert "source text does not establish publication" in text[:1000]
    assert "releases/tag/v0.6.0a1" not in text
    assert "Use only after external GitHub readback confirms the target tag exists." in text
    assert "release-contract evidence represented by this source" in text
    current_tool_lines = [line for line in text.splitlines()[:200] if "38" in line and "v8" in line]
    assert len(current_tool_lines) == 1, f"{path} omits the current 38-tool v8 surface"


def test_generated_codex_profile_matches_source() -> None:
    assert _status()["snapshot"]["codex_profile_version"] == CODEX_PROFILE_VERSION
    generated = host_documents(ROOT)
    for relative in (
        Path("profiles/codex/plugins/adaptive-agent-runtime/README.md"),
        Path("profiles/codex/plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"),
    ):
        assert (ROOT / relative).read_bytes() == generated[relative]


def test_generated_hermes_release_readme_matches_source() -> None:
    relative = Path(HERMES_PROFILE_README)
    generated = host_documents(ROOT)
    assert (ROOT / relative).read_bytes() == generated[relative]
    text = generated[relative].decode()
    assert "Release package" in text
    assert "release contract" in text.casefold()
    assert "profiles/release-status-v1.json" in text
    assert RELEASE_RECEIPT_ASSET in text


def test_release_status_preserves_behavior_and_external_boundaries() -> None:
    status = _status()
    behavior = status["behavior"]
    assert behavior["public_mcp_surface"] == MCP_TOOL_SURFACE_VERSION
    assert behavior["public_mcp_tool_count"] == len(_tool_manifest()["tools"])
    assert behavior["frozen_v7_compatibility_tool_count"] == 30
    assert behavior["codex_subprocess_provider_authority"] == "NO_ATOMIC_AUTHORITY"
    assert behavior["database_runtime_process_lock"] is True
    assert behavior["automatic_install_or_rollback"] is False
    assert behavior["provider_credentials_owner"] == "host"
    assert behavior["provider_physical_call_owner"] == "host"
    assert behavior["provider_ready_session_grants"] == "explicit_memory_only_generation_bound"
    assert behavior["hermes_host_adapter"] == "optional_standalone"
    assert behavior["runtime_root_policy"] == "clean_install_only"
    assert status["external_limits"] == {
        "pypi_publication": "not_established_by_this_snapshot",
        "official_plugin_directory": "not_established_by_this_snapshot",
        "openai_review_approval": "not_established_by_this_snapshot",
        "provider_signed_attestation": "not_established_by_this_snapshot",
        "general_production_deployment": "not_established_by_this_snapshot",
    }
