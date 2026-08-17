#!/usr/bin/env python3
# ruff: noqa: E501
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
CONTRACTS = ROOT / "contracts"
FIXTURES = ROOT / "fixtures"
BASELINE_TOOL_MANIFEST_SHA256 = "6ebf848eee74795771af23dfaaeb70fd5030cb88878327f76ffc10d1c4639ab8"
TARGET_PACKAGE_VERSION = "0.5.0a0"
DRAFT = "https://json-schema.org/draft/2020-12/schema"


def strict_object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    max_properties: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required if required is not None else list(properties),
    }
    if max_properties is not None:
        result["maxProperties"] = max_properties
    return result


def normalize_required_lists(value: Any) -> None:
    if isinstance(value, dict):
        required = value.get("required")
        if isinstance(required, list):
            value["required"] = sorted(required)
        for child in value.values():
            normalize_required_lists(child)
    elif isinstance(value, list):
        for child in value:
            normalize_required_lists(child)


def const(value: Any) -> dict[str, Any]:
    if value is None:
        value_type = "null"
    elif isinstance(value, bool):
        value_type = "boolean"
    elif isinstance(value, int):
        value_type = "integer"
    elif isinstance(value, float):
        value_type = "number"
    elif isinstance(value, str):
        value_type = "string"
    else:
        raise TypeError(f"unsupported const type: {type(value).__name__}")
    return {"const": value, "type": value_type}


def ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/$defs/{name}"}


def external(path: str, name: str) -> dict[str, Any]:
    return {"$ref": f"{path}#/$defs/{name}"}


def array(items: dict[str, Any], *, minimum: int = 0, maximum: int) -> dict[str, Any]:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


DIGEST = {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"}
COMMIT_SHA = {"type": "string", "pattern": r"^[0-9a-f]{40}$"}
IDENTITY = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._~-]*$",
}
CAPABILITY = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
}
MODEL_ROUTE_VALUE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._~:/+\\-]*$",
}
POSITIVE = {"type": "integer", "minimum": 1, "maximum": 9_223_372_036_854_775_807}
COUNTER = {"type": "integer", "minimum": 0, "maximum": 9_223_372_036_854_775_807}
REVISION = COUNTER
TIMESTAMP_MS = {"type": "integer", "minimum": 0, "maximum": 9_223_372_036_854_775_807}

model_route_binding = strict_object(
    {
        "schema_version": const("aar.model-route.v1"),
        "profile_id": MODEL_ROUTE_VALUE,
        "catalog_digest": DIGEST,
        "profile_digest": DIGEST,
        "provider_driver": MODEL_ROUTE_VALUE,
        "provider": MODEL_ROUTE_VALUE,
        "model": MODEL_ROUTE_VALUE,
        "reasoning_effort": {"oneOf": [MODEL_ROUTE_VALUE, {"type": "null"}]},
        "max_output_tokens": POSITIVE,
        "fallback_policy": enum("none", "explicit"),
        "cache_policy": enum("disabled", "run-scoped", "provider-managed"),
    }
)

workbench_route_binding = deepcopy(model_route_binding)
workbench_route_binding["properties"]["fallback_policy"] = const("none")

operation_ref = strict_object(
    {
        "type": const("operation"),
        "value": IDENTITY,
    }
)
workspace_ref = strict_object({"type": const("workspace"), "value": IDENTITY})

metadata_value = {
    "oneOf": [
        {"type": "string", "maxLength": 256},
        {"type": "integer", "minimum": -9_007_199_254_740_991, "maximum": 9_007_199_254_740_991},
        {"type": "boolean"},
        {"type": "null"},
    ]
}
metadata = {
    "type": "object",
    "additionalProperties": metadata_value,
    "maxProperties": 16,
    "propertyNames": {
        "pattern": r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
        "maxLength": 64,
    },
}

json_contract = strict_object(
    {
        "schema_version": const("aar.json-contract.v1"),
        "dialect": const("https://json-schema.org/draft/2020-12/schema"),
        "profile": const("aar.json-schema-profile.v1"),
        "schema_digest": DIGEST,
        "schema": {"type": "object", "maxProperties": 128},
        "max_instance_bytes": {"type": "integer", "minimum": 1, "maximum": 1_048_576},
    }
)

workspace_policy = strict_object(
    {
        "mode": const("operation_scoped"),
        "backend": const("ipython"),
        "security_profile": enum("trusted_local", "managed_restricted"),
        "seed_checkpoint": {
            "oneOf": [
                strict_object(
                    {
                        "manifest_digest": DIGEST,
                        "format": const("aar.workspace-checkpoint.v1"),
                    }
                ),
                {"type": "null"},
            ]
        },
        "terminal_disposition": enum("close_after_terminal_checkpoint", "retain_checkpoint"),
    }
)

model_policy = strict_object(
    {
        "execution_mode": enum("caller_delegated", "service_managed"),
        "route_binding": workbench_route_binding,
        "max_corrections": {"type": "integer", "minimum": 0, "maximum": 8},
    }
)

budget_policy = strict_object(
    {
        "total_wall_time_ms": {"type": "integer", "minimum": 1_000, "maximum": 900_000},
        "cell_wall_time_ms": {"type": "integer", "minimum": 1, "maximum": 60_000},
        "max_cells": {"type": "integer", "minimum": 1, "maximum": 128},
        "max_model_calls": {"type": "integer", "minimum": 1, "maximum": 128},
        "max_subagent_calls": {"type": "integer", "minimum": 0, "maximum": 64},
        "max_recursion_depth": {"type": "integer", "minimum": 0, "maximum": 8},
        "max_artifact_count": {"type": "integer", "minimum": 0, "maximum": 256},
        "max_artifact_bytes": {"type": "integer", "minimum": 0, "maximum": 33_554_432},
        "max_result_bytes": {"type": "integer", "minimum": 1, "maximum": 1_048_576},
        "max_events_per_cell": {"type": "integer", "minimum": 1, "maximum": 512},
        "max_output_chars_per_cell": {"type": "integer", "minimum": 1, "maximum": 262_144},
    }
)

artifact_policy = strict_object(
    {
        "publication": const("finalization_manifest"),
        "duplicate_name_policy": enum("reject"),
        "allowed_media_types": array(
            {"type": "string", "minLength": 1, "maxLength": 128}, minimum=1, maximum=64
        ),
    }
)

completion_policy = strict_object(
    {
        "output_contract": json_contract,
        "require_named_artifacts": {"type": "boolean"},
        "allow_optional_live_children": {"type": "boolean", "const": False},
    }
)

rlm_directive = {
    "$schema": DRAFT,
    "oneOf": [
        strict_object(
            {
                "kind": const("execute_cell"),
                "code": {"type": "string", "minLength": 1, "maxLength": 65_536},
                "expected_result_hint": {"oneOf": [{"type": "string", "maxLength": 4_096}, {"type": "null"}]},
            }
        ),
        strict_object(
            {
                "kind": const("finalize"),
                "output": {},
                "artifact_stage_ids": array(IDENTITY, maximum=256),
            }
        ),
        strict_object(
            {
                "kind": const("abstain"),
                "reason": {"type": "string", "minLength": 1, "maxLength": 4_096},
            }
        ),
    ]
}
normalize_required_lists(rlm_directive)

recovery_planner_input = strict_object(
    {
        "schema_version": const("aar.rlm-recovery-planner-input.v1"),
        "operation": operation_ref,
        "failed_cell_execution_id": IDENTITY,
        "failed_cell_source_digest": DIGEST,
        "pre_cell_checkpoint_digest": DIGEST,
        "suspension_revision": REVISION,
        "settled_ticket_receipt_digests": array(DIGEST, minimum=1, maximum=64),
        "committed_artifact_binding_digests": array(DIGEST, maximum=64),
        "committed_event_digests": array(DIGEST, maximum=256),
        "failure_code": enum("worker_lost", "supervisor_restarted", "rebind_failed", "stack_unavailable"),
        "remaining_model_calls": {"type": "integer", "minimum": 1, "maximum": 64},
        "remaining_wall_time_ms": {"type": "integer", "minimum": 1, "maximum": 900_000},
    }
)

job_spec = strict_object(
    {
        "schema_version": const("aar.rlm-workbench-job.v1"),
        "objective": {"type": "string", "minLength": 1, "maxLength": 65_536},
        "strategy": const("python_workbench"),
        "workspace": workspace_policy,
        "model": model_policy,
        "budgets": budget_policy,
        "artifacts": artifact_policy,
        "completion": completion_policy,
        "metadata": metadata,
    }
)

mutation_context = strict_object(
    {
        "schema_version": const("aar.mcp-rlm-workbench-context.v1"),
        "principal_id": IDENTITY,
        "session_id": IDENTITY,
        "runtime_generation": POSITIVE,
        "capability_digest": DIGEST,
        "request_id": IDENTITY,
        "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128},
        "deadline_unix_ms": TIMESTAMP_MS,
        "grant_ids": array(IDENTITY, minimum=1, maximum=64),
        "budget_wall_time_ms": {"type": "integer", "minimum": 1_000, "maximum": 900_000},
        "budget_model_requests": {"type": "integer", "minimum": 1, "maximum": 128},
        "budget_input_tokens": COUNTER,
        "budget_output_tokens": COUNTER,
        "budget_child_operations": {"type": "integer", "minimum": 0, "maximum": 64},
        "budget_artifact_bytes": {"type": "integer", "minimum": 0, "maximum": 33_554_432},
    }
)

broker_context_v2 = strict_object(
    {
        "schema_version": const("aar.broker-context.v2"),
        "operation": operation_ref,
        "attempt_id": IDENTITY,
        "attempt_fence": DIGEST,
        "workspace": workspace_ref,
        "workspace_generation": POSITIVE,
        "workspace_revision_before": REVISION,
        "cell_execution_id": IDENTITY,
        "broker_call_ordinal": COUNTER,
        "contract_id": IDENTITY,
        "grant_id": IDENTITY,
        "deadline_unix_ms": TIMESTAMP_MS,
        "idempotency_key": IDENTITY,
    }
)

execute_input = strict_object(
    {
        "context": mutation_context,
        "spec": job_spec,
        "start_only": {"type": "boolean"},
    }
)
execute_input["allOf"] = [
    {
        "if": {
            "properties": {
                "spec": {
                    "properties": {
                        "model": {
                            "properties": {"execution_mode": {"const": "caller_delegated"}}
                        }
                    }
                }
            }
        },
        "then": {"properties": {"start_only": {"const": True}}},
    }
]

workbench_phase = enum(
    "accepted",
    "preparing_workspace",
    "running",
    "waiting_external",
    "checkpointing",
    "finalizing",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "indeterminate",
    "parked",
)
legacy_operation_state = enum(
    "accepted", "running", "succeeded", "failed", "cancelled", "timed_out", "indeterminate"
)

