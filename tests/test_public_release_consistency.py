from __future__ import annotations

import json
from pathlib import Path

import pytest

from aar.compat.assets import CODEX_PROFILE_VERSION, host_documents

ROOT = Path(__file__).resolve().parents[1]
SUCCESSOR = "0.4.0a6"
TAG = "v0.4.0a6"
MATRIX_ROWS = 63
CANDIDATE_CODEX_PROFILE_VERSION = "0.5.0-a0+codex.20260820194429"
EXPECTED_CODEX_PROFILE_VERSION = "0.4.0+codex.20260816122000"
STATUS_PATH = ROOT / "profiles" / "release-status-v1.json"
RELEASE_RECEIPT_ASSET = "adaptive-agent-runtime-v0.4.0a6-release-receipt.json"
HERMES_PROFILE_README = "profiles/hermes/adaptive-agent-runtime/README.md"

PUBLIC_STATUS_CONSUMERS = (
    "README.md",
    "CHANGELOG.md",
    "TECHNICAL-STATUS.md",
    "HOST-COMPATIBILITY.md",
    "docs/RELEASE-v0.4.0a6.md",
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
    "docs/RELEASE-v0.4.0a6.md",
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
    "ci.head_sha",
    "ci.run_id",
    "ci.conclusion",
    "local_install.receipt_sha256",
    "native_capability.receipt_sha256",
    "caller_delegated_rlm.receipt_sha256",
    "independent_review.primary_sha256",
    "independent_review.narrow_sha256",
    "independent_review.verdict",
    "release.tag",
    "release.tag_target",
    "release.asset_readback",
)


def _surface(path: str) -> str:
    target = ROOT / path
    assert target.is_file(), f"required 0.4.0a6 public release surface is missing: {path}"
    return target.read_text(encoding="utf-8")


def _status() -> dict:
    assert STATUS_PATH.is_file(), "central release-status snapshot is missing"
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


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


def test_release_status_is_timeless_snapshot_contract() -> None:
    status = _status()
    assert status["schema_id"] == "aar.release-status.v1"
    assert status["snapshot"] == {
        "scope": "immutable_release_snapshot",
        "version": SUCCESSOR,
        "tag": TAG,
        "operation_skill_version": "0.9.6",
        "codex_profile_version": EXPECTED_CODEX_PROFILE_VERSION,
    }
    serialized = json.dumps(status, sort_keys=True)
    assert "candidate_unreleased" not in serialized
    assert "PENDING" not in serialized
    assert '"candidate"' not in serialized


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


def test_release_status_separates_completed_and_post_freeze_evidence() -> None:
    verification = _status()["verification"]
    assert verification["completed_before_freeze"] == {
        "lifecycle_repair_matrix": {"required_rows": MATRIX_ROWS, "status": "completed"},
        "windows_behavioral_suite": {"status": "completed"},
    }
    assert verification["post_freeze"] == {
        "authority": RELEASE_RECEIPT_ASSET,
        "required_fields_source": "evidence_binding.receipt.required_fields",
    }


def test_release_status_consumer_inventory_is_explicit() -> None:
    assert _status()["public_consumers"] == list(PUBLIC_STATUS_CONSUMERS)


@pytest.mark.parametrize("path", CURRENT_PUBLIC_SURFACES)
def test_current_public_surfaces_use_timeless_release_snapshot_wording(path: str) -> None:
    text = _surface(path)
    lowered = text.casefold()
    assert SUCCESSOR in text
    assert "release snapshot" in lowered
    assert "profiles/release-status-v1.json" in text
    assert RELEASE_RECEIPT_ASSET in text
    for stale in (
        "candidate_unreleased",
        "candidate, unreleased",
        "candidate-v0.4.0a6",
        "current source candidate",
        "`0.4.0a6` is unreleased",
        "unreleased `0.4.0a6`",
        "unreleased `v0.4.0a6`",
        "when its tag is published",
        "install the candidate tag after publication",
        "pending receipts",
        "remain `pending`",
        "are all `pending`",
        "will resolve only after",
        "does not exist until",
    ):
        assert stale not in lowered, f"{path} contains time-sensitive release wording: {stale}"
    assert "plugin directory" in lowered
    assert any(
        phrase in lowered
        for phrase in (
            "does not establish",
            "does not constitute",
            "requires separate",
            "not official",
        )
    ), f"{path} omits the separate Plugin Directory authority boundary"


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
def test_translated_quick_starts_preserve_manual_restart_handoff(path: str) -> None:
    _assert_restart_handoff(_surface(path), surface=path)


def test_generated_codex_profile_matches_source() -> None:
    assert CODEX_PROFILE_VERSION == CANDIDATE_CODEX_PROFILE_VERSION
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
    assert "release snapshot" in text.casefold()
    assert "profiles/release-status-v1.json" in text
    assert RELEASE_RECEIPT_ASSET in text


def test_release_status_preserves_behavior_and_external_boundaries() -> None:
    status = _status()
    assert status["behavior"]["codex_subprocess_provider_authority"] == "NO_ATOMIC_AUTHORITY"
    assert status["behavior"]["database_runtime_process_lock"] is True
    assert status["behavior"]["automatic_install_or_rollback"] is False
    assert status["external_limits"] == {
        "official_plugin_directory": "not_established_by_this_snapshot",
        "openai_review_approval": "not_established_by_this_snapshot",
        "provider_signed_attestation": "not_established_by_this_snapshot",
    }
