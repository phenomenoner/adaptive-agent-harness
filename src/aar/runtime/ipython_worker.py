"""Private JSONL worker hosting one supervised IPython namespace."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import sys
from typing import Any

import IPython
from IPython.core.interactiveshell import InteractiveShell

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_workbench_models import WorkspaceBrokerFrame
from aar.runtime.programming import (
    PlainPythonWorkspaceBackend,
    _portable_value,
    _PortableValueError,
)


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class _BoundedStream(io.TextIOBase):
    def __init__(self, remaining: list[int]) -> None:
        self._remaining = remaining
        self._parts: list[str] = []
        self.truncated = False

    def write(self, value: str) -> int:
        allowed = min(len(value), self._remaining[0], 8_192 - len(self.text))
        if allowed:
            self._parts.append(value[:allowed])
            self._remaining[0] -= allowed
        if allowed < len(value):
            self.truncated = True
        return len(value)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def _bounded_display_data(value: Any) -> Any:
    try:
        portable = _portable_value(value)
        if len(canonical_json_bytes(portable)) <= 8_192:
            return portable
    except _PortableValueError:
        pass
    return {"text/plain": f"<{type(value).__name__} display payload excluded>"}


class _Worker:
    def __init__(self) -> None:
        self.shell = InteractiveShell.instance()
        self._baseline_names = frozenset(self.shell.user_ns)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "execute":
            return self.execute(request)
        if command == "inspect":
            return {"ok": True, "variables": self.inspect()}
        if command == "checkpoint":
            return {"ok": True, **self.checkpoint(request["policy"])}
        if command == "restore":
            self.restore(request["values"])
            return {"ok": True}
        if command == "close":
            return {"ok": True, "closing": True}
        raise ValueError(f"unsupported worker command: {command}")

    def _is_user_name(self, name: str) -> bool:
        return (
            name not in self._baseline_names
            and PlainPythonWorkspaceBackend._is_user_name(name)
            and re.fullmatch(r"_(?:\d+|i\d*)", name) is None
            and name not in {"__", "___"}
        )

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        max_events = int(request["max_events"])
        max_output_chars = int(request["max_output_chars"])
        artifact_enabled = bool(request["artifact_enabled"])
        broker_enabled = bool(request.get("broker_enabled", False))
        wire_stdin = sys.stdin
        wire_stdout = sys.stdout
        events: list[dict[str, Any]] = []
        artifacts: list[dict[str, str]] = []
        dropped = False

        def add(event: dict[str, Any], *, force: bool = False) -> None:
            nonlocal dropped
            if len(events) >= max_events - 1:
                dropped = True
                if not force:
                    return
                events.pop()
            events.append(event)

        def progress(
            message: str,
            *,
            completed: int | None = None,
            total: int | None = None,
        ) -> None:
            data: dict[str, Any] = {"message": str(message)}
            if completed is not None:
                data["completed"] = completed
            if total is not None:
                data["total"] = total
            add({"kind": "progress", "data": data})

        def display(value: Any, media_type: str = "application/json") -> None:
            try:
                portable = _bounded_display_data(value)
                data: dict[str, Any] = {"media_type": media_type, "value": portable}
            except (TypeError, ValueError):
                data = {"media_type": "text/plain", "value": "<display payload excluded>"}
            add({"kind": "display", "data": data})

        def artifact(
            name: str,
            content: str | bytes,
            media_type: str = "application/octet-stream",
        ) -> dict[str, Any]:
            if not artifact_enabled:
                raise RuntimeError("no host artifact sink is configured")
            payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            if len(payload) > 1_048_576:
                raise RuntimeError("artifact payload exceeds 1048576 bytes")
            if len(artifacts) >= max_events - 2:
                raise RuntimeError("artifact event limit exceeded")
            index = len(artifacts)
            artifacts.append(
                {
                    "name": str(name),
                    "media_type": str(media_type),
                    "content_base64": base64.b64encode(payload).decode("ascii"),
                }
            )
            add({"kind": "artifact", "artifact_index": index})
            return {"artifact_index": index}

        def broker(value: Any) -> Any:
            if not broker_enabled:
                raise RuntimeError("no host broker is configured")
            payload = _portable_value(value)
            encoded = canonical_json_bytes(payload)
            if len(encoded) > 65_536:
                raise RuntimeError("broker request exceeds 65536 bytes")
            wire_stdout.write(
                json.dumps(
                    {"broker_request": payload},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            wire_stdout.flush()
            prepared_token: dict[str, Any] | None = None
            committed_receipt: dict[str, Any] | None = None
            while True:
                line = wire_stdin.readline()
                if not line:
                    raise RuntimeError("host broker response stream closed")
                response = json.loads(line)
                if not isinstance(response, dict):
                    raise RuntimeError("host broker response is not an object")
                if "rebind_prepare" in response:
                    frame = WorkspaceBrokerFrame.model_validate(
                        response["rebind_prepare"], strict=True
                    )
                    document = dict(frame.root)
                    if document["kind"] != "rebind_prepare":
                        raise RuntimeError("host rebind prepare frame kind changed")
                    token = dict(document["payload"]["token"])
                    if prepared_token is not None and prepared_token != token:
                        raise RuntimeError("host rebind prepare token changed")
                    prepared_token = token
                    add({"kind": "progress", "data": {"phase": "rebind_prepared"}})
                    continue
                if "broker_receipt" in response:
                    frame = WorkspaceBrokerFrame.model_validate(
                        response["broker_receipt"], strict=True
                    )
                    document = dict(frame.root)
                    if document["kind"] != "broker_receipt" or prepared_token is None:
                        raise RuntimeError("host broker receipt arrived before rebind prepare")
                    if (
                        document["attempt_id"] != prepared_token["successor_attempt_id"]
                        or document["attempt_fence"] != prepared_token["successor_attempt_fence"]
                        or document["cell_execution_id"] != prepared_token["cell_execution_id"]
                    ):
                        raise RuntimeError("host broker receipt authority changed")
                    receipt = dict(document["payload"])
                    if receipt["receipt_digest"] != prepared_token["settled_receipt_digest"]:
                        raise RuntimeError("host broker receipt settlement digest changed")
                    committed_receipt = receipt
                    continue
                if "rebind_commit" in response:
                    frame = WorkspaceBrokerFrame.model_validate(
                        response["rebind_commit"], strict=True
                    )
                    document = dict(frame.root)
                    if (
                        document["kind"] != "rebind_commit"
                        or prepared_token is None
                        or committed_receipt is None
                    ):
                        raise RuntimeError("host rebind commit has no prepared receipt")
                    token = dict(document["payload"]["token"])
                    if token != prepared_token:
                        raise RuntimeError("host rebind commit token changed")
                    ack_material = {
                        "token_digest": token["token_digest"],
                        "acknowledged_phase": "committed",
                        "worker_process_identity_digest": token["worker_process_identity_digest"],
                        "observed_workspace_revision": token["workspace_revision"],
                        "live_stack_resumed": True,
                    }
                    ack_payload = {
                        **ack_material,
                        "ack_digest": canonical_sha256(ack_material),
                    }
                    ack_document = {
                        "schema_version": "aar.workspace-broker-frame.v1",
                        "kind": "rebind_ack",
                        "direction": "worker_to_supervisor",
                        "committed": False,
                        "operation": document["operation"],
                        "attempt_id": document["attempt_id"],
                        "attempt_fence": document["attempt_fence"],
                        "workspace": document["workspace"],
                        "workspace_generation": document["workspace_generation"],
                        "workspace_revision": document["workspace_revision"],
                        "cell_execution_id": document["cell_execution_id"],
                        "frame_sequence": int(document["frame_sequence"]) + 1,
                        "broker_call_ordinal": None,
                        "deadline_unix_ms": document["deadline_unix_ms"],
                        "payload": ack_payload,
                        "payload_digest": canonical_sha256(ack_payload),
                        "payload_bytes": len(canonical_json_bytes(ack_payload)),
                    }
                    acknowledgement = WorkspaceBrokerFrame.model_validate(ack_document, strict=True)
                    wire_stdout.write(
                        json.dumps(
                            {"rebind_ack": dict(acknowledgement.root)},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    wire_stdout.flush()
                    observation = dict(committed_receipt["observation"])
                    outcome = observation.get("outcome")
                    ticket_state = (
                        "settled_success"
                        if outcome == "succeeded"
                        else (
                            "cancelled_certain"
                            if outcome in {"cancelled", "canceled"}
                            else "settled_failure"
                        )
                    )
                    add({"kind": "progress", "data": {"phase": "broker_resumed"}})
                    return {
                        "ticket_id": token["ticket_id"],
                        "ticket_state": ticket_state,
                        "observation": observation,
                        "receipt_digest": committed_receipt["receipt_digest"],
                    }
                if "broker_error" in response:
                    error = response["broker_error"]
                    raise RuntimeError(
                        f"host broker failed: {error.get('type')}: {error.get('message')}"
                    )
                if "broker_response" not in response:
                    raise RuntimeError("host broker response payload is missing")
                add({"kind": "progress", "data": {"phase": "broker_resumed"}})
                return response["broker_response"]

        def publish(data: Any = None, **_kwargs: Any) -> None:
            add(
                {
                    "kind": "display",
                    "data": {"mime_bundle": _bounded_display_data(dict(data or {}))},
                }
            )

        reserved = {
            "aar_artifact": artifact,
            "aar_broker": broker,
            "aar_display": display,
            "aar_progress": progress,
        }
        sentinel = object()
        previous = {name: self.shell.user_ns.get(name, sentinel) for name in reserved}
        self.shell.user_ns.update(reserved)
        add({"kind": "progress", "data": {"phase": "started"}})
        remaining = [max_output_chars]
        stdout = _BoundedStream(remaining)
        stderr = _BoundedStream(remaining)
        original_publish = self.shell.display_pub.publish
        try:
            self.shell.display_pub.publish = publish
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = self.shell.run_cell(
                    str(request["code"]),
                    store_history=False,
                    silent=False,
                )
        finally:
            self.shell.display_pub.publish = original_publish
            for name, prior in previous.items():
                if prior is sentinel:
                    self.shell.user_ns.pop(name, None)
                else:
                    self.shell.user_ns[name] = prior

        for kind, stream in (("stdout", stdout), ("stderr", stderr)):
            text = stream.text
            if not text:
                continue
            add({"kind": kind, "text": text, "truncated": stream.truncated})
            if stream.truncated:
                dropped = True

        error = result.error_before_exec or result.error_in_exec
        status = "failed" if error is not None else "succeeded"
        if error is not None:
            add(
                {
                    "kind": "exception",
                    "data": {
                        "type": type(error).__name__,
                        "message": str(error)[:2_048],
                    },
                },
                force=True,
            )
        portable_result = None
        excluded_reason = None
        if status == "succeeded" and result.result is not None:
            try:
                portable_result = _portable_value(result.result)
                if len(canonical_json_bytes(portable_result)) > 65_536:
                    raise _PortableValueError("portable result exceeds 65536 bytes")
            except _PortableValueError as error:
                portable_result = None
                excluded_reason = str(error)
        finish: dict[str, Any] = {"phase": "finished", "status": status}
        if dropped:
            finish["events_truncated"] = True
        events.append(
            {
                "kind": "progress",
                "data": finish,
                "truncated": dropped,
            }
        )
        return {
            "ok": True,
            "status": status,
            "result": portable_result,
            "result_excluded_reason": excluded_reason,
            "events": events,
            "artifacts": artifacts,
        }

    def inspect(self) -> list[dict[str, Any]]:
        return [
            PlainPythonWorkspaceBackend._variable_summary(name, value).model_dump(mode="json")
            for name, value in sorted(self.shell.user_ns.items())
            if self._is_user_name(name)
        ]

    def checkpoint(self, policy: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
        values: list[dict[str, Any]] = []
        exclusions: list[dict[str, Any]] = []
        consumed_bytes = 0
        for name, value in sorted(self.shell.user_ns.items()):
            if not self._is_user_name(name):
                continue
            if len(values) >= int(policy["max_values"]):
                exclusions.append(self._exclusion(name, value, "checkpoint value limit exceeded"))
                continue
            try:
                portable = _portable_value(
                    value,
                    max_depth=int(policy["max_depth"]),
                    max_collection_items=int(policy["max_collection_items"]),
                )
                encoded = canonical_json_bytes(portable)
                if consumed_bytes + len(encoded) > int(policy["max_bytes"]):
                    raise _PortableValueError("checkpoint byte limit exceeded")
                consumed_bytes += len(encoded)
                values.append({"name": name, "value": portable})
            except _PortableValueError as error:
                exclusions.append(self._exclusion(name, value, str(error)))
        return {"values": values, "exclusions": exclusions}

    @staticmethod
    def _exclusion(name: str, value: Any, reason: str) -> dict[str, str]:
        return {
            "name": name,
            "type_name": type(value).__name__[:128],
            "reason": reason[:256],
        }

    def restore(self, values: list[dict[str, Any]]) -> None:
        for name in tuple(self.shell.user_ns):
            if self._is_user_name(name):
                self.shell.user_ns.pop(name, None)
        for item in values:
            self.shell.user_ns[str(item["name"])] = item["value"]


def main() -> int:
    worker = _Worker()
    _write({"ok": True, "ready": True, "ipython_version": IPython.__version__})
    for line in sys.stdin:
        should_close = False
        try:
            request = json.loads(line)
            response = worker.dispatch(request)
            should_close = bool(response.pop("closing", False))
        except BaseException as error:
            response = {
                "ok": False,
                "error_type": type(error).__name__,
                "message": str(error)[:2_048],
            }
        _write(response)
        if should_close:
            break
    InteractiveShell.clear_instance()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