phase_projection = {
    "oneOf": [
        strict_object({"phase": const(phase), "legacy_operation_state": const(state)})
        for phase, state in (
            ("accepted", "accepted"),
            ("preparing_workspace", "accepted"),
            ("waiting_external", "accepted"),
            ("running", "running"),
            ("checkpointing", "running"),
            ("finalizing", "running"),
            ("succeeded", "succeeded"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
            ("timed_out", "timed_out"),
            ("indeterminate", "indeterminate"),
            ("parked", "indeterminate"),
        )
    ]
}

workbench_event = strict_object(
    {
        "schema_version": const("aar.rlm-workbench-event.v1"),
        "operation": operation_ref,
        "event_sequence": COUNTER,
        "phase": workbench_phase,
        "kind": enum(
            "phase_changed",
            "cell_started",
            "cell_suspended",
            "cell_resumed",
            "cell_lost",
            "caller_work_available",
            "caller_work_settled",
            "artifact_staged",
            "artifact_committed",
            "recovery_planned",
            "finalized",
            "failure",
        ),
        "payload_digest": DIGEST,
        "payload": {"type": "object", "additionalProperties": metadata_value, "maxProperties": 32},
    }
)

workspace_binding = strict_object(
    {
        "workspace": workspace_ref,
        "generation": POSITIVE,
        "revision": REVISION,
        "checkpoint_digest": {"oneOf": [DIGEST, {"type": "null"}]},
    }
)

child_lineage = strict_object(
    {
        "child_execution_id": IDENTITY,
        "accepted_handle_digest": DIGEST,
        "terminal_state": enum("succeeded", "failed", "cancelled", "timed_out", "indeterminate"),
        "result_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "artifact_reference_digests": array(DIGEST, maximum=64),
    }
)

workbench_result = strict_object(
    {
        "schema_version": const("aar.rlm-workbench-result.v1"),
        "operation": operation_ref,
        "terminal_status": const("succeeded"),
        "certainty": const("certain"),
        "output": {},
        "output_digest": DIGEST,
        "artifact_bindings": array(
            external("aar-artifact-publication-v1.schema.json", "ArtifactBinding"), maximum=256
        ),
        "child_lineage": array(child_lineage, maximum=64),
        "unresolved_required_children": const(0),
        "model_calls": COUNTER,
        "subagent_calls": COUNTER,
        "python_cells": COUNTER,
        "recovery_planner_calls": COUNTER,
        "input_tokens": COUNTER,
        "output_tokens": COUNTER,
        "step_trace_digest": DIGEST,
        "broker_trace_digest": DIGEST,
        "usage_receipt_digests": array(DIGEST, maximum=256),
        "workspace_disposition": enum("closed", "retained_checkpoint"),
        "final_checkpoint_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "result_digest": DIGEST,
    }
)

failure = strict_object(
    {
        "schema_version": const("aar.envelope.v1"),
        "category": enum(
            "validation",
            "authority",
            "budget",
            "deadline",
            "conflict",
            "stale",
            "cancelled",
            "transport",
            "worker",
            "internal",
        ),
        "code": enum(
            "INVALID_ARGUMENT",
            "CONTEXT_INVALID",
            "CAPABILITY_UNAVAILABLE",
            "GRANT_DENIED",
            "BUDGET_EXCEEDED",
            "CONFLICT",
            "NOT_FOUND",
            "ROUTE_UNAVAILABLE",
            "CALLER_WORK_CONFLICT",
            "CALLER_WORK_OUTCOME_UNKNOWN",
            "WORKSPACE_FAILED",
            "WORKER_LOST",
            "RECOVERY_PLANNER_FAILED",
            "ARTIFACT_PUBLICATION_FAILED",
            "CANCELLATION_INDETERMINATE",
            "DEADLINE_EXCEEDED",
            "RECONCILIATION_REQUIRED",
            "INTERNAL_ERROR",
        ),
        "message": {"type": "string", "minLength": 1, "maxLength": 512},
        "retryable": {"type": "boolean"},
        "certainty": enum("certain", "indeterminate"),
        "operation": {"oneOf": [operation_ref, {"type": "null"}]},
        "details": array(
            strict_object(
                {
                    "name": CAPABILITY,
                    "value": {"oneOf": [
                        {"type": "string", "maxLength": 512},
                        {"type": "integer"},
                        {
                            "allOf": [
                                {"type": "number"},
                                {"not": {"type": "integer"}},
                            ]
                        },
                        {"type": "boolean"},
                        {"type": "null"},
                    ]},
                }
            ),
            maximum=32,
        ),
    }
)

snapshot = strict_object(
    {
        "schema_version": const("aar.rlm-workbench-snapshot.v1"),
        "operation": operation_ref,
        "revision": REVISION,
        "phase": workbench_phase,
        "legacy_projection": phase_projection,
        "workspace": {"oneOf": [workspace_binding, {"type": "null"}]},
        "pending_tickets": array(
            external("aar-caller-work-v1.schema.json", "CallerWorkTicketRef"), maximum=16
        ),
        "result": {"oneOf": [workbench_result, {"type": "null"}]},
        "failure": {"oneOf": [failure, {"type": "null"}]},
        "certainty": enum("certain", "indeterminate"),
        "updated_at_unix_ms": TIMESTAMP_MS,
    }
)
snapshot["allOf"] = [
    {
        "if": {"properties": {"phase": {"const": "waiting_external"}}, "required": ["phase"]},
        "then": {"properties": {"pending_tickets": {"minItems": 1}, "result": {"type": "null"}}},
    },
    {
        "if": {"properties": {"phase": {"const": "succeeded"}}, "required": ["phase"]},
        "then": {
            "properties": {
                "pending_tickets": {"maxItems": 0},
                "result": workbench_result,
                "failure": {"type": "null"},
                "certainty": {"const": "certain"},
            }
        },
    },
    {
        "if": {
            "properties": {"phase": {"enum": ["failed", "cancelled", "timed_out"]}},
            "required": ["phase"],
        },
        "then": {
            "properties": {
                "pending_tickets": {"maxItems": 0},
                "result": {"type": "null"},
                "failure": failure,
                "certainty": {"const": "certain"},
            }
        },
    },
    {
        "if": {
            "properties": {"phase": {"enum": ["indeterminate", "parked"]}},
            "required": ["phase"],
        },
        "then": {
            "properties": {
                "pending_tickets": {"maxItems": 0},
                "result": {"type": "null"},
                "failure": failure,
                "certainty": {"const": "indeterminate"},
            }
        },
    },
    {
        "if": {
            "properties": {
                "phase": {
                    "enum": ["accepted", "preparing_workspace", "running", "checkpointing", "finalizing"]
                }
            },
            "required": ["phase"],
        },
        "then": {"properties": {"pending_tickets": {"maxItems": 0}, "result": {"type": "null"}}},
    },
]

status_input = strict_object(
    {
        "context": strict_object(
            {
                "principal_id": IDENTITY,
                "session_id": IDENTITY,
                "runtime_generation": POSITIVE,
                "capability_digest": DIGEST,
                "deadline_unix_ms": TIMESTAMP_MS,
            }
        ),
        "operation": operation_ref,
        "expected_revision": {"oneOf": [REVISION, {"type": "null"}]},
    }
)
read_context = status_input["properties"]["context"]
workbench_capabilities_input = strict_object({"context": read_context})

backend_availability = strict_object(
    {
        "method": enum(
            "model.request",
            "subagent.submit",
            "subagent.result",
            "evidence.query",
            "effect.propose",
            "artifact.put",
        ),
        "contract_id": IDENTITY,
        "request_schema_digest": DIGEST,
        "response_schema_digest": DIGEST,
        "backend_kind": enum("caller_driver", "native", "reference", "unconfigured"),
        "configured": {"type": "boolean"},
        "reference_only": {"type": "boolean"},
        "adapter_id": {"oneOf": [IDENTITY, {"type": "null"}]},
        "adapter_generation": {"oneOf": [POSITIVE, {"type": "null"}]},
        "evidence_tier": enum("unknown", "caller_observed", "host_receipt_bound", "provider_attested"),
    }
)
backend_availability["allOf"] = [
    {
        "if": {"properties": {"backend_kind": {"const": "unconfigured"}}, "required": ["backend_kind"]},
        "then": {
            "properties": {
                "configured": {"const": False},
                "reference_only": {"const": False},
                "adapter_id": {"type": "null"},
                "adapter_generation": {"type": "null"},
                "evidence_tier": {"const": "unknown"},
            }
        },
    },
    {
        "if": {"properties": {"backend_kind": {"const": "reference"}}, "required": ["backend_kind"]},
        "then": {
            "properties": {
                "configured": {"const": True},
                "reference_only": {"const": True},
                "adapter_id": {"type": "null"},
                "adapter_generation": {"type": "null"},
                "evidence_tier": {"const": "unknown"},
            }
        },
    },
    {
        "if": {
            "properties": {"backend_kind": {"enum": ["caller_driver", "native"]}},
            "required": ["backend_kind"],
        },
        "then": {
            "properties": {
                "configured": {"const": True},
                "reference_only": {"const": False},
                "adapter_id": IDENTITY,
                "adapter_generation": POSITIVE,
            }
        },
    },
]
workbench_capability = strict_object(
    {
        "schema_version": const("aar.rlm-workbench-capability.v1"),
        "surface_version": const("aar.mcp-tools.v8"),
        "tool_surface_digest": DIGEST,
        "broker_catalog_digest": DIGEST,
        "planner_directive_schema_version": const("aar.rlm-directive.v1"),
        "planner_directive_schema_digest": DIGEST,
        "security_profiles": array(
            enum("trusted_local", "managed_restricted"), minimum=1, maximum=2
        ),
        "methods": {
            "type": "array",
            "items": backend_availability,
            "minItems": 6,
            "maxItems": 6,
            "uniqueItems": True,
        },
    }
)
background_actor_state = strict_object(
    {
        "actor_id": IDENTITY,
        "kind": enum("lease_keeper", "ticket_sweeper", "deadline_sweeper", "reconciler", "worker_supervisor"),
        "state": enum("starting", "running", "stopping", "stopped", "failed"),
        "generation": POSITIVE,
        "last_heartbeat_unix_ms": {"oneOf": [TIMESTAMP_MS, {"type": "null"}]},
        "failure_digest": {"oneOf": [DIGEST, {"type": "null"}]},
    }
)
runtime_resource_owner_snapshot = strict_object(
    {
        "schema_version": const("aar.runtime-resource-owner.v1"),
        "owner_id": IDENTITY,
        "owner_generation": POSITIVE,
        "state": enum("starting", "running", "stopping", "stopped", "failed"),
        "accepting_new_work": {"type": "boolean"},
        "actors": array(background_actor_state, maximum=64),
        "live_worker_count": COUNTER,
        "waiting_ticket_count": COUNTER,
        "shutdown_deadline_unix_ms": {"oneOf": [TIMESTAMP_MS, {"type": "null"}]},
        "revision": REVISION,
    }
)
workbench_execute_outcome = {"oneOf": [snapshot, failure]}
workbench_status_outcome = {"oneOf": [snapshot, failure]}
workbench_capability_outcome = {"oneOf": [workbench_capability, failure]}

workbench_contract = {
    "$schema": DRAFT,
    "$id": "aar-rlm-workbench-v1.schema.json",
    "title": "AAR RLM-native workbench contracts v1",
    "$defs": {
        "Digest": DIGEST,
        "Identity": IDENTITY,
        "CapabilityName": CAPABILITY,
        "OperationRef": operation_ref,
        "WorkspaceRef": workspace_ref,
        "ModelRouteBinding": model_route_binding,
        "JsonContract": json_contract,
        "RlmDirective": rlm_directive,
        "RecoveryPlannerInput": recovery_planner_input,
        "RlmWorkbenchJobSpec": job_spec,
        "McpRlmWorkbenchMutationContext": mutation_context,
        "BrokerContextV2": broker_context_v2,
        "RlmWorkbenchExecuteInput": execute_input,
        "RlmWorkbenchPhase": workbench_phase,
        "LegacyOperationStateV1": legacy_operation_state,
        "PhaseProjection": phase_projection,
        "WorkbenchEvent": workbench_event,
        "WorkspaceBinding": workspace_binding,
        "ChildLineage": child_lineage,
        "RlmWorkbenchResult": workbench_result,
        "Failure": failure,
        "RlmWorkbenchSnapshot": snapshot,
        "RlmWorkbenchStatusInput": status_input,
        "RlmWorkbenchCapabilitiesInput": workbench_capabilities_input,
        "BackendAvailability": backend_availability,
        "RlmWorkbenchCapability": workbench_capability,
        "BackgroundActorState": background_actor_state,
        "RuntimeResourceOwnerSnapshot": runtime_resource_owner_snapshot,
        "RlmWorkbenchExecuteOutcome": workbench_execute_outcome,
        "RlmWorkbenchStatusOutcome": workbench_status_outcome,
        "RlmWorkbenchCapabilityOutcome": workbench_capability_outcome,
    },
    "oneOf": [ref("RlmWorkbenchExecuteInput"), ref("RlmWorkbenchSnapshot"), ref("RlmWorkbenchResult")],
}

logical_owner = {
    "oneOf": [
        strict_object(
            {
                "kind": const("planner"),
                "phase": enum("initial", "correction", "recovery", "finalizer"),
                "step_index": COUNTER,
            }
        ),
        strict_object(
            {
                "kind": const("cell"),
                "cell_execution_id": IDENTITY,
                "broker_call_ordinal": COUNTER,
            }
        ),
    ]
}

model_request_v2 = strict_object(
    {
        "contract_id": const("aar.broker-contract.model-request.v2"),
        "method": const("model.request"),
        "prompt": {"type": "string", "minLength": 1, "maxLength": 65_536},
        "response_contract": json_contract,
        "route_binding": model_route_binding,
        "max_output_bytes": {"type": "integer", "minimum": 1, "maximum": 1_048_576},
    }
)
subagent_submit_v2 = strict_object(
    {
        "contract_id": const("aar.broker-contract.subagent-submit.v2"),
        "method": const("subagent.submit"),
        "task": {"type": "string", "minLength": 1, "maxLength": 65_536},
        "child_profile_id": IDENTITY,
        "result_contract": json_contract,
        "max_depth": {"type": "integer", "minimum": 0, "maximum": 8},
        "required": {"type": "boolean", "const": True},
    }
)
subagent_result_v2 = strict_object(
    {
        "contract_id": const("aar.broker-contract.subagent-result.v2"),
        "method": const("subagent.result"),
        "child_execution_id": IDENTITY,
        "result_contract_digest": DIGEST,
    }
)
evidence_query_v2 = strict_object(
    {
        "contract_id": const("aar.broker-contract.evidence-query.v2"),
        "method": const("evidence.query"),
        "query": {"type": "string", "minLength": 1, "maxLength": 8192},
        "max_records": {"type": "integer", "minimum": 1, "maximum": 64},
    }
)
effect_propose_v2 = strict_object(
    {
        "contract_id": const("aar.broker-contract.effect-propose.v2"),
        "method": const("effect.propose"),
        "effect_type": CAPABILITY,
        "payload_digest": DIGEST,
        "payload_binding": external("aar-artifact-publication-v1.schema.json", "ArtifactBinding"),
        "summary": {"type": "string", "minLength": 1, "maxLength": 2048},
    }
)
request_union = {
    "oneOf": [
        model_request_v2,
        subagent_submit_v2,
        subagent_result_v2,
        evidence_query_v2,
        effect_propose_v2,
    ]
}

caller_ticket_state = enum(
    "pending",
    "send_reserved",
    "send_started",
    "settled_success",
    "settled_failure",
    "outcome_unknown",
    "cancel_requested",
    "cancelled_before_send",
    "cancelled_certain",
    "quarantined",
)

claimant = strict_object(
    {
        "principal_id": IDENTITY,
        "session_id": IDENTITY,
        "adapter_id": IDENTITY,
        "adapter_generation": POSITIVE,
        "claim_id": IDENTITY,
        "claim_fence": DIGEST,
        "claim_expires_at_unix_ms": TIMESTAMP_MS,
    }
)
physical_attempt = strict_object(
    {
        "physical_attempt_id": IDENTITY,
        "provider_or_child_idempotency_key": IDENTITY,
        "lookup_supported": {"type": "boolean"},
        "cancel_supported": {"type": "boolean"},
        "send_started_at_unix_ms": {"oneOf": [TIMESTAMP_MS, {"type": "null"}]},
        "sent_request_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "sent_at_unix_ms": {"oneOf": [TIMESTAMP_MS, {"type": "null"}]},
        "provider_or_child_request_id": {"oneOf": [IDENTITY, {"type": "null"}]},
    }
)

ticket_ref = strict_object(
    {
        "ticket_id": IDENTITY,
        "revision": REVISION,
        "ticket_digest": DIGEST,
        "state": caller_ticket_state,
    }
)

ticket = strict_object(
    {
        "schema_version": const("aar.caller-work-ticket.v1"),
        "operation": operation_ref,
        "suspension_revision": REVISION,
        "ticket_id": IDENTITY,
        "revision": REVISION,
        "owner": logical_owner,
        "request": request_union,
        "request_digest": DIGEST,
        "ticket_digest": DIGEST,
        "state": caller_ticket_state,
        "claimant": {"oneOf": [claimant, {"type": "null"}]},
        "physical_attempt": {"oneOf": [physical_attempt, {"type": "null"}]},
        "settled_receipt_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "settled_at_unix_ms": {"oneOf": [TIMESTAMP_MS, {"type": "null"}]},
        "deadline_unix_ms": TIMESTAMP_MS,
    }
)
ticket["allOf"] = [
    {
        "if": {"properties": {"state": {"const": "pending"}}, "required": ["state"]},
        "then": {"properties": {"claimant": {"type": "null"}, "physical_attempt": {"type": "null"}}},
    },
    {
        "if": {"properties": {"state": {"const": "send_reserved"}}, "required": ["state"]},
        "then": {
            "properties": {
                "claimant": claimant,
                "physical_attempt": {
                    "allOf": [
                        physical_attempt,
                        {
                            "properties": {
                                "send_started_at_unix_ms": {"type": "null"},
                                "sent_request_digest": {"type": "null"},
                                "sent_at_unix_ms": {"type": "null"},
                                "provider_or_child_request_id": {"type": "null"},
                            }
                        },
                    ]
                },
            }
        },
    },
    {
        "if": {
            "properties": {"state": {"const": "cancelled_before_send"}},
            "required": ["state"],
        },
        "then": {
            "properties": {
                "settled_receipt_digest": DIGEST,
                "settled_at_unix_ms": TIMESTAMP_MS,
            },
            "oneOf": [
                {
                    "properties": {
                        "claimant": {"type": "null"},
                        "physical_attempt": {"type": "null"},
                    }
                },
                {
                    "properties": {
                        "claimant": claimant,
                        "physical_attempt": {
                            "allOf": [
                                physical_attempt,
                                {
                                    "properties": {
                                        "send_started_at_unix_ms": {"type": "null"},
                                        "sent_request_digest": {"type": "null"},
                                        "sent_at_unix_ms": {"type": "null"},
                                        "provider_or_child_request_id": {"type": "null"},
                                    }
                                },
                            ]
                        },
                    }
                },
            ],
        },
    },
    {
        "if": {
            "properties": {
                "state": {
                    "enum": [
                        "send_started",
                        "settled_success",
                        "settled_failure",
                        "outcome_unknown",
                        "cancel_requested",
                        "cancelled_certain",
                        "quarantined",
                    ]
                }
            },
            "required": ["state"],
        },
        "then": {
            "properties": {
                "claimant": claimant,
                "physical_attempt": {
                    "allOf": [
                        physical_attempt,
                        {
                            "properties": {
                                "send_started_at_unix_ms": TIMESTAMP_MS,
                                "sent_request_digest": DIGEST,
                            }
                        },
                    ]
                },
            }
        },
    },
    {
        "if": {
            "properties": {
                "state": {
                    "enum": [
                        "pending",
                        "send_reserved",
                        "send_started",
                        "outcome_unknown",
                        "cancel_requested",
                        "quarantined",
                    ]
                }
            },
            "required": ["state"],
        },
        "then": {
            "properties": {
                "settled_receipt_digest": {"type": "null"},
                "settled_at_unix_ms": {"type": "null"},
            }
        },
    },
    {
        "if": {
            "properties": {
                "state": {
                    "enum": [
                        "settled_success",
                        "settled_failure",
                        "cancelled_before_send",
                        "cancelled_certain",
                    ]
                }
            },
            "required": ["state"],
        },
        "then": {
            "properties": {
                "settled_receipt_digest": DIGEST,
                "settled_at_unix_ms": TIMESTAMP_MS,
            }
        },
    },
]

caller_context = external("aar-rlm-workbench-v1.schema.json", "McpRlmWorkbenchMutationContext")
claim_input = strict_object(
    {
        "context": caller_context,
        "operation": operation_ref,
        "expected_control_revision": REVISION,
        "expected_cancellation_revision": REVISION,
        "expected_suspension_revision": REVISION,
        "expected_cumulative_deadline_unix_ms": TIMESTAMP_MS,
        "ticket_id": IDENTITY,
        "expected_revision": REVISION,
        "ticket_digest": DIGEST,
        "adapter_id": IDENTITY,
        "adapter_generation": POSITIVE,
        "claim_lease_ms": {"type": "integer", "minimum": 1_000, "maximum": 60_000},
        "idempotency_key": IDENTITY,
    }
)
mark_send_input = strict_object(
    {
        "context": caller_context,
        "operation": operation_ref,
        "expected_control_revision": REVISION,
        "expected_cancellation_revision": REVISION,
        "expected_suspension_revision": REVISION,
        "expected_cumulative_deadline_unix_ms": TIMESTAMP_MS,
        "ticket_id": IDENTITY,
        "expected_revision": REVISION,
        "ticket_digest": DIGEST,
        "claim_id": IDENTITY,
        "claim_fence": DIGEST,
        "physical_attempt_id": IDENTITY,
        "expected_claim_expires_at_unix_ms": TIMESTAMP_MS,
        "provider_or_child_idempotency_key": IDENTITY,
        "sent_request_digest": DIGEST,
        "lookup_supported": {"type": "boolean"},
        "cancel_supported": {"type": "boolean"},
        "idempotency_key": IDENTITY,
    }
)
cancel_before_send_input = strict_object(
    {
        "context": caller_context,
        "operation": operation_ref,
        "expected_control_revision": REVISION,
        "expected_cancellation_revision": REVISION,
        "expected_suspension_revision": REVISION,
        "expected_cumulative_deadline_unix_ms": TIMESTAMP_MS,
        "ticket_id": IDENTITY,
        "expected_revision": REVISION,
        "ticket_digest": DIGEST,
        "expected_pre_send_state": enum("pending", "send_reserved"),
        "claim_id": {"oneOf": [IDENTITY, {"type": "null"}]},
        "claim_fence": {"oneOf": [DIGEST, {"type": "null"}]},
        "physical_attempt_id": {"oneOf": [IDENTITY, {"type": "null"}]},
        "expected_claim_expires_at_unix_ms": {
            "oneOf": [TIMESTAMP_MS, {"type": "null"}]
        },
        "settled_receipt_digest": DIGEST,
        "settled_at_unix_ms": TIMESTAMP_MS,
        "reason": enum("user_requested", "deadline", "operation_cancel", "shutdown"),
        "idempotency_key": IDENTITY,
    }
)
cancel_before_send_input["allOf"] = [
    {
        "if": {
            "properties": {"expected_pre_send_state": {"const": "pending"}},
            "required": ["expected_pre_send_state"],
        },
        "then": {
            "properties": {
                "claim_id": {"type": "null"},
                "claim_fence": {"type": "null"},
                "physical_attempt_id": {"type": "null"},
                "expected_claim_expires_at_unix_ms": {"type": "null"},
            }
        },
    },
    {
        "if": {
            "properties": {"expected_pre_send_state": {"const": "send_reserved"}},
            "required": ["expected_pre_send_state"],
        },
        "then": {
            "properties": {
                "claim_id": IDENTITY,
                "claim_fence": DIGEST,
                "physical_attempt_id": IDENTITY,
                "expected_claim_expires_at_unix_ms": TIMESTAMP_MS,
            }
        },
    },
]

model_observation = strict_object(
    {
        "kind": const("model"),
        "outcome": enum("succeeded", "failed_certain", "outcome_unknown"),
        "output_text": {"oneOf": [{"type": "string", "maxLength": 1_048_576}, {"type": "null"}]},
        "output_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "route_receipt_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "usage_receipt_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "host_receipt_digest": DIGEST,
    }
)
model_observation["allOf"] = [
    {
        "if": {"properties": {"outcome": {"const": "succeeded"}}, "required": ["outcome"]},
        "then": {
            "properties": {
                "output_text": {"type": "string", "maxLength": 1_048_576},
                "output_digest": DIGEST,
                "route_receipt_digest": DIGEST,
            }
        },
    },
    {
        "if": {
            "properties": {"outcome": {"enum": ["failed_certain", "outcome_unknown"]}},
            "required": ["outcome"],
        },
        "then": {"properties": {"output_text": {"type": "null"}, "output_digest": {"type": "null"}}},
    },
]
child_observation = strict_object(
    {
        "kind": const("subagent"),
        "outcome": enum("accepted", "succeeded", "failed_certain", "outcome_unknown", "cancelled_certain"),
        "child_execution_id": IDENTITY,
        "result_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "artifact_manifest_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "host_receipt_digest": DIGEST,
    }
)
child_observation["allOf"] = [
    {
        "if": {"properties": {"outcome": {"const": "succeeded"}}, "required": ["outcome"]},
        "then": {"properties": {"result_digest": DIGEST}},
    },
    {
        "if": {
            "properties": {"outcome": {"enum": ["accepted", "failed_certain", "outcome_unknown", "cancelled_certain"]}},
            "required": ["outcome"],
        },
        "then": {"properties": {"result_digest": {"type": "null"}}},
    },
]
evidence_observation = strict_object(
    {
        "kind": const("evidence"),
        "outcome": enum("succeeded", "failed_certain"),
        "record_set_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "host_receipt_digest": DIGEST,
    }
)
evidence_observation["allOf"] = [
    {
        "if": {"properties": {"outcome": {"const": "succeeded"}}, "required": ["outcome"]},
        "then": {"properties": {"record_set_digest": DIGEST}},
    },
    {
        "if": {"properties": {"outcome": {"const": "failed_certain"}}, "required": ["outcome"]},
        "then": {"properties": {"record_set_digest": {"type": "null"}}},
    },
]
effect_observation = strict_object(
    {
        "kind": const("effect"),
        "outcome": enum("proposed", "rejected", "outcome_unknown"),
        "proposal_digest": DIGEST,
        "host_receipt_digest": DIGEST,
    }
)
observation_union = {
    "oneOf": [model_observation, child_observation, evidence_observation, effect_observation]
}
candidate_receipt = strict_object(
    {
        "schema_version": const("aar.caller-work-candidate-receipt.v1"),
        "ticket_id": IDENTITY,
        "ticket_digest": DIGEST,
        "physical_attempt_id": IDENTITY,
        "sent_request_digest": DIGEST,
        "sent_at_unix_ms": {"oneOf": [TIMESTAMP_MS, {"type": "null"}]},
        "provider_or_child_request_id": {"oneOf": [IDENTITY, {"type": "null"}]},
        "observation": observation_union,
        "receipt_digest": DIGEST,
        "callback_principal_id": IDENTITY,
        "callback_session_id": IDENTITY,
        "callback_adapter_id": IDENTITY,
        "callback_adapter_generation": POSITIVE,
        "observed_at_unix_ms": TIMESTAMP_MS,
        "signature_digest": DIGEST,
    }
)
commit_input = strict_object(
    {
        "context": caller_context,
        "operation": operation_ref,
        "expected_control_revision": REVISION,
        "expected_cancellation_revision": REVISION,
        "expected_suspension_revision": REVISION,
        "expected_cumulative_deadline_unix_ms": TIMESTAMP_MS,
        "ticket_id": IDENTITY,
        "expected_revision": REVISION,
        "ticket_digest": DIGEST,
        "claim_id": IDENTITY,
        "claim_fence": DIGEST,
        "physical_attempt_id": IDENTITY,
        "sent_request_digest": DIGEST,
        "sent_at_unix_ms": {"oneOf": [TIMESTAMP_MS, {"type": "null"}]},
        "provider_or_child_request_id": {"oneOf": [IDENTITY, {"type": "null"}]},
        "observation": observation_union,
        "idempotency_key": IDENTITY,
    }
)
reconcile_input = strict_object(
    {
        "context": caller_context,
        "operation": operation_ref,
        "expected_control_revision": REVISION,
        "expected_cancellation_revision": REVISION,
        "expected_suspension_revision": REVISION,
        "expected_cumulative_deadline_unix_ms": TIMESTAMP_MS,
        "ticket_id": IDENTITY,
        "expected_revision": REVISION,
        "ticket_digest": DIGEST,
        "physical_attempt_id": IDENTITY,
        "candidate_receipt_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "reconciler_id": IDENTITY,
        "reconciler_generation": POSITIVE,
        "reconcile_fence": DIGEST,
        "reconciliation_action": enum("lookup", "retry_if_certain_no_send", "quarantine", "settle"),
        "idempotency_key": IDENTITY,
    }
)
reconcile_input["allOf"] = [
    {
        "if": {
            "properties": {"reconciliation_action": {"enum": ["quarantine", "settle"]}},
            "required": ["reconciliation_action"],
        },
        "then": {"properties": {"candidate_receipt_digest": DIGEST}},
    }
]
caller_work_outcome = {
    "oneOf": [ticket, external("aar-rlm-workbench-v1.schema.json", "Failure")]
}

caller_contract = {
    "$schema": DRAFT,
    "$id": "aar-caller-work-v1.schema.json",
    "title": "AAR caller-delegated broker work contracts v1",
    "$defs": {
        "LogicalCallOwner": logical_owner,
        "ModelRequestV2": model_request_v2,
        "SubagentSubmitV2": subagent_submit_v2,
        "SubagentResultV2": subagent_result_v2,
        "EvidenceQueryV2": evidence_query_v2,
        "EffectProposeV2": effect_propose_v2,
        "CallerWorkRequest": request_union,
        "CallerWorkState": caller_ticket_state,
        "Claimant": claimant,
        "PhysicalAttempt": physical_attempt,
        "CallerWorkTicketRef": ticket_ref,
        "CallerWorkTicket": ticket,
        "CallerWorkClaimInput": claim_input,
        "CallerWorkMarkSendStartedInput": mark_send_input,
        "CallerWorkCancelBeforeSendInput": cancel_before_send_input,
        "CallerWorkCommitInput": commit_input,
        "CallerWorkReconcileInput": reconcile_input,
        "CallerWorkObservation": observation_union,
        "ModelObservation": model_observation,
        "ChildObservation": child_observation,
        "EvidenceObservation": evidence_observation,
        "EffectObservation": effect_observation,
        "CandidateReceipt": candidate_receipt,
        "CallerWorkOutcome": caller_work_outcome,
    },
    "oneOf": [
        ref("CallerWorkTicket"),
        ref("CallerWorkClaimInput"),
        ref("CallerWorkMarkSendStartedInput"),
        ref("CallerWorkCancelBeforeSendInput"),
        ref("CallerWorkCommitInput"),
        ref("CallerWorkReconcileInput"),
    ],
}

artifact_binding = strict_object(
    {
        "schema_version": const("aar.artifact-binding.v1"),
        "logical_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "pattern": r"^[A-Za-z0-9][-A-Za-z0-9._/]*$",
        },
        "media_type": {"type": "string", "minLength": 1, "maxLength": 128},
        "digest": DIGEST,
        "size_bytes": COUNTER,
        "operation": operation_ref,
        "workspace": workspace_ref,
        "workspace_generation": POSITIVE,
        "cell_execution_id": IDENTITY,
        "role": enum("result", "evidence", "diagnostic", "intermediate"),
    }
)
artifact_stage = strict_object(
    {
        "schema_version": const("aar.artifact-stage.v1"),
        "stage_id": IDENTITY,
        "binding": artifact_binding,
        "content_digest": DIGEST,
        "state": enum("staged", "committed", "discarded"),
    }
)
cell_commit_manifest = strict_object(
    {
        "schema_version": const("aar.cell-commit-manifest.v1"),
        "operation": operation_ref,
        "attempt_id": IDENTITY,
        "attempt_fence": DIGEST,
        "workspace": workspace_ref,
        "workspace_generation": POSITIVE,
        "pre_revision": REVISION,
        "post_revision": REVISION,
        "cell_execution_id": IDENTITY,
        "source_digest": DIGEST,
        "result_digest": DIGEST,
        "checkpoint_digest": DIGEST,
        "artifact_stage_ids": array(IDENTITY, maximum=256),
        "manifest_digest": DIGEST,
    }
)
finalization_manifest = strict_object(
    {
        "schema_version": const("aar.finalization-manifest.v1"),
        "operation": operation_ref,
        "finalizer_attempt_id": IDENTITY,
        "finalizer_attempt_fence": DIGEST,
        "expected_prior_phase": const("finalizing"),
        "control_revision": REVISION,
        "expected_cancellation_revision": REVISION,
        "expected_deadline_unix_ms": TIMESTAMP_MS,
        "worker_owner_generation": POSITIVE,
        "unresolved_required_ticket_count": const(0),
        "result_digest": DIGEST,
        "artifact_binding_digests": array(DIGEST, maximum=256),
        "usage_digest": DIGEST,
        "child_policy_digest": DIGEST,
        "workspace_disposition_digest": DIGEST,
        "manifest_digest": DIGEST,
    }
)
artifact_contract = {
    "$schema": DRAFT,
    "$id": "aar-artifact-publication-v1.schema.json",
    "title": "AAR staged artifact and finalization contracts v1",
    "$defs": {
        "ArtifactBinding": artifact_binding,
        "ArtifactStage": artifact_stage,
        "CellCommitManifest": cell_commit_manifest,
        "FinalizationManifest": finalization_manifest,
    },
    "oneOf": [ref("ArtifactBinding"), ref("ArtifactStage"), ref("CellCommitManifest"), ref("FinalizationManifest")],
}

