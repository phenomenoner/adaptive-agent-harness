# Hermes provider-ready host adapter v1

Status: **SPECIFIED**

## Intent and scope

Provide a standalone local Hermes host adapter for a clean-installed provider-ready AAR runtime.
The adapter MUST start or reuse one exact durable supervisor, attach the replaceable MCP stdio
frontend, and expose an explicit host-authority CLI that issues or revokes current-generation,
memory-only session grants.

The AAR provider-ready and caller-work contracts remain host-neutral and installable by any compatible
agent harness. This specification adds one optional Hermes composition/authority adapter and uses it
for the local installed-host acceptance; it does not make Hermes a package-core provider or a
required integration path.

In scope:

- a versioned `aar-hermes-mcp` launcher for a fresh canonical v6 runtime home;
- provider-ready startup from immutable install evidence and an exact route catalog;
- an owner-authenticated private grant issue/revoke control path;
- a host-authority CLI whose invocation is the explicit approval event;
- runtime-generation, process-identity, credential, capability, deadline, policy, budget, TTL,
  principal, session, and grant fences;
- restart and rollback behavior for the local Hermes integration.

Non-goals:

- no new public MCP tool, method, schema, durable store, activation schema, provider credential,
  external-effect executor, automatic grant policy engine, old-root migration/adoption, or live-root
  replacement;
- no implicit grant issuance during install, startup, Ready publication, MCP attach, or an ordinary
  mutation request;
- no change to the frozen MCP v8 compatibility bytes.

## Necessity decision

Observable outcome: a fresh provider-ready runtime is usable by Hermes without weakening the
memory-only, explicitly issued grant contract.

Minimum invariant: no state-changing MCP request is admitted unless an authenticated host action
issued a current, exact-session grant within the installed policy.

Decision: `PLATFORM_PRIMITIVE`.

- Declining the feature leaves the released provider-ready path undeployable by Hermes.
- A manual direct-library call cannot mutate grants inside the durable supervisor process.
- Auto-issuance at startup or request time violates the frozen contract.
- A new durable ledger adds an unnecessary authority owner, migration path, recovery path, and
  corruption state.
- The existing owner-only private supervisor channel already provides same-user authentication,
  process/generation fencing, bounded payloads, deadlines, and an exact live owner.

Complexity budget: two optional private command pairs (`grant_issue`/`grant_issued` and
`grant_revoke`/`grant_revoked`), one host-authority client, one provider-ready Hermes launcher, and no
new persisted state. Restart intentionally destroys every issued grant.

## Normative contract

### Clean installation and startup

1. The adapter MUST operate only on an absolute, already published clean-install runtime home.
2. It MUST verify immutable install evidence, installed distribution members, the canonical v6
   registry, profile/authority bindings, loaded package factories, programmable backend, and route
   catalog before allocating a runtime generation or publishing Ready.
3. The launcher MUST start or reuse one exact supervisor under a cross-process startup lock and MUST
   reject a live supervisor from another package version, route catalog, runtime home, or startup
   mode. Reuse requires a live MCP readback matching package version, process-identity digest,
   supervisor protocol version/digest, runtime and dispatcher generations, capability digest,
   route-catalog digest, default route profile, and current persisted activation grant set. Missing,
   unreadable, or mismatched evidence MUST NOT authorize replacement or handoff.
4. `aar-hermes-mcp` MUST NOT issue a session grant.
5. The standalone adapter MAY bind deterministic `reference-driver` profiles for qualification or
   `host-caller-driver-v1` profiles for real provider work. A caller-delegated profile MUST reject
   every service-owned model send and use only the durable claim/mark-send-started/commit protocol;
   the Hermes host owns the physical provider call and exact receipt submission.

### Explicit grant authority

1. `aar-hermes-authority issue` is an explicit host approval action. Successful invocation MAY issue
   exactly the requested capability for one exact principal/session and bounded TTL.
2. The control connection MUST authenticate with the current owner-only attachment credential and,
   on Unix, the current effective UID.
3. The request frame MUST match the current runtime generation, dispatcher generation, attachment
   digest, and an unexpired deadline. Any mismatch MUST fail before grant issuance.
4. The supervisor MUST derive `issued_at_unix_ms` from its own clock and set
   `policy_approved=True` only after the authenticated control request passes the frame fences.
5. Existing `SessionGrantOwner` validation remains authoritative for principal, capability, TTL,
   grant uniqueness, current activation, persisted readback, generation, profile, route, capability,
   and authority bindings.
6. The CLI MUST return the complete immutable issued-grant document on success. It MUST NOT return
   the attachment credential.
7. `aar-hermes-authority revoke` MAY revoke only a grant owned by the current activation process.
   Unknown, stale, prior-process, or already unavailable grants MUST fail closed.
8. Install, startup, Ready, MCP attach, and ordinary mutation handling MUST leave the live grant map
   unchanged.
9. In provider-ready mode every frozen public mutating MCP tool MUST validate a current memory-only
   grant for its exact principal, session, capability, generation, deadline, and budget before any
   durable accept, operation, workspace, asset, or ticket write. Static `REFERENCE_GRANTS`,
   `aar_reference_context`, and synthetic grant documents MUST NOT authorize provider-ready mutation.
