# Changelog

All notable public changes are documented here. The project is in public alpha; interfaces may change before a stable release.

## 0.6.0a1 — 2026-08-26 release-contract target

`v0.6.0a1` adds a standalone Hermes host adapter and authenticated trusted-local authority path while
keeping the frozen public MCP tool/schema surface unchanged. Its in-tree release contract is
[`profiles/release-status-v1.json`](profiles/release-status-v1.json). This source does not establish
that the target tag, GitHub prerelease, or `adaptive-agent-runtime-v0.6.0a1-release-receipt.json`
exists; publication and exact post-freeze evidence require external readback. It also does not
establish official Plugin Directory publication, which requires separate external authority.

### Added

- add `aar-hermes-mcp` for exact provider-ready supervisor startup/reuse and live package, process,
  protocol, generation, capability, activation, and route readback;
- add explicit `aar-hermes-authority issue/revoke` commands for generation-bound, memory-only session
  grants without startup, attach, reference-context, or ordinary-request auto-issuance;
- add `host-caller-driver-v1` for real host-owned provider calls through the existing durable
  caller-work claim/mark-send/commit protocol; direct service-owned sends fail closed;
- ship the Hermes profile on `aar-hermes-mcp` with explicit operator-owned absolute runtime-home,
  route-catalog, and default-route-profile bindings rather than ambient working-directory state.

### Security and verification boundary

- every provider-ready public mutation requires a current exact session grant before its first durable
  write; static reference grants and `aar_reference_context` cannot authorize provider-ready writes;
  caller-work claim additionally requires the ticket method's exact current executable adapter
  identity and generation before reserving a send;
- `aar-admin activation verify` joins the supplied profile to the exact installed candidate,
  activation history, current authority, and registry without mutation; a valid foreign profile or
  incomplete installed authority fails closed;
- Windows single-runtime ownership uses one process-owned, first-instance named-pipe handle with no
  retained lock artifact, preventing concurrent local owners without thread-recursive mutex behavior;
- a post-send lost or mismatched authority response is indeterminate and is never silently retried;
- this release remains clean-install-only: it does not adopt, promote, archive, remove, or mutate an
  existing runtime root; final Hermes acceptance uses an isolated fresh home bound for the first time
  to the new clean-install root, without switching any live integration pointer.

## [0.6.0a0](https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.6.0a0) — 2026-08-26

`v0.6.0a0` adds a clean-install-only provider-ready bootstrap and activation path without changing
the frozen MCP v8 tool surface or its compatibility schemas.

### Added

- build a fresh canonical v6 runtime home with exact install evidence, profile/history/current
  authority, package-owned broker factories, and no-replace atomic publication;
- activate provider-ready capability and grant-set authority only after the real host broker façade
  is bound, before Ready or durable dispatch;
- enforce current, memory-only session grants at MCP admission, with grant denial taking precedence
  over backend availability.

### Verification boundary

- exact frozen compatibility assets, generated contracts, host profiles, package metadata, the
  clean-install lifecycle, and provider-ready startup/admission are release gates;
- package installation or startup against an existing runtime home, symlink, or unknown residual
  state fails closed; this release does not migrate or adopt an existing home;
- GitHub publication does not establish package-index distribution, production deployment,
  provider-backed execution, or official Plugin Directory publication.

## [0.5.0a0](https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.5.0a0) — 2026-08-20

`v0.5.0a0` is a public GitHub alpha release. It does not claim package-index publication,
production deployment, external provider execution, or official Plugin Directory publication.

### Added

- add the successor-only MCP v8 surface with three RLM workbench tools and five durable caller-work
  lifecycle tools while retaining the frozen 30-tool v7 `aar_capabilities` projection;
- expose truthful host-owned per-method workbench capability and fail closed when requested broker
  methods are not configured;
- preserve claim, send-start, cancellation, receipt, reconciliation, deadline, and terminal authority
  by forwarding MCP calls into the existing durable runtime rather than duplicating its state machine.

### Verification boundary

- generated contracts, schemas, MCP assets, host profiles, operation-skill metadata, and the exact wheel
  are bound to the `0.5.0a0` candidate;
- the exact final candidate passed 743 repository tests with five platform-gated skips and one
  existing MCP Sampling deprecation warning; generated contracts, package assets, formatting,
  isolated wheel installation, and an independent Luna/max current-byte review passed;
- publication of this GitHub source/tag prerelease does not establish package-index distribution,
  production deployment, provider-backed execution, or official Plugin Directory publication.

## [0.4.0a6](https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.4.0a6)