frame_identity = {
    "schema_version": const("aar.workspace-broker-frame.v1"),
    "operation": operation_ref,
    "attempt_id": IDENTITY,
    "attempt_fence": DIGEST,
    "workspace": workspace_ref,
    "workspace_generation": POSITIVE,
    "workspace_revision": REVISION,
    "cell_execution_id": IDENTITY,
    "frame_sequence": COUNTER,
    "broker_call_ordinal": {"oneOf": [POSITIVE, {"type": "null"}]},
    "payload_digest": DIGEST,
    "payload_bytes": {"type": "integer", "minimum": 1, "maximum": 1_048_576},
    "deadline_unix_ms": TIMESTAMP_MS,
}
broker_intent_payload = strict_object(
    {
        "context": external("aar-rlm-workbench-v1.schema.json", "BrokerContextV2"),
        "method": enum("model.request", "subagent.submit", "subagent.result", "evidence.query", "effect.propose"),
        "contract_id": IDENTITY,
        "request_digest": DIGEST,
        "request": {
            "oneOf": [
                external("aar-caller-work-v1.schema.json", "ModelRequestV2"),
                external("aar-caller-work-v1.schema.json", "SubagentSubmitV2"),
                external("aar-caller-work-v1.schema.json", "SubagentResultV2"),
                external("aar-caller-work-v1.schema.json", "EvidenceQueryV2"),
                external("aar-caller-work-v1.schema.json", "EffectProposeV2"),
            ]
        },
    }
)
broker_receipt_payload = strict_object(
    {
        "context": external("aar-rlm-workbench-v1.schema.json", "BrokerContextV2"),
        "contract_id": IDENTITY,
        "request_digest": DIGEST,
        "receipt_digest": DIGEST,
        "observation": external("aar-caller-work-v1.schema.json", "CallerWorkObservation"),
    }
)
execute_request_payload = strict_object(
    {
        "source_utf8": {"type": "string", "minLength": 1, "maxLength": 1_048_576},
        "source_digest": DIGEST,
        "expected_pre_revision": REVISION,
        "expected_post_revision": REVISION,
        "pre_checkpoint_digest": DIGEST,
        "result_contract_digest": DIGEST,
        "capture_limit_bytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1_048_576,
        },
    }
)
stream_payload = strict_object(
    {
        "chunk_utf8": {"type": "string", "minLength": 1, "maxLength": 262_144},
        "stream_offset_bytes": COUNTER,
        "truncated": {"type": "boolean"},
    }
)
rich_display_payload = strict_object(
    {
        "display_id": IDENTITY,
        "media_type": enum("text/plain", "text/markdown", "application/json", "image/png"),
        "content_digest": DIGEST,
        "content_size_bytes": {"type": "integer", "minimum": 1, "maximum": 1_048_576},
        "worker_buffer_id": IDENTITY,
    }
)
progress_payload = strict_object(
    {
        "completed_units": COUNTER,
        "total_units": POSITIVE,
        "message": {"type": "string", "minLength": 1, "maxLength": 4096},
    }
)
artifact_stage_intent_payload = strict_object(
    {
        "binding": external("aar-artifact-publication-v1.schema.json", "ArtifactBinding"),
        "content_digest": DIGEST,
        "content_size_bytes": COUNTER,
        "worker_buffer_id": IDENTITY,
        "promotion_policy": enum("cell_commit", "finalization"),
    }
)
completion_intent_payload = strict_object(
    {
        "disposition": enum("succeed", "fail", "abstain"),
        "result_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "result_size_bytes": {"oneOf": [COUNTER, {"type": "null"}]},
        "output_contract_digest": DIGEST,
        "required_artifact_stage_ids": array(IDENTITY, maximum=256),
        "reason": {
            "oneOf": [
                {"type": "string", "minLength": 1, "maxLength": 4096},
                {"type": "null"},
            ]
        },
    }
)
completion_intent_payload["allOf"] = [
    {
        "if": {
            "properties": {"disposition": {"const": "succeed"}},
            "required": ["disposition"],
        },
        "then": {
            "properties": {
                "result_digest": DIGEST,
                "result_size_bytes": COUNTER,
                "reason": {"type": "null"},
            }
        },
    },
    {
        "if": {
            "properties": {"disposition": {"enum": ["fail", "abstain"]}},
            "required": ["disposition"],
        },
        "then": {
            "properties": {
                "result_digest": {"type": "null"},
                "result_size_bytes": {"type": "null"},
                "reason": {"type": "string", "minLength": 1, "maxLength": 4096},
            }
        },
    },
]
execute_result_payload = strict_object(
    {
        "status": enum("completed", "failed", "cancelled", "protocol_error"),
        "pre_workspace_revision": REVISION,
        "post_workspace_revision": {"oneOf": [REVISION, {"type": "null"}]},
        "result_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "checkpoint_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "failure": {
            "oneOf": [
                external("aar-rlm-workbench-v1.schema.json", "Failure"),
                {"type": "null"},
            ]
        },
    }
)
execute_result_payload["allOf"] = [
    {
        "if": {
            "properties": {"status": {"const": "completed"}},
            "required": ["status"],
        },
        "then": {
            "properties": {
                "post_workspace_revision": REVISION,
                "result_digest": DIGEST,
                "checkpoint_digest": DIGEST,
                "failure": {"type": "null"},
            }
        },
    },
    {
        "if": {
            "properties": {
                "status": {"enum": ["failed", "cancelled", "protocol_error"]}
            },
            "required": ["status"],
        },
        "then": {
            "properties": {
                "post_workspace_revision": {"type": "null"},
                "result_digest": {"type": "null"},
                "checkpoint_digest": {"type": "null"},
                "failure": external("aar-rlm-workbench-v1.schema.json", "Failure"),
            }
        },
    },
]
broker_suspend_payload = strict_object(
    {
        "ticket": external("aar-caller-work-v1.schema.json", "CallerWorkTicketRef"),
        "suspension_revision": REVISION,
        "control_revision": REVISION,
        "request_digest": DIGEST,
        "logical_owner_digest": DIGEST,
        "pre_checkpoint_digest": DIGEST,
    }
)
cancel_payload = strict_object(
    {
        "reason": enum("user_requested", "deadline", "shutdown", "ownership_lost"),
        "control_revision": REVISION,
        "cancellation_revision": REVISION,
        "effective_deadline_unix_ms": TIMESTAMP_MS,
        "cancellation_receipt_digest": DIGEST,
    }
)
protocol_error_payload = strict_object(
    {
        "code": enum(
            "sequence_mismatch",
            "identity_mismatch",
            "digest_mismatch",
            "size_limit",
            "wrong_direction",
            "stale_authority",
            "unsupported_kind",
        ),
        "message": {"type": "string", "minLength": 1, "maxLength": 4096},
        "offending_frame_sequence": {"oneOf": [COUNTER, {"type": "null"}]},
        "offending_payload_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "fatal": const(True),
    }
)
successor_rebind_token = strict_object(
    {
        "schema_version": const("aar.workspace-successor-rebind.v1"),
        "operation": operation_ref,
        "rebind_generation": POSITIVE,
        "prior_authority_generation": POSITIVE,
        "successor_authority_generation": POSITIVE,
        "expected_control_revision": REVISION,
        "expected_cancellation_revision": REVISION,
        "expected_cumulative_deadline_unix_ms": TIMESTAMP_MS,
        "prior_attempt_id": IDENTITY,
        "prior_attempt_fence": DIGEST,
        "successor_attempt_id": IDENTITY,
        "successor_attempt_fence": DIGEST,
        "workspace": workspace_ref,
        "workspace_generation": POSITIVE,
        "workspace_revision": REVISION,
        "cell_execution_id": IDENTITY,
        "suspension_revision": REVISION,
        "ticket_id": IDENTITY,
        "settled_receipt_digest": DIGEST,
        "successor_outbox_digest": DIGEST,
        "worker_owner_generation": POSITIVE,
        "worker_process_identity_digest": DIGEST,
        "expires_at_unix_ms": TIMESTAMP_MS,
        "single_use": const(True),
        "token_digest": DIGEST,
    }
)
rebind_payload = strict_object(
    {
        "token": successor_rebind_token,
        "phase": enum("prepare", "commit", "abort"),
    }
)
rebind_ack_payload = strict_object(
    {
        "token_digest": DIGEST,
        "acknowledged_phase": enum("prepared", "committed", "aborted"),
        "worker_process_identity_digest": DIGEST,
        "observed_workspace_revision": REVISION,
        "live_stack_resumed": {"type": "boolean"},
        "ack_digest": DIGEST,
    }
)
def typed_frame(
    kind: str,
    direction: str,
    payload: dict[str, Any],
    committed: bool,
    broker_ordinal_required: bool = False,
) -> dict[str, Any]:
    return strict_object(
        {
            **frame_identity,
            "broker_call_ordinal": (
                POSITIVE if broker_ordinal_required else {"type": "null"}
            ),
            "kind": const(kind),
            "direction": const(direction),
            "payload": payload,
            "committed": const(committed),
        }
    )


