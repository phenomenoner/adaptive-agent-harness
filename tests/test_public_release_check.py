from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "public_release_check", ROOT / "tools/public_release_check.py"
)
assert SPEC is not None and SPEC.loader is not None
PUBLIC_RELEASE_CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLIC_RELEASE_CHECK)


@pytest.mark.parametrize(
    "relative",
    [
        ".data/cache/result.json",
        "docs/secrets/token.txt",
        "config/credentials/provider.json",
        "artifacts/rollback/generation/manifest.json",
    ],
)
def test_public_release_guard_rejects_nested_sensitive_paths(relative: str) -> None:
    assert PUBLIC_RELEASE_CHECK.PROHIBITED_PATH.search(relative)


def test_public_release_guard_matches_generic_machine_paths_without_private_constants() -> None:
    pattern = PUBLIC_RELEASE_CHECK.BYTE_PATTERNS["machine-local-path"]
    private_examples = [
        b"/home/" + b"alice" + b"/project",
        b"/mnt/" + b"z/projects/demo",
        b"Q:\\" + b"Projects\\demo",
        b"C:\\Users\\" + b"alice\\repo",
    ]
    public_placeholders = [
        b"/home/user/project",
        b"/mnt/c/<project>",
        b"D:\\path\\to\\wheel.whl",
        b"C:\\Users\\user\\project",
    ]
    assert all(pattern.search(value) for value in private_examples)
    assert not any(pattern.search(value) for value in public_placeholders)


def test_public_release_guard_matches_contextual_platform_identifiers() -> None:
    pattern = PUBLIC_RELEASE_CHECK.BYTE_PATTERNS["private-message-id"]
    sample = b"channel_id=" + b"123456789" + b"012345678"
    assert pattern.search(sample)
    assert not pattern.search(b"max_value=9223372036854775807")


def test_public_release_guard_matches_opaque_task_ids_and_local_receipts() -> None:
    task_pattern = PUBLIC_RELEASE_CHECK.BYTE_PATTERNS["opaque-task-id"]
    task_id = b"01234567" + b"-89ab-cdef-0123-456789abcdef"
    assert task_pattern.search(b"desktop task `" + task_id + b"`")

    receipt_pattern = PUBLIC_RELEASE_CHECK.BYTE_PATTERNS["local-receipt-path"]
    local_receipt = b".aar/" + b"codex-run-summary.json"
    assert receipt_pattern.search(b"retained `" + local_receipt + b"`")


def test_public_release_guard_accepts_current_release_identity() -> None:
    assert (
        PUBLIC_RELEASE_CHECK.release_identity_failures(
            ROOT, PUBLIC_RELEASE_CHECK.EXPECTED_TRANSLATIONS
        )
        == []
    )


def test_public_release_guard_rejects_localized_release_identity_drift(
    tmp_path: Path,
) -> None:
    version = "1.2.3a1"
    tag = f"v{version}"
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        f"releases/tag/{tag}\n@{tag}\n**`{version}`**\n", encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        f"## [{version}]\nreleases/tag/{tag}\n", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / f"RELEASE-{tag}.md").write_text(
        f"# Adaptive Agent Harness {tag}\n@{tag}\n", encoding="utf-8"
    )
    translation_dir = tmp_path / "docs" / "i18n"
    translation_dir.mkdir()
    locale = translation_dir / "README.de.md"
    locale.write_text(
        f"releases/tag/{tag}\n@{tag}\n**`{version}`**\n", encoding="utf-8"
    )
    assert PUBLIC_RELEASE_CHECK.release_identity_failures(
        tmp_path, {locale.name}
    ) == []

    locale.write_text(locale.read_text(encoding="utf-8").replace(tag, "v1.2.3a0"))
    failures = PUBLIC_RELEASE_CHECK.release_identity_failures(
        tmp_path, {locale.name}
    )
    assert any(
        failure["kind"] == "release-identity-marker"
        and failure["detail"].startswith("docs/i18n/README.de.md")
        for failure in failures
    )
