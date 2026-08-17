#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "sdd-package-manifest.json"
EXCLUDED = {
    OUTPUT.name,
    "review-receipt.json",
    "test-results.json",
}
EXCLUDED_DIRECTORIES = {".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}


def digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return digest_bytes(raw)


def main() -> int:
    files: list[dict[str, Any]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if (
            path.name in EXCLUDED
            or EXCLUDED_DIRECTORIES.intersection(path.parts)
            or path.suffix == ".pyc"
        ):
            continue
        raw = path.read_bytes()
        files.append(
            {
                "path": relative,
                "size_bytes": len(raw),
                "sha256": digest_bytes(raw),
            }
        )
    contract_manifest = json.loads(
        (ROOT / "contracts" / "contract-manifest.json").read_text(encoding="utf-8")
    )
    payload = {
        "schema_version": "aar.sdd-package-manifest.v1",
        "sdd_id": "AR-RW",
        "specification_status": "hardening",
        "implementation_status": "planned",
        "baseline": {
            "package": "adaptive-agent-runtime",
            "version": "0.4.0a6",
            "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
            "tool_surface": "aar.mcp-tools.v7",
            "runtime_schema": "aar.runtime.v1",
            "operation_continuity_schema": "aar.operation-continuity.v1",
            "registry_schema_version": 5,
            "released_rlm_schema": "aar.rlm.v1",
        },
        "contract_manifest_digest": contract_manifest["manifest_digest"],
        "normative_files": [
            "README.md",
            "CONTRACTS.md",
            "LIFECYCLE.md",
            "MIGRATION.md",
            "migration-v6.sql",
            "portable_regex.py",
            "defect-register.json",
            "acceptance-matrix.json",
            "fault-matrix.json",
            "implementation-handoff.md",
            "contracts/contract-manifest.json",
        ],
        "files": files,
    }
    payload["manifest_digest"] = canonical_digest(payload)
    OUTPUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "file_count": len(files),
                "manifest_digest": payload["manifest_digest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
