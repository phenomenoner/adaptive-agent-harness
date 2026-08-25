from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from aar.canonical import canonical_sha256
from aar.provider_ready_install_models import FactoryEntry, InstallCandidateReceipt
from aar.provider_ready_models import ProviderReadyCandidate
from aar.runtime._install_artifacts import (
    FROZEN_MIGRATION_V6_ASSET,
    FROZEN_MIGRATION_V6_SHA256,
    frozen_migration_v6_bytes,
    inspect_wheel_bytes,
)
from aar.runtime._install_fs import InstallerError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "provider-ready"
SCHEMA_MEMBER = "aar/bundled/schemas/aar-provider-ready-schemas-v1.json"
MANIFEST_MEMBER = "aar/bundled/fixtures/provider-ready/manifest.json"
SKILL_MEMBER = "aar/bundled/aar-operations/SKILL.md"
FIXTURE_PREFIX = "aar/bundled/fixtures/provider-ready/"
FACTORY_METHODS = (
    "model-request",
    "subagent-submit",
    "subagent-result",
    "evidence-query",
    "artifact-put",
    "effect-propose",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prefixed_sha256(data: bytes) -> str:
    return "sha256:" + _sha256(data)


def _wheel_files() -> dict[str, bytes]:
    manifest = (FIXTURE_ROOT / "manifest.json").read_bytes()
    manifest_value = json.loads(manifest)
    files = {
        SCHEMA_MEMBER: (ROOT / "schemas" / "aar-provider-ready-schemas-v1.json").read_bytes(),
        MANIFEST_MEMBER: manifest,
        SKILL_MEMBER: (ROOT / "skills" / "aar-operations" / "SKILL.md").read_bytes(),
    }
    for fixture in manifest_value["fixtures"]:
        files[FIXTURE_PREFIX + fixture["path"]] = (FIXTURE_ROOT / fixture["path"]).read_bytes()
    for method in FACTORY_METHODS:
        files[f"aar/factories/{method}.py"] = f"factory:{method}\n".encode()
    return files


def _wheel_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return output.getvalue()


def _receipt(wheel: bytes, files: dict[str, bytes]) -> InstallCandidateReceipt:
    manifest = json.loads(files[MANIFEST_MEMBER])
    schema_bundle = json.loads(files[SCHEMA_MEMBER])
    contract_digest = canonical_sha256(
        {
            "schema_bundle_digest": schema_bundle["bundle_digest"],
            "fixture_set_digest": manifest["fixture_set_digest"],
        }
    )
    candidate = ProviderReadyCandidate(
        package_version="0.6.0a0",
        source_commit="0" * 40,
        wheel_digest=_prefixed_sha256(wheel),
        contract_manifest_digest=contract_digest,
        skill_digest=_prefixed_sha256(files[SKILL_MEMBER]),
    )
    entries = tuple(
        FactoryEntry(
            factory_id=f"aar-factory-{method}",
            wheel_member=f"aar/factories/{method}.py",
            implementation_digest=_prefixed_sha256(files[f"aar/factories/{method}.py"]),
        )
        for method in FACTORY_METHODS
    )
    return InstallCandidateReceipt.issue(
        candidate=candidate,
        wheel_size_bytes=len(wheel),
        wheel_digest=candidate.wheel_digest,
        contract_manifest_digest=candidate.contract_manifest_digest,
        skill_digest=candidate.skill_digest,
        factory_entries=entries,
    )


def _append_member(
    wheel: bytes,
    name: str,
    content: bytes = b"adversarial archive entry\n",
    *,
    symlink: bool = False,
) -> bytes:
    output = io.BytesIO(wheel)
    with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_STORED) as archive:
        if symlink:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, content)
        else:
            archive.writestr(name, content)
    return output.getvalue()


def _replace_member(wheel: bytes, name: str, content: bytes) -> bytes:
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(wheel), "r") as source,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as destination,
    ):
        for info in source.infolist():
            destination.writestr(info, content if info.filename == name else source.read(info))
    return output.getvalue()