The `0.4.0a6` release snapshot is governed by
[`profiles/release-status-v1.json`](profiles/release-status-v1.json). Exact source, wheel,
supported-Python CI, local-install, fresh-host/RLM, independent-review, tag, and asset-readback
evidence is bound by the external release asset
`adaptive-agent-runtime-v0.4.0a6-release-receipt.json`. This release uses exact native child
terminalization, the existing database-scoped process lock for single-runtime ownership,
generation-unique endpoint/credential/request paths with an atomically advanced stable discovery
pointer and **non-destructive** retention, and a subprocess Codex
setup route with `NO_ATOMIC_AUTHORITY` that returns an ordered manual plan before any mutation. A
non-empty manual-plan receipt explicitly preserves the required post-apply Codex restart handoff.
A GitHub release does not establish official Plugin Directory deployment/review/publication,
OpenAI approval, or provider-signed attestation; those require separate external authority.

## [0.4.0a5] — blocked, unreleased, historical

> `v0.4.0a5` is retained as historical input only. It was blocked by lifecycle and provider-authority
> findings and must not be treated as the current release.

### Fixed

- distinguish exact process `MATCH`, conclusive `MISMATCH`, and temporarily `UNAVAILABLE`
  observations throughout supervisor, frontend, worker heartbeat, orphan recovery, and stop paths;
- bind Linux destructive signals to verified pidfds and Windows termination/wait to one verified
  process handle, including terminal readback and PID-reuse denial;
- reap the exact launcher child on startup timeout or post-spawn failure before releasing the
  startup lock, and accept a foreign Ready owner only after that contender is terminal;
- prevent predecessor cleanup from removing replacement supervisor endpoints, credentials,
  shutdown requests, or discovery records; publish generation-specific Unix socket paths;
- stage checkpoint-restored workers before the durable old-binding handoff so one active binding is
  preserved without blocking a valid successor;
- bypass the Windows virtual-environment launcher redirector for supervisor and IPython children,
  while keeping the active environment's package and executable identity;
- make Codex configuration rollback readback-driven and fail-contained when a command outcome,
  current state, installed plugin version, or concurrent mutation cannot be proven transaction-
  owned;
- report a retained desired configuration plus failed production startup as
  `configuration_committed_runtime_unready`, not as setup success, rollback, or an unclassified
  partial state;
- keep Windows runtime ownership markers readable while locking a separate byte range, and cap
  deprecated MCP Sampling waits to the platform-supported timeout without changing host authority.

### Changed

- update the canonical `aar-operations` workflow to `0.9.5` with explicit identity-unavailable,
  retry-safe setup recovery, restart, fresh-task, and native-capability verification guidance;
- refresh the Codex plugin cachebuster so existing installations cannot retain the prior bundled
  lifecycle code or skill bytes.

### Verification gate

- SDD fail-first regressions cover identity uncertainty, exact signalling, startup cleanup,
  replacement cleanup, worker handoff, transaction containment, Windows child PID ownership,
  lock sharing, and large-deadline Sampling waits;
- the stable source candidate passes 445 Windows repository tests with one directory-symlink
  capability skip and one expected MCP Sampling deprecation warning;
- generated contracts, MCP assets, Codex/Hermes profiles, the exact wheel, supported-Python CI,
  restarted Desktop native verification, and a fresh independent release review are required on
  the exact final commit before publication.

### Boundaries

- the public caller-delegated RLM service still leaves every provider credential and physical model
  call with the host;
- GitHub release and local Codex verification are not deployment, ChatGPT execution, OpenAI
  approval, or official Plugin Directory publication.

## [0.4.0a4] — 2026-08-15

### Fixed

- add `aar-codex-mcp`, an empty-argument Codex host adapter that starts or reuses one exact durable
  supervisor before attaching the replaceable stdio frontend;
- make `aar-codex-setup` preflight the plugin manifest's exact command, arguments, and
  attached-supervisor mode instead of substituting an embedded `aar-mcp --database` lifecycle;
- replace a same-name `aar-local` marketplace that points outside the marketplace bundled in the
  installed wheel, even when its plugin version string matches;
- provision the stable production supervisor during explicit setup, fence concurrent first
  launchers, reject a live owner from another package version, and support identity-bound graceful
  shutdown during upgrades;
- on Windows, accept the signalled exact process handle as authoritative fallback-termination
  evidence instead of reporting a false failure while process-table visibility drains;
- retry a transient discovery-file sharing error only inside the bounded post-spawn readiness loop,
  while existing-owner and stop paths remain strict;
