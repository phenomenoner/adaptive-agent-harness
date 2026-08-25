# Luna/max Independent Architecture Review

- Model: `gpt-5.6-luna`
- Reasoning: `max`
- Mode: bounded read-only review of attached specification bytes
- Verdict: **CHANGES REQUIRED**
- Review scope: `README.md`, `BASELINE.md`, `ARCHITECTURE.md`, `CONTRACTS.md`, `LIFECYCLE.md`, and ADR-001 through ADR-003
- Files modified by reviewer: none

## BLOCKERS

1. **Profile digest computation is circular/undefined.** The profile requires both `candidate.host_profile_digest` and `profile_digest`, while also requiring all candidate/profile digests to be included in the profile digest. No excluded-field or preimage rule is defined, so activation cannot deterministically verify the profile bytes. **[CONTRACTS.md §1]**

2. **The no-schema-v7 claim is not proven.** Root-planner tickets require durable phase, ordinal, request, route, directive-schema, suspension, and fence identity, but the packet does not establish that these fields and uniqueness constraints are representable in v6. **[README.md §5; BASELINE.md §2; ARCHITECTURE.md §6; CONTRACTS.md §7]**

3. **Cross-store cutover recovery lacks a complete authority protocol.** The design commits SQLite and then publishes an external marker, but does not specify retention of the exclusive owner/fence through both writes, prevention of concurrent takeover, or crash-recovery linearization. **[LIFECYCLE.md §§2, 9; CONTRACTS.md §§3–4]**

## MAJOR FINDINGS

1. **Grant issuance ownership is ambiguous.** The authoritative issuer, mint/revoke operation, and ordering between `ACTIVE`, Ready publication, and grant visibility are not defined. **[ARCHITECTURE.md §§3, 8; CONTRACTS.md §9; LIFECYCLE.md §3]**

2. **Native and caller-delegated planner admission are inconsistent.** The architecture permits a native synchronous planner, but the admission function always starts with `model.request`, while the profile example defines only caller-delegated mode. **[ARCHITECTURE.md §§6–7; CONTRACTS.md §§1, 8]**

3. **Adapter instantiation is not bound to an authoritative factory.** A profile names adapters and manifests, but no package-owned registry, factory digest, or immutable ID-to-factory mapping is specified. **[ARCHITECTURE.md §§4–5; CONTRACTS.md §2; ADR-001]**

4. **The planner-ticket transition is not atomically specified.** Ticket persistence and `waiting_external` lack a named transaction boundary, uniqueness key, and successor-dispatch fence. **[LIFECYCLE.md §4; CONTRACTS.md §7]**

5. **Qualification gates were outside this packet.** Exact Luna/max qualification could not be assessed in this architecture-only review. This is a packet-scope limitation, not proof the artifacts are absent from the SDD.

## MINOR FINDINGS

1. The activation profile lacks an explicit cache-policy binding. **[ARCHITECTURE.md §9; CONTRACTS.md §1]**
2. `budget_ceiling` lacks field/unit/arithmetic definition. **[CONTRACTS.md §§1, 9]**
3. `activation_generation` and runtime generation relationship is unspecified. **[CONTRACTS.md §§1, 5, 9]**
4. Restore has no dedicated receipt/readback contract. **[CONTRACTS.md §§3–4; LIFECYCLE.md §2]**

## VERIFIED STRENGTHS

- The proposal is appropriately minimal and rejects a second broker, daemon, database, provider client, or inference channel.
- Owner boundaries are directionally sound.
- The root-planner design requires durable tickets, send-start, successor resumption, and no blind replay.
- Method-scoped admission avoids false global readiness.
- Mutation grants are withheld until activation, and capability rows are intended to derive from instantiated adapters.
- v7/v8 compatibility and no-new-MCP-tool constraints are explicit.

## REQUIRED EDITS

1. Define canonical digest domains and excluded self-referential fields.
2. Add a field-by-field v6 mapping; amend the schema decision if representation is insufficient.
3. Specify authority-store CAS/lock retention and recovery takeover.
4. Name the grant issuer and ordered publication/revocation protocol.
5. Separate native and caller-delegated admission; bind adapters to an immutable factory registry.
6. Review the evaluation/acceptance packet separately.
