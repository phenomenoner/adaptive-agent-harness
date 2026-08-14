# Changelog

All notable public changes are documented here. The project is in public alpha; interfaces may change before a stable release.

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
  dependencies, Ruff, and the full Python 3.11–3.14 Linux CI repository suite.

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
