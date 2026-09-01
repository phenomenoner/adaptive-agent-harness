"""Independent version domains for the first AAR contract candidate."""

from typing import Final

PACKAGE_VERSION: Final = "0.6.0a2"
FROZEN_COMPATIBILITY_PACKAGE_VERSION: Final = "0.5.0a0"
ENVELOPE_SCHEMA_VERSION: Final = "aar.envelope.v1"
RUNTIME_SCHEMA_VERSION: Final = "aar.runtime.v1"
WORKSPACE_SCHEMA_VERSION: Final = "aar.workspace.v1"
ARTIFACT_SCHEMA_VERSION: Final = "aar.artifact.v1"
BROKER_SCHEMA_VERSION: Final = "aar.broker.v1"
MODEL_ROUTE_SCHEMA_VERSION: Final = "aar.model-route.v1"
MODEL_RESPONSE_SCHEMA_VERSION: Final = "aar.model-response.v1"
MODEL_USAGE_SCHEMA_VERSION: Final = "aar.model-usage.v1"
RLM_SCHEMA_VERSION: Final = "aar.rlm.v1"
ADAPTIVE_ASSET_SCHEMA_VERSION: Final = "aar.adaptive-asset.v1"
OPERATION_CONTINUITY_SCHEMA_VERSION: Final = "aar.operation-continuity.v1"

SCHEMA_VERSIONS: Final = {
    "envelope": ENVELOPE_SCHEMA_VERSION,
    "runtime": RUNTIME_SCHEMA_VERSION,
    "workspace": WORKSPACE_SCHEMA_VERSION,
    "artifact": ARTIFACT_SCHEMA_VERSION,
    "broker": BROKER_SCHEMA_VERSION,
    "model_route": MODEL_ROUTE_SCHEMA_VERSION,
    "model_response": MODEL_RESPONSE_SCHEMA_VERSION,
    "model_usage": MODEL_USAGE_SCHEMA_VERSION,
    "rlm": RLM_SCHEMA_VERSION,
    "adaptive_asset": ADAPTIVE_ASSET_SCHEMA_VERSION,
    "operation_continuity": OPERATION_CONTINUITY_SCHEMA_VERSION,
}