def rebind_phase_payload(phase: str) -> dict[str, Any]:
    return {
        "allOf": [
            rebind_payload,
            {
                "properties": {"phase": {"const": phase}},
                "required": ["phase"],
            },
        ]
    }


frame_variants = [
    typed_frame("execute_request", "supervisor_to_worker", execute_request_payload, True),
    typed_frame("stdout", "worker_to_supervisor", stream_payload, False),
    typed_frame("stderr", "worker_to_supervisor", stream_payload, False),
    typed_frame("rich_display", "worker_to_supervisor", rich_display_payload, False),
    typed_frame("progress", "worker_to_supervisor", progress_payload, False),
    typed_frame(
        "broker_intent",
        "worker_to_supervisor",
        broker_intent_payload,
        False,
        True,
    ),
    typed_frame(
        "artifact_stage_intent",
        "worker_to_supervisor",
        artifact_stage_intent_payload,
        False,
    ),
    typed_frame(
        "completion_intent",
        "worker_to_supervisor",
        completion_intent_payload,
        False,
    ),
    typed_frame("execute_result", "worker_to_supervisor", execute_result_payload, False),
    typed_frame(
        "broker_receipt", "supervisor_to_worker", broker_receipt_payload, True, True
    ),
    typed_frame(
        "broker_suspend", "supervisor_to_worker", broker_suspend_payload, True, True
    ),
    typed_frame(
        "rebind_prepare",
        "supervisor_to_worker",
        rebind_phase_payload("prepare"),
        True,
    ),
    typed_frame(
        "rebind_commit",
        "supervisor_to_worker",
        rebind_phase_payload("commit"),
        True,
    ),
    typed_frame(
        "rebind_abort",
        "supervisor_to_worker",
        rebind_phase_payload("abort"),
        True,
    ),
    typed_frame("rebind_ack", "worker_to_supervisor", rebind_ack_payload, False),
    typed_frame("cancel", "supervisor_to_worker", cancel_payload, True),
    typed_frame(
        "worker_protocol_error",
        "worker_to_supervisor",
        protocol_error_payload,
        False,
    ),
    typed_frame(
        "supervisor_protocol_error",
        "supervisor_to_worker",
        protocol_error_payload,
        False,
    ),
]
frame = {"oneOf": frame_variants}
worker_contract = {
    "$schema": DRAFT,
    "$id": "aar-workspace-broker-frame-v1.schema.json",
    "title": "AAR workspace broker IPC frame v1",
    "$defs": {
        "ExecuteRequestPayload": execute_request_payload,
        "StreamPayload": stream_payload,
        "RichDisplayPayload": rich_display_payload,
        "ProgressPayload": progress_payload,
        "BrokerIntentPayload": broker_intent_payload,
        "ArtifactStageIntentPayload": artifact_stage_intent_payload,
        "CompletionIntentPayload": completion_intent_payload,
        "ExecuteResultPayload": execute_result_payload,
        "BrokerReceiptPayload": broker_receipt_payload,
        "BrokerSuspendPayload": broker_suspend_payload,
        "CancelPayload": cancel_payload,
        "ProtocolErrorPayload": protocol_error_payload,
        "SuccessorRebindToken": successor_rebind_token,
        "RebindPayload": rebind_payload,
        "RebindAckPayload": rebind_ack_payload,
        "WorkspaceBrokerFrame": frame,
    },
    "$ref": "#/$defs/WorkspaceBrokerFrame",
}

evidence_file_binding = strict_object(
    {
        "kind": enum(
            "source_archive",
            "source_manifest",
            "wheel",
            "wheel_build_manifest",
            "profile",
            "skill",
            "tool_surface",
            "test_bundle",
            "fixture_bundle",
            "environment",
            "stdout",
            "raw_receipt",
            "review_signature",
            "review_public_key",
        ),
        "relative_path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "pattern": r"^[A-Za-z0-9][-A-Za-z0-9._/]*$",
        },
        "sha256": DIGEST,
        "size_bytes": COUNTER,
    }
)
source_tree_entry = strict_object(
    {
        "relative_path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "pattern": r"^[A-Za-z0-9][-A-Za-z0-9._/]*$",
        },
        "sha256": DIGEST,
        "size_bytes": COUNTER,
        "executable": {"type": "boolean"},
    }
)
source_manifest = strict_object(
    {
        "schema_version": const("aar.acceptance-source.v1"),
        "candidate_id": IDENTITY,
        "source_commit": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "clean": const(True),
        "source_archive_digest": DIGEST,
        "source_archive_size_bytes": COUNTER,
        "entries": array(source_tree_entry, minimum=1, maximum=65536),
        "source_tree_digest": DIGEST,
        "manifest_digest": DIGEST,
    }
)
wheel_build_manifest = strict_object(
    {
        "schema_version": const("aar.acceptance-wheel-build.v1"),
        "candidate_id": IDENTITY,
        "source_manifest_file_digest": DIGEST,
        "source_manifest_digest": DIGEST,
        "source_commit": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "source_tree_digest": DIGEST,
        "source_archive_digest": DIGEST,
        "wheel_digest": DIGEST,
        "build_command": {"type": "string", "minLength": 1, "maxLength": 2048},
        "manifest_digest": DIGEST,
    }
)
environment_manifest = strict_object(
    {
        "schema_version": const("aar.acceptance-environment.v1"),
        "candidate_id": IDENTITY,
        "source_commit": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "package_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": r"^[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+)?$",
        },
        "tool_surface_digest": DIGEST,
        "python_version": {
            "type": "string",
            "pattern": r"^3\.(?:11|12|13|14)(?:\.[0-9]+)?$",
        },
        "platform": enum("linux-x86_64", "windows-x86_64", "macos-arm64"),
        "os_name": {"type": "string", "minLength": 1, "maxLength": 128},
        "os_version": {"type": "string", "minLength": 1, "maxLength": 256},
        "architecture": enum("x86_64", "arm64"),
        "registry_schema_version": const(6),
        "runtime_generation": POSITIVE,
        "command_environment_digest": DIGEST,
    }
)
fixture_entry = strict_object(
    {
        "fixture_id": IDENTITY,
        "kind": enum("input", "expected", "fault_injection", "baseline", "migration"),
        "relative_path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "pattern": r"^[A-Za-z0-9][-A-Za-z0-9._/]*$",
        },
        "sha256": DIGEST,
        "size_bytes": COUNTER,
    }
)
fixture_bundle_manifest = strict_object(
    {
        "schema_version": const("aar.acceptance-fixture-bundle.v1"),
        "bundle_id": IDENTITY,
        "candidate_id": IDENTITY,
        "entries": array(fixture_entry, minimum=1, maximum=256),
        "bundle_digest": DIGEST,
    }
)
semantic_receipt_document = strict_object(
    {
        "schema_version": const("aar.acceptance-semantic-receipt.v1"),
        "receipt_id": IDENTITY,
        "kind": enum(
            "host",
            "model_route",
            "model_usage",
            "subagent",
            "authorization",
            "artifact",
            "reconciler",
        ),
        "candidate_id": IDENTITY,
        "operation_id": {"oneOf": [IDENTITY, {"type": "null"}]},
        "issuer_id": IDENTITY,
        "status": enum(
            "succeeded",
            "failed",
            "indeterminate",
            "granted",
            "denied",
            "accepted",
            "cancelled",
        ),
        "certainty": enum("certain", "indeterminate"),
        "payload_digest": DIGEST,
        "observed_at_unix_ms": TIMESTAMP_MS,
    }
)
semantic_receipt = strict_object(
    {
        "kind": enum("host", "model_route", "model_usage", "subagent", "authorization", "artifact", "reconciler"),
        "receipt_digest": DIGEST,
        "file": evidence_file_binding,
    }
)
review_key_binding = strict_object(
    {
        "reviewer_principal_id": IDENTITY,
        "reviewer_key_id": IDENTITY,
        "algorithm": const("ed25519"),
        "public_key": evidence_file_binding,
        "public_key_digest": DIGEST,
    }
)
review_trust_entry = strict_object(
    {
        "reviewer_principal_id": IDENTITY,
        "reviewer_key_id": IDENTITY,
        "algorithm": const("ed25519"),
        "public_key_digest": DIGEST,
    }
)
review_trust_store = strict_object(
    {
        "schema_version": const("aar.acceptance-review-trust.v1"),
        "keys": array(review_trust_entry, minimum=1, maximum=64),
    }
)
candidate_manifest = strict_object(
    {
        "schema_version": const("aar.acceptance-candidate.v1"),
        "candidate_id": IDENTITY,
        "package_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": r"^[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+)?$",
        },
        "source_commit": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "source_tree_digest": DIGEST,
        "wheel_digest": DIGEST,
        "contract_manifest_digest": DIGEST,
        "tool_surface_digest": DIGEST,
        "profile_digests": array(DIGEST, minimum=1, maximum=32),
        "skill_digests": array(DIGEST, minimum=1, maximum=32),
        "artifacts": array(evidence_file_binding, minimum=8, maximum=256),
        "python_versions": array({"type": "string", "pattern": r"^3\.(?:11|12|13|14)(?:\.[0-9]+)?$"}, minimum=1, maximum=4),
        "platforms": array(enum("linux-x86_64", "windows-x86_64", "macos-arm64"), minimum=1, maximum=3),
        "build_command": {"type": "string", "minLength": 1, "maxLength": 2048},
        "created_at_unix_ms": TIMESTAMP_MS,
        "manifest_digest": DIGEST,
    }
)
evidence_item = strict_object(
    {
        "evidence_id": IDENTITY,
        "candidate_id": IDENTITY,
        "acceptance_id": IDENTITY,
        "fault_case_id": {"oneOf": [IDENTITY, {"type": "null"}]},
        "tier": enum("T0", "T1", "T2", "T3", "T4"),
        "command": {"type": "string", "minLength": 1, "maxLength": 4096},
        "environment_digest": DIGEST,
        "test_or_probe_digest": DIGEST,
        "fixture_digest": DIGEST,
        "stdout_digest": DIGEST,
        "receipt_digests": array(DIGEST, maximum=64),
        "files": array(evidence_file_binding, minimum=1, maximum=64),
        "semantic_receipts": array(semantic_receipt, maximum=64),
        "exit_code": {"type": "integer", "minimum": 0, "maximum": 255},
        "observed_outcome": enum("pass", "fail", "indeterminate", "not_run"),
        "reviewer_disposition": enum("accepted", "rejected", "pending"),
        "reviewer_principal_id": IDENTITY,
        "reviewer_key_id": IDENTITY,
        "review_signature": evidence_file_binding,
        "review_signature_digest": DIGEST,
        "observed_at_unix_ms": TIMESTAMP_MS,
    }
)
acceptance_result = strict_object(
    {
        "schema_version": const("aar.acceptance-results.v1"),
        "candidate": candidate_manifest,
        "review_keys": array(review_key_binding, minimum=1, maximum=64),
        "matrix_digest": DIGEST,
        "fault_matrix_digest": DIGEST,
        "evidence": array(evidence_item, maximum=4096),
        "authorization_receipt_digests": array(DIGEST, maximum=32),
        "claim_status": enum("not_verified", "implementation_verified", "live_qualified"),
        "results_digest": DIGEST,
    }
)
evidence_contract = {
    "$schema": DRAFT,
    "$id": "aar-acceptance-evidence-v1.schema.json",
    "title": "AAR candidate and acceptance evidence contracts v1",
    "$defs": {
        "EvidenceFileBinding": evidence_file_binding,
        "SourceTreeEntry": source_tree_entry,
        "SourceManifest": source_manifest,
        "WheelBuildManifest": wheel_build_manifest,
        "EnvironmentManifest": environment_manifest,
        "FixtureEntry": fixture_entry,
        "FixtureBundleManifest": fixture_bundle_manifest,
        "SemanticReceiptDocument": semantic_receipt_document,
        "SemanticReceipt": semantic_receipt,
        "ReviewKeyBinding": review_key_binding,
        "ReviewTrustStore": review_trust_store,
        "CandidateManifest": candidate_manifest,
        "EvidenceItem": evidence_item,
        "AcceptanceResults": acceptance_result,
    },
    "oneOf": [ref("CandidateManifest"), ref("EvidenceItem"), ref("AcceptanceResults")],
}

