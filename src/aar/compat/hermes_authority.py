"""Explicit trusted-local authority for current-generation AAR session grants."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import anyio

from aar.runtime.supervisor_client import (
    SupervisorClient,
    SupervisorControlOutcomeIndeterminate,
    SupervisorControlRejected,
)
from aar.runtime.supervisor_protocol import (
    SupervisorGrantIssueRequest,
    SupervisorGrantRevokeRequest,
)


def _default_runtime_home() -> Path:
    configured = os.environ.get("AAR_RUNTIME_HOME")
    return Path(configured) if configured else Path.cwd() / ".aar"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-home", type=Path, default=_default_runtime_home())
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue", help="Explicitly issue one memory-only session grant")
    issue.add_argument("--principal-id", required=True)
    issue.add_argument("--session-id", required=True)
    issue.add_argument("--capability", required=True)
    issue.add_argument("--ttl-ms", type=int, required=True)
    issue.add_argument("--grant-id")

    revoke = commands.add_parser("revoke", help="Explicitly revoke one current-process grant")
    revoke.add_argument("--grant-id", required=True)
    return parser


async def _run(args: argparse.Namespace):
    client = SupervisorClient(args.runtime_home)
    if args.command == "issue":
        request = SupervisorGrantIssueRequest(
            principal_id=args.principal_id,
            session_id=args.session_id,
            capability=args.capability,
            ttl_ms=args.ttl_ms,
            grant_id=args.grant_id or f"grant-{uuid.uuid4().hex}",
        )
        return await client.issue_session_grant(request)
    return await client.revoke_session_grant(
        SupervisorGrantRevokeRequest(grant_id=args.grant_id)
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        grant = anyio.run(_run, args)
    except SupervisorControlOutcomeIndeterminate as error:
        print(
            json.dumps(
                {
                    "outcome": "indeterminate",
                    "grant_id": error.grant_id,
                    "error_type": type(error).__name__,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3
    except SupervisorControlRejected as error:
        print(
            json.dumps(
                {"outcome": "rejected", "error_type": type(error).__name__},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        print(
            json.dumps(
                {"outcome": "failed", "error_type": type(error).__name__},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(grant.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