def test_positive_wheel_uses_exact_declared_fixture_bytes_and_documents() -> None:
    files = _wheel_files()
    wheel = _wheel_bytes(files)
    receipt = _receipt(wheel, files)

    inspection = inspect_wheel_bytes(wheel, receipt)
    manifest = json.loads(files[MANIFEST_MEMBER])
    for fixture in manifest["fixtures"]:
        member = FIXTURE_PREFIX + fixture["path"]
        expected = (FIXTURE_ROOT / fixture["path"]).read_bytes()
        assert inspection.members[member] == expected
        assert _sha256(expected) == fixture["raw_bytes_sha256"]
        assert inspection.member_digests[member] == _prefixed_sha256(expected)
        if fixture["document_digest"] is not None:
            assert canonical_sha256(json.loads(expected)) == fixture["document_digest"]


def test_fixture_member_tamper_is_rejected_without_manifest_or_receipt_change() -> None:
    files = _wheel_files()
    wheel = _wheel_bytes(files)
    receipt = _receipt(wheel, files)
    target = FIXTURE_PREFIX + "valid/activation-intent.json"
    original = files[target]
    tampered = original.replace(b"hermes-caller-luna-max-v1", b"profile-tampered", 1)
    tampered_wheel = _replace_member(wheel, target, tampered)

    with pytest.raises(InstallerError) as raised:
        inspect_wheel_bytes(tampered_wheel, receipt)

    assert raised.value.code == "FRESH_INSTALL_WHEEL_INVALID"
    assert "fixture member bytes mismatch" in str(raised.value)


def test_declared_document_digest_is_checked_after_raw_bytes_match() -> None:
    files = _wheel_files()
    target = FIXTURE_PREFIX + "valid/activation-intent.json"
    changed_document = json.loads(files[target])
    changed_document["profile_id"] = "profile-tampered"
    changed_bytes = (json.dumps(changed_document, indent=2) + "\n").encode()
    files[target] = changed_bytes

    manifest = json.loads(files[MANIFEST_MEMBER])
    fixture = next(item for item in manifest["fixtures"] if FIXTURE_PREFIX + item["path"] == target)
    fixture["raw_bytes_sha256"] = _sha256(changed_bytes)
    manifest_core = {
        key: manifest[key] for key in ("schema_version", "schema_bundle_digest", "fixtures")
    }
    manifest["fixture_set_digest"] = canonical_sha256(manifest_core)
    files[MANIFEST_MEMBER] = (json.dumps(manifest, indent=2) + "\n").encode()

    wheel = _wheel_bytes(files)
    receipt = _receipt(wheel, files)
    with pytest.raises(InstallerError) as raised:
        inspect_wheel_bytes(wheel, receipt)

    assert raised.value.code == "FRESH_INSTALL_WHEEL_INVALID"
    assert "fixture document digest mismatch" in str(raised.value)


@pytest.mark.parametrize(
    ("kind", "name", "expected_fragment"),
    (
        ("duplicate", FIXTURE_PREFIX + "valid/activation-intent.json", "unsafe or duplicate"),
        ("symlink", "aar/factories/fixture-link.py", "symlink member"),
        ("backslash", r"aar\bundled\fixtures\provider-ready\alias.json", "unsafe or duplicate"),
        ("absolute", "/aar/bundled/fixtures/provider-ready/alias.json", "unsafe or duplicate"),
        ("traversal", "../escape.py", "unsafe or duplicate"),
        (
            "noncanonical-alias",
            FIXTURE_PREFIX + "./valid/activation-intent.json",
            "unsafe or duplicate",
        ),
    ),
)
def test_adversarial_zip_members_reach_archive_validator(
    kind: str,
    name: str,
    expected_fragment: str,
) -> None:
    files = _wheel_files()
    wheel = _wheel_bytes(files)
    receipt = _receipt(wheel, files)
    adversarial = _append_member(wheel, name, symlink=kind == "symlink")

    with pytest.raises(InstallerError) as raised:
        inspect_wheel_bytes(adversarial, receipt)

    assert raised.value.code == "FRESH_INSTALL_WHEEL_INVALID"
    assert expected_fragment in str(raised.value)


def test_frozen_migration_resource_is_an_exact_verified_copy() -> None:
    canonical = (
        ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2" / "migration-v6.sql"
    ).read_bytes()

    assert frozen_migration_v6_bytes() == canonical
    assert _prefixed_sha256(canonical) == FROZEN_MIGRATION_V6_SHA256
    assert FROZEN_MIGRATION_V6_ASSET == "assets/migration-v6.sql"


def test_pyproject_explicitly_includes_frozen_migration_asset() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    asset_mapping = (
        '"src/aar/runtime/assets/migration-v6.sql" = "aar/runtime/assets/migration-v6.sql"'
    )

    assert asset_mapping in pyproject
