# Changelog

All notable public changes are documented here. The project is in public alpha; interfaces may change before a stable release.

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
