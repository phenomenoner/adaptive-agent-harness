"""Deterministic source-side generator for successor AR-RW contract projections."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from aar.canonical import canonical_sha256

BASELINE_TOOL_MANIFEST_SHA256 = (
    "6ebf848eee74795771af23dfaaeb70fd5030cb88878327f76ffc10d1c4639ab8"
)
TARGET_PACKAGE_VERSION = "0.5.0a0"
_CONTRACT_FILES = (
    "aar-acceptance-evidence-v1.schema.json",
    "aar-artifact-publication-v1.schema.json",
    "aar-caller-work-v1.schema.json",
    "aar-migration-cutover-v1.schema.json",
    "aar-rlm-workbench-v1.schema.json",
    "aar-workspace-broker-frame-v1.schema.json",
)
_SCHEMA_PREFIX = {
    "aar-acceptance-evidence-v1.schema.json": "evidence",
    "aar-artifact-publication-v1.schema.json": "artifact",
    "aar-caller-work-v1.schema.json": "caller",
    "aar-migration-cutover-v1.schema.json": "migration",
    "aar-rlm-workbench-v1.schema.json": "workbench",
    "aar-workspace-broker-frame-v1.schema.json": "worker",
}


def build_mcp_tools_v8_manifest(root: Path) -> dict[str, Any]:
    """Build the v8 surface from exact v7 bytes and additive schema references."""

    schema_root = root / "schemas"
    baseline_bytes = (schema_root / "aar-mcp-tools-v7.json").read_bytes()
    baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
    if baseline_hash != BASELINE_TOOL_MANIFEST_SHA256:
        raise ValueError(
            "baseline tool manifest hash mismatch: "
            f"expected {BASELINE_TOOL_MANIFEST_SHA256}, got {baseline_hash}"
        )
    baseline = _load_json_bytes(baseline_bytes, "aar-mcp-tools-v7.json")
    if (
        baseline.get("server_version") != "0.4.0a6"
        or baseline.get("tool_surface_version") != "aar.mcp-tools.v7"
        or len(baseline.get("tools", [])) != 30
    ):
        raise ValueError("baseline tool manifest identity mismatch")

    additive = _load_json(schema_root / "aar-mcp-tools-v8.json")
    contracts = {
        name: _load_json(schema_root / name)
        for name in _CONTRACT_FILES
    }
    additive_descriptors = []
    for tool in additive["tools"]:
        annotations = dict(tool["annotations"])
        annotations["title"] = tool["title"]
        additive_descriptors.append(
            {
                "_meta": None,
                "annotations": annotations,
                "description": tool["description"],
                "execution": None,
                "icons": None,
                "inputSchema": _bundle_schema(tool["input_schema"], contracts),
                "name": tool["name"],
                "outputSchema": _bundle_schema(tool["output_schema"], contracts),
                "title": None,
            }
        )

    instructions = (
        baseline["instructions"]
        + " AR-RW v8: call aar_rlm_workbench_capabilities before admitting a workbench job; "
        + "caller-delegated jobs require start_only=true and the "
        + "claim/mark-send-started/commit protocol."
    )
    core = {
        "server_name": baseline["server_name"],
        "server_version": TARGET_PACKAGE_VERSION,
        "sdk": baseline["sdk"],
        "protocol_versions": baseline["protocol_versions"],
        "instructions": instructions,
        "tool_surface_version": "aar.mcp-tools.v8",
        "tools": [*baseline["tools"], *additive_descriptors],
    }
    return {**core, "tool_surface_digest": canonical_sha256(core)}


def verify_generated_assets(root: Path) -> list[str]:
    """Return deterministic drift messages without mutating the checkout."""

    failures: list[str] = []
    sdd = root / "docs" / "sdd" / "aar-rlm-native-workbench-v2"
    for name in _CONTRACT_FILES:
        _compare_bytes(
            root / "schemas" / name,
            sdd / "contracts" / name,
            failures,
        )
    _compare_bytes(
        root / "schemas" / "aar-mcp-tools-v8.json",
        sdd / "contracts" / "aar-mcp-tools-v8.json",
        failures,
    )
    _compare_bytes(
        root / "schemas" / "aar-broker-catalog-v2.json",
        sdd / "fixtures" / "valid-broker-catalog.json",
        failures,
    )
    reviewed_combined = _load_json(
        sdd / "contracts" / "aar-mcp-tools-v8-combined.json"
    )
    if build_mcp_tools_v8_manifest(root) != reviewed_combined:
        failures.append("aar-mcp-tools-v8-combined.json: generated content drift")
    return failures


def _bundle_schema(
    schema_ref: str,
    contracts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    filename, fragment = schema_ref.split("#", 1)
    definition = fragment.removeprefix("/$defs/")
    if (
        not fragment.startswith("/$defs/")
        or filename not in contracts
        or definition not in contracts[filename].get("$defs", {})
    ):
        raise ValueError(f"unresolved additive descriptor schema ref {schema_ref}")

    collected: dict[tuple[str, str], dict[str, Any]] = {}
    in_progress: set[tuple[str, str]] = set()

    def alias(key: tuple[str, str]) -> str:
        try:
            prefix = _SCHEMA_PREFIX[key[0]]
        except KeyError as error:
            raise ValueError(f"schema prefix missing for {key[0]}") from error
        return f"{prefix}__{key[1]}"

    def split_ref(current_file: str, ref_value: str) -> tuple[str, str]:
        if ref_value.startswith("#/$defs/"):
            return current_file, ref_value.removeprefix("#/$defs/")
        target_file, target_fragment = ref_value.split("#", 1)
        return target_file, target_fragment.removeprefix("/$defs/")

    def collect(key: tuple[str, str]) -> None:
        if key in collected or key in in_progress:
            return
        if key[0] not in contracts or key[1] not in contracts[key[0]].get("$defs", {}):
            raise ValueError(f"unresolved bundled contract ref: {key}")
        in_progress.add(key)
        collected[key] = rewrite(deepcopy(contracts[key[0]]["$defs"][key[1]]), key[0])
        in_progress.remove(key)

    def rewrite(value: Any, current_file: str) -> Any:
        if isinstance(value, list):
            return [rewrite(item, current_file) for item in value]
        if not isinstance(value, dict):
            return value
        ref_value = value.get("$ref")
        if isinstance(ref_value, str):
            target = split_ref(current_file, ref_value)
            collect(target)
            replacement = {"$ref": f"#/$defs/{alias(target)}"}
            replacement.update(
                {
                    key: rewrite(item, current_file)
                    for key, item in value.items()
                    if key != "$ref"
                }
            )
            return replacement
        return {key: rewrite(item, current_file) for key, item in value.items()}

    root_schema = rewrite(deepcopy(contracts[filename]["$defs"][definition]), filename)
    if collected:
        root_schema["$defs"] = {
            alias(key): value
            for key, value in sorted(collected.items(), key=lambda item: alias(item[0]))
        }
    root_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(root_schema)
    return root_schema


def _load_json(path: Path) -> dict[str, Any]:
    return _load_json_bytes(path.read_bytes(), str(path))


def _load_json_bytes(content: bytes, owner: str) -> dict[str, Any]:
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{owner}: root must be an object")
    return value


def _compare_bytes(source: Path, reviewed: Path, failures: list[str]) -> None:
    if not source.is_file():
        failures.append(f"{source.name}: projection missing")
    elif source.read_bytes() != reviewed.read_bytes():
        failures.append(f"{source.name}: projection bytes drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if not args.check:
        parser.error("only the read-only --check operation is supported")
    failures = verify_generated_assets(args.root.resolve())
    for failure in failures:
        print(failure)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
