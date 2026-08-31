from __future__ import annotations

import argparse
import sys

from aar.caller_driver_models import CallerDriverReadyEnvelope
from aar.canonical import canonical_json_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("v1", "legacy"), required=True)
    parser.add_argument("--launch-nonce", required=True)
    parser.add_argument("--adapter-id", required=True)
    parser.add_argument("--adapter-generation", required=True, type=int)
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--ticket-digest", required=True)
    parser.add_argument("--request-digest", required=True)
    parser.add_argument("--physical-attempt-id", required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    if args.mode == "legacy":
        payload = {
            "ready": True,
            "host": "127.0.0.1",
            "port": 43123,
        }
    else:
        payload = CallerDriverReadyEnvelope.issue(
            launch_nonce=args.launch_nonce,
            adapter_id=args.adapter_id,
            adapter_generation=args.adapter_generation,
            ticket_id=args.ticket_id,
            ticket_digest=args.ticket_digest,
            request_digest=args.request_digest,
            physical_attempt_id=args.physical_attempt_id,
            base_url=args.base_url,
        )
    sys.stdout.buffer.write(canonical_json_bytes(payload) + b"\n")
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