- keep the Codex starter prompt within the host's 128-character limit.

### Changed

- update the canonical `aar-operations` workflow to `0.9.4`, retaining the public host-owned
  model-route guidance while adding exact launch-contract, marketplace-root, deferred-tool,
  fresh-task, and native-call verification rules;
- give the merged public plugin bytes a new cachebuster so an equal version can never hide a
  different bundled skill or MCP declaration.

### Verified

- 36 focused launcher, installer, MCP-asset, package-asset, host-profile, concurrency, and shutdown
  tests pass with Ruff and generated contract/profile verification;
- an isolated exact wheel exposes eleven console entrypoints and completes the 30-tool v7
  `aar-codex-mcp` preflight in attached-supervisor mode, including dependency-backed workspace
  execution and clean close;
- after a full Codex Desktop restart, fresh native calls read back package `0.4.0a4`, operation
  skill `0.9.4`, 30 tools, an ephemeral attached frontend, and the expected durable supervisor
  generation. Deferred tool search is treated only as loading, never as runtime proof.

The release process withholds the tag until the exact final commit passes the full Python 3.11–3.14
Linux GitHub Actions repository matrix.

### Boundaries

- the six disclosed Windows Sampling/process-owner suite failures do not exercise this Codex
  launcher path and still prevent a full Windows supervisor-suite claim;
- GitHub release and verified local Codex use do not constitute production deployment, ChatGPT
  execution, OpenAI approval, or official Plugin Directory publication.

## [0.4.0a0] — 2026-08-14

### Added

- add an OAuth-authenticated public Plugin Directory profile with six bounded tenant-workspace
  tools and five caller-delegated RLM coordination tools;
- let the main agent select one executor, model, and optional reasoning effort per job while the
  host retains provider credentials and performs every actual model call;
- add durable start, pre-spend claim, ticket-bound commit, status, cancellation, restart, tenant
  isolation, idempotency, compare-and-set, and bounded command-history behavior;
- add a scoped public runtime skill, deterministic plugin and skill-ZIP builder, reviewer cases,
  public deployment profile, and explicit privacy, retention, and submission gates.

### Changed

- deprecate the fixed Hermes Luna/max MCP Sampling flag as compatibility-only; new public RLM
  integrations use caller-delegated execution;
- update the bundled `aar-operations` workflow to `0.9.1`, retaining the host-owned model-route
  guidance from `v0.3.0a2` and adding caller-delegated retry and terminal-key boundaries;
- keep generated Codex starter prompts within the host's 128-character limit;
- reserve package identity `0.4.0a0` for the expanded executable bytes.

### Verified

- exact-wheel public HTTP, authentication, tenant isolation, quota, restart, idempotency,
  concurrency, claim/commit/cancel, and package-asset checks;
- deterministic plugin trees and scoped skill ZIPs with five positive and three negative reviewer
  cases;
- a fresh local Codex marketplace-derived lifecycle that persisted workspace state and completed an
  actual caller-delegated `gpt-5.6-sol` / `max` call;
- an independent fixed-point review covering 77 required cells with no actionable findings;
- public-repository release hygiene, generated-contract parity, bundled-skill parity, locked
  dependencies, and Ruff.

The release process withholds the tag until the exact commit passes the full Python 3.11–3.14
Linux GitHub Actions repository matrix.

### Boundaries

- GitHub source, tag, release assets, and a submission packet do not constitute official Plugin
  Directory publication;
- production HTTPS/OAuth, reviewer execution, public policy and operational controls, ChatGPT
  execution, portal scan, OpenAI review, approval, and publisher action remain separate gates;
- caller-reported or host-receipt-bound model provenance is not provider-signed attestation.

## [0.3.0a2] — 2026-08-14

### Added

- owner-authored, digest-bound model route catalogs and immutable admission bindings;
- a durable model-execution journal with explicit pre-send, sent, receipt, usage, committed,
  indeterminate, and quarantined outcomes;
- an owner-gateway driver boundary that keeps provider credentials outside AAR state;
- a bidirectional MCP Sampling transport for host-owned physical provider calls;
- requested-versus-effective route receipts, provider-reported usage, retry ordinals, fallback
  chains, and strict route-drift validation;
- pinned run-manifest and usage-ledger schemas plus an AAR evidence adapter for controlled
  `openai-codex / gpt-5.6-luna / max` comparisons.

### Safety and recovery

- expire pre-send requests without provider invocation;
- classify post-send timeout, cancellation, disconnect, or ownership loss as an unknown outcome
  rather than blindly replaying a potentially billable call;
