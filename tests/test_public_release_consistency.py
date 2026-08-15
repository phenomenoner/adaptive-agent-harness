from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUCCESSOR = "0.4.0a6"
MATRIX_ROWS = 63
STATUS_PATH = ROOT / "profiles" / "release-status-v1.json"


def _surface(path: str) -> str:
    target = ROOT / path
    assert target.is_file(), f"required 0.4.0a6 public release surface is missing: {path}"
    return target.read_text(encoding="utf-8")


def _status() -> dict:
    assert STATUS_PATH.is_file(), "central release-status authority is missing"
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    assert status["schema_id"] == "aar.release-status.v1"
    assert status["candidate"] == {
        "version": SUCCESSOR,
        "state": "candidate_unreleased",
        "operation_skill_version": "0.9.6",
        "codex_profile_version": "0.4.0+codex.20260815100000",
    }
    assert status["verification"]["matrix"] == {
        "total": MATRIX_ROWS,
        "local_repair_status": "pending",
    }
    assert status["source"]["commit"] is None
    assert status["source"]["tree"] is None
    assert status["source"]["wheel"] is None
    assert status["source"]["binding_mode"] == "post-freeze-external-receipt"
    for key in (
        "windows_full_suite",
        "supported_python_ci",
        "exact_wheel",
        "fresh_restarted_host_luna_drill",
        "independent_review",
    ):
        assert status["verification"][key] == "PENDING"
    assert status["behavior"]["codex_subprocess_provider_authority"] == "NO_ATOMIC_AUTHORITY"
    assert status["behavior"]["database_runtime_process_lock"] is True
    assert status["behavior"]["automatic_install_or_rollback"] is False
    assert status["external_limits"]["official_plugin_directory"] == "not_submitted"
    assert status["external_limits"]["openai_review_approval"] == "not_completed"
    assert status["external_limits"]["provider_signed_attestation"] == "not_available"
    return status


def _assert_successor_is_current(text: str, *, surface: str) -> None:
    _status()
    assert SUCCESSOR in text, f"{surface} does not identify the 0.4.0a6 successor"
    assert "profiles/release-status-v1.json" in text, (
        f"{surface} does not point to the central release-status authority"
    )
    assert "0.4.0a5" not in text or any(
        marker in text.casefold()
        for marker in ("blocked", "unreleased", "superseded", "historical")
    ), f"{surface} still presents 0.4.0a5 without blocked historical status"


def _assert_external_limits(text: str, *, surface: str) -> None:
    lowered = text.casefold()
    assert "plugin directory" in lowered, f"{surface} omits the Plugin Directory boundary"
    assert any(word in lowered for word in ("not ", "no ", "without", "requires")), (
        f"{surface} does not state the external publication limit"
    )


def test_readme_matches_release_status() -> None:
    text = _surface("README.md")
    _assert_successor_is_current(text, surface="README.md")
    _assert_external_limits(text, surface="README.md")
    assert "NO_ATOMIC_AUTHORITY" in text
    assert "non-destructive" in text.casefold()


def test_release_notes_match_release_status() -> None:
    text = _surface("docs/RELEASE-v0.4.0a6.md")
    _assert_successor_is_current(text, surface="docs/RELEASE-v0.4.0a6.md")
    _assert_external_limits(text, surface="docs/RELEASE-v0.4.0a6.md")
    assert "NO_ATOMIC_AUTHORITY" in text
    assert str(MATRIX_ROWS) in text


def test_changelog_matches_release_status() -> None:
    text = _surface("CHANGELOG.md")
    _assert_successor_is_current(text, surface="CHANGELOG.md")
    section = text.split("## [0.4.0a5]", 1)
    assert len(section) == 2
    assert any(word in section[1][:1_000].casefold() for word in ("blocked", "unreleased"))
    assert "profiles/release-status-v1.json" in text


def test_technical_status_matches_release_status() -> None:
    text = _surface("TECHNICAL-STATUS.md")
    _assert_successor_is_current(text, surface="TECHNICAL-STATUS.md")
    _assert_external_limits(text, surface="TECHNICAL-STATUS.md")
    assert str(MATRIX_ROWS) in text
    assert any(tier in text for tier in ("T1", "T2", "T3"))


def test_host_compatibility_matches_release_status() -> None:
    text = _surface("HOST-COMPATIBILITY.md")
    _assert_successor_is_current(text, surface="HOST-COMPATIBILITY.md")
    _assert_external_limits(text, surface="HOST-COMPATIBILITY.md")
    assert "NO_ATOMIC_AUTHORITY" in text
    assert "CAS" in text


def test_codex_profile_matches_release_status() -> None:
    text = _surface("profiles/codex/plugins/adaptive-agent-runtime/README.md")
    _assert_successor_is_current(
        text,
        surface="profiles/codex/plugins/adaptive-agent-runtime/README.md",
    )
    _assert_external_limits(text, surface="profiles/codex/plugins/adaptive-agent-runtime/README.md")
    assert "NO_ATOMIC_AUTHORITY" in text
    assert "non-destructive" in text.casefold()