migration_attestation_payload = strict_object(
    {
        "schema_version": const("aar.migration-v6-attestation-payload.v1"),
        "migration_version": const(6),
        "cutover_epoch": IDENTITY,
        "snapshot_id": IDENTITY,
        "snapshot_sha256": DIGEST,
        "snapshot_size_bytes": POSITIVE,
        "canonical_v5_row_set_digest": DIGEST,
        "source_commit": COMMIT_SHA,
        "wheel_digest": DIGEST,
        "profile_digest": DIGEST,
        "skill_digest": DIGEST,
        "contract_manifest_digest": DIGEST,
        "migration_sql_digest": DIGEST,
        "external_authority_store_id": IDENTITY,
        "external_authority_prepared_digest": DIGEST,
        "started_at_unix_ms": TIMESTAMP_MS,
        "completed_at_unix_ms": TIMESTAMP_MS,
        "foreign_key_violation_count": const(0),
        "integrity_result": const("ok"),
    }
)
migration_attestation_document = strict_object(
    {
        "attestation": migration_attestation_payload,
        "attestation_digest": DIGEST,
    }
)

cutover_event_payload = strict_object(
    {
        "schema_version": const("aar.migration-cutover-event-payload.v1"),
        "cutover_epoch": IDENTITY,
        "sequence": COUNTER,
        "previous_event_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "state": enum(
            "prepared",
            "candidate_active",
            "rolled_back_before_write",
            "post_snapshot_write",
            "retired_forward",
        ),
        "occurred_at_unix_ms": TIMESTAMP_MS,
        "migration_attestation_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "candidate_readback_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "post_snapshot_barrier_digest": {"oneOf": [DIGEST, {"type": "null"}]},
        "decision_digest": {"oneOf": [DIGEST, {"type": "null"}]},
    }
)
cutover_event_payload["allOf"] = [
    {
        "if": {"properties": {"state": {"const": "prepared"}}, "required": ["state"]},
        "then": {
            "properties": {
                "sequence": {"const": 0},
                "previous_event_digest": {"type": "null"},
                "migration_attestation_digest": {"type": "null"},
                "candidate_readback_digest": {"type": "null"},
                "post_snapshot_barrier_digest": {"type": "null"},
                "decision_digest": {"type": "null"},
            }
        },
    },
    {
        "if": {
            "properties": {
                "state": {"enum": ["candidate_active", "rolled_back_before_write"]}
            },
            "required": ["state"],
        },
        "then": {
            "properties": {
                "sequence": POSITIVE,
                "previous_event_digest": DIGEST,
                "migration_attestation_digest": DIGEST,
                "candidate_readback_digest": DIGEST,
                "post_snapshot_barrier_digest": {"type": "null"},
            }
        },
    },
    {
        "if": {
            "properties": {
                "state": {"enum": ["post_snapshot_write", "retired_forward"]}
            },
            "required": ["state"],
        },
        "then": {
            "properties": {
                "sequence": POSITIVE,
                "previous_event_digest": DIGEST,
                "migration_attestation_digest": DIGEST,
                "candidate_readback_digest": DIGEST,
                "post_snapshot_barrier_digest": DIGEST,
            }
        },
    },
    {
        "if": {
            "properties": {
                "state": {"enum": ["rolled_back_before_write", "retired_forward"]}
            },
            "required": ["state"],
        },
        "then": {"properties": {"decision_digest": DIGEST}},
    },
]
cutover_event_document = strict_object(
    {
        "event": cutover_event_payload,
        "event_digest": DIGEST,
    }
)
cutover_authority_ledger = strict_object(
    {
        "schema_version": const("aar.migration-cutover-authority.v1"),
        "authority_store_id": IDENTITY,
        "cutover_epoch": IDENTITY,
        "snapshot_id": IDENTITY,
        "snapshot_sha256": DIGEST,
        "source_commit": COMMIT_SHA,
        "wheel_digest": DIGEST,
        "events": array(cutover_event_document, minimum=1, maximum=8),
        "head_event_digest": DIGEST,
    }
)
migration_contract = {
    "$schema": DRAFT,
    "$id": "aar-migration-cutover-v1.schema.json",
    "title": "AAR v5-to-v6 migration and cutover authority contracts v1",
    "$defs": {
        "MigrationAttestationPayload": migration_attestation_payload,
        "MigrationAttestationDocument": migration_attestation_document,
        "CutoverEventPayload": cutover_event_payload,
        "CutoverEventDocument": cutover_event_document,
        "CutoverAuthorityLedger": cutover_authority_ledger,
    },
    "oneOf": [
        ref("MigrationAttestationDocument"),
        ref("CutoverEventDocument"),
        ref("CutoverAuthorityLedger"),
    ],
}

