"""Clean-install and read-only activation operator CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aar.provider_ready_operator_inputs import (
    issue_candidate_receipt_from_path,
    issue_initial_host_activation_intent,
)
from aar.runtime.installer import (
    InstallerError,
    InstallResult,
    PublicationIndeterminate,
    install_clean_runtime,
    verify_published_install,
)
from aar.runtime.operator import (
    OperatorError,
    activation_status,
    emit_canonical,
    load_activation_profile,
    verify_activation_profile,
)

_READ_ONLY_COMMANDS = {
    ("runtime", "candidate"),
    ("activation", "intent"),
    ("activation", "status"),
    ("activation", "verify"),
}


def _path(value: str) -> Path:
    return Path(value)


def _add_runtime_home(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-home", type=_path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aar-admin")
    domains = parser.add_subparsers(dest="domain", required=True)

    runtime = domains.add_parser("runtime")
    runtime_commands = runtime.add_subparsers(dest="command", required=True)
    install = runtime_commands.add_parser("install")
    _add_runtime_home(install)
    install.add_argument("--intent", type=_path, required=True)
    install.add_argument("--candidate-receipt", type=_path, required=True)
    install.add_argument("--wheel", type=_path, required=True)
    candidate = runtime_commands.add_parser("candidate")
    candidate.add_argument("--wheel", type=_path, required=True)
    candidate.add_argument("--source-commit", required=True)

    activation = domains.add_parser("activation")
    activation_commands = activation.add_subparsers(dest="command", required=True)
    intent = activation_commands.add_parser("intent")
    _add_runtime_home(intent)
    intent.add_argument("--candidate-receipt", type=_path, required=True)
    intent.add_argument("--route-catalog", type=_path, required=True)
    intent.add_argument("--template", type=_path, required=True)
    verify = activation_commands.add_parser("verify")
    _add_runtime_home(verify)
    verify.add_argument("--profile", type=_path, required=True)
    activation_status = activation_commands.add_parser("status")
    _add_runtime_home(activation_status)
    return parser


def _run_read_only(args: argparse.Namespace) -> int:
    command = (args.domain, args.command)
    if command == ("runtime", "candidate"):
        emit_canonical(
            issue_candidate_receipt_from_path(
                args.wheel,
                source_commit=args.source_commit,
            )
        )
        return 0
    if command == ("activation", "intent"):
        emit_canonical(
            issue_initial_host_activation_intent(
                args.runtime_home,
                candidate_receipt_path=args.candidate_receipt,
                route_catalog_path=args.route_catalog,
                template_path=args.template,
            )
        )
        return 0
    if command == ("activation", "status"):
        emit_canonical(activation_status(args.runtime_home))
        return 0
    if command == ("activation", "verify"):
        supplied_profile = load_activation_profile(args.profile)
        installed = verify_published_install(args.runtime_home, allow_runtime_state=True)
        emit_canonical(
            verify_activation_profile(
                args.runtime_home,
                supplied_profile=supplied_profile,
                installed_profile=installed.profile,
                installed_authority=installed.authority,
                installed_candidate=installed.receipt.candidate,
            )
        )
        return 0
    raise AssertionError(f"unhandled read-only command: {command!r}")


def _install_result_document(result: InstallResult) -> dict[str, Any]:
    return {
        "target": str(result.target),
        "runtime_home_digest": result.runtime_home_digest,
        "database_identity": result.database_identity,
        "install_epoch": result.install_epoch,
        "snapshot_id": result.snapshot_id,
        "attestation_digest": result.attestation_digest,
        "profile_digest": result.profile_digest,
        "authority_digest": result.authority_digest,
        "preparation": result.preparation,
        "attestation": result.attestation,
        "profile": result.profile,
        "authority": result.authority,
    }


def _publication_indeterminate_document(
    error: PublicationIndeterminate,
) -> dict[str, Any]:
    return {
        "schema_version": "aar.clean-install-publication-indeterminate.v1",
        "outcome": "indeterminate",
        "publication_state": "rename_linearized",
        "target": str(error.target),
        "install_epoch": error.install_epoch,
        "target_may_exist": True,
        "automatic_retry_safe": False,
        "error_code": error.code,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = (args.domain, args.command)
    try:
        if command in _READ_ONLY_COMMANDS:
            return _run_read_only(args)
        if command == ("runtime", "install"):
            result = install_clean_runtime(
                args.runtime_home,
                args.intent,
                args.candidate_receipt,
                args.wheel,
            )
            emit_canonical(_install_result_document(result))
            return 0
        raise AssertionError(f"unhandled command: {command!r}")
    except PublicationIndeterminate as error:
        emit_canonical(_publication_indeterminate_document(error))
        return 3
    except (InstallerError, OperatorError) as error:
        print(f"aar-admin: {error.code}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
