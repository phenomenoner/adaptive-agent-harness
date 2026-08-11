# Changelog

All notable public changes are documented here. The project is in public alpha; interfaces may change before a stable release.

## [0.3.0a0] — 2026-08-11

### Added

- first public Adaptive Agent Harness repository;
- marketing-first English README and 17 linked translations;
- MIT license, security policy, contribution guide, and CI workflow;
- versioned recovery-policy bindings for durable RLM operations;
- exact continuation boundaries across operation, attempt, generation, lease, input, environment, policy, receipt, usage, deadline, and cancellation state;
- schema v4 recovery policy, boundary, and successor records;
- policy-bound RLM successor attempts that reuse authoritative receipts and refuse blind replay;
- Linux/WSL and native Windows exact-wheel compatibility evidence.

### Verified

- full repository suite: 199 passed, 1 platform-gated skip;
- Python 3.11–3.14 support;
- 30-tool MCP v7 surface;
- additive v3-to-v4 migration and older-reader fail-closed behavior;
- durable supervisor, frontend replacement, single-owner fencing, and installed host canary.

### Known limits

- public alpha; no stable API guarantee;
- not a security sandbox;
- no generic external-effect execution or final delivery;
- no universal exactly-once guarantee;
- automatic broad IPython workspace restoration remains in progress.

[0.3.0a0]: https://github.com/phenomenoner/adaptive-agent-harness/releases/tag/v0.3.0a0
