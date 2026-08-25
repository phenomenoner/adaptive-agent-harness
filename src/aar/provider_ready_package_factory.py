"""Pure package-owned provider-ready factory declarations and C1 joins.

The six public methods intentionally share the existing broker implementation
member.  Factory identity remains method-scoped while implementation provenance
is the SHA-256 of the exact candidate-wheel member bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aar.provider_ready_install_models import (
    InstallCandidateFactoryEntry,
    InstallCandidateReceipt,
)
from aar.provider_ready_models import (
    HostActivationIntent,
    WorkbenchActivationMethodName,
)

PACKAGE_FACTORY_WHEEL_MEMBER = "aar/runtime/brokers.py"


class PackageFactoryBindingError(ValueError):
    """A C1 intent/receipt/wheel join violates package-owned authority."""


@dataclass(frozen=True, slots=True)
class PackageFactoryDeclaration:
    """One immutable method-to-factory-to-wheel-member declaration."""

    method: WorkbenchActivationMethodName
    factory_id: str
    wheel_member: str
    contract_id: str
    request_schema_digest: str
    response_schema_digest: str


PACKAGE_FACTORY_DECLARATIONS = (
    PackageFactoryDeclaration(
        method="model.request",
        factory_id="aar.factory.model-request.v1",
        wheel_member=PACKAGE_FACTORY_WHEEL_MEMBER,
        contract_id="aar.broker-contract.model-request.v2",
        request_schema_digest="sha256:d4d7f6e03a12d1aa4428ee623e29f3f68f5fab95632891acbd27cf2bae1581de",
        response_schema_digest="sha256:20cd938101b51673f85b5234e373eab0063cc26a670a1fa25f884e1afa029dce",
    ),
    PackageFactoryDeclaration(
        method="subagent.submit",
        factory_id="aar.factory.subagent-submit.v1",
        wheel_member=PACKAGE_FACTORY_WHEEL_MEMBER,
        contract_id="aar.broker-contract.subagent-submit.v2",
        request_schema_digest="sha256:767942bcccf46d58512208fb53346dd9ece68b87d541ac542c25c4642f0ff1f3",
        response_schema_digest="sha256:ab383558e919ff14f4c90cd47b7c7a60189a17a6e7fb718976b21c91983582c6",
    ),
    PackageFactoryDeclaration(
        method="subagent.result",
        factory_id="aar.factory.subagent-result.v1",
        wheel_member=PACKAGE_FACTORY_WHEEL_MEMBER,
        contract_id="aar.broker-contract.subagent-result.v2",
        request_schema_digest="sha256:84ea9032e15367f766499ceee69966716cd68b0b1a6d4d9ef7398167141a852b",
        response_schema_digest="sha256:ab383558e919ff14f4c90cd47b7c7a60189a17a6e7fb718976b21c91983582c6",
    ),
    PackageFactoryDeclaration(
        method="evidence.query",
        factory_id="aar.factory.evidence-query.v1",
        wheel_member=PACKAGE_FACTORY_WHEEL_MEMBER,
        contract_id="aar.broker-contract.evidence-query.v2",
        request_schema_digest="sha256:2dfcb0991e56f8a4d3a7306a48058896f521a7c2fa376102adb92633799c5243",
        response_schema_digest="sha256:37ad0560c2f4a7ae41ee4db3848867456a65c27e9080585551d7055c57e5cd55",
    ),
    PackageFactoryDeclaration(
        method="artifact.put",
        factory_id="aar.factory.artifact-put.v1",
        wheel_member=PACKAGE_FACTORY_WHEEL_MEMBER,
        contract_id="aar.artifact-stage.v1",
        request_schema_digest="sha256:564182c3967f9debf4447b3a3b9af2e36a296989672c9ead70d168b6ed1a09dc",
        response_schema_digest="sha256:31973b2ae43a7193fe9e6adf77dc4cffbc8e57f36f1630b707561d0d2d22836f",
    ),
    PackageFactoryDeclaration(
        method="effect.propose",
        factory_id="aar.factory.effect-propose.v1",
        wheel_member=PACKAGE_FACTORY_WHEEL_MEMBER,
        contract_id="aar.broker-contract.effect-propose.v2",
        request_schema_digest="sha256:16f3b469077af425c59811a5cb18da170dd306377a8cd8aa201899a8edd30de4",
        response_schema_digest="sha256:4ef2782939021587992f021fe18c864efd843d72f4aced6238df9180730e6b64",
    ),
)

_DECLARATIONS_BY_METHOD = {item.method: item for item in PACKAGE_FACTORY_DECLARATIONS}
_DECLARATIONS_BY_FACTORY_ID = {item.factory_id: item for item in PACKAGE_FACTORY_DECLARATIONS}


def factory_entries_from_member_digests(
    member_digests: Mapping[str, str],
) -> tuple[InstallCandidateFactoryEntry, ...]:
    """Project the six canonical receipt entries from verified wheel members."""

    try:
        implementation_digest = member_digests[PACKAGE_FACTORY_WHEEL_MEMBER]
    except KeyError as error:
        raise PackageFactoryBindingError(
            f"package factory wheel member is absent: {PACKAGE_FACTORY_WHEEL_MEMBER}"
        ) from error
    return tuple(
        sorted(
            (
                InstallCandidateFactoryEntry(
                    factory_id=declaration.factory_id,
                    wheel_member=declaration.wheel_member,
                    implementation_digest=implementation_digest,
                )
                for declaration in PACKAGE_FACTORY_DECLARATIONS
            ),
            key=lambda entry: entry.factory_id,
        )
    )


def validate_package_factory_bindings(
    intent: HostActivationIntent,
    receipt: InstallCandidateReceipt,
    member_digests: Mapping[str, str],
) -> None:
    """Validate exact method/factory/member/catalog joins without side effects."""

    methods = tuple(adapter.method for adapter in intent.adapters)
    expected_methods = tuple(item.method for item in PACKAGE_FACTORY_DECLARATIONS)
    if methods != expected_methods:
        raise PackageFactoryBindingError(
            "intent adapters do not match package factory method order"
        )

    factory_ids = tuple(adapter.factory_id for adapter in intent.adapters)
    if len(factory_ids) != len(set(factory_ids)):
        raise PackageFactoryBindingError("intent adapters must bind a unique package factory")

    try:
        implementation_digest = member_digests[PACKAGE_FACTORY_WHEEL_MEMBER]
    except KeyError as error:
        raise PackageFactoryBindingError(
            f"package factory wheel member is absent: {PACKAGE_FACTORY_WHEEL_MEMBER}"
        ) from error

    for adapter in intent.adapters:
        declaration = _DECLARATIONS_BY_METHOD[adapter.method]
        observed = (
            adapter.factory_id,
            adapter.contract_id,
            adapter.request_schema_digest,
            adapter.response_schema_digest,
        )
        expected = (
            declaration.factory_id,
            declaration.contract_id,
            declaration.request_schema_digest,
            declaration.response_schema_digest,
        )
        if observed != expected:
            raise PackageFactoryBindingError(
                f"intent adapter differs from package declaration: {adapter.method}"
            )
        if adapter.factory_digest != implementation_digest:
            raise PackageFactoryBindingError(
                f"intent factory digest differs from candidate member: {adapter.factory_id}"
            )

    entries = {entry.factory_id: entry for entry in receipt.factory_entries}
    if set(entries) != set(_DECLARATIONS_BY_FACTORY_ID):
        raise PackageFactoryBindingError(
            "receipt factory inventory differs from package declarations"
        )
    for factory_id, declaration in _DECLARATIONS_BY_FACTORY_ID.items():
        entry = entries[factory_id]
        if entry.wheel_member != declaration.wheel_member:
            raise PackageFactoryBindingError(
                f"receipt wheel member differs from package declaration: {factory_id}"
            )
        if entry.implementation_digest != implementation_digest:
            raise PackageFactoryBindingError(
                f"receipt implementation digest differs from candidate member: {factory_id}"
            )


__all__ = [
    "PACKAGE_FACTORY_DECLARATIONS",
    "PACKAGE_FACTORY_WHEEL_MEMBER",
    "PackageFactoryBindingError",
    "PackageFactoryDeclaration",
    "factory_entries_from_member_digests",
    "validate_package_factory_bindings",
]
