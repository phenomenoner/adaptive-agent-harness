"""Explicit operator CLI for provider-ready cutover and activation custody."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from aar.runtime.operator import (
    OperatorError,
    activation_status,
    build_cutover_plan,
    emit_canonical,
    verify_activation_profile,
)

_READ_ONLY_COMMANDS = {
    ("cutover", "plan"),
    ("cutover", "status"),
    ("activation", "status"),
    ("activation", "verify"),
}
_MUTATION_COMMANDS = {
    ("cutover", "apply"),
    ("cutover", "abort"),
    ("cutover", "reconcile"),
    ("cutover", "restore"),
    ("runtime", "initialize"),
}


def _path(value: str) -> Path:
    return Path(value)


def _add_runtime_home(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-home", type=_path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aar-admin")
    domains = parser.add_subparsers(dest="domain", required=True)

    cutover = domains.add_parser("cutover")
    cutover_commands = cutover.add_subparsers(dest="command", required=True)

    plan = cutover_commands.add_parser("plan")
    _add_runtime_home(plan)
    plan.add_argument("--intent", type=_path, required=True)
    plan.add_argument("--profile-output", type=_path, required=True)

    status = cutover_commands.add_parser("status")
    _add_runtime_home(status)

    apply = cutover_commands.add_parser("apply")
    apply.add_argument("--plan", required=True)

    abort = cutover_commands.add_parser("abort")
    abort.add_argument("--plan", type=_path, required=True)

    reconcile = cutover_commands.add_parser("reconcile")
    _add_runtime_home(reconcile)
    reconcile.add_argument("--epoch", required=True)
    reconcile.add_argument("--plan", type=_path, required=True)

    restore = cutover_commands.add_parser("restore")
    restore.add_argument("--plan", type=_path, required=True)
    restore.add_argument("--snapshot", type=_path, required=True)

    runtime = domains.add_parser("runtime")
    runtime_commands = runtime.add_subparsers(dest="command", required=True)
    initialize = runtime_commands.add_parser("initialize")
    _add_runtime_home(initialize)

    activation = domains.add_parser("activation")
    activation_commands = activation.add_subparsers(dest="command", required=True)
    verify = activation_commands.add_parser("verify")
    _add_runtime_home(verify)
    verify.add_argument("--profile", type=_path, required=True)
    activation_status = activation_commands.add_parser("status")
    _add_runtime_home(activation_status)
    return parser


def _run_read_only(args: argparse.Namespace) -> int:
    command = (args.domain, args.command)
    if command == ("cutover", "plan"):
        emit_canonical(
            build_cutover_plan(
                args.runtime_home,
                args.intent,
                args.profile_output,
            )
        )
        return 0
    if command in {("cutover", "status"), ("activation", "status")}:
        emit_canonical(activation_status(args.runtime_home))
        return 0
    if command == ("activation", "verify"):
        emit_canonical(verify_activation_profile(args.runtime_home, args.profile))
        return 0
    raise AssertionError(f"unhandled read-only command: {command!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = (args.domain, args.command)
    try:
        if command in _READ_ONLY_COMMANDS:
            return _run_read_only(args)
        if command in _MUTATION_COMMANDS:
            raise OperatorError(
                "COMMAND_NOT_IMPLEMENTED",
                f"{args.domain} {args.command} has no mutation implementation in B1",
            )
        raise AssertionError(f"unhandled command: {command!r}")
    except OperatorError as error:
        print(f"aar-admin: {error.code}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