10. The only trusted-local issuer is the supervisor's authenticated grant-control handler. The issue
    payload MUST NOT contain `issued_at_unix_ms` or `policy_approved`; the supervisor derives both
    after peer, credential, frame, generation, deadline, policy, and activation checks pass.

### Memory-only and restart semantics

1. Session grants MUST never be written to the runtime database, activation files, lifecycle log,
   discovery record, launcher log, or another durable artifact.
2. A supervisor restart MUST create an empty grant map. A prior grant ID MUST be denied after restart
   even when the runtime home, activation generation, profile, or route catalog are unchanged.
3. A successor generation MAY issue a fresh grant only through a new explicit authority command.

### Private protocol

1. The public MCP tool names, descriptions, and JSON schemas MUST remain byte-identical.
2. The private supervisor protocol digest MUST include the added message kinds.
3. Existing attach/MCP clients MUST remain compatible; they are not required to send or understand
   optional grant-control frames.
4. Control frames are one-request connections: authenticate, submit one command, receive one terminal
   response, close.
5. Only a matching `grant_issued`, `grant_revoked`, or authenticated terminal error is certain.
   Transport loss or a malformed/mismatched response after send is `INDETERMINATE`, MUST preserve the
   requested grant ID for reconciliation evidence, and MUST NOT be silently retried.
6. The caller MUST use a fresh grant ID for a new issue attempt unless it can prove the prior request
   was not accepted. Reusing an exact live grant ID MUST conflict.

## Ownership and compatibility

- Host/operator: chooses runtime home, route catalog, principal, session, capability, TTL, and the
  exact moment to invoke authority.
- Hermes adapter: authenticates, frames, launches/attaches, and reports receipts; it does not widen
  policy or infer user approval.
- Provider-ready supervisor: verifies installed authority, owns live session grants, performs
  admission, and publishes Ready.
- AAR: computes and proposes. The host still owns provider credentials, physical model calls,
  external effects, activation choice, and final delivery.

`v0.6.0a0` and its runtime home remain immutable. This feature ships as package/release
`0.6.0a1` / `v0.6.0a1`; its exact wheel digest and persistent install root are frozen only after the
reviewed release artifact exists. Rollback restores the prior Hermes MCP command/runtime-home pointer;
it never mutates, adopts, archives, removes, or promotes an old root.

## Failure semantics

Fail closed before issuance or mutation on invalid credentials, foreign UID, malformed/noncanonical
payloads, unknown command kind, stale process/generation/attachment/capability binding, expired
control deadline, absent provider-ready startup, policy mismatch, unapproved principal/capability,
TTL overflow, duplicate live grant ID, missing persisted activation readback, or unknown/restarted
grant ownership.

An unreadable or temporarily unavailable process identity is not absence and does not authorize a
replacement owner. A control transport failure is reported as indeterminate to the caller; it is not
silently retried.

## Falsifiable acceptance

- `HERMES-HOST-001` (T1): install/start/attach paths create zero session grants.
- `HERMES-HOST-002` (T1): every frozen public mutating MCP tool rejects absent, static reference,
  wrong-capability, stale-generation, foreign-principal/session, expired, or over-budget authority
  before its first durable write; authenticated current-generation issue admits only the exact
  policy-bounded mutation context.
- `HERMES-HOST-003` (T1): wrong credential, UID, generation, attachment digest, deadline, principal,
  capability, TTL, or duplicate ID is rejected before issuance; the supervisor, not the caller,
  determines policy approval and issuance time.
- `HERMES-HOST-004` (T1): revoke invalidates the exact grant and cannot revoke a foreign/stale grant;
  restart destroys all session grants without changing durable activation authority.
- `HERMES-HOST-005` (T2): public MCP tool manifest and frozen compatibility assets do not drift.
- `HERMES-HOST-006` (T2): launcher reuses one exact matching owner and rejects any mismatch in package,
  startup mode, process identity, protocol, generation, capability digest, activation grant set, route
  catalog, or default profile without replacing or handing off the live owner.
- `HERMES-HOST-007` (T3): exact `v0.6.0a1` wheel clean-installs a fresh v6 root, verifies activation,
  starts provider-ready Ready generation 1, executes real IPython, and completes a durable
  caller-delegated RLM operation through claim/mark-send-started/exact-receipt commit using the real
  Hermes provider call.
- `HERMES-HOST-008` (T3): after supervisor restart, generation advances, prior grant is denied, a new
  explicit grant works, and durable RLM state reconciles.
- `HERMES-HOST-009` (T4): after Hermes config cutover/restart, a fresh native `aar_capabilities`
  readback reports the new package, attached supervisor, provider-ready model route, and successor
  generation; one explicitly granted mutation succeeds.
- `HERMES-HOST-010` (T4): the pre-cutover Hermes integration pointer, command, arguments, environment
  keys, and config digest are retained as rollback evidence; fresh readback proves only the intended
  pointer changed, and the untouched v0.5 runtime can be restored without modifying either root.
- `HERMES-HOST-011` (T1): a post-send lost or mismatched grant-control response is reported
  `INDETERMINATE` with the original grant ID and is never silently retried.
