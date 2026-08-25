"""Descriptor-bound candidate and wheel inspection primitives."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.provider_ready_install_models import InstallCandidateReceipt
from aar.provider_ready_models import HostActivationIntent
from aar.provider_ready_package_factory import (
    PackageFactoryBindingError,
    validate_package_factory_bindings,
)
from aar.runtime._install_fs import FileIdentity, InstallerError, _require_absolute_file_path

_FIXED_SCHEMA_MEMBER = "aar/bundled/schemas/aar-provider-ready-schemas-v1.json"
_FIXED_MANIFEST_MEMBER = "aar/bundled/fixtures/provider-ready/manifest.json"
_FIXED_SKILL_MEMBER = "aar/bundled/aar-operations/SKILL.md"
_PROVIDER_READY_PREFIX = "aar/bundled/fixtures/provider-ready/"
FROZEN_MIGRATION_V6_ASSET = "assets/migration-v6.sql"
FROZEN_MIGRATION_V6_SHA256 = (
    "sha256:8e7080b319aadb4eb98b5e3b9e12efe8c189c0bd12a82dc8ba1eea7c7bfe29b7"
)


@dataclass(frozen=True, slots=True)
class OpenedInput:
    path: Path
    descriptor: int
    data: bytes
    identity: FileIdentity

    def close(self) -> None:
        os.close(self.descriptor)


@dataclass(frozen=True, slots=True)
class WheelInspection:
    digest: str
    size_bytes: int
    members: Mapping[str, bytes]
    member_digests: Mapping[str, str]


def _digest_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _parse_json_bytes(data: bytes, *, label: str) -> Any:
    if not isinstance(data, bytes):
        raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"{label} is not bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"{label} contains a UTF-8 BOM")

    def duplicate_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"{label} has a duplicate key")
            result[key] = value
        return result

    def reject_float(_value: str) -> Any:
        raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"{label} contains a float")

    def reject_constant(_value: str) -> Any:
        raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"{label} contains a non-finite number")

    try:
        text = data.decode("utf-8", "strict")
        return json.loads(
            text,
            object_pairs_hook=duplicate_guard,
            parse_float=reject_float,
            parse_constant=reject_constant,
        )
    except InstallerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"invalid {label} JSON") from error


def _open_input(path_value: os.PathLike[str] | str, *, label: str) -> OpenedInput:
    path = _require_absolute_file_path(path_value, label=label)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"cannot open {label}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InstallerError("FRESH_INSTALL_INPUT_INVALID", f"{label} is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or len(b"".join(chunks)) != after.st_size
        ):
            raise InstallerError("FRESH_INSTALL_INPUT_CHANGED", f"{label} changed while reading")
        return OpenedInput(path, descriptor, b"".join(chunks), FileIdentity.from_stat(after))
    except BaseException:
        os.close(descriptor)
        raise


def _validate_intent(raw: bytes, *, label: str = "intent") -> HostActivationIntent:
    _parse_json_bytes(raw, label=label)
    try:
        intent = HostActivationIntent.model_validate_json(raw, strict=True)
    except (ValidationError, ValueError) as error:
        raise InstallerError(
            "FRESH_INSTALL_INITIAL_AUTHORITY_INVALID", f"invalid {label}"
        ) from error
    if intent.activation_generation != 1 or intent.previous_activation_authority_digest is not None:
        raise InstallerError(
            "FRESH_INSTALL_INITIAL_AUTHORITY_INVALID",
            "clean install requires activation_generation=1 and a null predecessor",
        )
    return intent


def _validate_receipt(raw: bytes) -> InstallCandidateReceipt:
    _parse_json_bytes(raw, label="candidate receipt")
    try:
        return InstallCandidateReceipt.model_validate_json(raw, strict=True)
    except (ValidationError, ValueError) as error:
        raise InstallerError(
            "FRESH_INSTALL_RECEIPT_INVALID", "candidate receipt is invalid"
        ) from error


def _zip_member_is_safe(name: str) -> bool:
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or (len(name) >= 2 and name[1] == ":")
        or name.endswith("/")
    ):
        return False
    return all(part not in {"", ".", ".."} for part in name.split("/"))


def _verify_schema_bundle(raw: bytes) -> dict[str, Any]:
    value = _parse_json_bytes(raw, label="provider-ready schema bundle")
    if not isinstance(value, dict):
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "schema bundle is not an object")
    required = {"bundle_digest", "schema_digests", "schema_version", "schemas"}
    if (
        set(value) != required
        or value.get("schema_version") != "aar.provider-ready-schema-bundle.v1"
    ):
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "schema bundle fields are not exact")
    core = {key: value[key] for key in ("schema_version", "schemas", "schema_digests")}
    if value["bundle_digest"] != canonical_sha256(core):
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "schema bundle digest mismatch")
    digests = value["schema_digests"]
    schemas = value["schemas"]
    if (
        not isinstance(digests, dict)
        or not isinstance(schemas, dict)
        or set(digests) != set(schemas)
    ):
        raise InstallerError(
            "FRESH_INSTALL_WHEEL_INVALID", "schema bundle digest inventory mismatch"
        )
    for schema_id, schema in schemas.items():
        if digests[schema_id] != canonical_sha256(schema):
            raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "schema member digest mismatch")
    return value


def _verify_fixture_manifest(raw: bytes, schema_bundle_digest: str) -> dict[str, Any]:
    value = _parse_json_bytes(raw, label="provider-ready fixture manifest")
    if not isinstance(value, dict):
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "fixture manifest is not an object")
    required = {"fixture_set_digest", "fixtures", "schema_bundle_digest", "schema_version"}
    if (
        set(value) != required
        or value.get("schema_version") != "aar.provider-ready-fixture-manifest.v1"
    ):
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "fixture manifest fields are not exact")
    if value["schema_bundle_digest"] != schema_bundle_digest:
        raise InstallerError(
            "FRESH_INSTALL_WHEEL_INVALID", "fixture manifest binds another schema bundle"
        )
    core = {key: value[key] for key in ("schema_version", "schema_bundle_digest", "fixtures")}
    if value["fixture_set_digest"] != canonical_sha256(core):
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "fixture manifest digest mismatch")
    fixtures = value["fixtures"]
    if not isinstance(fixtures, list):
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "fixture inventory is not an array")
    paths = [item.get("path") for item in fixtures if isinstance(item, dict)]
    if len(paths) != len(fixtures) or paths != sorted(paths) or len(paths) != len(set(paths)):
        raise InstallerError(
            "FRESH_INSTALL_WHEEL_INVALID", "fixture paths are not sorted and unique"
        )
    return value


def _verify_fixture_members(members: Mapping[str, bytes], manifest: Mapping[str, Any]) -> None:
    for item in manifest["fixtures"]:
        path = item["path"]
        member = _PROVIDER_READY_PREFIX + path
        if member not in members:
            raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", f"fixture member is absent: {path}")
        content = members[member]
        expected_raw_digest = item.get("raw_bytes_sha256")
        if (
            not isinstance(expected_raw_digest, str)
            or hashlib.sha256(content).hexdigest() != expected_raw_digest
        ):
            raise InstallerError(
                "FRESH_INSTALL_WHEEL_INVALID", f"fixture member bytes mismatch: {path}"
            )
        expected_document_digest = item.get("document_digest")
        if expected_document_digest is not None:
            try:
                document = _parse_json_bytes(content, label=f"fixture {path}")
            except InstallerError as error:
                raise InstallerError(
                    "FRESH_INSTALL_WHEEL_INVALID", f"fixture document is not valid JSON: {path}"
                ) from error
            if canonical_sha256(document) != expected_document_digest:
                raise InstallerError(
                    "FRESH_INSTALL_WHEEL_INVALID", f"fixture document digest mismatch: {path}"
                )


def frozen_migration_v6_bytes() -> bytes:
    """Load and verify the exact frozen v6 migration packaged with the runtime."""

    try:
        data = resources.files("aar.runtime").joinpath(FROZEN_MIGRATION_V6_ASSET).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as error:
        raise InstallerError(
            "FRESH_INSTALL_V6_FAILED", "packaged frozen migration-v6.sql is unavailable"
        ) from error
    if _digest_bytes(data) != FROZEN_MIGRATION_V6_SHA256:
        raise InstallerError(
            "FRESH_INSTALL_V6_FAILED", "packaged frozen migration-v6.sql hash mismatch"
        )
    return data


def inspect_wheel_bytes(raw: bytes, receipt: InstallCandidateReceipt) -> WheelInspection:
    """Hash and inspect one retained wheel byte string, including fixed assets."""

    digest = _digest_bytes(raw)
    members: dict[str, bytes] = {}
    member_digests: dict[str, str] = {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), mode="r")
    except (OSError, zipfile.BadZipFile) as error:
        raise InstallerError("FRESH_INSTALL_WHEEL_INVALID", "wheel is not a valid ZIP") from error
    try:
        for info in archive.infolist():
            name = info.filename
            if not _zip_member_is_safe(name) or name in members:
                raise InstallerError(
                    "FRESH_INSTALL_WHEEL_INVALID", "wheel has unsafe or duplicate members"
                )
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise InstallerError(
                    "FRESH_INSTALL_WHEEL_INVALID", "wheel contains a symlink member"
                )
            try:
                content = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise InstallerError(
                    "FRESH_INSTALL_WHEEL_INVALID", "wheel member cannot be read"
                ) from error
            members[name] = content
            member_digests[name] = _digest_bytes(content)
    finally:
        archive.close()

    if (
        _FIXED_SCHEMA_MEMBER not in members
        or _FIXED_MANIFEST_MEMBER not in members
        or _FIXED_SKILL_MEMBER not in members
    ):
        raise InstallerError(
            "FRESH_INSTALL_WHEEL_INVALID", "wheel is missing a fixed contract member"
        )
    bundle = _verify_schema_bundle(members[_FIXED_SCHEMA_MEMBER])
    manifest = _verify_fixture_manifest(members[_FIXED_MANIFEST_MEMBER], bundle["bundle_digest"])
    declared = {_PROVIDER_READY_PREFIX + str(item["path"]) for item in manifest["fixtures"]}
    actual = {
        name
        for name in members
        if name.startswith(_PROVIDER_READY_PREFIX) and name != _FIXED_MANIFEST_MEMBER
    }
    if actual != declared:
        raise InstallerError(
            "FRESH_INSTALL_WHEEL_INVALID", "provider-ready fixture members are not exact"
        )
    _verify_fixture_members(members, manifest)
    expected_contract_digest = canonical_sha256(
        {
            "schema_bundle_digest": bundle["bundle_digest"],
            "fixture_set_digest": manifest["fixture_set_digest"],
        }
    )
    if expected_contract_digest != receipt.contract_manifest_digest:
        raise InstallerError(
            "FRESH_INSTALL_RECEIPT_WHEEL_MISMATCH", "contract asset digest mismatch"
        )
    if member_digests[_FIXED_SKILL_MEMBER] != receipt.skill_digest:
        raise InstallerError("FRESH_INSTALL_RECEIPT_WHEEL_MISMATCH", "skill asset digest mismatch")
    return WheelInspection(digest, len(raw), members, member_digests)


def validate_receipt_against_intent(
    receipt: InstallCandidateReceipt,
    intent: HostActivationIntent,
    wheel: WheelInspection,
) -> None:
    """Join strict receipt, intent candidate, wheel bytes, and factory declarations."""

    if receipt.candidate != intent.candidate:
        raise InstallerError(
            "FRESH_INSTALL_RECEIPT_WHEEL_MISMATCH", "receipt candidate differs from intent"
        )
    if receipt.wheel_size_bytes != wheel.size_bytes or receipt.wheel_digest != wheel.digest:
        raise InstallerError(
            "FRESH_INSTALL_RECEIPT_WHEEL_MISMATCH", "receipt does not bind exact wheel bytes"
        )
    try:
        validate_package_factory_bindings(intent, receipt, wheel.member_digests)
    except PackageFactoryBindingError as error:
        raise InstallerError("FRESH_INSTALL_FACTORY_MISMATCH", str(error)) from error