tool_surface = {
    "schema_version": "aar.sdd-mcp-tool-surface.v1",
    "surface_version": "aar.mcp-tools.v8",
    "tools": [
        {
            "name": "aar_rlm_workbench_execute",
            "title": "Execute RLM-native workbench job",
            "description": "Admit or idempotently recover one bounded RLM-native IPython workbench operation.",
            "capability": "rlm.workbench.execute",
            "input_schema": "aar-rlm-workbench-v1.schema.json#/$defs/RlmWorkbenchExecuteInput",
            "output_schema": "aar-rlm-workbench-v1.schema.json#/$defs/RlmWorkbenchExecuteOutcome",
            "annotations": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": True, "openWorldHint": True},
        },
        {
            "name": "aar_rlm_workbench_capabilities",
            "title": "Read RLM workbench capabilities",
            "description": "Read v8 workbench and per-method backend availability without changing frozen aar_capabilities v7 response bytes.",
            "capability": "rlm.workbench.read",
            "input_schema": "aar-rlm-workbench-v1.schema.json#/$defs/RlmWorkbenchCapabilitiesInput",
            "output_schema": "aar-rlm-workbench-v1.schema.json#/$defs/RlmWorkbenchCapabilityOutcome",
            "annotations": {"readOnlyHint": True, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "aar_rlm_workbench_status",
            "title": "Read RLM workbench status",
            "description": "Read the typed workbench phase, tickets, workspace binding and terminal result.",
            "capability": "rlm.workbench.read",
            "input_schema": "aar-rlm-workbench-v1.schema.json#/$defs/RlmWorkbenchStatusInput",
            "output_schema": "aar-rlm-workbench-v1.schema.json#/$defs/RlmWorkbenchStatusOutcome",
            "annotations": {"readOnlyHint": True, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "aar_broker_work_claim",
            "title": "Reserve caller work send",
            "description": "Reserve a pre-send physical caller-work attempt under claimant and adapter fences.",
            "capability": "broker.caller.claim",
            "input_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkClaimInput",
            "output_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkOutcome",
            "annotations": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "aar_broker_work_mark_send_started",
            "title": "Mark caller work send started",
            "description": "Commit the conservative may-have-sent linearization point before physical dispatch.",
            "capability": "broker.caller.send",
            "input_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkMarkSendStartedInput",
            "output_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkOutcome",
            "annotations": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "aar_broker_work_cancel_before_send",
            "title": "Cancel caller work before send",
            "description": "CAS a pending or send-reserved ticket to durable no-send cancellation.",
            "capability": "broker.caller.cancel",
            "input_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkCancelBeforeSendInput",
            "output_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkOutcome",
            "annotations": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": True, "openWorldHint": False},
        },
        {
            "name": "aar_broker_work_commit",
            "title": "Commit caller work observation",
            "description": "Append and reconcile one digest-bound model, subagent, evidence or effect observation.",
            "capability": "broker.caller.commit",
            "input_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkCommitInput",
            "output_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkOutcome",
            "annotations": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "aar_broker_work_reconcile",
            "title": "Reconcile caller work",
            "description": "Apply lookup, cancellation or candidate-receipt evidence through the current reconciler.",
            "capability": "broker.caller.reconcile",
            "input_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkReconcileInput",
            "output_schema": "aar-caller-work-v1.schema.json#/$defs/CallerWorkOutcome",
            "annotations": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False},
        },
    ],
}

fault_cases = [
    ("FC-CALL-001", "AC-L05", "before_claim_commit", "claimant crash before reservation commit", "ticket stays pending; physical sends=0"),
    ("FC-CALL-002", "AC-L05", "after_claim_before_send_mark", "claimant crash after send reservation", "reservation expires safely; physical sends=0"),
    ("FC-CALL-003", "AC-L05", "before_send_mark_commit", "send attempted before durable mark", "adapter rejects; physical sends=0"),
    ("FC-CALL-004", "AC-L05", "after_send_mark_before_physical_send", "claimant crash", "ticket is may-have-sent and requires lookup; no blind reclaim"),
    ("FC-CALL-005", "AC-L05", "after_physical_send_before_receipt", "claimant crash", "ticket is outcome_unknown; duplicate sends=0"),
    ("FC-CALL-006", "AC-L06", "late_receipt_after_cancel", "append candidate receipt", "current reconciler alone may settle or quarantine"),
    ("FC-CALL-007", "AC-L06", "conflicting_receipts", "append different digest for same physical attempt", "ticket quarantines; terminal truth is not overwritten"),
    ("FC-CALL-008", "AC-L04", "expired_pre_send_reservation", "second claimant claims", "one current claim; stale claimant mutations=0"),
    ("FC-CALL-009", "AC-L04", "concurrent_claim", "two claimants race", "one reservation succeeds; one conflict"),
    ("FC-CALL-010", "AC-B04", "foreign_claimant_commit", "wrong principal/generation commits", "mutation rejected; ticket unchanged"),
    ("FC-CALL-011", "AC-B04", "wrong_ticket_digest", "commit with wrong digest", "mutation rejected before observation write"),
    ("FC-CALL-012", "AC-L11", "same_key_changed_payload", "replay changed claim/commit payload", "idempotency conflict"),
    ("FC-CALL-013", "AC-L03", "cancel_before_send_start_cas", "cancellation/control revision wins", "ticket settles cancelled_before_send; physical sends=0"),
    ("FC-CALL-014", "AC-L03", "cancel_after_send_start_cas", "send-start commit wins", "pre-send cancellation conflicts; may-have-sent reconciliation applies"),
    ("FC-CALL-015", "AC-L03", "stale_send_start_cross_row_fence", "stale control/cancellation/suspension/deadline/claim input", "send-start rejected; physical sends=0"),
    ("FC-WAIT-001", "AC-L02", "suspend_transaction_before_commit", "crash", "old attempt remains sole owner; no ticket visible"),
    ("FC-WAIT-002", "AC-L02", "suspend_transaction_after_commit", "crash", "ticket visible; no active attempt; worker write-fenced"),
    ("FC-WAIT-003", "AC-L04", "settlement_before_successor_outbox", "crash", "reconciler enqueues one successor"),
    ("FC-WAIT-004", "AC-L04", "duplicate_successor_enqueue", "two reconcilers race", "unique operation/suspension key yields one successor"),
    ("FC-WAIT-005", "AC-L03", "cancel_vs_ticket_settle", "race", "one CAS winner; losing evidence retained as candidate only"),
    ("FC-WAIT-006", "AC-L10", "quiet_restart_past_deadline", "startup sweep", "no new send; cancel/lookup/quarantine per send state"),
    ("FC-LEASE-001", "AC-L01", "schedule_before_keeper_register", "close races", "new attempt aborts or registered keeper is joined"),
    ("FC-LEASE-002", "AC-L01", "keeper_heartbeat_vs_terminal", "race", "terminal attempt never renewed"),
    ("FC-LEASE-003", "AC-L01", "keeper_failure", "inject failure", "attempt parks/fails; no silent expiry"),
    ("FC-LEASE-004", "AC-B03", "close_with_live_callbacks", "close runtime owner", "callbacks drained/fenced before stores close"),
    ("FC-LEASE-005", "AC-L07", "stale_attempt_inner_write", "allow old worker completion after successor claim", "authoritative broker/RLM/artifact writes=0; matching external evidence enters candidate lane only"),
    ("FC-WORKER-001", "AC-L08", "worker_loss_before_broker_intent", "kill worker", "restore checkpoint and recovery-plan; no automatic cell replay"),
    ("FC-WORKER-002", "AC-L08", "worker_loss_after_send_started", "kill worker", "receipt lookup/reconcile precedes continuation"),
    ("FC-WORKER-003", "AC-L09", "ambient_effect_before_suspend", "attempt durable facade call", "automatic replay forbidden; job recovery-plans or parks"),
    ("FC-WORKER-004", "AC-I01", "typed_frame_payload_or_direction_mutant", "empty/missing/extra/wrong-direction frame", "schema rejects before semantic or authority handling"),
    ("FC-REBIND-001", "AC-I06", "crash_before_rebind_commit", "coordinator restart", "prepared transfer retries or aborts; predecessor remains fenced; successor not authoritative"),
    ("FC-REBIND-002", "AC-I06", "crash_after_commit_before_delivery", "coordinator restart", "exact committed token redelivers; one successor authority"),
    ("FC-REBIND-003", "AC-I06", "crash_after_delivery_before_ack", "coordinator restart", "idempotent commit redelivery and acknowledgement; live stack resumes at most once"),
    ("FC-REBIND-004", "AC-I06", "committed_worker_lost", "worker process unavailable", "successor fenced; transfer consumed as recovery_fenced_loss; recovery planner uses checkpoint"),
    ("FC-ART-001", "AC-A02", "failed_cell_with_staged_artifact", "cell raises", "published final artifacts=0"),
    ("FC-ART-002", "AC-A03", "second_artifact_stage_failure", "inject failure", "visible artifacts=0; staged rows reconcile/discard"),
    ("FC-ART-003", "AC-A04", "crash_after_cell_manifest_before_promotion", "restart", "manifest-authorized promotion occurs once"),
    ("FC-FINAL-001", "AC-J05", "planner_and_cell_completion_race", "two proposals", "one coordinator FinalizationManifest wins"),
    ("FC-FINAL-002", "AC-L03", "cancel_before_finalization_cas", "race", "no success/artifact visibility"),
    ("FC-FINAL-003", "AC-J05", "crash_after_finalization_cas", "restart", "same terminal result/artifacts are projected once"),
    ("FC-CHILD-001", "AC-S01", "crash_before_child_acceptance", "restart", "safe resend using child idempotency identity"),
    ("FC-CHILD-002", "AC-S03", "crash_after_child_acceptance", "restart", "lookup retained child; duplicate child starts=0"),
    ("FC-CHILD-003", "AC-S04", "parent_cancel_with_live_child", "cancel", "child cancellation requested and terminal parent waits or parks"),
    ("FC-EVID-001", "AC-E01", "reference_fake_profile", "admit full journey", "admission fails; no reference-only backend advertised as configured"),
    ("FC-CONTRACT-001", "AC-C02", "overlapping_bounded_quantifiers", "validate hostile completion regex", "profile rejects before engine compilation; no superlinear matcher work"),
    ("FC-CONTRACT-002", "AC-C02", "trailing_dollar_newline_divergence", "validate hostile completion regex", "profile rejects the nonportable end anchor"),
    ("FC-CONTRACT-003", "AC-C02", "ecmascript_unicode_identity_escape", "validate hostile completion regex", "profile rejects the nonportable identity escape"),
    ("FC-MIG-001", "AC-C04", "crash_before_begin", "terminate migrator", "baseline DB remains authoritative and byte-stable"),
    ("FC-MIG-002", "AC-C04", "crash_after_ddl_before_registry", "terminate transaction", "DDL and registry row roll back together"),
    ("FC-MIG-003", "AC-C04", "crash_after_registry_before_commit", "terminate transaction", "registry row and DDL remain absent after reopen"),
    ("FC-MIG-004", "AC-C04", "duplicate_apply", "run exact migration twice", "second apply is idempotent and digest-equal"),
    ("FC-MIG-005", "AC-C04", "integrity_failure", "inject foreign-key/integrity failure", "transaction rolls back and preserved copy remains recovery authority"),
    ("FC-MIG-006", "AC-C04", "committed_pages_only_in_wal", "create snapshot through SQLite backup API", "snapshot contains the WAL-only row and canonical populated row-set digest"),
    ("FC-MIG-007", "AC-C04", "crash_after_registry_before_attestation", "terminate migration transaction", "DDL, schema row and migration attestation are all absent after reopen"),
    ("FC-MIG-008", "AC-C04", "rollback_after_post_snapshot_write", "request prior-snapshot rollback", "cutover authority rejects rollback and v6 remains authoritative for forward recovery"),
    ("FC-MIG-009", "AC-C04", "missing_external_cutover_authority", "start candidate launcher", "launcher refuses both candidate admission and prior-snapshot selection"),
]
fault_matrix = {
    "schema_version": "aar.sdd-fault-matrix.v1",
    "cases": [
        {
            "id": case_id,
            "acceptance_id": acceptance_id,
            "barrier": barrier,
            "action": action,
            "expected": expected,
            "required": True,
        }
        for case_id, acceptance_id, barrier, action, expected in fault_cases
    ],
}

contracts = {
    "aar-rlm-workbench-v1.schema.json": workbench_contract,
    "aar-caller-work-v1.schema.json": caller_contract,
    "aar-artifact-publication-v1.schema.json": artifact_contract,
    "aar-workspace-broker-frame-v1.schema.json": worker_contract,
    "aar-acceptance-evidence-v1.schema.json": evidence_contract,
    "aar-migration-cutover-v1.schema.json": migration_contract,
}

SCHEMA_PREFIX = {
    "aar-rlm-workbench-v1.schema.json": "workbench",
    "aar-caller-work-v1.schema.json": "caller",
    "aar-artifact-publication-v1.schema.json": "artifact",
    "aar-workspace-broker-frame-v1.schema.json": "worker",
    "aar-acceptance-evidence-v1.schema.json": "evidence",
}


def bundle_schema(schema_ref: str) -> dict[str, Any]:
    filename, fragment = schema_ref.split("#", 1)
    def_name = fragment.removeprefix("/$defs/")
    collected: dict[tuple[str, str], dict[str, Any]] = {}
    in_progress: set[tuple[str, str]] = set()

    def alias(key: tuple[str, str]) -> str:
        return f"{SCHEMA_PREFIX[key[0]]}__{key[1]}"

    def split_ref(current_file: str, ref_value: str) -> tuple[str, str]:
        if ref_value.startswith("#/$defs/"):
            return current_file, ref_value.removeprefix("#/$defs/")
        target_file, target_fragment = ref_value.split("#", 1)
        return target_file, target_fragment.removeprefix("/$defs/")

    def collect(key: tuple[str, str]) -> None:
        if key in collected or key in in_progress:
            return
        document = contracts.get(key[0])
        if document is None or key[1] not in document["$defs"]:
            raise ValueError(f"unresolved contract ref: {key}")
        in_progress.add(key)
        rewritten = rewrite(deepcopy(document["$defs"][key[1]]), key[0])
        collected[key] = rewritten
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
            for key, item in value.items():
                if key != "$ref":
                    replacement[key] = rewrite(item, current_file)
            return replacement
        return {key: rewrite(item, current_file) for key, item in value.items()}

    document = contracts.get(filename)
    if document is None or def_name not in document["$defs"]:
        raise ValueError(f"unknown schema ref: {schema_ref}")
    root_schema = rewrite(deepcopy(document["$defs"][def_name]), filename)
    if collected:
        root_schema["$defs"] = {
            alias(key): value for key, value in sorted(collected.items(), key=lambda item: alias(item[0]))
        }
    root_schema["$schema"] = DRAFT
    Draft202012Validator.check_schema(root_schema)
    return root_schema


def build_full_tool_manifest(baseline_path: Path) -> tuple[Path, Path]:
    baseline_bytes = baseline_path.read_bytes()
    baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
    if baseline_hash != BASELINE_TOOL_MANIFEST_SHA256:
        raise ValueError(
            f"baseline tool manifest hash mismatch: expected {BASELINE_TOOL_MANIFEST_SHA256}, got {baseline_hash}"
        )
    baseline = json.loads(baseline_bytes.decode("utf-8"))
    if (
        baseline.get("server_version") != "0.4.0a6"
        or baseline.get("tool_surface_version") != "aar.mcp-tools.v7"
        or len(baseline.get("tools", [])) != 30
    ):
        raise ValueError("baseline tool manifest identity mismatch")

    additive_descriptors = []
    for tool in tool_surface["tools"]:
        annotations = dict(tool["annotations"])
        annotations["title"] = tool["title"]
        additive_descriptors.append(
            {
                "_meta": None,
                "annotations": annotations,
                "description": tool["description"],
                "execution": None,
                "icons": None,
                "inputSchema": bundle_schema(tool["input_schema"]),
                "name": tool["name"],
                "outputSchema": bundle_schema(tool["output_schema"]),
                "title": None,
            }
        )

    instructions = (
        baseline["instructions"]
        + " AR-RW v8: call aar_rlm_workbench_capabilities before admitting a workbench job; "
        + "caller-delegated jobs require start_only=true and the claim/mark-send-started/commit protocol."
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
    combined = {**core, "tool_surface_digest": canonical_digest(core)}
    combined_path = CONTRACTS / "aar-mcp-tools-v8-combined.json"
    binding_path = CONTRACTS / "aar-mcp-tools-v7-binding.json"
    write_json(combined_path, combined)
    write_json(
        binding_path,
        {
            "schema_version": "aar.sdd-baseline-tool-binding.v1",
            "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
            "source_path": "schemas/aar-mcp-tools-v7.json",
            "sha256": f"sha256:{baseline_hash}",
            "size_bytes": len(baseline_bytes),
            "tool_count": 30,
            "tool_names": [tool["name"] for tool in baseline["tools"]],
            "tool_surface_version": baseline["tool_surface_version"],
            "tool_surface_digest": baseline["tool_surface_digest"],
        },
    )
    return combined_path, binding_path


def build_fixtures() -> dict[str, Any]:
    def normalize_profile_schema(value: Any) -> None:
        if isinstance(value, dict):
            required = value.get("required")
            if isinstance(required, list):
                value["required"] = sorted(required)
            for child in value.values():
                normalize_profile_schema(child)
        elif isinstance(value, list):
            for child in value:
                normalize_profile_schema(child)

    planner_schema: dict[str, Any] = deepcopy(rlm_directive)
    planner_schema["$schema"] = DRAFT
    normalize_profile_schema(planner_schema)
    output_schema = {
        "$schema": DRAFT,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string", "maxLength": 65536},
            "artifacts": {
                "type": "array",
                "items": {"type": "string", "maxLength": 255},
                "maxItems": 32,
            },
        },
        "required": ["answer", "artifacts"],
    }
    profile = {
        "schema_version": "aar.model-route.v1",
        "profile_id": "hermes-codex-luna-max",
        "provider_driver": "openai-codex",
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "max_output_tokens": 32768,
        "fallback_policy": "none",
        "cache_policy": "disabled",
    }
    catalog_payload = {
        "schema_version": "aar.model-route.v1",
        "profiles": [profile],
    }
    route_binding = {
        **profile,
        "catalog_digest": canonical_digest(catalog_payload),
        "profile_digest": canonical_digest(profile),
    }
    planner_contract = {
        "schema_version": "aar.json-contract.v1",
        "dialect": DRAFT,
        "profile": "aar.json-schema-profile.v1",
        "schema_digest": canonical_digest(planner_schema),
        "schema": planner_schema,
        "max_instance_bytes": 262144,
    }
    output_contract = {
        "schema_version": "aar.json-contract.v1",
        "dialect": DRAFT,
        "profile": "aar.json-schema-profile.v1",
        "schema_digest": canonical_digest(output_schema),
        "schema": output_schema,
        "max_instance_bytes": 262144,
    }
    spec = {
        "schema_version": "aar.rlm-workbench-job.v1",
        "objective": "Produce a verified implementation artifact bundle.",
        "strategy": "python_workbench",
        "workspace": {
            "mode": "operation_scoped",
            "backend": "ipython",
            "security_profile": "trusted_local",
            "seed_checkpoint": None,
            "terminal_disposition": "close_after_terminal_checkpoint",
        },
        "model": {
            "execution_mode": "caller_delegated",
            "route_binding": route_binding,
            "max_corrections": 2,
        },
        "budgets": {
            "total_wall_time_ms": 900000,
            "cell_wall_time_ms": 60000,
            "max_cells": 32,
            "max_model_calls": 40,
            "max_subagent_calls": 8,
            "max_recursion_depth": 2,
            "max_artifact_count": 32,
            "max_artifact_bytes": 33554432,
            "max_result_bytes": 262144,
            "max_events_per_cell": 128,
            "max_output_chars_per_cell": 65536,
        },
        "artifacts": {
            "publication": "finalization_manifest",
            "duplicate_name_policy": "reject",
            "allowed_media_types": ["application/json", "text/markdown", "text/plain"],
        },
        "completion": {
            "output_contract": output_contract,
            "require_named_artifacts": True,
            "allow_optional_live_children": False,
        },
        "metadata": {"purpose": "contract-fixture"},
    }
    context = {
        "schema_version": "aar.mcp-rlm-workbench-context.v1",
        "principal_id": "fixture-principal",
        "session_id": "fixture-session",
        "runtime_generation": 1,
        "capability_digest": "sha256:" + "1" * 64,
        "request_id": "fixture-request",
        "idempotency_key": "fixture-idempotency-key",
        "deadline_unix_ms": 2000000000000,
        "grant_ids": [
            "fixture-artifact-grant",
            "fixture-model-grant",
            "fixture-rlm-grant",
            "fixture-subagent-grant",
        ],
        "budget_wall_time_ms": 900000,
        "budget_model_requests": 40,
        "budget_input_tokens": 1000000,
        "budget_output_tokens": 500000,
        "budget_child_operations": 8,
        "budget_artifact_bytes": 33554432,
    }
    valid = {"context": context, "spec": spec, "start_only": True}
    invalid_start = deepcopy(valid)
    invalid_start["start_only"] = False
    invalid_grants = deepcopy(valid)
    invalid_grants["spec"]["grants"] = []
    invalid_route = deepcopy(valid)
    del invalid_route["spec"]["model"]["route_binding"]["profile_digest"]
    invalid_fallback = deepcopy(valid)
    invalid_fallback["spec"]["model"]["route_binding"]["fallback_policy"] = "explicit"
    invalid_planner_override = deepcopy(valid)
    invalid_planner_override["spec"]["model"]["planner_response_contract"] = {
        "schema_version": "aar.json-contract.v1",
        "dialect": DRAFT,
        "profile": "aar.json-schema-profile.v1",
        "schema_digest": canonical_digest({"$schema": DRAFT, "type": "string"}),
        "schema": {"$schema": DRAFT, "type": "string"},
        "max_instance_bytes": 1024,
    }
    invalid_remote_ref = deepcopy(valid)
    invalid_remote_ref["spec"]["completion"]["output_contract"]["schema"] = {
        "$schema": DRAFT,
        "$ref": "https://example.invalid/schema.json",
    }
    invalid_remote_ref["spec"]["completion"]["output_contract"]["schema_digest"] = canonical_digest(
        invalid_remote_ref["spec"]["completion"]["output_contract"]["schema"]
    )
    profile_mutants: dict[str, dict[str, Any]] = {}
    for filename, schema in {
        "invalid-schema-format.json": {"$schema": DRAFT, "type": "string", "format": "email"},
        "invalid-schema-pattern-properties.json": {
            "$schema": DRAFT,
            "type": "object",
            "patternProperties": {".*": {"type": "string"}},
        },
        "invalid-schema-missing-local-ref.json": {
            "$schema": DRAFT,
            "$defs": {},
            "$ref": "#/$defs/Missing",
        },
        "invalid-schema-lookbehind.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": "(?<=x)y",
        },
        "invalid-schema-python-anchors.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": r"\Afoo\Z",
        },
        "invalid-schema-nested-quantifier.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": "^(a+)+$",
        },
        "invalid-schema-possessive-quantifier.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": "^a++$",
        },
        "invalid-schema-alternation.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": "foo|bar",
        },
        "invalid-schema-overlapping-quantified-atoms.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": "^" + "a?" * 35 + "a{35}b",
        },
        "invalid-schema-trailing-end-anchor.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": "a$",
        },
        "invalid-schema-ecmascript-identity-escape.json": {
            "$schema": DRAFT,
            "type": "string",
            "pattern": r"\-",
        },
    }.items():
        mutant = deepcopy(valid)
        mutant["spec"]["completion"]["output_contract"]["schema"] = schema
        mutant["spec"]["completion"]["output_contract"]["schema_digest"] = canonical_digest(schema)
        profile_mutants[filename] = mutant
    portable_schema = {
        "$schema": DRAFT,
        "type": "string",
        "pattern": "^[A-Za-z0-9_]{1,64}",
    }
    valid_portable_pattern = deepcopy(valid)
    valid_portable_pattern["spec"]["completion"]["output_contract"]["schema"] = portable_schema
    valid_portable_pattern["spec"]["completion"]["output_contract"]["schema_digest"] = canonical_digest(
        portable_schema
    )
    deep_schema: dict[str, Any] = {"type": "string"}
    for _ in range(35):
        deep_schema = {"allOf": [deep_schema]}
    deep_schema["$schema"] = DRAFT
    invalid_deep = deepcopy(valid)
    invalid_deep["spec"]["completion"]["output_contract"]["schema"] = deep_schema
    invalid_deep["spec"]["completion"]["output_contract"]["schema_digest"] = canonical_digest(deep_schema)
    invalid_instance_limit = deepcopy(valid)
    invalid_instance_limit["spec"]["completion"]["output_contract"]["max_instance_bytes"] = 1_048_577

    def contract_row(
        method: str,
        contract_id: str,
        request_file: str,
        request_def: str,
        response_file: str,
        response_def: str,
    ) -> dict[str, Any]:
        return {
            "method": method,
            "contract_id": contract_id,
            "request_schema_digest": canonical_digest(contracts[request_file]["$defs"][request_def]),
            "response_schema_digest": canonical_digest(contracts[response_file]["$defs"][response_def]),
        }

    contract_rows = [
        contract_row("model.request", "aar.broker-contract.model-request.v2", "aar-caller-work-v1.schema.json", "ModelRequestV2", "aar-caller-work-v1.schema.json", "ModelObservation"),
        contract_row("subagent.submit", "aar.broker-contract.subagent-submit.v2", "aar-caller-work-v1.schema.json", "SubagentSubmitV2", "aar-caller-work-v1.schema.json", "ChildObservation"),
        contract_row("subagent.result", "aar.broker-contract.subagent-result.v2", "aar-caller-work-v1.schema.json", "SubagentResultV2", "aar-caller-work-v1.schema.json", "ChildObservation"),
        contract_row("evidence.query", "aar.broker-contract.evidence-query.v2", "aar-caller-work-v1.schema.json", "EvidenceQueryV2", "aar-caller-work-v1.schema.json", "EvidenceObservation"),
        contract_row("artifact.put", "aar.artifact-stage.v1", "aar-artifact-publication-v1.schema.json", "ArtifactStage", "aar-artifact-publication-v1.schema.json", "ArtifactBinding"),
        contract_row("effect.propose", "aar.broker-contract.effect-propose.v2", "aar-caller-work-v1.schema.json", "EffectProposeV2", "aar-caller-work-v1.schema.json", "EffectObservation"),
    ]
    broker_catalog_core = {"schema_version": "aar.broker-catalog.v2", "contracts": contract_rows}
    broker_catalog = {**broker_catalog_core, "catalog_digest": canonical_digest(broker_catalog_core)}
    availability = []
    for row in contract_rows:
        caller_driven = row["method"] != "artifact.put"
        availability.append(
            {
                **row,
                "backend_kind": "caller_driver" if caller_driven else "native",
                "configured": True,
                "reference_only": False,
                "adapter_id": "fixture-caller-driver" if caller_driven else "fixture-artifact-store",
                "adapter_generation": 1,
                "evidence_tier": "host_receipt_bound",
            }
        )
    combined_surface = json.loads((CONTRACTS / "aar-mcp-tools-v8-combined.json").read_text(encoding="utf-8"))
    valid_capability = {
        "schema_version": "aar.rlm-workbench-capability.v1",
        "surface_version": "aar.mcp-tools.v8",
        "tool_surface_digest": combined_surface["tool_surface_digest"],
        "broker_catalog_digest": broker_catalog["catalog_digest"],
        "planner_directive_schema_version": "aar.rlm-directive.v1",
        "planner_directive_schema_digest": canonical_digest(rlm_directive),
        "security_profiles": ["trusted_local"],
        "methods": availability,
    }
    invalid_capability_digest = deepcopy(valid_capability)
    invalid_capability_digest["methods"][0]["request_schema_digest"] = "sha256:" + "0" * 64
    invalid_capability_planner = deepcopy(valid_capability)
    invalid_capability_planner["planner_directive_schema_digest"] = "sha256:" + "0" * 64
    invalid_capability_reference = deepcopy(valid_capability)
    invalid_capability_reference["methods"][0].update(
        {
            "backend_kind": "reference",
            "configured": True,
            "reference_only": True,
            "adapter_id": None,
            "adapter_generation": None,
            "evidence_tier": "unknown",
        }
    )
    operation_fixture = {"type": "operation", "value": "fixture-operation"}
    workspace_fixture = {"type": "workspace", "value": "fixture-workspace"}
    request_fixture = {
        "contract_id": "aar.broker-contract.model-request.v2",
        "method": "model.request",
        "prompt": "fixture",
        "response_contract": planner_contract,
        "route_binding": route_binding,
        "max_output_bytes": 4096,
    }
    claimant_fixture = {
        "principal_id": "fixture-principal",
        "session_id": "fixture-session",
        "adapter_id": "fixture-adapter",
        "adapter_generation": 1,
        "claim_id": "fixture-claim",
        "claim_fence": "sha256:" + "7" * 64,
        "claim_expires_at_unix_ms": 2_000_000_000_000,
    }
    physical_attempt_fixture = {
        "physical_attempt_id": "fixture-physical-attempt",
        "provider_or_child_idempotency_key": "fixture-provider-key",
        "lookup_supported": True,
        "cancel_supported": True,
        "send_started_at_unix_ms": None,
        "sent_request_digest": None,
        "sent_at_unix_ms": None,
        "provider_or_child_request_id": None,
    }
    valid_send_reserved = {
        "schema_version": "aar.caller-work-ticket.v1",
        "operation": operation_fixture,
        "suspension_revision": 1,
        "ticket_id": "fixture-ticket",
        "revision": 1,
        "owner": {"kind": "planner", "phase": "initial", "step_index": 0},
        "request": request_fixture,
        "request_digest": canonical_digest(request_fixture),
        "ticket_digest": "sha256:" + "2" * 64,
        "state": "send_reserved",
        "claimant": claimant_fixture,
        "physical_attempt": physical_attempt_fixture,
        "settled_receipt_digest": None,
        "settled_at_unix_ms": None,
        "deadline_unix_ms": 2_000_000_000_000,
    }
    invalid_send_reserved = deepcopy(valid_send_reserved)
    invalid_send_reserved["claimant"] = None
    valid_send_started = deepcopy(valid_send_reserved)
    valid_send_started["state"] = "send_started"
    valid_send_started["revision"] = 2
    valid_send_started["physical_attempt"]["send_started_at_unix_ms"] = 1_999_999_999_000
    valid_send_started["physical_attempt"]["sent_request_digest"] = valid_send_started[
        "request_digest"
    ]
    invalid_send_started = deepcopy(valid_send_started)
    invalid_send_started["physical_attempt"]["sent_request_digest"] = None
    valid_cancelled_before_send_reserved = deepcopy(valid_send_reserved)
    valid_cancelled_before_send_reserved["revision"] = 2
    valid_cancelled_before_send_reserved["state"] = "cancelled_before_send"
    valid_cancelled_before_send_reserved["settled_receipt_digest"] = "sha256:" + "6" * 64
    valid_cancelled_before_send_reserved["settled_at_unix_ms"] = 2_000_000_000_005
    valid_cancelled_before_send_pending = deepcopy(valid_cancelled_before_send_reserved)
    valid_cancelled_before_send_pending["claimant"] = None
    valid_cancelled_before_send_pending["physical_attempt"] = None
    invalid_cancelled_before_send = deepcopy(valid_cancelled_before_send_reserved)
    invalid_cancelled_before_send["physical_attempt"][
        "send_started_at_unix_ms"
    ] = 1_999_999_999_000
    valid_cancelled_certain = deepcopy(valid_send_started)
    valid_cancelled_certain["revision"] = 3
    valid_cancelled_certain["state"] = "cancelled_certain"
    valid_cancelled_certain["settled_receipt_digest"] = "sha256:" + "7" * 64
    valid_cancelled_certain["settled_at_unix_ms"] = 2_000_000_000_010
    invalid_cancelled_certain = deepcopy(valid_cancelled_certain)
    invalid_cancelled_certain["settled_receipt_digest"] = None
    valid_phase_projection = {
        "phase": "waiting_external",
        "legacy_operation_state": "accepted",
    }
    invalid_phase_projection = deepcopy(valid_phase_projection)
    invalid_phase_projection["legacy_operation_state"] = "running"
    valid_model_observation = {
        "kind": "model",
        "outcome": "failed_certain",
        "output_text": None,
        "output_digest": None,
        "route_receipt_digest": None,
        "usage_receipt_digest": None,
        "host_receipt_digest": "sha256:" + "5" * 64,
    }
    invalid_model_observation = deepcopy(valid_model_observation)
    invalid_model_observation["output_text"] = "must-not-exist"
    valid_failure = {
        "schema_version": "aar.envelope.v1",
        "category": "internal",
        "code": "INTERNAL_ERROR",
        "message": "bounded failure fixture",
        "retryable": False,
        "certainty": "certain",
        "operation": None,
        "details": [],
    }
    invalid_failure = deepcopy(valid_failure)
    invalid_failure["code"] = "UNKNOWN_EXTERNAL_CODE"
    invalid_failure_details = deepcopy(valid_failure)
    invalid_failure_details["details"] = [
        {"name": f"detail-{index}", "value": index}
        for index in range(33)
    ]
    broker_context_fixture = {
        "schema_version": "aar.broker-context.v2",
        "operation": operation_fixture,
        "attempt_id": "fixture-attempt",
        "attempt_fence": "sha256:" + "6" * 64,
        "workspace": workspace_fixture,
        "workspace_generation": 1,
        "workspace_revision_before": 0,
        "cell_execution_id": "fixture-cell",
        "broker_call_ordinal": 1,
        "contract_id": "aar.broker-contract.model-request.v2",
        "grant_id": "fixture-model-grant",
        "deadline_unix_ms": 2_000_000_000_000,
        "idempotency_key": "fixture-broker-idempotency",
    }
    broker_payload_fixture = {
        "context": broker_context_fixture,
        "method": "model.request",
        "contract_id": "aar.broker-contract.model-request.v2",
        "request_digest": canonical_digest(request_fixture),
        "request": request_fixture,
    }
    valid_worker_intent = {
        "schema_version": "aar.workspace-broker-frame.v1",
        "operation": operation_fixture,
        "attempt_id": "fixture-attempt",
        "attempt_fence": "sha256:" + "6" * 64,
        "workspace": workspace_fixture,
        "workspace_generation": 1,
        "workspace_revision": 0,
        "cell_execution_id": "fixture-cell",
        "frame_sequence": 1,
        "broker_call_ordinal": 1,
        "payload_digest": canonical_digest(broker_payload_fixture),
        "payload_bytes": len(canonical_bytes(broker_payload_fixture)),
        "deadline_unix_ms": 2_000_000_000_000,
        "kind": "broker_intent",
        "direction": "worker_to_supervisor",
        "payload": broker_payload_fixture,
        "committed": False,
    }
    invalid_worker_intent = deepcopy(valid_worker_intent)
    invalid_worker_intent["payload"] = {}

    artifact_binding_fixture = {
        "schema_version": "aar.artifact-binding.v1",
        "logical_name": "reports/result.json",
        "media_type": "application/json",
        "digest": "sha256:" + "3" * 64,
        "size_bytes": 128,
        "operation": operation_fixture,
        "workspace": workspace_fixture,
        "workspace_generation": 1,
        "cell_execution_id": "fixture-cell",
        "role": "result",
    }
    model_observation_fixture = {
        "kind": "model",
        "outcome": "succeeded",
        "output_text": "fixture output",
        "output_digest": "sha256:" + "4" * 64,
        "route_receipt_digest": "sha256:" + "5" * 64,
        "usage_receipt_digest": "sha256:" + "6" * 64,
        "host_receipt_digest": "sha256:" + "7" * 64,
    }
    rebind_token_fixture = {
        "schema_version": "aar.workspace-successor-rebind.v1",
        "operation": operation_fixture,
        "rebind_generation": 1,
        "prior_authority_generation": 1,
        "successor_authority_generation": 2,
        "expected_control_revision": 3,
        "expected_cancellation_revision": 0,
        "expected_cumulative_deadline_unix_ms": 2_000_000_000_000,
        "prior_attempt_id": "fixture-prior-attempt",
        "prior_attempt_fence": "sha256:" + "1" * 64,
        "successor_attempt_id": "fixture-successor-attempt",
        "successor_attempt_fence": "sha256:" + "2" * 64,
        "workspace": workspace_fixture,
        "workspace_generation": 1,
        "workspace_revision": 0,
        "cell_execution_id": "fixture-cell",
        "suspension_revision": 1,
        "ticket_id": "fixture-ticket",
        "settled_receipt_digest": "sha256:" + "3" * 64,
        "successor_outbox_digest": "sha256:" + "4" * 64,
        "worker_owner_generation": 1,
        "worker_process_identity_digest": "sha256:" + "5" * 64,
        "expires_at_unix_ms": 2_000_000_000_000,
        "single_use": True,
        "token_digest": "sha256:" + "6" * 64,
    }
    protocol_error_fixture = {
        "code": "stale_authority",
        "message": "fixture protocol error",
        "offending_frame_sequence": 1,
        "offending_payload_digest": "sha256:" + "7" * 64,
        "fatal": True,
    }
    frame_specs = [
        (
            "execute-request",
            "execute_request",
            "supervisor_to_worker",
            True,
            None,
            {
                "source_utf8": "value = 1",
                "source_digest": canonical_digest("value = 1"),
                "expected_pre_revision": 0,
                "expected_post_revision": 1,
                "pre_checkpoint_digest": "sha256:" + "1" * 64,
                "result_contract_digest": "sha256:" + "2" * 64,
                "capture_limit_bytes": 65_536,
            },
        ),
        (
            "stdout",
            "stdout",
            "worker_to_supervisor",
            False,
            None,
            {"chunk_utf8": "fixture stdout", "stream_offset_bytes": 0, "truncated": False},
        ),
        (
            "stderr",
            "stderr",
            "worker_to_supervisor",
            False,
            None,
            {"chunk_utf8": "fixture stderr", "stream_offset_bytes": 0, "truncated": False},
        ),
        (
            "rich-display",
            "rich_display",
            "worker_to_supervisor",
            False,
            None,
            {
                "display_id": "fixture-display",
                "media_type": "application/json",
                "content_digest": "sha256:" + "3" * 64,
                "content_size_bytes": 128,
                "worker_buffer_id": "fixture-display-buffer",
            },
        ),
        (
            "progress",
            "progress",
            "worker_to_supervisor",
            False,
            None,
            {"completed_units": 1, "total_units": 2, "message": "fixture progress"},
        ),
        (
            "broker-intent-typed",
            "broker_intent",
            "worker_to_supervisor",
            False,
            1,
            broker_payload_fixture,
        ),
        (
            "artifact-stage-intent",
            "artifact_stage_intent",
            "worker_to_supervisor",
            False,
            None,
            {
                "binding": artifact_binding_fixture,
                "content_digest": artifact_binding_fixture["digest"],
                "content_size_bytes": artifact_binding_fixture["size_bytes"],
                "worker_buffer_id": "fixture-artifact-buffer",
                "promotion_policy": "cell_commit",
            },
        ),
        (
            "completion-intent",
            "completion_intent",
            "worker_to_supervisor",
            False,
            None,
            {
                "disposition": "succeed",
                "result_digest": "sha256:" + "4" * 64,
                "result_size_bytes": 128,
                "output_contract_digest": "sha256:" + "5" * 64,
                "required_artifact_stage_ids": ["fixture-stage"],
                "reason": None,
            },
        ),
        (
            "execute-result",
            "execute_result",
            "worker_to_supervisor",
            False,
            None,
            {
                "status": "completed",
                "pre_workspace_revision": 0,
                "post_workspace_revision": 1,
                "result_digest": "sha256:" + "4" * 64,
                "checkpoint_digest": "sha256:" + "5" * 64,
                "failure": None,
            },
        ),
        (
            "broker-receipt",
            "broker_receipt",
            "supervisor_to_worker",
            True,
            1,
            {
                "context": broker_context_fixture,
                "contract_id": "aar.broker-contract.model-request.v2",
                "request_digest": canonical_digest(request_fixture),
                "receipt_digest": "sha256:" + "6" * 64,
                "observation": model_observation_fixture,
            },
        ),
        (
            "broker-suspend",
            "broker_suspend",
            "supervisor_to_worker",
            True,
            1,
            {
                "ticket": {
                    "ticket_id": "fixture-ticket",
                    "revision": 1,
                    "ticket_digest": "sha256:" + "2" * 64,
                    "state": "pending",
                },
                "suspension_revision": 1,
                "control_revision": 3,
                "request_digest": canonical_digest(request_fixture),
                "logical_owner_digest": "sha256:" + "3" * 64,
                "pre_checkpoint_digest": "sha256:" + "4" * 64,
            },
        ),
        *(
            (
                f"rebind-{phase}",
                f"rebind_{phase}",
                "supervisor_to_worker",
                True,
                None,
                {"token": rebind_token_fixture, "phase": phase},
            )
            for phase in ("prepare", "commit", "abort")
        ),
        (
            "rebind-ack",
            "rebind_ack",
            "worker_to_supervisor",
            False,
            None,
            {
                "token_digest": rebind_token_fixture["token_digest"],
                "acknowledged_phase": "committed",
                "worker_process_identity_digest": rebind_token_fixture[
                    "worker_process_identity_digest"
                ],
                "observed_workspace_revision": 0,
                "live_stack_resumed": True,
                "ack_digest": "sha256:" + "7" * 64,
            },
        ),
        (
            "cancel",
            "cancel",
            "supervisor_to_worker",
            True,
            None,
            {
                "reason": "user_requested",
                "control_revision": 4,
                "cancellation_revision": 1,
                "effective_deadline_unix_ms": 2_000_000_000_000,
                "cancellation_receipt_digest": "sha256:" + "7" * 64,
            },
        ),
        (
            "protocol-error-worker",
            "worker_protocol_error",
            "worker_to_supervisor",
            False,
            None,
            protocol_error_fixture,
        ),
        (
            "protocol-error-supervisor",
            "supervisor_protocol_error",
            "supervisor_to_worker",
            False,
            None,
            protocol_error_fixture,
        ),
    ]

    def frame_fixture(
        kind: str,
        direction: str,
        committed: bool,
        broker_call_ordinal: int | None,
        payload: dict[str, Any],
        sequence: int,
    ) -> dict[str, Any]:
        return {
            "schema_version": "aar.workspace-broker-frame.v1",
            "operation": operation_fixture,
            "attempt_id": "fixture-attempt",
            "attempt_fence": "sha256:" + "6" * 64,
            "workspace": workspace_fixture,
            "workspace_generation": 1,
            "workspace_revision": 0,
            "cell_execution_id": "fixture-cell",
            "frame_sequence": sequence,
            "broker_call_ordinal": broker_call_ordinal,
            "payload_digest": canonical_digest(payload),
            "payload_bytes": len(canonical_bytes(payload)),
            "deadline_unix_ms": 2_000_000_000_000,
            "kind": kind,
            "direction": direction,
            "payload": payload,
            "committed": committed,
        }

    frame_fixture_files: dict[str, dict[str, Any]] = {}
    frame_negative_entries: list[dict[str, Any]] = []
    for sequence, (slug, kind, direction, committed, ordinal, payload) in enumerate(
        frame_specs, start=1
    ):
        valid_name = f"valid-worker-{slug}.json"
        valid_frame = frame_fixture(
            kind, direction, committed, ordinal, deepcopy(payload), sequence
        )
        frame_fixture_files[valid_name] = valid_frame
        mutations: list[tuple[str, str, str]] = [
            ("empty-payload", "/payload", "replace"),
            ("missing-payload-field", f"/payload/{next(iter(payload))}", "remove"),
            ("extra-payload-field", "/payload/unexpected", "add"),
            ("wrong-direction", "/direction", "replace"),
        ]
        for mutation_name, pointer, mutation_op in mutations:
            invalid_name = f"invalid-worker-{slug}-{mutation_name}.json"
            invalid_frame = deepcopy(valid_frame)
            if mutation_name == "empty-payload":
                invalid_frame["payload"] = {}
            elif mutation_name == "missing-payload-field":
                del invalid_frame["payload"][next(iter(payload))]
            elif mutation_name == "extra-payload-field":
                invalid_frame["payload"]["unexpected"] = True
            else:
                invalid_frame["direction"] = (
                    "worker_to_supervisor"
                    if direction == "supervisor_to_worker"
                    else "supervisor_to_worker"
                )
            frame_fixture_files[invalid_name] = invalid_frame
            frame_negative_entries.append(
                {
                    "path": invalid_name,
                    "valid_path": valid_name,
                    "mutation_pointer": pointer,
                    "mutation_op": mutation_op,
                    "contract_file": "aar-workspace-broker-frame-v1.schema.json",
                    "definition": "WorkspaceBrokerFrame",
                }
            )

    migration_attestation_fixture = {
        "schema_version": "aar.migration-v6-attestation-payload.v1",
        "migration_version": 6,
        "cutover_epoch": "cutover-fixture",
        "snapshot_id": "snapshot-fixture",
        "snapshot_sha256": "sha256:" + "1" * 64,
        "snapshot_size_bytes": 4096,
        "canonical_v5_row_set_digest": "sha256:" + "2" * 64,
        "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "wheel_digest": "sha256:" + "3" * 64,
        "profile_digest": "sha256:" + "4" * 64,
        "skill_digest": "sha256:" + "5" * 64,
        "contract_manifest_digest": "sha256:" + "6" * 64,
        "migration_sql_digest": "sha256:" + "7" * 64,
        "external_authority_store_id": "cutover-authority-fixture",
        "external_authority_prepared_digest": "sha256:" + "8" * 64,
        "started_at_unix_ms": 2_000_000_000_000,
        "completed_at_unix_ms": 2_000_000_000_001,
        "foreign_key_violation_count": 0,
        "integrity_result": "ok",
    }
    valid_migration_attestation = {
        "attestation": migration_attestation_fixture,
        "attestation_digest": canonical_digest(migration_attestation_fixture),
    }

    def migration_event(
        sequence: int,
        previous_event_digest: str | None,
        state: str,
        barrier_digest: str | None,
        decision_digest: str | None,
    ) -> dict[str, Any]:
        event = {
            "schema_version": "aar.migration-cutover-event-payload.v1",
            "cutover_epoch": "cutover-fixture",
            "sequence": sequence,
            "previous_event_digest": previous_event_digest,
            "state": state,
            "occurred_at_unix_ms": 2_000_000_000_010 + sequence,
            "migration_attestation_digest": (
                None if state == "prepared" else valid_migration_attestation["attestation_digest"]
            ),
            "candidate_readback_digest": (
                None if state == "prepared" else "sha256:" + "a" * 64
            ),
            "post_snapshot_barrier_digest": barrier_digest,
            "decision_digest": decision_digest,
        }
        return {"event": event, "event_digest": canonical_digest(event)}

    prepared_cutover = migration_event(0, None, "prepared", None, None)
    active_cutover = migration_event(
        1,
        prepared_cutover["event_digest"],
        "candidate_active",
        None,
        None,
    )
    post_write_cutover = migration_event(
        2,
        active_cutover["event_digest"],
        "post_snapshot_write",
        "sha256:" + "b" * 64,
        None,
    )
    retired_cutover = migration_event(
        3,
        post_write_cutover["event_digest"],
        "retired_forward",
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
    )
    valid_cutover_ledger = {
        "schema_version": "aar.migration-cutover-authority.v1",
        "authority_store_id": "cutover-authority-fixture",
        "cutover_epoch": "cutover-fixture",
        "snapshot_id": "snapshot-fixture",
        "snapshot_sha256": "sha256:" + "1" * 64,
        "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "wheel_digest": "sha256:" + "3" * 64,
        "events": [prepared_cutover, active_cutover, post_write_cutover, retired_cutover],
        "head_event_digest": retired_cutover["event_digest"],
    }
    invalid_cutover_atomic = deepcopy(valid_cutover_ledger)
    invalid_cutover_atomic["events"][3]["event"]["state"] = "rolled_back_before_write"
    invalid_cutover_semantic = deepcopy(valid_cutover_ledger)
    semantic_event = invalid_cutover_semantic["events"][3]["event"]
    semantic_event["state"] = "rolled_back_before_write"
    semantic_event["post_snapshot_barrier_digest"] = None
    invalid_cutover_semantic["events"][3]["event_digest"] = canonical_digest(semantic_event)
    invalid_cutover_semantic["head_event_digest"] = invalid_cutover_semantic["events"][3][
        "event_digest"
    ]
    negative_contract_index = {
        "schema_version": "aar.sdd-negative-contract-index.v1",
        "fixtures": [
            {
                "path": "invalid-ticket-send-reserved-unclaimed.json",
                "valid_path": "valid-ticket-send-reserved.json",
                "mutation_pointer": "/claimant",
                "contract_file": "aar-caller-work-v1.schema.json",
                "definition": "CallerWorkTicket",
            },
            {
                "path": "invalid-ticket-send-started-missing-request-digest.json",
                "valid_path": "valid-ticket-send-started.json",
                "mutation_pointer": "/physical_attempt/sent_request_digest",
                "contract_file": "aar-caller-work-v1.schema.json",
                "definition": "CallerWorkTicket",
            },
            {
                "path": "invalid-ticket-cancelled-before-send-has-send-mark.json",
                "valid_path": "valid-ticket-cancelled-before-send-reserved.json",
                "mutation_pointer": "/physical_attempt/send_started_at_unix_ms",
                "contract_file": "aar-caller-work-v1.schema.json",
                "definition": "CallerWorkTicket",
            },
            {
                "path": "invalid-ticket-cancelled-certain-missing-settlement.json",
                "valid_path": "valid-ticket-cancelled-certain.json",
                "mutation_pointer": "/settled_receipt_digest",
                "contract_file": "aar-caller-work-v1.schema.json",
                "definition": "CallerWorkTicket",
            },
            {
                "path": "invalid-planner-schema-override.json",
                "valid_path": "valid-workbench-execute.json",
                "mutation_pointer": "/spec/model/planner_response_contract",
                "mutation_op": "add",
                "contract_file": "aar-rlm-workbench-v1.schema.json",
                "definition": "RlmWorkbenchExecuteInput",
            },
            {
                "path": "invalid-phase-projection-mismatch.json",
                "valid_path": "valid-phase-projection.json",
                "mutation_pointer": "/legacy_operation_state",
                "contract_file": "aar-rlm-workbench-v1.schema.json",
                "definition": "PhaseProjection",
            },
            {
                "path": "invalid-model-observation-output-on-failure.json",
                "valid_path": "valid-model-observation-failure.json",
                "mutation_pointer": "/output_text",
                "contract_file": "aar-caller-work-v1.schema.json",
                "definition": "ModelObservation",
            },
            {
                "path": "invalid-failure-unknown-code.json",
                "valid_path": "valid-failure.json",
                "mutation_pointer": "/code",
                "contract_file": "aar-rlm-workbench-v1.schema.json",
                "definition": "Failure",
            },
            {
                "path": "invalid-failure-details-overflow.json",
                "valid_path": "valid-failure.json",
                "mutation_pointer": "/details",
                "contract_file": "aar-rlm-workbench-v1.schema.json",
                "definition": "Failure",
            },
            {
                "path": "invalid-worker-empty-broker-intent.json",
                "valid_path": "valid-worker-broker-intent.json",
                "mutation_pointer": "/payload",
                "contract_file": "aar-workspace-broker-frame-v1.schema.json",
                "definition": "WorkspaceBrokerFrame",
            },
            *frame_negative_entries,
            {
                "path": "invalid-cutover-post-write-rollback.json",
                "valid_path": "valid-cutover-forward-ledger.json",
                "mutation_pointer": "/events/3/event/state",
                "contract_file": "aar-migration-cutover-v1.schema.json",
                "definition": "CutoverAuthorityLedger",
            },
        ],
    }
    return {
        "valid-route-catalog.json": catalog_payload,
        "valid-broker-catalog.json": broker_catalog,
        "valid-workbench-capabilities.json": valid_capability,
        "invalid-capability-schema-digest.json": invalid_capability_digest,
        "invalid-capability-planner-digest.json": invalid_capability_planner,
        "invalid-capability-reference-only.json": invalid_capability_reference,
        "negative-contract-index.json": negative_contract_index,
        "valid-ticket-send-reserved.json": valid_send_reserved,
        "invalid-ticket-send-reserved-unclaimed.json": invalid_send_reserved,
        "valid-ticket-send-started.json": valid_send_started,
        "invalid-ticket-send-started-missing-request-digest.json": invalid_send_started,
        "valid-ticket-cancelled-before-send-pending.json": valid_cancelled_before_send_pending,
        "valid-ticket-cancelled-before-send-reserved.json": valid_cancelled_before_send_reserved,
        "invalid-ticket-cancelled-before-send-has-send-mark.json": invalid_cancelled_before_send,
        "valid-ticket-cancelled-certain.json": valid_cancelled_certain,
        "invalid-ticket-cancelled-certain-missing-settlement.json": invalid_cancelled_certain,
        "valid-phase-projection.json": valid_phase_projection,
        "invalid-phase-projection-mismatch.json": invalid_phase_projection,
        "valid-model-observation-failure.json": valid_model_observation,
        "invalid-model-observation-output-on-failure.json": invalid_model_observation,
        "valid-failure.json": valid_failure,
        "invalid-failure-unknown-code.json": invalid_failure,
        "invalid-failure-details-overflow.json": invalid_failure_details,
        "valid-worker-broker-intent.json": valid_worker_intent,
        "invalid-worker-empty-broker-intent.json": invalid_worker_intent,
        **frame_fixture_files,
        "valid-migration-attestation.json": valid_migration_attestation,
        "valid-cutover-forward-ledger.json": valid_cutover_ledger,
        "invalid-cutover-post-write-rollback.json": invalid_cutover_atomic,
        "invalid-cutover-semantic-rollback.json": invalid_cutover_semantic,
        "valid-workbench-execute.json": valid,
        "invalid-caller-start-only-false.json": invalid_start,
        "invalid-job-supplied-grants.json": invalid_grants,
        "invalid-route-missing-profile-digest.json": invalid_route,
        "invalid-route-fallback-explicit.json": invalid_fallback,
        "invalid-planner-schema-override.json": invalid_planner_override,
        "invalid-remote-schema-ref.json": invalid_remote_ref,
        "valid-schema-portable-pattern.json": valid_portable_pattern,
        **profile_mutants,
        "invalid-schema-depth.json": invalid_deep,
        "invalid-schema-instance-limit.json": invalid_instance_limit,
        "fixture-index.json": {
            "schema_version": "aar.sdd-fixture-index.v1",
            "fixtures": [
                {"path": "valid-route-catalog.json", "schema_valid": True, "profile_valid": True},
                {"path": "valid-workbench-execute.json", "schema_valid": True, "profile_valid": True},
                {"path": "invalid-caller-start-only-false.json", "schema_valid": False, "profile_valid": False},
                {"path": "invalid-job-supplied-grants.json", "schema_valid": False, "profile_valid": False},
                {"path": "invalid-route-missing-profile-digest.json", "schema_valid": False, "profile_valid": False},
                {"path": "invalid-route-fallback-explicit.json", "schema_valid": False, "profile_valid": False},
                {"path": "invalid-planner-schema-override.json", "schema_valid": False, "profile_valid": False},
                {"path": "invalid-remote-schema-ref.json", "schema_valid": True, "profile_valid": False},
                {"path": "valid-schema-portable-pattern.json", "schema_valid": True, "profile_valid": True},
                {"path": "invalid-schema-format.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-pattern-properties.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-missing-local-ref.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-lookbehind.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-python-anchors.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-nested-quantifier.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-possessive-quantifier.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-alternation.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-overlapping-quantified-atoms.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-trailing-end-anchor.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-ecmascript-identity-escape.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-depth.json", "schema_valid": True, "profile_valid": False},
                {"path": "invalid-schema-instance-limit.json", "schema_valid": False, "profile_valid": False},
            ],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate hash-bound AR-RW contracts and fixtures")
    parser.add_argument(
        "--baseline-tool-manifest",
        type=Path,
        required=True,
        help="Exact a585ac52 schemas/aar-mcp-tools-v7.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    CONTRACTS.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for stale in CONTRACTS.glob("*.json"):
        stale.unlink()
    for filename, contract in contracts.items():
        Draft202012Validator.check_schema(contract)
        write_json(CONTRACTS / filename, contract)
    write_json(CONTRACTS / "aar-mcp-tools-v8.json", tool_surface)
    build_full_tool_manifest(args.baseline_tool_manifest)
    write_json(ROOT / "fault-matrix.json", fault_matrix)

    for stale in FIXTURES.glob("*.json"):
        if stale.name == "fixture-index.json" or stale.name.startswith(("valid-", "invalid-")):
            stale.unlink()
    fixtures = build_fixtures()
    execute_validator = Draft202012Validator(execute_input)
    for filename, value in fixtures.items():
        write_json(FIXTURES / filename, value)
    for required_fixture in (FIXTURES / "registry-v5.sql", FIXTURES / "registry-v5-binding.json"):
        if not required_fixture.is_file():
            raise RuntimeError(f"missing exact baseline fixture: {required_fixture}")
    fixture_index = fixtures["fixture-index.json"]
    for item in fixture_index["fixtures"]:
        if item["path"] == "valid-route-catalog.json":
            continue
        structural_valid = not list(
            execute_validator.iter_errors(fixtures[item["path"]])
        )
        if structural_valid is not item["schema_valid"]:
            raise ValueError(
                f"fixture structural expectation mismatch: {item['path']}"
            )

    file_entries = []
    generated_paths = [
        *(path for path in sorted(CONTRACTS.glob("*.json")) if path.name != "contract-manifest.json"),
        *(
            path
            for path in sorted(FIXTURES.iterdir())
            if path.is_file()
            and path.name != "registry-v5-migration-verification.json"
        ),
        ROOT / "fault-matrix.json",
        ROOT / "migration-v6.sql",
    ]
    for path in sorted(generated_paths):
        raw = path.read_bytes()
        file_entries.append(
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "size_bytes": len(raw),
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
        )
    manifest_payload = {
        "schema_version": "aar.sdd-contract-manifest.v1",
        "generator": "generate_contracts.py",
        "generator_sha256": "sha256:"
        + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "json_schema_profile": "aar.json-schema-profile.v1",
        "files": file_entries,
    }
    manifest_payload["manifest_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    write_json(CONTRACTS / "contract-manifest.json", manifest_payload)
    print(
        json.dumps(
            {
                "contracts": len(contracts),
                "fault_cases": len(fault_cases),
                "manifest_digest": manifest_payload["manifest_digest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
