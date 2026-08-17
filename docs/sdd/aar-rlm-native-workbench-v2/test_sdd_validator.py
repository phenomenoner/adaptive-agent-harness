#!/usr/bin/env python3
# ruff: noqa: E501
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent
DIGEST = "sha256:" + "1" * 64


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class SddValidatorTests(unittest.TestCase):
    def make_copy(self) -> Path:
        temp_root = Path(tempfile.mkdtemp(prefix="aar-rw-sdd-test-"))
        destination = temp_root / "sdd"
        shutil.copytree(
            ROOT,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        destination.chmod(destination.stat().st_mode | 0o700)
        for copied_path in destination.rglob("*"):
            write_bits = 0o700 if copied_path.is_dir() else 0o600
            copied_path.chmod(copied_path.stat().st_mode | write_bits)
        self.addCleanup(shutil.rmtree, temp_root, True)
        return destination

    def run_validator(self, root: Path, results: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "validate_sdd.py"]
        if results is not None:
            command.extend(
                [
                    "--results",
                    str(results),
                    "--review-trust",
                    str(results.parent / "review-trust.json"),
                ]
            )
        return subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def rebind_contract_manifest(self, root: Path) -> None:
        path = root / "contracts" / "contract-manifest.json"
        manifest = load(path)
        for entry in manifest["files"]:
            target = root / entry["path"]
            entry["size_bytes"] = target.stat().st_size
            entry["sha256"] = file_digest(target)
        manifest["generator_sha256"] = file_digest(root / "generate_contracts.py")
        payload = {key: value for key, value in manifest.items() if key != "manifest_digest"}
        manifest["manifest_digest"] = canonical_digest(payload)
        write(path, manifest)
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        if receipt_path.is_file():
            receipt = load(receipt_path)
            receipt["contract_manifest_digest"] = manifest["manifest_digest"]
            receipt["verifier_sha256"] = file_digest(root / "verify_registry_v5_migration.py")
            write(receipt_path, receipt)

    def rebind_package_manifest(self, root: Path) -> None:
        outcome = subprocess.run(
            [sys.executable, "generate_package_manifest.py"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)

    @staticmethod
    def file_binding(root: Path, relative: str, kind: str, content: bytes | None = None) -> dict[str, Any]:
        path = root / relative
        if content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return {
            "kind": kind,
            "relative_path": relative,
            "sha256": file_digest(path),
            "size_bytes": path.stat().st_size,
        }

    @staticmethod
    def rewrite_result_bundle(
        results_path: Path,
        results: dict[str, Any],
        *,
        candidate_changed: bool = False,
    ) -> None:
        if candidate_changed:
            candidate = results["candidate"]
            candidate["manifest_digest"] = canonical_digest(
                {key: value for key, value in candidate.items() if key != "manifest_digest"}
            )
        results["results_digest"] = canonical_digest(
            {key: value for key, value in results.items() if key != "results_digest"}
        )
        write(results_path, results)

    def build_results(self, root: Path, claim_status: str) -> Path:
        matrix = load(root / "acceptance-matrix.json")
        fault_matrix = load(root / "fault-matrix.json")
        bundle_root = root.parent / "results-bundle"
        bundle_root.mkdir(parents=True, exist_ok=True)
        reviewer_private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        reviewer_public_key_bytes = reviewer_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        source_content = b"source fixture\n"
        source_buffer = io.BytesIO()
        with tarfile.open(fileobj=source_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            source_info = tarfile.TarInfo("src/marker.txt")
            source_info.size = len(source_content)
            source_info.mode = 0o644
            source_info.mtime = 0
            source_info.uid = 0
            source_info.gid = 0
            archive.addfile(source_info, io.BytesIO(source_content))
        source_archive_bytes = source_buffer.getvalue()
        source_entries = [
            {
                "relative_path": "src/marker.txt",
                "sha256": "sha256:" + hashlib.sha256(source_content).hexdigest(),
                "size_bytes": len(source_content),
                "executable": False,
            }
        ]
        source_tree_digest = canonical_digest(
            {"schema_version": "aar.source-tree.v1", "entries": source_entries}
        )
        bindings = {
            "source": self.file_binding(
                bundle_root,
                "evidence/source.tar",
                "source_archive",
                source_archive_bytes,
            ),
            "wheel": self.file_binding(bundle_root, "evidence/candidate.whl", "wheel", b"wheel"),
            "tool": self.file_binding(
                bundle_root,
                "contracts/aar-mcp-tools-v8-combined.json",
                "tool_surface",
                (root / "contracts" / "aar-mcp-tools-v8-combined.json").read_bytes(),
            ),
            "profile": self.file_binding(bundle_root, "evidence/profile.json", "profile", b"profile"),
            "skill": self.file_binding(bundle_root, "evidence/SKILL.md", "skill", b"skill"),
            "tests": self.file_binding(bundle_root, "evidence/tests.tar", "test_bundle", b"tests"),
            "stdout": self.file_binding(bundle_root, "evidence/stdout.txt", "stdout", b"ok"),
            "public_key": self.file_binding(
                bundle_root,
                "evidence/reviewer-public-key.ed25519",
                "review_public_key",
                reviewer_public_key_bytes,
            ),
        }
        build_command = "uv build --no-sources"
        source_manifest = {
            "schema_version": "aar.acceptance-source.v1",
            "candidate_id": "candidate-one",
            "source_commit": "a" * 40,
            "clean": True,
            "source_archive_digest": bindings["source"]["sha256"],
            "source_archive_size_bytes": bindings["source"]["size_bytes"],
            "entries": source_entries,
            "source_tree_digest": source_tree_digest,
        }
        source_manifest["manifest_digest"] = canonical_digest(source_manifest)
        bindings["source_manifest"] = self.file_binding(
            bundle_root,
            "evidence/source-manifest.json",
            "source_manifest",
            json.dumps(source_manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        wheel_build = {
            "schema_version": "aar.acceptance-wheel-build.v1",
            "candidate_id": "candidate-one",
            "source_manifest_file_digest": bindings["source_manifest"]["sha256"],
            "source_manifest_digest": source_manifest["manifest_digest"],
            "source_commit": source_manifest["source_commit"],
            "source_tree_digest": source_tree_digest,
            "source_archive_digest": bindings["source"]["sha256"],
            "wheel_digest": bindings["wheel"]["sha256"],
            "build_command": build_command,
        }
        wheel_build["manifest_digest"] = canonical_digest(wheel_build)
        bindings["wheel_build"] = self.file_binding(
            bundle_root,
            "evidence/wheel-build-manifest.json",
            "wheel_build_manifest",
            json.dumps(wheel_build, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        combined_surface = load(root / "contracts" / "aar-mcp-tools-v8-combined.json")
        environment = {
            "schema_version": "aar.acceptance-environment.v1",
            "candidate_id": "candidate-one",
            "source_commit": "a" * 40,
            "package_version": "0.5.0a0",
            "tool_surface_digest": combined_surface["tool_surface_digest"],
            "python_version": "3.11.15",
            "platform": "linux-x86_64",
            "os_name": "linux",
            "os_version": "test-kernel",
            "architecture": "x86_64",
            "registry_schema_version": 6,
            "runtime_generation": 1,
            "command_environment_digest": file_digest(bundle_root / "evidence" / "stdout.txt"),
        }
        bindings["environment"] = self.file_binding(
            bundle_root,
            "evidence/environment.json",
            "environment",
            json.dumps(environment, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        fixture_input_path = bundle_root / "evidence" / "fixture-input.json"
        fixture_input_path.write_text('{"input":"deterministic"}\n', encoding="utf-8")
        fixture_bundle = {
            "schema_version": "aar.acceptance-fixture-bundle.v1",
            "bundle_id": "fixture-bundle-one",
            "candidate_id": "candidate-one",
            "entries": [
                {
                    "fixture_id": "fixture-input-one",
                    "kind": "input",
                    "relative_path": "evidence/fixture-input.json",
                    "sha256": file_digest(fixture_input_path),
                    "size_bytes": fixture_input_path.stat().st_size,
                }
            ],
        }
        fixture_bundle["bundle_digest"] = canonical_digest(fixture_bundle)
        bindings["fixture"] = self.file_binding(
            bundle_root,
            "evidence/fixture-manifest.json",
            "fixture_bundle",
            json.dumps(fixture_bundle, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        host_receipt = {
            "schema_version": "aar.acceptance-semantic-receipt.v1",
            "receipt_id": "host-receipt-one",
            "kind": "host",
            "candidate_id": "candidate-one",
            "operation_id": "operation-one",
            "issuer_id": "reference-host-one",
            "status": "succeeded",
            "certainty": "certain",
            "payload_digest": bindings["stdout"]["sha256"],
            "observed_at_unix_ms": 2_000_000_000_000,
        }
        bindings["host_receipt"] = self.file_binding(
            bundle_root,
            "evidence/host-receipt.json",
            "raw_receipt",
            json.dumps(host_receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        candidate = {
            "schema_version": "aar.acceptance-candidate.v1",
            "candidate_id": "candidate-one",
            "package_version": "0.5.0a0",
            "source_commit": "a" * 40,
            "source_tree_digest": source_tree_digest,
            "wheel_digest": bindings["wheel"]["sha256"],
            "contract_manifest_digest": load(root / "contracts" / "contract-manifest.json")["manifest_digest"],
            "tool_surface_digest": combined_surface["tool_surface_digest"],
            "profile_digests": [bindings["profile"]["sha256"]],
            "skill_digests": [bindings["skill"]["sha256"]],
            "artifacts": [
                bindings["source"],
                bindings["source_manifest"],
                bindings["wheel"],
                bindings["wheel_build"],
                bindings["tool"],
                bindings["profile"],
                bindings["skill"],
                bindings["tests"],
            ],
            "python_versions": ["3.11", "3.12", "3.13", "3.14"],
            "platforms": ["linux-x86_64"],
            "build_command": build_command,
            "created_at_unix_ms": 2_000_000_000_000,
        }
        candidate["manifest_digest"] = canonical_digest(candidate)
        evidence: list[dict[str, Any]] = []
        for row in matrix["rows"]:
            if not row["required_for_implementation_verified"]:
                continue
            evidence.append(self.evidence_item(row["id"], row["altitude"], None, len(evidence), bindings))
        row_altitude = {row["id"]: row["altitude"] for row in matrix["rows"]}
        for case in fault_matrix["cases"]:
            parent = case["acceptance_id"]
            if not next(
                row["required_for_implementation_verified"]
                for row in matrix["rows"]
                if row["id"] == parent
            ):
                continue
            evidence.append(
                self.evidence_item(parent, row_altitude[parent], case["id"], len(evidence), bindings)
            )
        for item in evidence:
            unsigned_item = {
                key: value
                for key, value in item.items()
                if key not in {"review_signature", "review_signature_digest"}
            }
            payload = canonical_bytes(
                {
                    "schema_version": "aar.acceptance-review-signature-payload.v1",
                    "candidate_manifest_digest": candidate["manifest_digest"],
                    "evidence_item": unsigned_item,
                }
            )
            signature_binding = self.file_binding(
                bundle_root,
                f"evidence/signatures/{item['evidence_id']}.sig",
                "review_signature",
                reviewer_private_key.sign(payload),
            )
            item["review_signature"] = signature_binding
            item["review_signature_digest"] = signature_binding["sha256"]
        results = {
            "schema_version": "aar.acceptance-results.v1",
            "candidate": candidate,
            "review_keys": [
                {
                    "reviewer_principal_id": "reviewer-one",
                    "reviewer_key_id": "reviewer-key-one",
                    "algorithm": "ed25519",
                    "public_key": bindings["public_key"],
                    "public_key_digest": bindings["public_key"]["sha256"],
                }
            ],
            "matrix_digest": file_digest(root / "acceptance-matrix.json"),
            "fault_matrix_digest": file_digest(root / "fault-matrix.json"),
            "evidence": evidence,
            "authorization_receipt_digests": [],
            "claim_status": claim_status,
        }
        results["results_digest"] = canonical_digest(results)
        write(
            bundle_root / "review-trust.json",
            {
                "schema_version": "aar.acceptance-review-trust.v1",
                "keys": [
                    {
                        "reviewer_principal_id": "reviewer-one",
                        "reviewer_key_id": "reviewer-key-one",
                        "algorithm": "ed25519",
                        "public_key_digest": bindings["public_key"]["sha256"],
                    }
                ],
            },
        )
        path = bundle_root / "test-results.json"
        write(path, results)
        return path

    @staticmethod
    def evidence_item(
        acceptance_id: str,
        tier: str,
        fault_case_id: str | None,
        index: int,
        bindings: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        semantic_receipts = []
        if tier in {"T3", "T4"}:
            semantic_receipts.append(
                {
                    "kind": "host",
                    "receipt_digest": bindings["host_receipt"]["sha256"],
                    "file": bindings["host_receipt"],
                }
            )
        return {
            "evidence_id": f"evidence-{index:04d}",
            "candidate_id": "candidate-one",
            "acceptance_id": acceptance_id,
            "fault_case_id": fault_case_id,
            "tier": tier,
            "command": f"run {acceptance_id} {fault_case_id or 'row'}",
            "environment_digest": bindings["environment"]["sha256"],
            "test_or_probe_digest": bindings["tests"]["sha256"],
            "fixture_digest": bindings["fixture"]["sha256"],
            "stdout_digest": bindings["stdout"]["sha256"],
            "receipt_digests": [item["receipt_digest"] for item in semantic_receipts],
            "files": [bindings["environment"], bindings["tests"], bindings["fixture"], bindings["stdout"]],
            "semantic_receipts": semantic_receipts,
            "exit_code": 0,
            "observed_outcome": "pass",
            "reviewer_disposition": "accepted",
            "reviewer_principal_id": "reviewer-one",
            "reviewer_key_id": "reviewer-key-one",
            "review_signature": bindings["public_key"],
            "review_signature_digest": bindings["public_key"]["sha256"],
            "observed_at_unix_ms": 2_000_000_000_000 + index,
        }

    def test_default_is_structural_only(self) -> None:
        result = self.run_validator(ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "STRUCTURALLY_VALID")

    def test_results_without_review_trust_are_rejected(self) -> None:
        root = self.make_copy()
        results = self.build_results(root, "implementation_verified")
        outcome = subprocess.run(
            [sys.executable, "validate_sdd.py", "--results", str(results)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("acceptance results require --review-trust", outcome.stderr)

    def test_complete_t0_t3_results_claim_implementation_verified(self) -> None:
        root = self.make_copy()
        results = self.build_results(root, "implementation_verified")
        outcome = self.run_validator(root, results)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertEqual(json.loads(outcome.stdout)["status"], "IMPLEMENTATION_VERIFIED")

    def test_rebound_but_invalid_review_signature_is_rejected(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        results = load(results_path)
        item = results["evidence"][0]
        signature_path = results_path.parent / item["review_signature"]["relative_path"]
        signature = bytearray(signature_path.read_bytes())
        signature[0] ^= 1
        signature_path.write_bytes(signature)
        digest = file_digest(signature_path)
        item["review_signature"]["sha256"] = digest
        item["review_signature"]["size_bytes"] = signature_path.stat().st_size
        item["review_signature_digest"] = digest
        results["results_digest"] = canonical_digest(
            {key: value for key, value in results.items() if key != "results_digest"}
        )
        write(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("review signature invalid", outcome.stderr)

    def test_live_claim_without_t4_is_rejected(self) -> None:
        root = self.make_copy()
        results = self.build_results(root, "live_qualified")
        outcome = self.run_validator(root, results)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("must be implementation_verified", outcome.stderr)

    def test_mutable_matrix_status_is_rejected(self) -> None:
        root = self.make_copy()
        matrix_path = root / "acceptance-matrix.json"
        matrix = load(matrix_path)
        matrix["rows"][0]["status"] = "passed"
        write(matrix_path, matrix)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("mutable status", outcome.stderr)

    def test_generated_contract_drift_is_rejected(self) -> None:
        root = self.make_copy()
        target = root / "contracts" / "aar-caller-work-v1.schema.json"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("manifest", outcome.stderr)

    def test_package_manifest_digest_changes_on_spec_change(self) -> None:
        root = self.make_copy()
        before = load(root / "sdd-package-manifest.json")["manifest_digest"]
        readme = root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.rebind_package_manifest(root)
        after = load(root / "sdd-package-manifest.json")["manifest_digest"]
        self.assertNotEqual(before, after)

    def test_runtime_contract_and_registry_storage_versions_cannot_be_conflated(self) -> None:
        root = self.make_copy()
        path = root / "sdd-package-manifest.json"
        manifest = load(path)
        manifest["baseline"]["runtime_schema"] = "aar.runtime.v5"
        payload = {key: value for key, value in manifest.items() if key != "manifest_digest"}
        manifest["manifest_digest"] = canonical_digest(payload)
        write(path, manifest)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("runtime contract version mismatch", outcome.stderr)

    def test_missing_required_fault_parent_is_rejected(self) -> None:
        root = self.make_copy()
        path = root / "fault-matrix.json"
        matrix = load(path)
        matrix["cases"] = [
            case for case in matrix["cases"] if case["acceptance_id"] != "AC-L07"
        ]
        write(path, matrix)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("AC-L07", outcome.stderr)

    def test_portable_regex_rejects_complexity_and_cross_runtime_counterexamples(self) -> None:
        from portable_regex import validate_portable_linear_pattern

        counterexamples = [
            "^" + "a?" * 35 + "a{35}b$",
            "a$",
            r"\-",
        ]
        for pattern in counterexamples:
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                validate_portable_linear_pattern(pattern)

        accepted = ["a", "^a", "^[A-Z]{1,64}", r"\$", "prefix[0-9]?"]
        for pattern in accepted:
            with self.subTest(accepted=pattern):
                validate_portable_linear_pattern(pattern)

    def test_schema_profile_rejects_patterns_before_engine_compile(self) -> None:
        import re

        from validate_sdd import validate_schema_profile_contract

        original_compile = re.compile
        patterns = ["^" + "a?" * 35 + "a{35}b$", "a$", r"\-"]

        def compile_tracker(expected: str, observed: list[str]) -> Any:
            def tracking_compile(expression: str, *args: Any, **kwargs: Any) -> Any:
                if expression == expected:
                    observed.append(expression)
                return original_compile(expression, *args, **kwargs)

            return tracking_compile

        for pattern in patterns:
            seen: list[str] = []
            schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "string", "pattern": pattern}
            tracker = compile_tracker(pattern, seen)
            with self.subTest(pattern=pattern), mock.patch("re.compile", side_effect=tracker), self.assertRaises(ValueError):
                validate_schema_profile_contract(schema)
            self.assertEqual([], seen, "candidate pattern reached regex engine before profile rejection")

    def test_broken_tool_schema_ref_is_rejected(self) -> None:
        root = self.make_copy()
        path = root / "contracts" / "aar-mcp-tools-v8.json"
        surface = load(path)
        surface["tools"][0]["input_schema"] = "aar-rlm-workbench-v1.schema.json#/$defs/Missing"
        write(path, surface)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("unresolved schema ref", outcome.stderr)

    def test_migration_cannot_alter_frozen_operations(self) -> None:
        root = self.make_copy()
        path = root / "migration-v6.sql"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nALTER TABLE operations ADD COLUMN forbidden TEXT;\n",
            encoding="utf-8",
        )
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("may not alter frozen operations", outcome.stderr)

    def test_pass_evidence_requires_zero_exit(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        results = load(results_path)
        results["evidence"][0]["exit_code"] = 255
        results["results_digest"] = canonical_digest({key: value for key, value in results.items() if key != "results_digest"})
        write(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("non-zero exit", outcome.stderr)

    def test_fault_evidence_cannot_replace_direct_row_evidence(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        results = load(results_path)
        results["evidence"] = [
            item
            for item in results["evidence"]
            if not (item["acceptance_id"] == "AC-L05" and item["fault_case_id"] is None)
        ]
        results["results_digest"] = canonical_digest({key: value for key, value in results.items() if key != "results_digest"})
        write(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("direct row evidence", outcome.stderr)

    def test_candidate_source_commit_must_match_source_manifest(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        results = load(results_path)
        results["candidate"]["source_commit"] = "b" * 40
        self.rewrite_result_bundle(results_path, results, candidate_changed=True)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("source-manifest-bound", outcome.stderr)

    def test_candidate_source_tree_must_match_source_manifest(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        results = load(results_path)
        results["candidate"]["source_tree_digest"] = "sha256:" + "0" * 64
        self.rewrite_result_bundle(results_path, results, candidate_changed=True)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("source-manifest-bound", outcome.stderr)

    def test_second_source_archive_is_rejected(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        source_bytes = (results_path.parent / "evidence" / "source.tar").read_bytes()
        second_binding = self.file_binding(
            results_path.parent,
            "evidence/source-copy.tar",
            "source_archive",
            source_bytes,
        )
        results = load(results_path)
        results["candidate"]["artifacts"].append(second_binding)
        self.rewrite_result_bundle(results_path, results, candidate_changed=True)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("exactly one source_archive", outcome.stderr)

    def test_wheel_build_must_bind_exact_source_manifest(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        wheel_build_path = results_path.parent / "evidence" / "wheel-build-manifest.json"
        wheel_build = load(wheel_build_path)
        wheel_build["source_manifest_digest"] = "sha256:" + "0" * 64
        wheel_build["manifest_digest"] = canonical_digest(
            {key: value for key, value in wheel_build.items() if key != "manifest_digest"}
        )
        write(wheel_build_path, wheel_build)
        replacement = self.file_binding(
            results_path.parent,
            "evidence/wheel-build-manifest.json",
            "wheel_build_manifest",
        )
        results = load(results_path)
        results["candidate"]["artifacts"] = [
            replacement if item["kind"] == "wheel_build_manifest" else item
            for item in results["candidate"]["artifacts"]
        ]
        self.rewrite_result_bundle(results_path, results, candidate_changed=True)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("source manifest binding mismatch", outcome.stderr)

    def test_signed_pass_fail_pair_for_one_obligation_is_rejected(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        results = load(results_path)
        conflict = json.loads(json.dumps(results["evidence"][0]))
        conflict["evidence_id"] = "conflicting-evidence"
        conflict["observed_outcome"] = "fail"
        conflict["reviewer_disposition"] = "rejected"
        conflict["exit_code"] = 1
        unsigned = {
            key: value
            for key, value in conflict.items()
            if key not in {"review_signature", "review_signature_digest"}
        }
        payload = {
            "schema_version": "aar.acceptance-review-signature.v1",
            "candidate_manifest_digest": results["candidate"]["manifest_digest"],
            "evidence_item": unsigned,
        }
        private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        signature = private_key.sign(canonical_bytes(payload))
        conflict["review_signature"] = self.file_binding(
            results_path.parent,
            "evidence/signature-conflict.bin",
            "review_signature",
            signature,
        )
        conflict["review_signature_digest"] = conflict["review_signature"]["sha256"]
        results["evidence"].append(conflict)
        self.rewrite_result_bundle(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("conflicting or duplicate evidence", outcome.stderr)

    def test_candidate_contract_manifest_must_match_sdd(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        results = load(results_path)
        results["candidate"]["contract_manifest_digest"] = "sha256:" + "0" * 64
        candidate_payload = {key: value for key, value in results["candidate"].items() if key != "manifest_digest"}
        results["candidate"]["manifest_digest"] = canonical_digest(candidate_payload)
        results["results_digest"] = canonical_digest({key: value for key, value in results.items() if key != "results_digest"})
        write(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("contract manifest digest mismatch", outcome.stderr)

    def test_candidate_artifact_must_resolve_to_bytes(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        (results_path.parent / "evidence" / "candidate.whl").unlink()
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("evidence file missing", outcome.stderr)

    def test_duplicate_json_key_is_rejected(self) -> None:
        root = self.make_copy()
        path = root / "acceptance-matrix.json"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            '"schema_version": "aar.sdd.acceptance-matrix.v1",',
            '"schema_version": "aar.sdd.acceptance-matrix.v1",\n  "schema_version": "aar.sdd.acceptance-matrix.v1",',
            1,
        )
        path.write_text(text, encoding="utf-8")
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("duplicate JSON key", outcome.stderr)

    def test_lone_surrogate_is_rejected(self) -> None:
        root = self.make_copy()
        path = root / "acceptance-matrix.json"
        text = path.read_text(encoding="utf-8").replace(
            "aar.sdd.acceptance-matrix.v1",
            "aar.sdd.acceptance-matrix.\\ud800v1",
            1,
        )
        path.write_text(text, encoding="utf-8")
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("surrogate", outcome.stderr)


    def test_unbounded_failure_details_contract_is_rejected(self) -> None:
        root = self.make_copy()
        path = root / "contracts" / "aar-rlm-workbench-v1.schema.json"
        schema = load(path)
        details = schema["$defs"]["Failure"]["properties"]["details"]
        details["maxItems"] = 64
        write(path, schema)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("invalid-failure-details-overflow", outcome.stderr)


    def test_send_started_requires_sent_request_digest(self) -> None:
        root = self.make_copy()
        path = root / "contracts" / "aar-caller-work-v1.schema.json"
        schema = load(path)
        branches = schema["$defs"]["CallerWorkTicket"]["allOf"]
        branch = next(
            item
            for item in branches
            if "send_started"
            in item.get("if", {})
            .get("properties", {})
            .get("state", {})
            .get("enum", [])
        )
        sent_state = branch["then"]["properties"]["physical_attempt"]["allOf"][1]
        sent_state["properties"].pop("sent_request_digest")
        write(path, schema)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("invalid-ticket-send-started-missing-request-digest", outcome.stderr)


    def test_published_contract_must_be_draft_2020_12_meta_valid(self) -> None:
        root = self.make_copy()
        path = root / "contracts" / "aar-artifact-publication-v1.schema.json"
        schema = load(path)
        schema["type"] = 7
        write(path, schema)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("INVALID:", outcome.stderr)
        self.assertIn("not valid", outcome.stderr)


    def test_environment_evidence_cannot_be_an_empty_shell(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        environment_path = results_path.parent / "evidence" / "environment.json"
        environment_path.write_bytes(
            json.dumps({"python_version": "3.11", "os_name": "linux"}).encode("utf-8")
        )
        digest = file_digest(environment_path)
        size = environment_path.stat().st_size
        results = load(results_path)
        for item in results["evidence"]:
            item["environment_digest"] = digest
            for binding in item["files"]:
                if binding["kind"] == "environment":
                    binding["sha256"] = digest
                    binding["size_bytes"] = size
        results["results_digest"] = canonical_digest(
            {key: value for key, value in results.items() if key != "results_digest"}
        )
        write(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("EnvironmentManifest invalid", outcome.stderr)


    def test_fixture_evidence_cannot_be_digest_only(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        fixture_path = results_path.parent / "evidence" / "fixture-manifest.json"
        fixture = {
            "schema_version": "aar.acceptance-fixture-bundle.v1",
            "bundle_id": "fixture-bundle-one",
            "candidate_id": "candidate-one",
            "entries": [],
        }
        fixture["bundle_digest"] = canonical_digest(fixture)
        fixture_path.write_text(json.dumps(fixture, sort_keys=True), encoding="utf-8")
        digest = file_digest(fixture_path)
        size = fixture_path.stat().st_size
        results = load(results_path)
        for item in results["evidence"]:
            item["fixture_digest"] = digest
            for binding in item["files"]:
                if binding["kind"] == "fixture_bundle":
                    binding["sha256"] = digest
                    binding["size_bytes"] = size
        results["results_digest"] = canonical_digest(
            {key: value for key, value in results.items() if key != "results_digest"}
        )
        write(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("FixtureBundleManifest invalid", outcome.stderr)


    def test_semantic_receipt_document_kind_must_match_binding(self) -> None:
        root = self.make_copy()
        results_path = self.build_results(root, "implementation_verified")
        receipt_path = results_path.parent / "evidence" / "host-receipt.json"
        receipt = load(receipt_path)
        receipt["kind"] = "authorization"
        write(receipt_path, receipt)
        digest = file_digest(receipt_path)
        size = receipt_path.stat().st_size
        results = load(results_path)
        for item in results["evidence"]:
            for semantic in item["semantic_receipts"]:
                semantic["receipt_digest"] = digest
                semantic["file"]["sha256"] = digest
                semantic["file"]["size_bytes"] = size
            item["receipt_digests"] = [
                semantic["receipt_digest"] for semantic in item["semantic_receipts"]
            ]
        results["results_digest"] = canonical_digest(
            {key: value for key, value in results.items() if key != "results_digest"}
        )
        write(results_path, results)
        outcome = self.run_validator(root, results_path)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("semantic receipt content mismatch", outcome.stderr)


    def test_broker_catalog_contract_id_must_match_request_schema(self) -> None:
        root = self.make_copy()
        path = root / "fixtures" / "valid-broker-catalog.json"
        catalog = load(path)
        model = next(item for item in catalog["contracts"] if item["method"] == "model.request")
        model["contract_id"] = "aar.model-request.v2"
        catalog["catalog_digest"] = canonical_digest(
            {key: value for key, value in catalog.items() if key != "catalog_digest"}
        )
        write(path, catalog)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("catalog contract id mismatch", outcome.stderr)


    def test_duplicate_tool_name_is_rejected_in_each_v8_surface(self) -> None:
        for filename in ("aar-mcp-tools-v8.json", "aar-mcp-tools-v8-combined.json"):
            with self.subTest(filename=filename):
                root = self.make_copy()
                path = root / "contracts" / filename
                surface = load(path)
                surface["tools"][1]["name"] = surface["tools"][0]["name"]
                write(path, surface)
                self.rebind_contract_manifest(root)
                self.rebind_package_manifest(root)
                outcome = self.run_validator(root)
                self.assertNotEqual(outcome.returncode, 0)
                self.assertIn("duplicate tool names", outcome.stderr)

    def test_duplicate_broker_identity_is_rejected(self) -> None:
        for field, message in (
            ("method", "duplicate methods"),
            ("contract_id", "duplicate contract ids"),
        ):
            with self.subTest(field=field):
                root = self.make_copy()
                path = root / "fixtures" / "valid-broker-catalog.json"
                catalog = load(path)
                catalog["contracts"][1][field] = catalog["contracts"][0][field]
                catalog["catalog_digest"] = canonical_digest(
                    {key: value for key, value in catalog.items() if key != "catalog_digest"}
                )
                write(path, catalog)
                self.rebind_contract_manifest(root)
                self.rebind_package_manifest(root)
                outcome = self.run_validator(root)
                self.assertNotEqual(outcome.returncode, 0)
                self.assertIn(message, outcome.stderr)

    def test_cancelled_certain_requires_settlement_receipt(self) -> None:
        root = self.make_copy()
        path = root / "contracts" / "aar-caller-work-v1.schema.json"
        schema = load(path)
        branches = schema["$defs"]["CallerWorkTicket"]["allOf"]
        branch = next(
            item
            for item in branches
            if "cancelled_certain"
            in item.get("if", {}).get("properties", {}).get("state", {}).get("enum", [])
            and "settled_receipt_digest"
            in item.get("then", {}).get("properties", {})
        )
        branch["then"]["properties"].pop("settled_receipt_digest")
        write(path, schema)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("invalid-ticket-cancelled-certain-missing-settlement", outcome.stderr)


    def test_ticket_suspension_semantic_binding_fk_is_required(self) -> None:
        root = self.make_copy()
        migration = root / "migration-v6.sql"
        text = migration.read_text(encoding="utf-8").replace(
            "contract_id, request_digest, logical_owner_json",
            "contract_id, request_digest",
        )
        migration.write_text(text, encoding="utf-8")
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        receipt = load(receipt_path)
        receipt["migration_digest"] = file_digest(migration)
        write(receipt_path, receipt)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("suspension semantic-binding FK missing", outcome.stderr)

    def test_nonpending_ticket_identity_constraint_is_required(self) -> None:
        root = self.make_copy()
        migration = root / "migration-v6.sql"
        text = migration.read_text(encoding="utf-8").replace(
            "CHECK ((state NOT IN ('pending', 'cancelled_before_send')) <= (",
            "CHECK ((state = 'send_reserved') <= (",
            1,
        )
        migration.write_text(text, encoding="utf-8")
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        receipt = load(receipt_path)
        receipt["migration_digest"] = file_digest(migration)
        write(receipt_path, receipt)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("caller ticket sent-state constraint missing", outcome.stderr)

    def test_cancelled_certain_migration_constraint_is_required(self) -> None:
        root = self.make_copy()
        migration = root / "migration-v6.sql"
        text = migration.read_text(encoding="utf-8").replace(
            "CHECK ((state IN ('settled_success', 'settled_failure',\n"
            "                      'cancelled_before_send', 'cancelled_certain')) <= (",
            "CHECK ((state IN ('settled_success', 'settled_failure',\n"
            "                      'cancelled_before_send')) <= (",
            1,
        )
        migration.write_text(text, encoding="utf-8")
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        receipt = load(receipt_path)
        receipt["migration_digest"] = file_digest(migration)
        write(receipt_path, receipt)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("cancelled_certain settlement constraint missing", outcome.stderr)

    def test_migration_attestation_table_is_required(self) -> None:
        root = self.make_copy()
        migration = root / "migration-v6.sql"
        migration.write_text(
            migration.read_text(encoding="utf-8").replace(
                "CREATE TABLE IF NOT EXISTS migration_v6_attestations",
                "CREATE TABLE IF NOT EXISTS removed_migration_v6_attestations",
                1,
            ),
            encoding="utf-8",
        )
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        receipt = load(receipt_path)
        receipt["migration_digest"] = file_digest(migration)
        write(receipt_path, receipt)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("migration missing tables", outcome.stderr)

    def test_wal_backup_receipt_claim_is_required(self) -> None:
        root = self.make_copy()
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        receipt = load(receipt_path)
        receipt["wal_committed_rows_included_in_snapshot"] = False
        write(receipt_path, receipt)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("wal_committed_rows_included_in_snapshot", outcome.stderr)

    def test_v5_rejection_cannot_be_reduced_to_catch_all(self) -> None:
        root = self.make_copy()
        verifier = root / "verify_registry_v5_migration.py"
        verifier.write_text(
            verifier.read_text(encoding="utf-8").replace(
                "except UnsupportedRegistrySchema as error:",
                "except Exception as error:",
                1,
            ),
            encoding="utf-8",
        )
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        receipt = load(receipt_path)
        receipt["verifier_sha256"] = file_digest(verifier)
        write(receipt_path, receipt)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("discriminate newer-schema rejection", outcome.stderr)

    def test_migration_receipt_must_bind_equal_pre_post_v5_rows(self) -> None:
        root = self.make_copy()
        receipt_path = root / "fixtures" / "registry-v5-migration-verification.json"
        receipt = load(receipt_path)
        receipt["post_migration_v5_data_row_set_digest"] = "sha256:" + "0" * 64
        write(receipt_path, receipt)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("frozen v5 row-set mismatch", outcome.stderr)

    def test_populated_v5_fixture_is_required(self) -> None:
        root = self.make_copy()
        binding_path = root / "fixtures" / "registry-v5-binding.json"
        binding = load(binding_path)
        binding["populated_tables"].remove("operations")
        write(binding_path, binding)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("lacks populated tables", outcome.stderr)

    def test_raw_database_copy_in_verifier_is_rejected(self) -> None:
        root = self.make_copy()
        verifier = root / "verify_registry_v5_migration.py"
        verifier.write_text(
            verifier.read_text(encoding="utf-8").replace(
                "source.backup(destination)",
                "shutil.copyfile(source, destination)",
                1,
            ),
            encoding="utf-8",
        )
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("SQLite backup API", outcome.stderr)

    def test_semantic_post_write_rollback_is_rejected(self) -> None:
        root = self.make_copy()
        valid = load(root / "fixtures" / "valid-cutover-forward-ledger.json")
        invalid_path = root / "fixtures" / "invalid-cutover-semantic-rollback.json"
        write(invalid_path, valid)
        self.rebind_contract_manifest(root)
        self.rebind_package_manifest(root)
        outcome = self.run_validator(root)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("semantic post-write rollback fixture was accepted", outcome.stderr)


if __name__ == "__main__":
    unittest.main()
