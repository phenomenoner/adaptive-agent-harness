"""Build a deterministic Codex public-plugin candidate and submission packet."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import stat
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aar.canonical import pretty_json_bytes
from aar.versions import PACKAGE_VERSION

PUBLIC_PLUGIN_NAME = "adaptive-agent-runtime"
PUBLIC_PLUGIN_VERSION = "0.4.0"
BUILD_SCHEMA_VERSION = "aar.public-plugin-build.v2"
SUBMISSION_SCHEMA_VERSION = "aar.public-plugin-submission.v2"
PUBLIC_PLUGIN_REPOSITORY = "https://github.com/phenomenoner/adaptive-agent-harness"
PUBLIC_MARKETPLACE_NAME = "aar-public-candidate"
PUBLIC_REQUIRED_SCOPES = ("aar:rlm", "aar:workspace")
PUBLIC_SKILL_PATH = "./skills/"
PUBLIC_MCP_PATH = "./.mcp.json"
PUBLIC_ICON_PATH = "./assets/icon.png"
PUBLIC_LOGO_PATH = "./assets/logo.png"
PUBLIC_DARK_LOGO_PATH = "./assets/logo-dark.png"

PUBLIC_PLUGIN_SOURCE_ASSETS = (
    "skills/aar-public-runtime/SKILL.md",
    "assets/icon.png",
    "assets/logo.png",
    "assets/logo-dark.png",
    "submission/test-cases.json",
)

STARTER_PROMPTS = (
    "Use Adaptive Agent Runtime when the user needs tenant-isolated structured state across "
    "ChatGPT or Codex tasks.",
    "Open or inspect the named workspace before updating it, and preserve the returned "
    "generation and revision.",
    "For bounded RLM work, set one host-authorized model and effort per job, then claim each call "
    "and commit its result.",
)
EXTERNAL_REQUIREMENTS = (
    "production HTTPS endpoint and OAuth flow",
    "domain challenge",
    "reviewer credentials",
    "public website, support, privacy, retention/deletion, and terms pages",
    "production rate limits, abuse controls, storage quotas, backup, purge, and incident response",
    "single-owner or tenant-sticky persistent deployment topology",
    "five positive and three negative deployed test passes",
    "portal scan of the expected eleven-tool surface and skill snapshot",
    "fresh-host caller-delegated RLM execution and recovery proof",
    "verified Platform identity",
    "Apps Management Write",
    "final regional/legal approval",
    "OpenAI review approval",
    "publisher publish action",
)
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

PUBLIC_PLUGIN_DESCRIPTION = (
    "OAuth-authenticated, tenant-isolated structured workspaces and caller-delegated RLM "
    "coordination for durable work across ChatGPT or Codex tasks."
)
PUBLIC_PLUGIN_SHORT_DESCRIPTION = (
    "Durable workspaces and caller-delegated RLM over authenticated MCP"
)
PUBLIC_PLUGIN_LONG_DESCRIPTION = (
    "Provides a curated HTTP MCP surface for OAuth-authenticated, tenant-isolated structured "
    "workspaces and bounded caller-delegated RLM jobs. AAR persists one host-selected route per "
    "job, exact prompts, claim tickets, idempotent continuation, and bounded caller receipts; the "
    "host retains control of model selection, reasoning effort, model execution, authorization, "
    "and user-facing actions."
)
PUBLIC_RELEASE_NOTES = (
    "Public candidate for tenant-isolated workspaces and receipt-bound caller-delegated RLM."
)


@dataclass(frozen=True, slots=True)
class PublicPluginBuildSettings:
    """Immutable inputs for one public-plugin candidate build."""

    output_root: Path
    mcp_url: str
    website_url: str
    support_url: str
    privacy_policy_url: str
    terms_of_service_url: str
    developer_name: str
    source_root: Path | None = None


@dataclass(frozen=True, slots=True)
class _PreparedSource:
    copies: tuple[tuple[str, bytes], ...]
    skill_members: tuple[tuple[str, bytes], ...]
    submission_test_cases: bytes


def _source_candidates() -> tuple[Path, ...]:
    module_root = Path(__file__).resolve()
    package_root = module_root.parents[1]
    repository_root = module_root.parents[3]
    relative = Path("profiles/codex-public/adaptive-agent-workspace")
    return (
        package_root / "bundled" / relative,
        repository_root / relative,
    )


def bundled_public_plugin_source() -> Path:
    """Locate public-plugin source assets in an installed wheel or source checkout."""

    for candidate in _source_candidates():
        if all((candidate / relative).is_file() for relative in PUBLIC_PLUGIN_SOURCE_ASSETS):
            return candidate.resolve(strict=True)
    raise RuntimeError("the bundled AAR public-plugin source assets are missing")


def _validate_public_url(label: str, value: str, *, require_path: bool = False) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be an absolute production HTTPS URL")
    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError(f"{label} must not contain whitespace")
    try:
        parsed = urlsplit(value)
        username = parsed.username
        password = parsed.password
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid URL") from exc
    if parsed.scheme.casefold() != "https" or not parsed.netloc or hostname is None:
        raise ValueError(f"{label} must be an absolute production HTTPS URL")
    if username is not None or password is not None or "@" in parsed.netloc:
        raise ValueError(f"{label} must not contain userinfo")
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise ValueError(f"{label} must not contain a query or fragment")
    host = hostname.rstrip(".").casefold()
    if not host:
        raise ValueError(f"{label} must include a production hostname")
    special_suffixes = (".example", ".invalid", ".local", ".localhost", ".test")
    documentation_domains = ("example.com", "example.net", "example.org")
    if (
        host == "localhost"
        or host.endswith(special_suffixes)
        or any(host == domain or host.endswith(f".{domain}") for domain in documentation_domains)
    ):
        raise ValueError(f"{label} must not use a local or special-use hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError(f"{label} hostname must contain a dot") from None
    else:
        mapped = getattr(address, "ipv4_mapped", None)
        if not address.is_global or (mapped is not None and not mapped.is_global):
            raise ValueError(f"{label} must use a globally routable address")
    if require_path and parsed.path in {"", "/"}:
        raise ValueError("mcp_url must include a non-root endpoint path")


def _developer_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("developer_name must not be empty or whitespace")
    return value.strip()


def _normalise_output_root(value: Path) -> Path:
    try:
        return Path(value).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"output_root could not be normalized: {value}") from exc


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _lstat_without_links(path: Path, label: str) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    if path.is_symlink() or bool(
        getattr(result, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    ):
        raise ValueError(f"{label} contains a symlink or reparse point: {path}")
    return result


def _validate_source_root(value: Path) -> Path:
    root = Path(value)
    result = _lstat_without_links(root, "source root")
    if not stat.S_ISDIR(result.st_mode):
        raise ValueError(f"source root is not a directory: {root}")
    try:
        return root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"source root could not be resolved: {root}") from exc


def _resolve_source_root(settings: PublicPluginBuildSettings) -> Path:
    if settings.source_root is None:
        return _validate_source_root(bundled_public_plugin_source())
    return _validate_source_root(Path(settings.source_root))


def _validate_source_entry(path: Path, source_root: Path, label: str) -> os.stat_result:
    try:
        relative = path.relative_to(source_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes source root: {path}") from exc
    current = source_root
    for part in relative.parts:
        current /= part
        _lstat_without_links(current, label)
    result = _lstat_without_links(path, label)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(source_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes source root: {path}") from exc
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{label} could not be resolved: {path}") from exc
    return result


def _collect_regular_files(directory: Path, source_root: Path, label: str) -> list[Path]:
    directory_stat = _validate_source_entry(directory, source_root, label)
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise ValueError(f"{label} is not a directory: {directory}")
    pending = [directory]
    files: list[Path] = []
    while pending:
        current = pending.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ValueError(f"{label} could not be enumerated: {current}") from exc
        for entry in entries:
            entry_stat = _validate_source_entry(entry, source_root, label)
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append(entry)
            elif stat.S_ISREG(entry_stat.st_mode):
                files.append(entry)
            else:
                raise ValueError(f"{label} contains a non-regular file: {entry}")
    return sorted(files, key=lambda item: item.relative_to(source_root).as_posix())


def _validate_regular_file(path: Path, source_root: Path, label: str) -> None:
    result = _validate_source_entry(path, source_root, label)
    if not stat.S_ISREG(result.st_mode):
        raise ValueError(f"{label} is not a regular file: {path}")


def _read_source_file(path: Path, source_root: Path, label: str) -> bytes:
    _validate_regular_file(path, source_root, label)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} could not be read: {path}") from exc


def _validate_submission_test_cases(content: bytes) -> None:
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("submission/test-cases.json is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("submission/test-cases.json must contain a JSON object")
    for section, expected_count in (("positive", 5), ("negative", 3)):
        cases = document.get(section)
        if not isinstance(cases, list) or len(cases) != expected_count:
            raise ValueError(
                f"submission/test-cases.json {section} must contain exactly "
                f"{expected_count} objects"
            )
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                raise ValueError(f"submission/test-cases.json {section}[{index}] must be an object")
            for field in ("name", "prompt", "expected_behavior", "expected_result"):
                value = case.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"submission/test-cases.json {section}[{index}] {field} must be non-empty"
                    )


def _prepare_source(settings: PublicPluginBuildSettings) -> _PreparedSource:
    source_root = _resolve_source_root(settings)
    skills_root = source_root / "skills"
    assets_root = source_root / "assets"
    skill_paths = _collect_regular_files(skills_root, source_root, "skills source tree")
    asset_paths = _collect_regular_files(assets_root, source_root, "assets source tree")

    for relative in PUBLIC_PLUGIN_SOURCE_ASSETS:
        path = source_root / Path(relative)
        try:
            _validate_regular_file(path, source_root, "required source asset")
        except ValueError as exc:
            raise ValueError(f"required source asset is invalid: {relative}") from exc

    test_cases_path = source_root / "submission" / "test-cases.json"
    test_cases = _read_source_file(
        test_cases_path, source_root, "submission/test-cases.json"
    )
    _validate_submission_test_cases(test_cases)

    copy_paths = [
        (path.relative_to(source_root).as_posix(), path)
        for path in (*skill_paths, *asset_paths)
    ]
    copy_paths.sort(key=lambda item: item[0])
    copies = tuple(
        (
            relative,
            _read_source_file(path, source_root, "source tree file"),
        )
        for relative, path in copy_paths
    )

    skill_root = source_root / "skills" / "aar-public-runtime"
    skill_members = []
    for path in skill_paths:
        try:
            relative = path.relative_to(skill_root).as_posix()
        except ValueError:
            continue
        skill_members.append(
            (
                relative,
                _read_source_file(path, source_root, "public workspace skill file"),
            )
        )
    skill_members.sort(key=lambda item: item[0])
    return _PreparedSource(
        copies=copies,
        skill_members=tuple(skill_members),
        submission_test_cases=test_cases,
    )


def _plugin_manifest(settings: PublicPluginBuildSettings, developer_name: str) -> dict[str, Any]:
    return {
        "author": {"name": developer_name},
        "description": PUBLIC_PLUGIN_DESCRIPTION,
        "homepage": settings.website_url,
        "interface": {
            "capabilities": ["Read", "Write"],
            "category": "Developer Tools",
            "defaultPrompt": list(STARTER_PROMPTS),
            "developerName": developer_name,
            "displayName": "Adaptive Agent Runtime",
            "composerIcon": PUBLIC_ICON_PATH,
            "logo": PUBLIC_LOGO_PATH,
            "logoDark": PUBLIC_DARK_LOGO_PATH,
            "longDescription": PUBLIC_PLUGIN_LONG_DESCRIPTION,
            "privacyPolicyURL": settings.privacy_policy_url,
            "shortDescription": PUBLIC_PLUGIN_SHORT_DESCRIPTION,
            "termsOfServiceURL": settings.terms_of_service_url,
            "websiteURL": settings.website_url,
        },
        "keywords": ["agents", "mcp", "oauth", "rlm", "tenant", "workspace"],
        "license": "MIT",
        "mcpServers": PUBLIC_MCP_PATH,
        "name": PUBLIC_PLUGIN_NAME,
        "repository": PUBLIC_PLUGIN_REPOSITORY,
        "skills": PUBLIC_SKILL_PATH,
        "version": PUBLIC_PLUGIN_VERSION,
    }


def _marketplace_manifest() -> dict[str, Any]:
    return {
        "interface": {"displayName": "AAR Public Candidate"},
        "name": PUBLIC_MARKETPLACE_NAME,
        "plugins": [
            {
                "category": "Developer Tools",
                "name": PUBLIC_PLUGIN_NAME,
                "policy": {
                    "authentication": "ON_USE",
                    "installation": "AVAILABLE",
                },
                "source": {
                    "path": "./plugins/adaptive-agent-runtime",
                    "source": "local",
                },
            }
        ],
    }


def _submission_manifest(
    settings: PublicPluginBuildSettings, developer_name: str
) -> dict[str, Any]:
    return {
        "external_requirements": list(EXTERNAL_REQUIREMENTS),
        "listing": {
            "asset_paths": {
                "icon": PUBLIC_ICON_PATH,
                "logo": PUBLIC_LOGO_PATH,
                "logo_dark": PUBLIC_DARK_LOGO_PATH,
            },
            "category": "Developer Tools",
            "description": PUBLIC_PLUGIN_DESCRIPTION,
            "display_name": "Adaptive Agent Runtime",
            "developer_name": developer_name,
            "license": "MIT",
            "name": PUBLIC_PLUGIN_NAME,
            "privacy_policy_url": settings.privacy_policy_url,
            "repository": PUBLIC_PLUGIN_REPOSITORY,
            "support_url": settings.support_url,
            "terms_of_service_url": settings.terms_of_service_url,
            "version": PUBLIC_PLUGIN_VERSION,
            "website_url": settings.website_url,
        },
        "plugin": {
            "name": PUBLIC_PLUGIN_NAME,
            "skill_path": PUBLIC_SKILL_PATH,
            "starter_prompts": list(STARTER_PROMPTS),
            "version": PUBLIC_PLUGIN_VERSION,
        },
        "release": {
            "notes": PUBLIC_RELEASE_NOTES,
            "public_plugin_version": PUBLIC_PLUGIN_VERSION,
            "runtime_package_version": PACKAGE_VERSION,
        },
        "schema_version": SUBMISSION_SCHEMA_VERSION,
        "universal_mcp": {
            "authentication": {
                "protocol": "OAuth 2.1",
                "required_scopes": list(PUBLIC_REQUIRED_SCOPES),
                "type": "OAuth 2.1",
            },
            "url": settings.mcp_url,
        },
    }


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(path, pretty_json_bytes(value))


def _write_skill_zip(path: Path, members: tuple[tuple[str, bytes], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        path,
        mode="w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for relative, content in members:
            info = zipfile.ZipInfo(
                filename=f"aar-public-runtime/{relative}",
                date_time=_ZIP_TIMESTAMP,
            )
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.extra = b""
            info.comment = b""
            info.internal_attr = 0
            archive.writestr(info, content)


def _manifest_entries(output_root: Path, manifest_relative: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for path in _collect_regular_files(output_root, output_root, "generated output tree"):
        relative = path.relative_to(output_root).as_posix()
        if relative == manifest_relative:
            continue
        entries[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(entries.items()))


def _remove_created_output_root(output_root: Path) -> None:
    if output_root.is_symlink() or output_root.is_file():
        output_root.unlink()
    elif output_root.exists():
        shutil.rmtree(output_root)


def build_public_plugin(settings: PublicPluginBuildSettings) -> dict[str, object]:
    """Build one new deterministic public-plugin candidate tree."""

    _validate_public_url("mcp_url", settings.mcp_url, require_path=True)
    _validate_public_url("website_url", settings.website_url)
    _validate_public_url("support_url", settings.support_url)
    _validate_public_url("privacy_policy_url", settings.privacy_policy_url)
    _validate_public_url("terms_of_service_url", settings.terms_of_service_url)
    developer_name = _developer_name(settings.developer_name)
    output_root = _normalise_output_root(settings.output_root)
    if _path_exists(output_root):
        raise FileExistsError(f"output_root already exists: {output_root}")

    prepared = _prepare_source(settings)
    plugin_root = output_root / "plugins" / PUBLIC_PLUGIN_NAME
    mcp_manifest = {
        "mcpServers": {
            "aar-public": {
                "type": "http",
                "url": settings.mcp_url,
            }
        }
    }
    marketplace_manifest = _marketplace_manifest()
    plugin_manifest = _plugin_manifest(settings, developer_name)
    submission_manifest = _submission_manifest(settings, developer_name)
    manifest_relative = "submission/manifest.sha256.json"

    created = False
    try:
        output_root.mkdir(parents=True, exist_ok=False)
        created = True

        _write_json(
            output_root / ".agents" / "plugins" / "marketplace.json",
            marketplace_manifest,
        )
        _write_json(plugin_root / ".mcp.json", mcp_manifest)
        _write_json(plugin_root / ".codex-plugin" / "plugin.json", plugin_manifest)
        for relative, content in prepared.copies:
            _write_bytes(plugin_root / Path(relative), content)
        _write_bytes(
            output_root / "submission" / "test-cases.json",
            prepared.submission_test_cases,
        )
        _write_skill_zip(
            output_root / "submission" / "aar-public-runtime-skill.zip",
            prepared.skill_members,
        )
        _write_json(output_root / "submission" / "submission.json", submission_manifest)

        manifest = _manifest_entries(output_root, manifest_relative)
        manifest_path = output_root / Path(manifest_relative)
        _write_json(manifest_path, manifest)

        final_files = _collect_regular_files(
            output_root, output_root, "generated output tree"
        )
        final_names = {
            path.relative_to(output_root).as_posix() for path in final_files
        }
        expected_names = set(manifest) | {manifest_relative}
        if final_names != expected_names:
            raise RuntimeError("generated output tree does not match its manifest")
        manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return {
            "file_count": len(final_files),
            "manifest_sha256": manifest_digest,
            "mcp_url": settings.mcp_url,
            "output_root": os.fspath(output_root),
            "package_version": PACKAGE_VERSION,
            "plugin_name": PUBLIC_PLUGIN_NAME,
            "plugin_version": PUBLIC_PLUGIN_VERSION,
            "schema_version": BUILD_SCHEMA_VERSION,
            "status": "built",
        }
    except BaseException:
        if created:
            _remove_created_output_root(output_root)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--website-url", required=True)
    parser.add_argument("--support-url", required=True)
    parser.add_argument("--privacy-policy-url", required=True)
    parser.add_argument("--terms-of-service-url", required=True)
    parser.add_argument("--developer-name", required=True)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_public_plugin(
            PublicPluginBuildSettings(
                output_root=args.output_root,
                mcp_url=args.mcp_url,
                website_url=args.website_url,
                support_url=args.support_url,
                privacy_policy_url=args.privacy_policy_url,
                terms_of_service_url=args.terms_of_service_url,
                developer_name=args.developer_name,
                source_root=args.source_root,
            )
        )
    except Exception as exc:
        error = {
            "error": str(exc),
            "schema_version": BUILD_SCHEMA_VERSION,
            "status": "failed",
        }
        sys.stderr.write(pretty_json_bytes(error).decode("utf-8"))
        return 1
    sys.stdout.write(pretty_json_bytes(report).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