- sanitize public provider failures and keep credentials out of durable requests, journals, traces,
  benchmark fragments, and bundled profiles;
- reject fallback, route mutation, non-provider usage, and unaccounted retry from evaluation evidence.

### Documentation and packaging

- document model routing, MCP Sampling, receipt semantics, and fair-evaluation requirements in a
  public integration guide;
- update the bundled `aar-operations` skill to `0.9.0` with model-route, receipt, uncertainty, and
  evaluation guidance;
- refresh package-bound schemas, MCP assets, Codex/Hermes bundles, host profiles, and all localized
  release references under immutable version `0.3.0a2`.

### Boundaries

- this release provides route and evidence contracts, not provider credentials, a provider account,
  a benchmark score, or a winner;
- live provider qualification and formal evaluation remain explicit operator-authorized actions;
- MCP Sampling has no portable provider-receipt lookup API, so unresolved post-send outcomes remain
  indeterminate or quarantined.

### Verified

- public repository suite: 328 passed, 1 platform-gated skip;
- credential-free model broker, benchmark adapter, and bidirectional MCP Sampling scenarios;
- generated contract, MCP, Codex/Hermes host-profile, and bundled operation-skill assets agree with
  executable source;
- source hygiene, Ruff, exact-wheel build/install, and fresh-host readback are release gates.

## [0.3.0a1] — 2026-08-12

### Fixed

- recheck compensation authority after waiting for the SQLite write reservation and again after
  durable intent commit immediately before provider invocation;
- refuse provider invocation when the effective compensation deadline expires in either admission
  window, with regressions for both the lock-wait and post-commit seams;
- synchronize public executable source with the exact standalone AR-LT3 source used for the verified
  schema-v5 cutover instead of reusing the earlier `0.3.0a0` identity for different bytes.

### Documentation and packaging

- publish the maintenance source under a new prerelease identity without rewriting `v0.3.0a0`;
- update current-state projections to schema v5 and runtime/dispatcher generation 13 while retaining
  older generation-12/schema-v4 records as historical evidence;
- refresh package-bound schema, MCP tool-surface, host-profile, Codex cachebuster, and operation-skill
  metadata for `0.3.0a1`;
- update all 17 localized README files to the current tag and verified public test count.

### Verified

- public repository suite: 249 passed, 1 platform-gated skip;
- 30-tool MCP v7 surface;
- generated contract, MCP, and host-profile assets agree with executable source;
- source hygiene, Ruff, exact-wheel build/install, and fresh-clone readback are release gates.

## [0.3.0a0] — 2026-08-11

### Added

- first public Adaptive Agent Harness repository;
- marketing-first English README and 17 linked translations;
- MIT license, security policy, contribution guide, and CI workflow;
- versioned recovery-policy bindings for durable RLM operations;
- exact continuation boundaries across operation, attempt, generation, lease, input, environment, policy, receipt, usage, deadline, and cancellation state;
- schema v5 recovery policy, checkpoint, effect-receipt, boundary, and successor records;
- policy-bound RLM successor attempts that reuse authoritative receipts and refuse blind replay;
- Linux/WSL exact-wheel compatibility evidence; earlier native-Windows host results are retained as maintainer-reported historical context.

### Public-release hardening

- define RLM jobs and IPython workspaces as host-composed sibling surfaces rather than an automatic shared runtime;
- bump Codex and Hermes profile versions so older v5/29-tool installations are refreshed for v7/30 tools;
- harden the tracked-file public guard against generic machine paths, platform identifiers, local receipts, and nested sensitive directories;
- qualify host-specific historical rows whose supporting private receipts are not part of the public tree.

### Verified

- full repository suite: 245 passed, 1 platform-gated skip;
- Python 3.11–3.14 support;
- 30-tool MCP v7 surface;
- additive v3-to-v4-to-v5 migration and older-reader fail-closed behavior;
- durable supervisor, frontend replacement, single-owner fencing, and reference-runtime recovery scenarios.

### Known limits

- public alpha; no stable API guarantee;
- not a security sandbox;
- no generic external-effect execution or final delivery;
- no universal exactly-once guarantee;
- automatic broad IPython workspace restoration remains in progress.

[0.3.0a0]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.3.0a0
[0.3.0a1]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.3.0a1
[0.3.0a2]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.3.0a2
[0.4.0a0]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.4.0a0
[0.4.0a4]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.4.0a4
[0.4.0a5]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.4.0a5
[0.4.0a6]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.4.0a6
