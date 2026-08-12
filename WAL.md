# Adaptive Agent Runtime Work Log

Append-only record of material project transitions. Corrections are new entries; earlier entries are not rewritten.

> **Public evidence note:** this fresh-history repository preserves a sanitized copy of the
> maintainer work log. Historical local-host results are maintainer-reported unless their supporting
> artifact is committed in this tree. Private receipts, raw traces, opaque task identifiers, and
> machine-local paths are intentionally omitted; do not treat their historical precision as
> independently auditable public evidence.

## 2026-08-08 — Repository baseline and MCP-first plan

**State before:** no standalone AAR Git repository; planning lived in the AHC × AAR coordination workspace.
**State after:** standalone repository initialized on `main`; project remains planning-only with no runtime implementation.

Decisions:

- Move the minimal MCP adapter from ecosystem expansion into AR-0.
- Use MCP as the first public cross-harness integration surface for Codex App and Hermes Agent.
- Keep AAR schemas and operation semantics transport-neutral.
- Keep supervisor/worker framed IPC private.
- Preserve AHC as the first strong managed host and a separate integration gate.
- Require fresh-host operation readback; config/catalog/startup evidence alone is insufficient.
- Start MCP work with an exact modern/legacy compatibility probe because target clients may not speak the same protocol era.

Evidence gathered:

- the prior AAR tree contained planning Markdown only and no implementation;
- CodeGraph found no indexable source in that tree;
- official Codex documentation currently describes local stdio and Streamable HTTP MCP servers and shared Codex-host configuration;
- the latest MCP specification observed on this date is `2026-07-28`, with a compatibility boundary against legacy initialization-based revisions.

Remaining work:

- choose the Python packaging baseline;
- implement and verify AR-0A schemas/fixtures;
- select the MCP SDK only after its protocol-era behavior is measured;
- execute Codex App, Hermes, and AHC compatibility gates before changing any row to verified.

---

## 2026-08-08 — Local development navigation and overview

**Objective:** keep local project settings, human-readable progress, and implementation navigation stable without publishing machine-specific paths or operator notes.

Changes:

- Added ignored `README.local.md` with repository binding, source-of-truth order, WAL split, Git/commit rules, Baton/Luna routing, CodeGraph guidance, and Context Canvas boundaries.
- Added ignored `roadmap.local.html`, a concise Traditional Chinese overview of current state, architecture, five delivery horizons, and the next three actions.
- Added tracked ignore rules and project-agent instructions so future tasks know to read and maintain the local files when present.
- Required Context Canvas navigation during implementation when the current task has a trusted hook-provided opaque ID and the tools are callable. IDs must never be stored or inferred from the repository.
- Kept repository WAL and executed evidence authoritative. Canvas remains a bounded semantic map; the HTML remains a human-readable projection.

Current task note:

- A trusted task binding was present, but no checkpoint existed and implementation had not started. No checkpoint was created automatically.
- AR-0 remains `planned`; this is workflow and local-visibility setup only.

---

## 2026-08-08 — Bundled MCP operation skill added to the plan

**Objective:** make agent-facing operation guidance a first-class part of the MCP deliverable rather than relying on tool descriptions or server instructions alone.

Decision:

- AR-0C now owns a canonical, versioned, digest-bound `skills/aar-operations/SKILL.md` contract after the public MCP tool names and schemas are executable.
- MCP server instructions remain the short server-wide constraint layer; the skill owns the full multi-step workflow.
- AR-0D host bundles deliver the same canonical skill semantics to Codex App and Hermes.
- The skill covers capability discovery, workspace create/attach, execute, operation handles, status/cancel/reconcile, artifact resolution, stale/restarted handles, unsupported capabilities, and authority limits.
- Skill installation or visibility is setup evidence only. Each compatibility row requires a fresh-host, skill-guided MCP workflow and exact result readback bound to the skill version/digest.
- The skill grants no authority, contains no provider credentials or operator paths, and cannot substitute for host policy or missing capabilities.

Phase status remains `planned`; this entry changes scope and acceptance only. No skill or MCP implementation exists yet.

---

## 2026-08-08 — AR-0A host-neutral contract baseline verified

**Objective:** turn the planning-only repository into the first executable, falsifiable AAR
contract candidate without introducing host-specific authority or transport dependencies.

**State before:** AR-0 planned; no package, source, schema bundle, fixture set, or runtime tests.

**State after:** AR-0 is in progress. AR-0A is verified against executable candidate
`0731847a908041cc46c7cdf02eebe57d7ff50d3c`; AR-0B reference-host lifecycle is the next gate.

Implementation:

- Added the `adaptive-agent-runtime` `0.1.0a0` package using standard `pyproject.toml`,
  Hatchling, a uv lockfile, and a `src/` layout.
- Declared and exercised Python `>=3.11,<3.15`.
- Froze independent v1 domains for envelope, runtime, workspace, artifact, and broker schemas.
- Added strict, immutable, extra-field-denying Pydantic schemas for opaque typed identities,
  capabilities, grants, budgets, deadlines, generations, revisions, idempotency, failures,
  reconciliation, artifacts, and security-boundary metadata.
- Added deterministic canonical JSON, content digests, generated JSON Schemas, six valid fixtures,
  nine invalid fixtures, fixture round-trip validation, and an AST import-boundary check that
  rejects AHC, Codex, or Hermes imports from AAR core.

Bound evidence:

- Schema bundle digest:
  `sha256:e45f45a5c2df8dfaf4561bcc59410b4a35c51775a9f713211eee6e655e35fa5f`.
- Fixture set digest:
  `sha256:18f42e30c9119bd82be81eebe7d5163ef67509e22945168e257dfa1500b91835`.
- Checked-in schema file SHA-256:
  `95103695caa9fcd67e228c2d875d6757d50678e3552311e1ed3b7439771de0fc`.
- Checked-in fixture manifest SHA-256:
  `e3f32bc7a5bc8c4045cc3395c37deae118aab7d2b21e2059f8346b4618321f4d`.
- Candidate wheel: `adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`, 11,826 bytes,
  SHA-256 `22db5d16921990dd0708be158700c7748de61eafe786f6533dac9f60ac30d8ec`.
- Candidate sdist: `adaptive_agent_runtime-0.1.0a0.tar.gz`, 44,214 bytes,
  SHA-256 `e8c1e48f5a0461179b794212352ad5d36aa4a2372c359d29eecf766c5762600b`.
  These ignored local build artifacts are evidence inputs, not a published release.

Executed verification:

- `uv run --locked aar-contract verify`: passed.
- `uv run --locked pytest -q`: 8 passed on the committed candidate.
- `uv run --locked ruff check src tests`: passed.
- Isolated Python 3.11: 8 passed.
- Local Python 3.12 project environment: 8 passed.
- Isolated Python 3.13: 8 passed.
- Isolated uv-managed CPython 3.14.2: 8 passed.
- `uv build --no-sources`: wheel and source distribution built successfully from the committed
  candidate.
- CodeGraph: current, six Python files, 109 nodes, 254 edges.

Invalid coverage fails closed for identity shape, schema version, digest shape and content,
generation, revision, grant principal, budget, and unknown fields. No provider credential,
direct effect, host admission, activation, or final-delivery path was added.

Remaining gaps and next dependency:

- No operation registry, fake brokers, workspace backend, restart/reconciliation lifecycle, MCP
  server, operation skill, or host compatibility row exists yet.
- Implement AR-0B with persisted idempotency and explicit accepted/running/terminal/indeterminate
  transitions before selecting the MCP SDK.

---

## 2026-08-08 — AR-0B deterministic reference host verified

**Objective:** prove the host-neutral operation lifecycle through a deterministic direct-SDK host
before binding it to MCP or a production workspace engine.

**State before:** AR-0A verified; no operation registry, reference host, fake brokers, persisted
workspace receipt, restart scenario, or direct SDK lifecycle.

**State after:** AR-0A and AR-0B are verified. AR-0 remains in progress; AR-0C MCP plus the
canonical operation skill is the next gate.

**Executable candidate:** `a04ab92f5900c59e7dd8f4ec1e268369488aee5f`.

Implementation:

- Added a SQLite/WAL operation registry with `synchronous=FULL`, state-changing intent persisted
  before acceptance, scoped idempotency keys, input-digest conflicts, monotonic record revisions,
  progress events, explicit certainty, and guarded lifecycle transitions.
- Added runtime generations. Restart rebinds accepted/no-effect intent to the new generation and
  classifies interrupted running work as indeterminate until reconciliation.
- Added a deterministic JSON-scalar WorkspaceBackend fake. Workspace state and its operation
  receipt commit in one `IMMEDIATE` transaction; generation, revision, and session bindings fail
  closed.
- Added a direct-SDK reference host with exact capability-digest, grant, deadline, budget, payload,
  session, generation, and revision checks.
- Added deterministic credential-free Model, retained Subagent, proposal-only Effect,
  content-addressed Artifact, and Evidence broker fakes. No direct effect, provider credential,
  activation, or final-delivery method exists.
- Added child-process probes for a committed-but-uncertain result and an abrupt
  `os._exit(23)` while running.

Executed verification against the committed candidate:

- `uv run --locked pytest -q`: 28 passed on local Python 3.12.
- Isolated Python 3.11: 28 passed.
- Isolated Python 3.13: 28 passed.
- Isolated uv-managed CPython 3.14.2: 28 passed.
- Focused child-process recovery: 2 passed.
- `uv run --locked aar-contract verify`: AR-0A schema/fixture bytes still passed unchanged.
- `uv run --locked ruff check src tests`: passed.
- CodeGraph: current, 15 Python files, 303 nodes, 913 edges.

Lifecycle coverage:

- ready/capability readback;
- accept, run, progress, success, inspect, cancellation, and timeout;
- same-key/same-digest replay and same-key/different-digest conflict;
- stale runtime generation, workspace generation, and workspace revision;
- capability, grant, budget, deadline, payload, and session rejection;
- atomic rollback between workspace state update and receipt insertion;
- transport loss after committed workspace mutation, followed by success reconciliation without
  duplicate revision;
- fresh-process restart after abrupt running-process loss, followed by certain no-receipt failure;
- accepted-intent rebind across runtime generation restart.

Build evidence:

- Wheel `adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`, 24,027 bytes,
  SHA-256 `c554718b8c7d2534c9e119d7619904b320b69444017b84a7d7d09a1f407fd71f`.
- Source distribution `adaptive_agent_runtime-0.1.0a0.tar.gz`, 57,665 bytes,
  SHA-256 `e3e40b1e463e8aabe9f4150ac358b703a6cee5e93c2bf4fee8e31c339feaada2`.
- These ignored artifacts are local verification inputs, not a published release.

Remaining gaps and next dependency:

- The workspace is a deterministic conformance fake, not arbitrary Python execution, IPython, or
  a sandbox claim.
- No MCP transport, MCP protocol-era probe, operation skill, Codex App/Hermes guided workflow, or
  host compatibility row exists yet.
- Implement AR-0C by selecting and measuring the MCP SDK against these unchanged core operation
  semantics. Freeze the canonical `aar-operations` skill only after the executable tool surface is
  stable.

---

## 2026-08-08 — Deterministic MCP surface and bundled operation skill verified

**Objective:** expose the reference-host operation lifecycle through a host-neutral local-stdio MCP
surface and bind the agent workflow to exact tool and skill bytes.

**State before:** AR-0A and AR-0B verified; no MCP transport, protocol-era probe, operation skill,
or reference-host MCP compatibility evidence.

**State after:** AR-0A through AR-0C are verified. AR-0 remains in progress; fresh Codex App and
Hermes host rows in AR-0D are the next gate.

**Executable candidate:** `7656baa23d35086b1ecb20df50c3fdebf7e92ebd`.

Implementation:

- Pinned `mcp==2.0.0` and added the `aar-mcp` local-stdio entrypoint.
- Added ten ordered, structured tools for capabilities, workspace create/attach/execute/inspect,
  operation status/cancel/reconcile, checkpoint metadata, and bounded artifact resolution.
- Kept concise server-wide instructions separate from the canonical
  `skills/aar-operations/SKILL.md` multi-step workflow.
- Added generated and verified MCP tool schemas plus digest-bound skill metadata. The wheel embeds
  the exact skill bytes under `aar/bundled/aar-operations`.
- Published deterministic reference-only fake grants and verify each mutating tool against its
  declared capability. Tool annotations remain descriptive metadata rather than authorization.
- Preserved explicit runtime/workspace generation, revision, deadline, budget, principal, session,
  request, idempotency, operation, certainty, and reconciliation fields across the adapter.
- Kept checkpoint portability, arbitrary Python execution, direct effects, provider credentials,
  activation, and final delivery explicitly unsupported.

Frozen metadata:

- MCP SDK: `mcp==2.0.0`.
- Protocol revisions: modern `2026-07-28`; legacy `2025-11-25`.
- Tool-surface version: `aar.mcp-tools.v1`.
- Tool-surface digest:
  `sha256:a6d5723fdddde62683d3908dc68161f193c612e7e7ac18081482296438db0e14`.
- Operation-skill version: `0.1.0`.
- Operation-skill digest:
  `sha256:ce5dc48d883baec0e42220031f502b13515957a545273910d7f79ab5ad5fbfa7`.
- Schema bundle and fixture set remain
  `sha256:e45f45a5c2df8dfaf4561bcc59410b4a35c51775a9f713211eee6e655e35fa5f` and
  `sha256:18f42e30c9119bd82be81eebe7d5163ef67509e22945168e257dfa1500b91835`.

Executed verification:

- `uv run --locked pytest -q`: 38 passed under local Python 3.12.
- Isolated Python 3.11, 3.13, and uv-managed CPython 3.14.2: 38 passed in each environment.
- Real child-process stdio clients listed and called the same ten tools under both modern and legacy
  negotiation modes.
- MCP scenarios covered structured output plus JSON text fallback, happy path, status, start-only
  cancellation, authority denial, deadline, stale revision, restart/indeterminate status,
  no-receipt reconciliation, unsupported checkpoint metadata, and bounded artifact readback.
- `uv run --locked aar-contract verify --root .`: passed.
- `uv run --locked aar-mcp-assets verify --root .`: passed.
- `uv run --locked ruff check src tests`: passed.
- `quick_validate.py skills/aar-operations`: passed.
- CodeGraph: current, 22 indexed files, 444 nodes, 1,405 edges.

Committed-candidate package evidence:

- Wheel `adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`, 36,460 bytes,
  SHA-256 `805ce9669efea1ce0baf83aee9947148bc5685fbad30eb92047ae39ef054a9d2`.
- Source distribution `adaptive_agent_runtime-0.1.0a0.tar.gz`, 101,384 bytes,
  SHA-256 `4d5589de8786bdbbb609a06ebbdb2d2ac760006833c59424ee7fa2a3b2e75e8c`.
- A fresh Python 3.14 wheel environment called `aar_capabilities` and read back the exact bundled
  skill digest, tool-surface digest, and modern protocol revision.
- These local artifacts are verification inputs, not a published release.

Remaining gaps and next dependency:

- The reference workspace remains a deterministic JSON-scalar conformance fake, not arbitrary
  Python execution, IPython, or a sandbox claim.
- Codex App and Hermes have not yet started a fresh AAR process or completed the skill-guided
  workflow. Their rows remain planned.
- AR-0 becomes verified only after both AR-0D portability rows pass.

---

## 2026-08-08 — AR-0D Codex and Hermes portability verified

**Objective:** package one canonical MCP operation surface and skill for Codex and Hermes, then
accept each host only after a fresh process completes the same public-tool workflow with exact
result readback.

**State before:** AR-0A through AR-0C were verified at historical executable candidate
`7656baa23d35086b1ecb20df50c3fdebf7e92ebd`; no generated host bundles, reusable compatibility
smoke pack, or executed Codex/Hermes rows existed.

**State after:** AR-0A through AR-0D are verified. AR-0 is locally complete at executable candidate
`f3d1b913b479d7f8ef329c0bcc45ddd1f6e04395`; AR-1 programmable workspace is next. No package,
remote, pull request, release, live effect, activation, or final delivery was published or
performed.

Implementation:

- Added `profiles/host-profiles-v1.json` and deterministic generators/verifiers for a Codex plugin
  bundle and Hermes profile distribution. Both copy the exact canonical `aar-operations` skill and
  metadata.
- Put the Codex marketplace manifest at the repository layout required by the native plugin CLI,
  wrapped `.mcp.json` under `mcpServers`, and used the plugin-creator cachebuster/reinstall flow for
  local development pickup.
- Added both Hermes review metadata and the runtime-authoritative `config.yaml.mcp_servers` entry.
  Profile installation preserves user-owned credentials while replacing declared
  distribution-owned configuration and skills.
- Added `aar-host-assets` and `aar-compat-smoke`. The black-box smoke pack launches the real stdio
  command and uses only public MCP calls for capabilities, discovery, create/execute/inspect,
  status, cancellation, stale revision, checkpoint limitation, and artifact readback.
- Successful operations now return a deterministic content-addressed operation-receipt artifact,
  making positive artifact resolution possible without hidden access to the reference host.
- Added request-context `negotiated_protocol_version` telemetry. The first Codex probe disproved the
  prompt-supplied `2026-07-28` expectation by reporting `2025-06-18`; that earlier result was not
  accepted. The server now derives and advertises all revisions actually served by `mcp==2.0.0`:
  `2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`, and `2024-11-05`. Tests require the
  negotiated value to occur in that list.
- Preserved Python `>=3.11,<3.15`; Python 3.14 remains a tested first-class target.

Current metadata:

- Package: `adaptive-agent-runtime==0.1.0a0`.
- Tool-surface version: `aar.mcp-tools.v1`.
- Tool-surface digest:
  `sha256:5b503d1b0600d70a8d1d3f7f8995378533694279dc8b233c7c9342e96d075e5d`.
- Operation-skill version: `0.2.2`.
- Operation-skill digest:
  `sha256:8ef484155b31022293596dfb5825aaa66e03d5ac9b82dcaa89226b1d3e54fa68`.
- Schema bundle and fixture set remain
  `sha256:e45f45a5c2df8dfaf4561bcc59410b4a35c51775a9f713211eee6e655e35fa5f` and
  `sha256:18f42e30c9119bd82be81eebe7d5163ef67509e22945168e257dfa1500b91835`.
- Codex `.mcp.json` config digest:
  `sha256:94d2e67b919c375a8b03ed2f79014451727c3275ad2d051612cee54b4ea6423c`.
- Hermes `config.yaml` config digest:
  `sha256:49d3ef8a0e4e2a1e7b76fec5e2a9f1f8ccb37b89af55427e4151f41bc3639585`.

Executed repository verification:

- `uv run --python 3.11 --frozen pytest -q`: 44 passed.
- `uv run --python 3.12 --frozen pytest -q`: 44 passed.
- `uv run --python 3.13 --frozen pytest -q`: 44 passed.
- `uv run --python 3.14 --frozen pytest -q`: 44 passed under CPython `3.14.2`.
- `uv run ruff check src tests`: passed.
- `uv run aar-mcp-assets verify`: passed.
- `uv run aar-host-assets verify`: passed.
- Codex plugin structural validation: passed.
- CodeGraph was refreshed after the implementation and reported 31 files, 512 nodes, and 1,542
  edges.

Committed-candidate package evidence:

- Wheel `adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`, 53,949 bytes, SHA-256
  `000f975f16f3ab84780ae90a307106e267da80f919f05514a2a846f2c4840498`.
- Source distribution `adaptive_agent_runtime-0.1.0a0.tar.gz`, 113,360 bytes, SHA-256
  `8230583345ffeb473a0383dde32c980291a75859311d730b35054899acd1a43f`.
- These are local verification artifacts, not a published release.

Codex row:

- Host: `codex-cli 0.146.0` in the Windows 11 build `26200` Codex App environment.
- The local marketplace and plugin were installed natively; the installed cache read back plugin
  version `0.1.0+codex.20260808144312`, skill `0.2.2`, and the exact digests above.
- A fresh ephemeral `codex exec` process, using `gpt-5.6-terra` at high effort, loaded the skill and
  called the installed MCP server. Server-side request telemetry reported protocol `2025-06-18`
  and runtime generation `5`.
- All required results passed: ten-tool capability readback, create, succeeded/certain
  `answer=42`, inspect revision `1`, status, start-only plus real cancellation, stale revision
  `WORKSPACE_REVISION_CONFLICT`, explicit unsupported checkpoint, and artifact resolution.
- The resolved 331-byte receipt bound the succeeded operation and `answer=42`; declared and
  computed digest both equaled
  `sha256:1dc96f0a0a1be236364eda8b43d53f2dfbff6415851470f9d1902b3b2d63b812`.
- Final receipt `codex-ar0d-f3d1b91-final.json`: SHA-256
  `badbb5f4ea4b527cfbc349c9ae6f2c9968309702e2c366cf7d4f9570c9314762`.
- JSONL tool trace `codex-ar0d-f3d1b91-events.jsonl`: SHA-256
  `f99d01e7b6a77c0e3f183aef3d36490e01a9016171e280dc61138097be6dc9b7`.
- The agent made one schema-invalid create call using `principal`/`session`; the tool rejected it
  before mutation, and the corrected call completed. This invocation noise is preserved in the
  trace rather than reported as a zero-error run.

Hermes row:

- Host: Hermes Agent `0.19.0` (`2026.7.20`), local `431a588f` with five carried commits, Python
  `3.11.15`, on Ubuntu `24.04.4` under WSL2.
- An isolated `aarverifyf3d1b91` profile installed the distribution. Native `hermes mcp test aar`
  connected over stdio in 2,122 ms and discovered all ten tools.
- The first one-shot attempt failed before session, MCP, or database mutation because Auto routed
  `gpt-5.6-sol` to `openai-api` without an API key. The preserved failure receipt identified the
  route; the accepted run explicitly selected existing `openai-codex` OAuth and `gpt-5.6-sol`.
- The accepted one-shot loaded the canonical skill from the isolated profile, made ten AAR MCP
  calls, and completed the same required workflow without an invalid tool call. Request telemetry
  reported protocol `2025-11-25` and runtime generation `7`.
- The resolved 332-byte receipt bound the succeeded operation and `answer=42`; declared and
  computed digest both equaled
  `sha256:c908a0e42a83c97f71e257f45fcaf182f5d82db460955a8672451e6a87ba0422`.
- Final receipt `hermes-ar0d-f3d1b91-b-final.json`: SHA-256
  `0671cae2aea42873460c11c87332fb9f21f95c7faa0f49aa97c14a0b1d75384e`.
- Usage receipt: SHA-256
  `cfdb6af869c85b3802054bca5010812e8fb6d4902c2ad0f458e662c5cb38dd94`; provider
  `openai-codex`, model `gpt-5.6-sol`, 17 API calls, completed true.
- Redacted trace: SHA-256
  `2dc32a0044268c422eda4624227746d6f5ac1099438f59e7e17605f5b93434b2`.
  Redacted session export: SHA-256
  `1a257b02a6998dcb933aa30759a6b722b30a5c584def44d02b632c6d5f79d915`.

Known limits and retained gaps:

- Both managed MCP launchers ignored the parent process's ad hoc `AAR_DATABASE` override, so each
  fresh MCP child reused the default repository-local recovery database. Fresh-process evidence is
  bound by increasing runtime generation, not a fresh-database claim.
- The Codex row is a fresh CLI process against the local plugin surface shared with Codex App; a
  separate new desktop-task UI pickup was not replayed. The Windows read-only shell could not run
  the deadline probe, so the accepted run used explicit host approval and `danger-full-access`.
- The Hermes row is CLI one-shot only. The distribution supplies MCP configuration but not an
  inference model; the test selected provider/model explicitly. Gateway parity and messaging
  delivery are not claimed.
- Checkpoint portability, arbitrary Python execution, direct effects, provider credentials,
  activation, and final delivery remain unsupported until later gates.
- A Luna/max Codex compatibility attempt was calibrated but did not honor the cancellation stop
  condition before its bounded deadline. The host-judgment row therefore used Terra/high; Luna was
  not treated as an independent reviewer or acceptance oracle.
- Context Canvas navigation remained optional as designed. An MCP read timed out; the same trusted
  checkpoint was available through the CLI fallback, and repository WAL/source/test evidence
  remained authoritative.

Next dependency:

- Start AR-1 by defining the shared workspace backend contract and a deterministic plain-Python
  programmable lifecycle before adding the supervised IPython worker.
- Keep AHC compatibility, RLM, assets, shadow adaptation, managed materialization, and any public
  release behind their separate gates.

---

## 2026-08-09 — AR-1 programmable workspace verified

**Objective:** add a backend-neutral programmable workspace contract, separately supervised
plain-Python and IPython implementations, portable checkpoint/restore, and restart-safe
reconciliation; accept AR-1 only after direct, MCP, Python 3.11–3.14, Codex, and Hermes evidence.

**State before:** AR-0 was verified at
`f3d1b913b479d7f8ef329c0bcc45ddd1f6e04395`; arbitrary Python execution, backend-bound handles,
portable checkpoints, worker health/interrupt, and programmable restart reconciliation did not
exist.

**State after:** AR-1 is locally verified at executable candidate
`6aa8fc7f7598ebc77560defe1d31d30719a64339`. AR-2 portable RLM is next. No package, remote, pull
request, release, live effect, activation, or final delivery was published or performed.

Implementation commits:

- `4db026cedb10e658971aa6de6a6827d634f8b91e` — programmable workspace contract.
- `5f686de58d3fefeab4a1e857155602548acc1fdf` — supervised IPython workspaces.
- `046c9eeee836948c7a7ae7c0e6092ea147b7b0cb` — backend identity, checkpoint provenance, and
  restart reconciliation.
- `427070ab12c09352123db60813774d3e4be35f8d` — server clock for bounded request construction.
- `6aa8fc7f7598ebc77560defe1d31d30719a64339` — Codex profile cachebuster and final executable
  candidate.

Implemented behavior:

- `ProgrammableWorkspaceHandle` binds workspace, complete backend descriptor, generation, and
  revision. The descriptor carries kind, version, capability digest, checkpoint formats, and
  features. Plain and IPython backends reject foreign or partially reconstructed identities.
- Program execution returns bounded structured events and advances revision even when a failed cell
  may have partially changed the namespace. IPython history variables are excluded from user
  artifacts.
- Checkpoint creation is a recorded mutation operation but does not advance workspace revision.
  The manifest binds source handle, creation operation, trace, environment fingerprint, values,
  exclusions, artifacts, and content digest. Restore supports the declared JSON subset and creates
  a new generation at revision zero.
- Generic cancellation rejects programmable operations; interrupt must reach the worker. Restore
  rejects a running operation and fences the replaced IPython worker.
- On runtime restart, a running programmable operation becomes indeterminate. Missing backend
  receipts reconcile as lost with `PROGRAM_RECEIPT_UNAVAILABLE_AFTER_RESTART` and remain
  indeterminate instead of manufacturing success or failure.
- MCP surface v2 contains 20 tools. `aar_capabilities.server_now_unix_ms` lets a fresh host build a
  reference deadline within the 60-second budget without relying on prompt-time clock guesses.
- `aar-operations` `0.3.1` documents the exact flat read and mutation context fields, complete
  programmable handles, checkpoint provenance, program-specific interruption/reconciliation, and
  server-clock deadline construction.

Current metadata:

- Package: `adaptive-agent-runtime==0.1.0a0`; Python range `>=3.11,<3.15`.
- Tool-surface version: `aar.mcp-tools.v2`; 20 public tools.
- Tool-surface digest:
  `sha256:71a42a4b8dd554c717bfd0a5e36f601234da3016d6fa96138548c2291747d44b`.
- Operation-skill version/digest: `0.3.1`,
  `sha256:bc83747e71ec7ae9e9bbf8c35661bfad770f282e035fae8ffa675f91bc5ca7d3`.
- Schema bundle and fixture set remain
  `sha256:e45f45a5c2df8dfaf4561bcc59410b4a35c51775a9f713211eee6e655e35fa5f` and
  `sha256:18f42e30c9119bd82be81eebe7d5163ef67509e22945168e257dfa1500b91835`.
- IPython `9.16.1` backend capability digest:
  `sha256:0a9e7aeef5b6c086e329d3822e560910c76ce26ee15878093e76bf9d34691be6`.
- Clean-candidate wheel `adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`, 80,998 bytes, SHA-256
  `d4e7a7a4a15bd626cc6116bcad0180f9e1b5f091fe7af40974c3365e6c232970`.
- Codex plugin cachebuster: `0.1.0+codex.20260808172114`; generated Codex config digest remains
  `sha256:94d2e67b919c375a8b03ed2f79014451727c3275ad2d051612cee54b4ea6423c`.

Executed repository verification:

- Focused direct and MCP programmable tests: 27 passed before the server-clock correction.
- Final `uv run --isolated --python 3.11 --frozen pytest -q`: 64 passed, exit 0.
- Final `uv run --isolated --python 3.12 --frozen pytest -q`: 64 passed, exit 0.
- Final `uv run --isolated --python 3.13 --frozen pytest -q`: 64 passed, exit 0.
- Final Python 3.14 suite: 64 passed.
- `uv run ruff check src tests`: passed.
- `uv run aar-mcp-assets verify`: passed.
- `uv run aar-host-assets verify`: passed.
- Skill Creator quick validation: passed.
- MCP and host asset generation is ordered: MCP generation precedes host generation. An earlier
  parallel invocation observed a harmless stale-copy race and was not used as final evidence.
- CodeGraph reported the current repository-local index at 37 files, 802 nodes, and 2,774 edges.

Codex row:

- Host: `codex-cli 0.146.0` on Windows 11 build `26200`; fresh ephemeral process using
  `gpt-5.6-terra` at high effort.
- Server telemetry reported MCP `2025-06-18`, runtime generation `10`, v2/20 tools, and the exact
  tool and skill digests above.
- The accepted lifecycle created the IPython handle, executed and inspected `answer=42` at revision
  1, reconciled completed, checkpointed with bound source/operation/trace/environment provenance,
  restored generation 2/revision 0, read ready health, and closed succeeded.
- Final receipt `codex-ar1-6aa8fc7-d-final.json`: SHA-256
  `c45f1220f2e63b3091dc18d26a499d959061feb1bdf02c0cd0bfea5673cdd0c4`.
- Three earlier probes were retained as evidence: the first found `aar-mcp` absent from PATH; the
  second used schema-invalid nested context fields and was rejected before mutation; the third
  used a prompt-fixed deadline that expired during startup. Installing the exact wheel, documenting
  flat fields, and adding the server clock addressed those failures.
- The accepted agent read the installed skill once through shell before the MCP lifecycle. This
  proves installed-byte pickup plus the real AAR lifecycle, but is not claimed as tool-exclusive.

Hermes row:

- Host: Hermes Agent `0.19.0` (`2026.7.20`), local `431a588f`, Python `3.11.15`, Ubuntu `24.04.4`
  under WSL2; provider `openai-codex`, model `gpt-5.6-sol`.
- Isolated profile `aarverify6aa8fc7` installed the generated distribution and exact skill bytes.
  Native `hermes mcp test aar` discovered all 20 public tools.
- The agent path exposed the public calls as `mcp__aar__aar_*` and required invocation toolset
  `aar`. Its parent-death watchdog intermittently failed to execute the generated bare `aar-mcp`
  command with `PermissionError`; all failed attempts stopped before mutation. The accepted isolated
  profile bound `<user-home>/.local/bin/aar-mcp`, producing config digest
  `sha256:07b25637a25a5ab44ec1b1a09206791cdbc0e562cf638c316cc0edbd9430e17a`.
  This is a documented host-launcher workaround; the generated bare-command profile retains digest
  `sha256:49d3ef8a0e4e2a1e7b76fec5e2a9f1f8ccb37b89af55427e4151f41bc3639585`.
- The accepted one-shot made 15 AAR MCP calls and no shell, file, browser, Python, or tool-search
  call. It negotiated `2025-11-25`, runtime generation `1`, and completed the same create,
  execute/inspect/reconcile, checkpoint, restore generation 2, health, and close lifecycle.
- Final receipt `hermes-ar1-6aa8fc7-raw.txt`: SHA-256
  `6a87db832a7ae0e385643906feb7268c9ac1974e05130e78128943ed96d916c8`.
- Usage receipt: SHA-256
  `66eaeb207ac3908cb318705d80f329a1283c80a3e9c73a189491528e4edcfa98`; 13 API calls,
  completed true.
- Redacted trace: SHA-256
  `05d1119895d5555863f386c2e3f507f098feb9a76bd13d5d359b6c340bde2d71`.

Architecture decisions for AR-2 and AHC integration:

- A read-only `gpt-5.6-sol`/max design sidejob reviewed
  `references/aar-ipython-codegraph-nooa-synergy-and-design.md` in the AHC-by-AAR topic without
  writing repository files. Main-agent verification adopted the AR-1 identity, checkpoint, and
  restart hardening and moved the typed broker bridge to AR-2.
- NOOA is design input only. AR-2 may cherry-pick progressive-disclosure, typed-routing, and
  observability patterns, but will add no NOOA adapter, workspace backend, package dependency, or
  Python core dependency.
- CodeGraph begins as a separate optional skill-guided external workflow for AAR IPython artifacts.
  `aar-operations` will not silently install or sync it. An optional provider package remains
  benchmark-gated.
- AHC consumes transport-neutral broker and operation contracts. A native adapter may strengthen
  identity, budgets, cancellation, restart reconciliation, evidence, activation, and delivery
  fencing without making AAR core depend on AHC or NOOA.
- Context Canvas decision node `N000008` records the NOOA and CodeGraph split. Repository source,
  tests, host receipts, and this WAL remain authoritative.

Remaining gaps and next dependency:

- The accepted Hermes row requires an isolated absolute-command workaround; generated-profile
  bare-PATH startup under the agent watchdog remains an explicit host compatibility gap.
- Neither IPython backend is a security sandbox. Provider credentials, external effects,
  activation, and final delivery remain absent.
- Start AR-2 with the bounded persisted RLM state machine and typed broker facades. Keep CodeGraph
  optional and skill-guided, keep NOOA out of runtime dependencies, and integrate AHC at the shared
  contract/fixture boundary.
- AR-3 assets, managed AHC behavior, publication, and release remain behind their own gates.

---

## 2026-08-09 — AR-2 portable RLM and AR-3 canonical assets verified

**Objective:** complete and accept a portable persisted RLM engine plus canonical adaptive assets
without adding provider credentials, direct effects, activation, final delivery, AHC internals,
NOOA runtime code, or an implicit CodeGraph dependency.

**State before:** AR-1 was locally verified at
`6aa8fc7f7598ebc77560defe1d31d30719a64339`. Broker fakes existed, but there was no persisted RLM
job/trace, retained broker-call contract, adaptive-asset catalog, explicit asset lifecycle, or
portable asset bundle.

**State after:** AR-2 and AR-3 are locally verified at executable candidate
`ea8340275bdb82e883711f8360f5ef1af869c1f5`. The current tracked documentation records that
candidate without changing its executable bytes. No remote, pull request, package publication,
release, live effect, serving activation, or final delivery was performed.

Implementation commits:

- `59fb736` — persisted brokered RLM core.
- `99a1d9d` — portable RLM contracts and MCP exposure.
- `ac58942` — deterministic RLM benchmark pack and transport-neutral AHC contract fixture.
- `117cbc5` — separate public broker and RLM contract modules; process-loss reconstruction from
  persisted outer intent.
- `06603f9` — canonical adaptive-asset lifecycle.
- `d93bae2` — refreshed host profile and personal plugin assets.
- `ea83402` — bundled schemas, fixtures, benchmarks, integration fixtures, and skills in package
  artifacts, with package-content verification.

RLM behavior:

- Persisted jobs bind typed strategy identity, programmable workspace, budgets, grants, trace
  steps, broker calls, retained child/artifact/evidence handles, terminal result, uncertainty, and
  reconciliation.
- Model, Subagent, Effect-proposal, Artifact, and Evidence facades are transport-neutral public
  contracts separated from the reference broker implementation. Effect remains proposal-only.
- An outer accepted/running operation whose RLM row is missing after process loss reconstructs the
  job from its persisted request payload. Missing completion evidence remains indeterminate rather
  than being converted into success or failure.
- The benchmark pack compares an evidence-heavy strategy with a non-RLM baseline and reports
  quality, latency, cost, uncertainty, and safety bounds.
- `integration/ahc-broker-contract-v1.json` exercises the shared operation/broker boundary without
  importing AHC or claiming a Rust/native-adapter row.

Adaptive-asset behavior:

- Public immutable types cover agent fingerprints, episodes, outcomes, evaluations, and proposals;
  documents and manifests use content-addressed references.
- The catalog records explicit selection, disclosure, use, outcome, and attribution events. Missing
  outcomes remain unknown. Conflicting explicit outcomes fail closed.
- Export is deterministic and dependency-closed. Import validates the complete bundle before one
  SQLite transaction, rejects missing or kind-conflicting references, and returns a stable replay
  result.
- Detached schema migrations run twice and reject nondeterministic output.
- The materializer seam only prepares a bounded preview. There is no activation or serving-write
  API.
- Asset import uses the normal outer-operation/idempotency/reconciliation contract and repairs the
  process-loss window without manufacturing terminal truth.

Current contract metadata:

- Package: `adaptive-agent-runtime==0.1.0a0`; Python range `>=3.11,<3.15`.
- Schema bundle:
  `sha256:6e2aee5ba92c07fb18b7cb074712679617d64b2c895ebf4fbd167f3f31f112ab`.
- Fixture set:
  `sha256:5d07a6dac808b44339bfd93a37257fb74ea575a64b40b6c1cda26cb0e6add1e4`.
- MCP surface: `aar.mcp-tools.v4`, 28 tools,
  `sha256:25a4d4e14d62fb5bd934382b05f8cc2228b6f7d17cd3b08053161c71cda810fb`.
- `aar-operations`: `0.5.0`,
  `sha256:59b2e32427cd4b6c84b2c17ebac540fa11dbc6bf49e56118d8b92cfa3037278a`.
- Personal Codex plugin: `0.1.0+codex.20260808192807`.
- Installed/source selected-file readback: plugin manifest
  `9ecf2684fb09ed95ce6e88599517ce111ce4d65929b54b6832f1bb2d95d33be8`, MCP config
  `94d2e67b919c375a8b03ed2f79014451727c3275ad2d051612cee54b4ea6423c`, operation skill
  `59b2e32427cd4b6c84b2c17ebac540fa11dbc6bf49e56118d8b92cfa3037278a`, skill metadata
  `3954c341cf9c87c2faea512441b08b02ef383199d05de9746143a5952c0c992f`, and optional
  CodeGraph workflow skill
  `77ec795673a972fa88735feae56e6389d97e08725d012d9ea1c2e7e6ba961bc6`.

Executed repository verification:

- Focused AR-2/AR-3 batches reached 56 passing tests before the final package-content test and
  passed Ruff, MCP asset generation, host-profile generation, contract verification, and Skill
  Creator validation.
- Final executable-candidate matrix command, run once per interpreter through one sequential
  session: `uv run --isolated --python <3.11|3.12|3.13|3.14> --frozen pytest -q`.
- Python 3.11: 92 passed in 90.67 seconds.
- Python 3.12: 92 passed in 52.14 seconds.
- Python 3.13: 92 passed in 47.53 seconds.
- Python 3.14: 92 passed in 49.39 seconds.
- The earlier source-only matrix before the package-content test passed 91 tests on all four
  interpreters; it is superseded by the 92-test matrix above.

Package and isolated-wheel evidence before the documentation freeze:

- Wheel `adaptive_agent_runtime-0.1.0a0-py3-none-any.whl` contained 82 members and every required
  schema, fixture, benchmark, integration fixture, operation skill, and optional CodeGraph workflow
  skill.
- Isolated Python 3.11 and 3.14 environments installed the exact wheel and passed
  `aar-compat-smoke`: MCP `2026-07-28`, 28 tools, v4 surface, skill `0.5.0`, all nine checks passed,
  and bundled-resource readback passed.
- The final wheel is rebuilt after this README/evidence freeze so its digest reflects the final
  package metadata; the resulting digest is recorded in a later append-only WAL entry.

Fresh Codex AR-2/AR-3 row:

- Host: `codex-cli 0.146.0`, fresh ephemeral `gpt-5.6-sol` high-effort process on Windows 11 build
  `26200`; no shell or repository-file authority was granted to the delegated compatibility run.
- Installed plugin `adaptive-agent-runtime` version `0.1.0+codex.20260808192807` loaded the bundled
  `aar-operations` skill and v4/28-tool MCP surface.
- The accepted RLM job succeeded with a certain terminal result and trace steps `evidence.query`
  and `model.request`; one model request was retained. Broker method/readback contract digest:
  `sha256:bd06715bade9ef377400fa145484d7a330fdfc4f6cb554d43c39ed4ca62058b7`.
- Canonical empty asset export/import succeeded with certain outer operations; bundle digest:
  `sha256:f6ece0928b3dae780d60af332d1c1858b2d34cf30f1c86c822ee2f2d9c8c23bd`.
- Active serving remained false. Provider credentials, external effects, activation, and final
  delivery were neither available nor exercised.
- Six schema/deadline-invalid calls were rejected before the accepted lifecycle. The final row is
  accepted because the capability/RLM/asset operations passed, not because those correction calls
  were hidden.
- A redacted summary was retained in maintainer-private evidence but is not included in this public
  repository. Treat this host row as maintainer-reported historical context rather than
  independently auditable public proof. The raw trace was not retained.

Design and integration boundary:

- The sidejob `gpt-5.6-sol`/max design review informed contract separation and lifecycle hardening;
  main-agent source/test review decided what to adopt.
- CodeGraph is a separate optional skill-guided external workflow for organizing and navigating AAR
  IPython artifacts. It is not part of `aar-operations`, does not auto-install or auto-sync, and is
  not an AAR core dependency. A package-level provider remains benchmark-gated.
- NOOA contributed design patterns only. There is no NOOA adapter, backend, dependency, or Python
  compatibility constraint.
- AHC integration begins at the shared transport-neutral fixtures. A future native adapter may
  strengthen identity, budgets, cancellation, restart recovery, evidence, activation, and delivery
  fencing, but it must preserve the same contract semantics.

Known limits and next dependencies:

- The current Codex desktop task predates the plugin cachebuster; reliable plugin pickup requires a
  fresh task or application restart. The accepted host row used a fresh CLI process.
- The AR-1 Hermes row was not replayed for AR-2/AR-3. The existing absolute-command launcher
  workaround and gateway/messaging gaps remain.
- The fresh Codex asset row used an empty canonical bundle. Contentful bundles are proven by direct
  and MCP tests, not by that fresh-host row.
- The fresh Codex raw trace was not retained; only the redacted deterministic summary receipt is
  hash-bound.
- No Rust AHC consumer/native adapter, AR-4 shadow adaptation, managed materialization, publication,
  or release is claimed.

### Final wheel and post-documentation readback

- Documentation/evidence commit: `319a792` (`docs: record portable RLM and asset verification`).
  It changes package README metadata but no executable module, schema, fixture, skill, or test.
- Rebuilt wheel: `dist/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`, 163,774 bytes,
  SHA-256 `9199ac153c99bb3d1fc9a7d782ec7ef8e4835b20fc4ff78c9749f5ef745c7f78`.
- Wheel readback found 82 members: the canonical schema bundle, 23 fixture files, one benchmark,
  two AHC integration files, three packaged copies of the operation skill, and two host-profile
  copies of the optional CodeGraph workflow skill.
- `uv tool install --force <exact-wheel>` succeeded. The installed `aar-compat-smoke` reported all
  nine checks passed with MCP `2026-07-28`, 28 tools, v4 surface, schema/skill/tool digests above.
- Exact-wheel isolated Python 3.11 and 3.14 runs passed all nine checks sequentially with unique
  scenario identities (`final-wheel-py311` and `final-wheel-py314-b`).
- Two initially parallel isolated-wheel runs were rejected at the cancellation distinction, and a
  parallel retry with distinct requested database paths left those paths unused and one process
  was fenced during programmable creation. `StdioServerParameters` did not forward the ad hoc
  `AAR_DATABASE` variable, so both managed MCP children shared the repository default database;
  concurrent acceptance processes against one single-writer runtime database are not an isolated
  test topology. These attempts are retained as invalid execution-shape evidence, not counted as
  passing. Sequential unique-scenario runs against the same final wheel are the accepted package
  readback.
- Final focused gates passed: Ruff; contract assets; MCP assets; host-profile assets; the package
  content test; and both Codex skill validations. The first skill-validator invocation lacked its
  external `PyYAML` helper and failed before validation; rerunning the same validator through
  `uv run --with pyyaml` passed both skills.

## 2026-08-09 — AR-2/AR-3 independent-review recovery candidate

**Objective:** resolve every blocker in the formal `bdb2810` review wave without weakening the
Portable RLM, adaptive-asset, Python 3.11–3.14, AHC-boundary, CodeGraph, or NOOA contracts.

**State before:** source candidate `ea8340275bdb82e883711f8360f5ef1af869c1f5` passed its original
92-test matrix and package/host probes, but the formal primary review was `BLOCKED`. The narrow audit
expanded the replay family and left the audited finding set `INCOMPLETE`; those artifacts override
the earlier local acceptance claim.

Review bindings:

- frozen review wave `c096e780bcc46741c16a9d703521d3d999b04eae969be53fa3b9c5ee97ff5478`;
- primary report SHA-256 `ab1965c030a63509ff368ddee0599461db8c9ba334428d42fec87f632055c349`;
- narrow audit SHA-256 `5efd9f3b8160611e45602b2aea27c8d0781f901f6dad622c529880bdddefcee6`;
- valid blocked/incomplete union synthesis SHA-256
  `899db69d4bbf3de6e7c4f7fe612a122940bb3be9e914646ccf764c8afc2a289a`;
- the installed audit validator cannot validate its own specified `NARROW` same-report topology
  because it hardcodes reciprocal bindings. This tooling defect did not weaken the review verdict.

Accepted blocker family and repair:

- the shared idempotency replay primitive now rejects mismatched session, lane, workspace binding,
  parent operation, payload bytes, or input digest before returning an existing operation;
- all nine public mutation families have atomic cross-session replay regressions. Three already
  rejected earlier through authority checks; the other six reproduced disclosure before the fix;
- `aar_operation_status` and `aar_rlm_status` are zero-write. Missing inner RLM state is reported as
  `RLM_STATE_MISSING`; only mutation-authorized reconciliation may reconstruct it;
- outer cancellation is durable authority before the derived RLM projection. Reconciliation
  canonicalizes a lagging inner job to the outer terminal state without execution;
- benchmark identity excludes only locally observed wall-clock timing;
- `DeterministicPrepareOnlyMaterializer` executes the concrete deterministic, effect-free T1 seam;
- MCP invalid-bundle regressions cover dependency-incomplete and conflicting-kind bundles without
  partial catalog mutation;
- child processes use `os._exit` at RLM pre-broker, RLM post-terminal, outer-cancel, and atomic
  asset-import durability boundaries, then reopen and reconcile through a fresh process;
- correction to the earlier entry: the current RLM is session/operation-bound and
  workspace-independent. Programmable workspace and RLM are separate operations. CodeGraph stays an
  optional external workflow, and NOOA remains design input only.

Repair commit and executed evidence:

- repair source commit: `c21d595` (`fix: close portable RLM and asset review gaps`);
- fail-first replay run: 6 failed, 3 passed; the six failures disclosed prior results, while the
  three passing families already rejected a foreign session before replay;
- fail-first status/benchmark/materializer run: 5 failed, demonstrating both status writes, hidden
  RLM-row repair, nondeterministic benchmark identity, and the missing concrete materializer;
- focused recovery and review regressions: 23 passed in 19.28 seconds;
- repository suite on Python 3.14 before the matrix: 113 passed in 56.82 seconds;
- Python 3.11: 113 passed in 74.24 seconds;
- Python 3.12: 113 passed in 77.02 seconds;
- Python 3.13: 113 passed in 76.94 seconds;
- Python 3.14: 113 passed in 68.65 seconds;
- contract, MCP-asset, host-profile, Ruff, and diff checks passed.

**State after:** `c21d595` is the source repair candidate. Final wheel readback, refreshed fresh-Codex
evidence, atomic review-matrix rebind, and a new independent full review remain required before the
AR-2/AR-3 acceptance claim can be restored. AHC code, publication, activation, effects, and final
delivery remain untouched and separately gated.

### Package and fresh-Codex refresh before the final documentation freeze

- Documentation/evidence commit before package rebuild: `69c9650`.
- Rebuilt wheel before the final status wording: 164,772 bytes, SHA-256
  `46524e73cbf9159b84209a279856805651cdae1ee8a4bc459947941b609fda12`, 82 members. The
  installed exact wheel and isolated Python 3.11/3.14 runs passed all nine compatibility checks.
  Because README status wording is finalized after this evidence, a later append-only subsection
  records the final wheel digest and readback.
- The first installed-smoke invocation incorrectly supplied unsupported `--json` and exited before
  running checks; the corrected command passed 9/9. This correction is retained rather than hidden.
- Fresh `codex-cli 0.146.0`, `gpt-5.6-sol` high-effort process loaded
  `adaptive-agent-runtime:aar-operations` `0.5.0` and only used callable AAR MCP tools.
- Accepted results: RLM `succeeded/certain`, actions `evidence.query` then `model.request`, one model
  request, status projection with zero artifacts, exact model-request contract digest
  `sha256:bd06715bade9ef377400fa145484d7a330fdfc4f6cb554d43c39ed4ca62058b7`, foreign-session
  replay rejected as `OPERATION_CONFLICT` with no operation/result/artifact disclosure, and empty
  asset import `succeeded/certain` with active serving unchanged.
- Five rejected schema/budget/deadline calls were corrected before accepted results and are retained
  only in maintainer-private evidence. That summary is not included in this public repository, so
  this fresh-host row is maintainer-reported historical context rather than independently auditable
  public proof.

### Final documentation-bound wheel readback

- Final pre-review documentation commit: `fca8bdb9094b466e4735a85f06f59db9883d96bb`
  (`docs: freeze refreshed AR-2 and AR-3 host evidence`). It changes tracked documentation only;
  executable source remains bound to `c21d595`.
- `uv build --no-sources` rebuilt
  `dist/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl` from that commit. The wheel is 164,794
  bytes, has 82 members, passes ZIP integrity readback, and has SHA-256
  `1001ced234dc45a1a1a4c6e8cd46e98c7b26cde0a025dc0851fb26f6cd7cd224`.
- `uv tool install --force <exact-wheel>` succeeded, and the installed `aar-compat-smoke` passed
  all nine checks with scenario `ar23-fca8bdb-installed`.
- Fresh isolated environments installed that exact wheel and passed all nine checks on CPython
  3.11.9 (`ar23-fca8bdb-wheel-py311`) and CPython 3.14.2
  (`ar23-fca8bdb-wheel-py314`). The runs were sequential because the reference MCP child uses the
  repository's single-writer runtime database.
- The final independent review must bind the tracked commit that contains this append-only receipt,
  the executable source commit, the exact wheel digest, and the fresh-Codex summary above.

### Final escalation evidence-provenance correction

- Frozen review wave
  `06535e735642f814cbe4874b01b8339f8f20e207796e77fc3eb75a97cdc0fb29` bound tracked
  candidate `9129fb1` and 80 atomic coverage cells.
- The independent full-escalation report passed the official report validator with no errors or
  warnings, but correctly returned `BLOCKED / EVIDENCE_CLOSURE_INCOMPLETE`. Report SHA-256:
  `45351171498515b1ef9e8809efd6c7c3d25d58725e740bb8b09de34dd3a568c0`.
- No additional executable blocker was found. The blocking evidence defect was that the current
  `c21d595` compatibility row still pointed to the older `ea83402` summary and said six rejected
  corrections, while the frozen current receipt contains five.
- `HOST-COMPATIBILITY.md` now records all five rejected schema, budget, and deadline corrections.
  The supporting summary remains maintainer-private and is not part of this public repository. This
  is an evidence-wording repair; executable source, tests, schemas, fixtures, skills, and the exact
  final wheel are unchanged.
- A new review wave must rebind the corrected host document and evidence index. The current host
  receipt, six reopened host/evidence cells, and the remaining 74 unchanged atomic cells must close
  before acceptance is restored.

### AR-2/AR-3 audited local acceptance

- Evidence-only repair commit: `1967dd57621cebec784d645e6d7cdc825f667314`; its Git diff from
  reviewed candidate `9129fb1` changes only `HOST-COMPATIBILITY.md` and this append-only WAL.
- Rebound closure wave:
  `9c9e7a87472469cb30191e8debeb301cf634b7db32415819e17a6b6d51f419df`.
- Independent closure report: 6/6 reopened cells, zero findings, `PASS / BATCH_COMPLETE`, SHA-256
  `4481bbe955878568ae6470f534cefe3a3bf3c43172998004c0a31651e393ca20`; the official
  report validator returned no errors or warnings.
- Independent narrow audit: `READY_FOR_SYNTHESIS`, no new findings or unresolved challenges,
  SHA-256 `0a1d8750daa35dada6a6b1463fc372d13e500c111cb884ce7ba5e6d9396a8705`.
  Schema and intended NARROW semantics pass. The installed public audit CLI still rejects only
  because it hardcodes reciprocal topology; this known tool defect does not weaken synthesis.
- Valid final synthesis: `PASS / AUDITED_BATCH_COMPLETE`, third reviewer not required, SHA-256
  `5b5872c084f878a5f6d18b07f2229f2f59d4064a702495ee5c4f6f6f2558b60e`; the official
  synthesis validator returned no errors or warnings.
- AR-2 Portable RLM and AR-3 Canonical Adaptive Assets are therefore locally verified at
  executable `c21d595` and audited evidence candidate `1967dd5` for the declared
  AAR/reference/Codex boundary. Python 3.11–3.14, exact-wheel, and fresh-Codex evidence remains as
  recorded above.
- This post-acceptance status projection changes tracked documentation only. It does not rebuild or
  replace the exact accepted wheel; that immutable instance remains bound to its recorded
  `1001ced2...d224` digest rather than being represented as a build of the later status wording.
- Open rows remain open: Hermes v4, a fresh-host contentful asset bundle, Rust AHC consumer/native
  adapter, shared IG gates, shadow adaptation, managed activation, external effects, final
  delivery, publication, release, and reproducible-build identity.

## 2026-08-09 — Codex dependency, launcher, public setup, and interim-IPython retirement

**Objective:** inspect the actually callable AAR MCP surface in Codex App, install the runtime and
its analysis dependencies, remove PATH-sensitive launch assumptions shared with Context Canvas,
provide a short public install path, and retire only the temporary IPython plugin behavior that AAR
now replaces.

### Initial runtime evidence and completeness judgment

- Repository starting HEAD: `479b2ec8458817690482eb27b2f8c30b9da5fab2`; worktree was clean.
  The three unrelated untracked coordination references under `<local-coordination-workspace>`
  were inventoried and left untouched.
- Installed AAR plugin `0.1.0+codex.20260808192807` exposed all 28 native
  `mcp__aar__aar_*` tools in the current desktop task. Capabilities reported package `0.1.0a0`, MCP
  SDK `2.0.0`, negotiated protocol `2025-06-18`, v4 surface digest
  `sha256:25a4d4e14d62fb5bd934382b05f8cc2228b6f7d17cd3b08053161c71cda810fb`, and
  `aar-operations` `0.5.0` digest
  `sha256:59b2e32427cd4b6c84b2c17ebac540fa11dbc6bf49e56118d8b92cfa3037278a`.
- A real programmable lifecycle persisted pure-Python state, checkpointed/restored it, and closed,
  but the first pandas cell failed with `No module named 'pandas'`. The failed cell advanced the
  revision and exposed no variables, as designed; it was not counted as success.
- Project completion remains bounded: AR-0 through AR-3 are locally verified. AR-4 through AR-6,
  Hermes v4, fresh-host contentful assets, Rust AHC/native integration, shared IG gates, managed
  activation, effects, final delivery, publication, and release remain open.

### Dependency and compatibility repair

- `pyproject.toml` now declares `numpy>=2,<3` and `pandas>=2.2,<4`; `uv.lock` was regenerated. The
  wheel already declared IPython and now owns the complete default analysis environment instead of
  inheriting it accidentally.
- `aar-compat-smoke` schema advanced to `aar.compat-smoke.v3`. Its supervised IPython cell imports
  IPython, NumPy, and pandas, builds `DataFrame({'qty': [3, 7, 8]})`, verifies sum `18`/three rows,
  inspects the live namespace, and proves modules/DataFrames are explicit checkpoint exclusions.
- The first updated focused run failed because the existing ten-second worker deadline was too
  short for a cold dependency import. Raising only that cell's declared wall time to 30 seconds
  produced the accepted focused result; the failure is retained.
- Ruff passed. Dependency/profile gates passed 7/7 on each of Python 3.11, 3.12, 3.13, and 3.14.
  The pre-installer repository suite passed 113 tests in 56.46 seconds.
- An initial isolated-wheel smoke reused the default `black-box` scenario in the shared database
  and failed the cancellation distinction. Sequential reruns with unique scenario identities and
  explicit unique database paths passed all ten v3 checks on Python 3.11 and 3.14. The collision is
  retained as invalid test-isolation evidence.

### PATH-independent launchers and Context Canvas case

- Both plugin configs used bare launchers: Context Canvas used `python`; AAR used `aar-mcp`.
  A child `cmd.exe` with PATH restricted to Windows system directories reproduced both failures as
  “not recognized”; the exact Python and AAR executable paths succeeded under the same PATH.
- Codex user MCP configuration now binds AAR to
  `<user-home>\AppData\Roaming\uv\tools\adaptive-agent-runtime\Scripts\aar-mcp.exe` and
  Context Canvas to exact Python plus the exact cached server script. `codex mcp get` and
  `codex mcp list` read back those paths.
- Context Canvas canonical README and its plugin-structure regression now publish the same exact
  launcher procedure. Focused validation passed, the plugin was cache-busted/reinstalled as
  `0.4.0+codex.20260809042618`, and a fresh Codex process successfully called `canvas_list` without
  creating or mutating a Canvas.
- This fixes executable discovery, not identity semantics. The trusted current opaque ID had no
  checkpoint, so none was created automatically.

### Public installation toolset

- General users now have a two-command path: install the package with `uv tool install`, then run
  `aar-codex-setup`. The new command locates the exact tool-environment launcher, performs a
  temporary real-MCP dependency create/execute/inspect/close preflight, adds or reuses the bundled
  marketplace, installs the plugin, writes the exact user MCP override, and emits a bounded JSON
  receipt with `restart_required`.
- `docs/CODEX-INSTALL.md` is public and bundled in the wheel. It separates the approximately
  eight-second setup preflight from maintainer-only Python matrices and the full suite. Windows
  upgrades explicitly require closing App tasks already using AAR before replacing the uv tool.
- Installer/package focused gates passed 4/4 on each of Python 3.11, 3.12, 3.13, and 3.14. Ruff
  passed and the final Python 3.14 repository suite passed 116 tests in 83.26 seconds.
- Final exact wheel:
  `dist/candidate-20260809-public-setup-final/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`,
  171,614 bytes, 84 members, SHA-256
  `f1eae3501a820063a0bc925b6c70c40abe626d3f94272a82dd5fa646477ccaa5`. ZIP integrity,
  entrypoints, install guide, and dependencies read back. An isolated Python 3.14 installation ran
  the final `aar-codex-setup --preflight-only` successfully.
- The exact final wheel is installed in the existing uv tool environment. Its full setup receipt
  passed with 28 tools, IPython `9.16.1`, NumPy `2.5.1`, pandas `3.0.5`, sum `18`, three rows, and a
  closed workspace. A final fresh `codex-cli 0.146.0` process called Canvas read-only, executed and
  inspected the same dependency result through AAR, and closed successfully. Schema/stale-handle
  correction calls in that trace remain failures and were not counted as passing.

### Interim plugin retirement and remaining local action

- The temporary `bounded-ipython-workspace@personal` plugin remains installed and recoverable but
  is now disabled in Codex configuration. Existing App processes may remain alive until restart;
  no source or cache was deleted.
- Global `programmatic-tool-composition` routing now selects callable `aar-operations` programmable
  workspaces for persistent Python/NumPy/pandas state, requires capability/dependency evidence, and
  retains host authority boundaries. The skill validator passed. The optional
  `aar-ipython-codegraph` artifact-navigation skill is distinct and remains enabled.
- Updating the uv tool while old App processes held `<user-home>\.local\bin\aar-mcp.exe` returned
  exit 2 at global entrypoint replacement, although the package environment and new setup command
  were installed. The exact final wheel was then installed directly into that uv tool environment
  and verified. After the user restarts/closes those App tasks, rerun `uv tool install --force`
  against the final wheel to obtain a clean package-manager entrypoint receipt.
- No commit, push, publication, activation, external effect, or final delivery was performed.

### Restart closeout and lean verification cadence

- After the user restarted the App, `uv tool install --force` against the original final setup
  wheel completed with exit zero. The Windows launcher lock was therefore process-lifetime state,
  not a missing dependency or persistent uv failure.
- The active `completeness-and-test-synthesis` and `codegraph-first-navigation` skills already
  require the lowest falsifying altitude, evidence reuse, and bounded navigation; neither required
  the repeated matrices or artifact rebuilds observed during the earlier exploratory install.
  Project-local ritual came from two stale instructions instead: the development plan applied the
  Baton brake to all bounded implementation, and the local operator guide required full cross-repo
  reads, phase narration, WAL, and roadmap work for small changes. Those instructions now apply
  only when delegation, durable status/evidence, or cross-repo gates make them relevant.
- Added `docs/TESTING.md` with an explicit claim-driven edit/candidate cadence. Focused checks run
  during editing; any required matrix/full suite is deferred until executable bytes stabilize;
  packaged docs/profiles freeze before one final wheel; exact-wheel and fresh-host probes run once
  per artifact/configuration claim. Documentation, WAL, formatting, and receipt-only changes reuse
  unchanged executable evidence.
- A real no-op setup after the restart still reinstalled plugin/MCP configuration and returned
  `restart_required: true`. A fail-first installer slice produced 2 failures and 2 passes. The
  repair reads current plugin and MCP state, writes only missing/stale bindings, reports
  `configuration_changed`, `plugin_changed`, and `mcp_changed`, and derives `restart_required` from
  those actual changes. The repaired installer tests passed 4/4.
- Final focused verification: 11 tests passed across installer, package metadata, and generated
  host-profile coverage; Ruff passed for the affected Python files; `aar-host-assets verify` and
  `git diff --check` passed. The full 116-test suite and Python 3.11-3.14 matrix were deliberately
  not repeated because the dependency/MCP-worker/runtime contracts and their prior evidence were
  unchanged.
- Final lean-setup wheel:
  `dist/candidate-20260809-lean-setup-final/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`,
  172,232 bytes, 84 members, SHA-256
  `519b8e1afd9574551055124b4251dd9131f18beba3b6f17934960384946934f2`. ZIP integrity passed;
  exact-wheel readback found the setup entrypoint, idempotent installer source/guide, and plugin
  `0.1.0+codex.20260809134104`.
- Clean exact-wheel `uv tool install --force` passed. The first installed setup run changed only
  the plugin (`mcp_changed: false`) and requested one restart. The second installed run made no
  writes and returned all change flags false plus `restart_required: false`. The installed direct
  URL and exact AAR launcher read back correctly; Context Canvas remains enabled at its exact
  launcher, and the temporary bounded-IPython plugin remains installed but disabled.
- Because the first run installed a new plugin cachebuster, one App restart remained pending at
  that boundary. No fresh-host AAR row was claimed from the pre-restart task.

### Second-restart fresh-host closeout

- After the user completed the second App restart, `codex-cli 0.146.0` read back enabled plugin
  `0.1.0+codex.20260809134104` and the exact tool-environment AAR launcher. Installed
  `aar-codex-setup --preflight-only` passed again with 28 tools, dependency versions IPython
  `9.16.1`/NumPy `2.5.1`/pandas `3.0.5`, result `18`/three rows, and a closed temporary workspace.
- A fresh ephemeral Codex probe under `read-only` exposed no AAR public tools and made no calls. The
  same probe under `workspace-write` loaded `aar-operations` and the 28-tool v4 surface. Its first
  mutation used an obsolete nested grant/schema shape and was rejected before mutation. A corrected
  flat-context probe at runtime generation 38 created an IPython workspace, executed and inspected
  the dependency result at revision 1, and observed exactly `IPython`, `answer`, `frame`, `np`, and
  `pd`.
- The corrected probe's close call was cancelled by the child CLI's non-interactive host approval
  layer. Starting a new AAR server at runtime generation 39 found the prior workspace absent, so no
  worker or attachable workspace leaked. A final minimal closure probe with explicit
  `approval_policy=never` still supplied `mutation_context` instead of the tool's required
  `context`; it failed schema validation before mutation and was not retried.
- Exact wheel comparison found only seven changed members: bundled setup guide, Codex profile
  manifest/README, `aar.compat.assets`, `aar.compat.codex_setup`, and dist-info metadata/RECORD.
  MCP server, reference host, IPython backend, workspace runtime, and operation-skill bytes are
  unchanged. The prior accepted fresh-host close evidence and the current exact-wheel direct
  preflight close therefore remain valid for the unchanged close path. The new post-restart row
  independently proves current plugin pickup and real create/execute/inspect behavior; the
  cancelled close is recorded as a host-approval limitation rather than counted as passing.
- The maintainer cadence now records sandbox/approval policy for Codex probes and requires exact
  wheel-member comparison before lifecycle evidence reuse. No full suite, Python matrix, or wheel
  rebuild was triggered by this evidence-only closeout. No commit, push, publication, activation,
  external effect, or final delivery was performed.

### Reference-context UX closure

- A first schema-guidance repair added outer-argument and flat-field descriptions while preserving
  strict context validation. Its fresh Codex probe stopped before mutation: the agent now used the
  correct outer `context` and no nested grant, but omitted principal/session/request identity and
  added obsolete `expected_record_revision`. This failure showed that documentation alone did not
  make the reference-host authority inputs obtainable enough for a general caller.
- MCP surface `aar.mcp-tools.v5` therefore adds read-only `aar_reference_context`. Given the exact
  reference capability, a payload-bound context key, and wall-time budget, it returns a complete
  flat mutation `context` plus `read_context`. Mutation tools still require every explicit
  generation/digest/identity/deadline/grant/budget/request/idempotency field and still reject extra
  or nested inputs. The helper does not extend production-host authority.
- Final contract: 29 tools, tool-surface digest
  `sha256:1ba64cb622ee9c75f3302c9d90cdabcb08804c56bd0d9b9138080b8772911a2c`;
  `aar-operations` `0.6.0`, digest
  `sha256:4e3b8387f0d790f6eaa81d03ac881ce09c727732eb8ec69da618c99380f9e262`;
  Codex plugin `0.1.0+codex.20260809064045`.
- Focused verification passed 18 tests across generated MCP/host assets, the copy-ready helper,
  skill-guided scalar lifecycle, dependency smoke, installer, and package metadata. Ruff, skill
  validation, and plugin validation passed. The unchanged full-suite and four-interpreter runtime
  evidence was not rerun.
- Final wheel:
  `dist/candidate-20260809-context-helper-final2/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`,
  196,291 bytes, 85 members, SHA-256
  `36eee122eb3befdb18db944e6d68eb077e0977c5942eb7ad3c879cec1e211e7d`. Exact readback matched
  canonical v4/v5 schemas, skill, metadata, and plugin manifest. `uv tool install --force` passed.
  Relative to the fresh-probed predecessor, only the restored archived v4 schema and wheel
  `RECORD` changed; active v5/runtime/skill/plugin bytes remained identical, so the fresh v5 proof
  was reused without another agent run.
- Full installed `aar-codex-setup` on the active-byte-equivalent predecessor passed with 29 tools,
  IPython `9.16.1`, NumPy `2.5.1`, pandas `3.0.5`, calculation `18`/three rows, and
  `workspace_closed: true`; it kept the exact launcher and reported `restart_required: true`
  because the final plugin cachebuster changed. After final2 installation,
  `aar-codex-setup --preflight-only` reproduced the same v5/29-tool dependency and close result.
- A fresh ephemeral `codex-cli 0.146.0` child (`gpt-5.6-sol`, max effort, `workspace-write`,
  approval `never`) received no context-field or helper hint. Following `$aar-operations`, it called
  capabilities, reference context, create, reference context, execute, and inspect; all completed,
  revision 1 contained `answer: 18`, and no shell or file edit occurred. This is the current v5
  fresh-host proof; the desktop App still needs one restart to load the final cachebuster.
- No commit, push, publication, activation, provider effect, or final delivery was performed.

### AAR-assisted analysis routing

- `aar-operations` `0.6.1` now tells agents to consider native AAR MCP early for tasks that already
  need tool use or analysis. Software planning, development, testing, and troubleshooting are
  explicitly included, while a simpler host-native path remains preferred when AAR adds no
  material capability. The skill still does not grant authority, activation, effects, or sandbox
  semantics.
- Canonical, Codex profile, installed plugin cache, and exact-wheel skill bytes all match
  `sha256:f4c47ebee6e114de990d2be6339698584809c10d1ab1b10bf799f69c1bc5c045`.
  Codex plugin cachebuster is `0.1.0+codex.20260809080423`.
- Fail-first coverage produced four expected contract failures before the source update. The same
  four checks then passed, and the complete affected MCP-asset, host-profile, and compatibility
  smoke shard passed 10/10. Skill and plugin validators passed.
- Exact wheel
  `dist/candidate-20260809-aar-routing/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`
  has SHA-256 `163fcb42ab1edac9d35c3bb3a5711f8b87a4f1e79ed015226c74bffb04347ce4`.
  Its isolated black-box stdio smoke passed all ten checks and reported skill `0.6.1`, the exact
  new skill digest, 29 tools, and unchanged v5 tool-surface digest.
- Exact-wheel `uv tool install --force`, plugin installation, installed-source readback, and
  `aar-codex-setup --preflight-only` passed. The current desktop task still exposes zero native
  `mcp__aar__*` tools, so this update does not close the existing Codex App discovery gap and no new
  desktop compatibility row is claimed. A fresh App task is required to load the new plugin
  cachebuster; a computer reboot is not required.
- No commit, AAR push, activation, provider effect, or final delivery was performed.

### Desktop plugin-only MCP authority closeout

- The accumulated v5/reference-context/Codex setup/routing candidate was committed as `9a97a14`
  after 120/120 repository tests, Ruff, generated host-profile verification, plugin validation, and
  cached-diff checks passed.
- An existing desktop task was restarted; it and a bounded readonly subagent both
  exposed zero native AAR and Context Canvas tools while each server had two registration
  authorities: its installed plugin and a same-name global MCP transport. The AAR global command
  was the exact uv-tool launcher; the Context Canvas global command pinned an older plugin cache.
- Plugin-resolved `<user-home>\.local\bin\aar-mcp.exe` passed the real-worker preflight with 29
  tools, v5, dependency result 18/three rows, and a closed workspace. Its SHA-256 matched the exact
  uv-tool launcher. The two global registrations were removed, leaving the enabled plugins and
  hooks as the sole transport owners. A computer restart was not used.
- A fresh desktop task then exposed 29 native AAR tools and 12
  native Context Canvas tools. Native `aar_capabilities` returned `aar-mcp`, v5, runtime generation
  1, and `aar-operations` `0.6.1` with digest
  `sha256:f4c47ebee6e114de990d2be6339698584809c10d1ab1b10bf799f69c1bc5c045`.
  The hook supplied an opaque Canvas identity; no Canvas or AAR mutation was made.
- Installer schema `aar.codex-setup.v2` now keeps the plugin as the sole AAR MCP authority, removes
  only a legacy global entry naming the same preflighted launcher, and rejects a conflicting global
  owner without deleting it. A second observed regression showed that `codex mcp get` also lists a
  plugin-provided server, so the final implementation reads the actual `[mcp_servers.aar]` config
  table instead. Two fail-first passes each produced four expected failures; each repaired focused
  slice passed 6/6. The final full suite passed 121/121 in 85.42 seconds; Ruff, host-profile byte
  verification, and plugin validation passed.
- Exact wheel
  `dist/candidate-20260809-plugin-authority-final2/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`
  is 198,995 bytes with SHA-256
  `cd8d9d78f12d05645ae24f9ba7f203e1446a07fe90c78eccac5225a196b7aa27`. Installed setup-v2 preflight
  passed with 29 tools, v5, dependency result 18/three rows, and a closed workspace. Installed
  configuration-only setup was a true no-op with plugin authority, no legacy global entry, and
  `restart_required: false`. Final Codex readback lists AAR and Context Canvas once each through
  plugin-owned stdio commands; the AAR plugin is `0.1.0+codex.20260809083709` and Context Canvas is
  `0.4.0+codex.20260809075929`.
- Terminating the first readonly verification task's MCP child produced `Transport closed` on a
  same-task native retry; Codex App did not transparently rebuild that stdio client. A second fresh
  desktop task natively restarted the updated installed runtime and
  reproduced 29 AAR tools, the v5 capability result, 12 Canvas tools, and hook identity. Current
  compatibility therefore proves fresh-task restart recovery, not same-task transparent reconnect.
  The final wheel differs from that native-probed wheel only in setup/install documentation,
  `aar/compat/codex_setup.py`, and wheel `RECORD`; AAR MCP server and skill bytes are unchanged, so
  the native proof remains fresh for those contracts.
- Current desktop discovery is closed under plugin-only ownership. Provider credentials, external
  effects, activation, final delivery, publication, Hermes v5, and an always-on AAR service remain
  separate claims.

---

## 2026-08-10 — Durable long-operation continuity workstream proposed

**Objective:** evaluate whether AAR can add long-task and disconnect/restart continuity without
binding the core runtime to MCP connection lifetime or to Hermes, Codex, or AHC private authority.

**State before:** AR-0 through AR-3 were locally verified; `start_only` persisted accepted scalar/RLM
intent but did not enqueue or dispatch it; non-`start_only` execution remained in the MCP request
path; programmable worker state/receipts remained in memory; current Codex evidence proved only
fresh-task stdio-server restart, not same-task transparent reconnect.

**State after:** a new `AR-LT` workstream is **proposed / planning only**. Verified executable,
phase, package, tool-surface, compatibility, AHC/IG, activation, effect, delivery, and release states
are unchanged. The first decision is whether to authorize the bounded `AR-LT0` contract and
failure-model spike.

Repository state observed before this documentation batch:

- AAR branch `main`, HEAD `de3e19697c124b93dcbfb516df3ad79f4198ed8a`, clean;
- executable acceptance remains `c21d595`, audited evidence remains `1967dd5`;
- AHC coordination HEAD was `3c10b02aa8ac89e35b0bc4c9ced9dc8b40a30efb` with pre-existing
  dirty coordination artifacts; none were modified by this task;
- the HC-R0 implementation state remains G2/G3 HOLD and was used only as design input.

Source-backed findings:

- `OperationRegistry` already persists intent, idempotency, runtime generation, revisions, events,
  result/failure, certainty, and reconciliation state in SQLite/WAL. Restart rebinds accepted intent
  and marks interrupted running work indeterminate.
- `aar_workspace_execute` and `aar_rlm_execute` treat `start_only=true` as persisted acceptance only;
  no durable queue claimant, dispatcher, lease, heartbeat, or startup pickup loop exists.
- `RlmStore` persists jobs and steps. `BrokerJournal` commits `started` before invocation and an
  authoritative response after success; a started-without-receipt replay is indeterminate rather
  than repeated.
- the supervised IPython backend supports explicit JSON-subset checkpoint/restore, health,
  interrupt, reconciliation, generation, and revision fencing, but worker state and receipts remain
  process memory. Arbitrary live Python/native state is not portable.
- AHC design/source contributes generic lease, generation-fencing, successor-binding, progress
  cursor, and recovery-decision patterns. AAR must not import AHC source/channel/final-delivery or
  memory-owner authority into its core schema.

Planning decision:

- separate logical `operation_id` from immutable execution attempts, runtime/worker generations,
  leases, event cursors, checkpoints, and recovery policy;
- keep MCP as one adapter over transport-neutral submit/status/events/cancel/reconcile/recover
  contracts;
- make disconnect independent of cancellation;
- require a host-issued continuity authority for a new connection/session rather than weakening the
  current exact principal/session binding or treating an operation ID as authorization;
- resume replay-safe work through a fenced successor attempt; resume RLM only after the last certain
  step and programmable work only from a compatible portable checkpoint;
- park ambiguous broker/effect calls for reconciliation; do not claim generic exactly-once effects,
  arbitrary process resurrection, distributed workflow, or sandbox semantics.

Planned work packages:

- `AR-LT0` (S, estimate 3–5 engineering days): versioned attempt/lease/event-page/checkpoint-binding/
  continuity-authority/recovery fixtures and failure state machine;
- `AR-LT1` (M, estimate 2–4 engineering weeks): durable dispatcher, real enqueue, restart pickup,
  paged events, explicit cancel, and replay-safe vertical slices;
- `AR-LT2` (M/L, additional 3–6 weeks): ephemeral MCP frontend over a durable supervisor and
  authorized cross-connection attach;
- `AR-LT3` (M/L, additional 4–8 weeks): RLM step-boundary successor and portable workspace
  checkpoint recovery;
- distributed cross-host workflow remains a separate deferred project.

Planning-only capability estimates, not measured reliability or SLA:

- client disconnect with live durable runtime: 90–98%;
- ephemeral frontend restart with live supervisor: 85–95%;
- accepted/no-effect work after supervisor restart: 90–98%;
- RLM at a certain step boundary: 75–90%;
- programmable workspace at a portable checkpoint boundary: 60–80%;
- arbitrary in-flight Python/native state: 20–45%;
- generic exactly-once external effects: not claimable.

Artifacts:

- `docs/LONG-TASK-CONTINUITY-PLAN.md` — authoritative AAR-local proposal, contracts, phases, and
  verification matrix;
- `DEVELOPMENT-PLAN.md` — adds the proposed `AR-LT` lane and makes `AR-LT0` an explicit decision in
  the immediate backlog without reopening prior acceptance;
- `README.md` — points readers to the proposal and records that phase/compatibility status is
  unchanged;
- ignored `roadmap.local.html` — adds the proposed lane, current observed repository bindings, and
  unverified-boundary warning;
- external Traditional Chinese assessment:
  `<local-research-workspace>/research/aar/long-task-continuity-opportunity-assessment.md`.

Executed verification for this documentation/planning batch:

- ten focused source assertions passed for current `start_only`, registry restart, RLM store,
  broker journal, in-memory IPython state, and checkpoint-format facts;
- three modified/added Markdown surfaces had zero missing relative links;
- `roadmap.local.html` parsed with zero duplicate IDs, retained the AR-LT/commit markers, and inline
  JavaScript syntax passed;
- the updated executive HTML parsed with zero duplicate IDs and zero missing relative links; its
  inline JavaScript syntax and new artifact checks passed;
- `git diff --check` passed.

No runtime source, schema, generated fixture, package, installed MCP, AHC source/deployment, live
`.agent-harness`, provider, workspace, external effect, activation, final delivery, commit, push, or
release was changed. Runtime tests and interpreter matrices were intentionally not rerun because the
executable bytes and existing accepted claims are unchanged.

Follow-up browser QA:

- the first executive-HTML pass exposed that the four new AR-LT phase items used a nonexistent
  `identity-grid` class and therefore rendered as unboxed vertical text;
- the markup was corrected to the existing responsive `compare-grid` plus `card identity-card`
  primitives; the refreshed desktop render shows an aligned 2×2 card grid with no overlap or
  horizontal clipping;
- the executive report and ignored local roadmap both rendered successfully in the browser after the
  correction; the AR-LT table/cards, final recommendation, six-stage roadmap, five immediate items,
  and footer were fully visible, and both browser consoles reported zero JavaScript errors;
- temporary localhost QA servers were terminated after verification; ports 8765 and 8766 read back
  closed.

---

## 2026-08-10 — AR-LT0 through AR-LT3 phase-level handoffs and AHC coordination projection

**Objective:** turn the previously approved high-level AR-LT opportunity assessment into
implementation-ready phase plans, synchronize the separate AHC coordination workspace without
changing either runtime contract, and rebuild the ignored local roadmap as a high-level overview and
progress tracker.

**State before:** `docs/LONG-TASK-CONTINUITY-PLAN.md` defined architecture, phase intent and gates,
but AR-LT0 through AR-LT3 did not each have a source-impact map, ordered work packages, scenario
matrix, exit evidence and rollback handoff. The AHC coordination dashboard still described HC-R0 as
planned even though its append-only WAL recorded G0/G1 and bounded compiled recovery progress.

**State after:** all four phase handoffs are prepared and remain **planned**. AR-LT0 is the only phase
ready for an approval decision; AR-LT1, AR-LT2 and AR-LT3 remain dependency-blocked. No schema,
fixture, dispatcher, supervisor, worker, package, capability, test-evidence or shared-gate claim has
advanced.

New standalone AAR plans:

- `docs/AR-LT0-CONTRACT-AND-FAILURE-MODEL-PLAN.md` — 280 lines covering additive models, SQLite
  migration registry, fixtures, authority, rollback and AR-LT1 handoff;
- `docs/AR-LT1-DURABLE-ASYNC-DISPATCH-PLAN.md` — 283 lines covering durable RLM dispatch, atomic
  claim/lease, reconnect, cancellation, scenarios and feature rollback;
- `docs/AR-LT2-DURABLE-SUPERVISOR-PLAN.md` — 289 lines covering frontend/supervisor separation,
  authenticated private IPC, process identity, restart recovery, scenarios and rollback;
- `docs/AR-LT3-CHECKPOINT-AND-EFFECT-RECOVERY-PLAN.md` — 276 lines covering RLM step successors,
  workspace restore, broker/effect reconciliation, scenario evidence and rollback.

Updated standalone AAR projections:

- `docs/LONG-TASK-CONTINUITY-PLAN.md` now indexes the phase handoffs and records that cross-project
  planning is non-normative to AAR core;
- `DEVELOPMENT-PLAN.md` records exact phase entry/exit states and the bounded AR-LT0 approval gate;
- `README.md` points to all detailed phase plans and separates planning readiness from executable
  maturity;
- ignored `roadmap.local.html` was rebuilt with a target architecture, 4/4 planning versus 0/4
  implementation/T3 tracking, phase dependencies, detailed phase cards, AHC mapping, claim
  boundaries and the next AR-LT0-only decision.

AHC coordination was updated in the separate `AHC-by-AAR` planning workspace:

- `AAR-LONG-TASK-CONTINUITY-COORDINATION-PLAN.md` defines AAR/AHC identity, lease, event, recovery,
  checkpoint and terminal-result mappings without importing AHC types into AAR;
- the integrated plan, root dashboard and project mirrors record that AR-LT adds no IG gate, does not
  block HC-R0, and may later contribute optional continuity rows to HC-0/IG-0, HC-1/IG-1 and
  HC-2/IG-2;
- HC-R0 is projected as `in-progress` from existing executed evidence: G0/G1 and bounded compiled
  accepted-work restart slices pass, while full G3/G4/G5 and live gates remain HOLD.

Observed boundaries:

- standalone AAR remained on branch `main`, source HEAD
  `de3e19697c124b93dcbfb516df3ad79f4198ed8a`;
- AHC implementation worktree was read-only for this planning update at branch
  `codex/hc-r0a-contracts`, HEAD `2cb2356b66f53a2f22525db6203e0cf3d55fbdc7`, with its existing
  dirty HC-R0 source preserved;
- coordination workspace began at `3c10b02fbb05f1232f664fab354dc44ee7bac5dd` with unrelated dirty
  and untracked HC-R0/reference artifacts; those artifacts were preserved and not rewritten.

Executed verification:

- four phase plans contain objective, source-impact, ordered work, fault/scenario, exit and rollback
  sections; line counts are 280/283/289/276;
- 17 task-facing Markdown files had zero missing relative links;
- public standalone AAR docs gained zero Windows-local paths and zero sibling-repository links;
- `roadmap.local.html` parsed with six unique section IDs, four phase cards, nine valid local links,
  no missing link target and no horizontal overflow at a 1280-pixel viewport;
- desktop browser visual QA found the hero, overview, tracker, 2×2 phase cards, AHC table, claim
  boundaries, next decision and footer complete with no overlap or clipping;
- browser console read back zero messages and zero JavaScript errors;
- credential-shaped scan across 17 changed planning surfaces returned zero hits;
- `git diff --check` passed in both the standalone AAR repository and the AHC coordination workspace;
- the temporary QA server was stopped after verification and port 8766 was confirmed closed.

No runtime source, generated fixture, package, installed MCP, AHC source or live `.agent-harness`,
provider, external effect, activation, delivery, commit, push, release or shared-gate state was
changed. Runtime test suites were not rerun because this batch changes planning and projections only.

---

## 2026-08-11 — AR-LT0/AR-LT1 implementation, installed v6, and local candidate freeze

**Objective:** implement the standalone continuity contract and durable asynchronous RLM slice,
prove it across real process loss and an installed Hermes MCP boundary, and freeze the exact local
candidate before AR-LT2 supervisor work begins.

**Source candidate:** local commit
`04238e83dd5702ee92f49fad2da425e06fd8dc57` (`feat: add durable operation continuity and RLM
dispatch`). No push or published release was performed.

Implemented contract and runtime:

- additive `aar.operation-continuity.v1` attempt, lease, event-page, control, checkpoint-binding,
  recovery-policy, recovery-decision, and snapshot models with canonical valid/invalid fixtures;
- additive SQLite registry migration with durable logical-operation input, immutable attempts,
  generation/lease fencing, bounded event pages, broker journals, cancellation intent, and exact
  terminal projection;
- a durable dispatcher for `rlm.execute` that separates submission from execution, recovers safe
  successor attempts after process restart, reuses authoritative broker receipts, and parks
  unresolved started-without-receipt calls as indeterminate;
- public status/events/cancel/reconcile tools in `aar.mcp-tools.v6`, yielding 30 public MCP tools;
- a database-scoped OS ownership lock acquired before runtime generation allocation; duplicate live
  owners fail closed without stealing the generation;
- an idle-dispatcher regression after finding that two workers' unconditional condition notifications
  formed a ping-pong busy loop.

Executed evidence:

- focused continuity, durable-RLM, dispatcher, process-recovery, runtime-ownership and affected
  regression shards passed;
- all IPython/setup startup regressions passed after the shared load source was identified rather
  than increasing timeouts;
- the final repository suite passed 163 tests in 178.40 seconds;
- Ruff, contract verification, MCP assets, and host-profile assets all passed;
- an isolated wheel environment imported AAR from its own site-packages and passed compatibility,
  package, 12 process-recovery/ownership tests, and all 30-tool discovery checks;
- T3 process probes proved accepted-before-claim restart, claimed successor attempt, old-generation
  late-write rejection, durable cancellation, receipt reuse, indeterminate unresolved broker state,
  strict event sequencing, and duplicate-owner rejection;
- the live installed Hermes generation completed a durable operation and returned its exact terminal
  RLM trace; the second-owner probe left the runtime generation unchanged;
- measured live idle CPU changed from approximately 86% during the notification ping-pong to about
  0.5% after the fix.

Package and host binding:

- final local wheel SHA-256:
  `f3748ddfc444478407a9258018f711a363210cc7d6a4266070718c3ed2a507b5`;
- MCP surface `aar.mcp-tools.v6`, digest
  `sha256:e3d319b447ebbdf8b055ef8f710e2889184bc425fc88078caaf5de0a205081e4`;
- schema bundle
  `sha256:e69ff34ab4b74b514f69919ec5244fb6d4d994dce16af3e47965348b0b3a892d`;
- fixture set
  `sha256:d9fe836b884fcae09fed9377042664f3dc9df3f5bde6f8dc78f424df389c1820`;
- operation skill `0.7.0`, digest
  `sha256:1dbf36ff6be3651d95f777e008bcd8ad1341df1dcaecc00994414b4c4fb9303a`;
- the final wheel was installed in the configured Hermes AAR tool environment and a fresh
  `hermes mcp test aar` process connected in 3175 ms and discovered 30 tools.

The prior v5 environment, manifest, and pre-v6 database copy remain retained as a local rollback
point. Replacing the MCP child intentionally closed the already-open transport for this long-lived
Discord session; fresh processes use the final wheel, while existing sessions require MCP reload or
gateway restart. A single controlled gateway restart is deferred until the LT2 final candidate to
avoid two avoidable host-wide interruptions.

Boundaries remain explicit: AR-LT1 is still one combined frontend/runtime process. It does not prove
the AR-LT2 durable supervisor, arbitrary IPython/native-state resurrection, provider credentials,
external effect execution, activation, final delivery, AHC admission, live `.agent-harness` changes,
or an operating-system service installation.

---

## 2026-08-11 — AR-LT2 durable supervisor installed and verified

**Objective:** separate durable operation ownership from ephemeral MCP frontends, prove exact
process/worker identity and restart recovery, and enable the exact candidate for Hermes without
silently installing service policy from the package.

**State before:** AR-LT1 was installed as one combined MCP frontend/runtime owner. Replacing the MCP
child closed the durable runtime and an existing host transport.

**State after:** AR-LT2 is verified at the standalone reference-host boundary and installed for
Hermes. AR-LT3 checkpoint/effect-aware continuation is the next standalone phase; AHC admission,
activation, delivery, and shared gates remain unchanged.

Source and package binding:

- source commit `38f338255f38b266f0cbe4d87db6f659274d80c6` (`feat: add durable supervisor
  and ephemeral MCP frontend`);
- package `adaptive-agent-runtime==0.2.0a0`;
- exact local wheel SHA-256
  `e867c69c82139d567303591a8c94a204d9b779366e6e22927ad184ae6a6ad624`;
- MCP surface `aar.mcp-tools.v7`, 30 tools, digest
  `sha256:370d8a3177e80e94523c07537fc6b3107beacd52956add61b0e31b9d32bf2555`;
- private supervisor protocol digest
  `sha256:2ec0e07c390041517632aee6d2962bec55cd974fd31c2a99284ff5e45a2196c8`;
- schema bundle
  `sha256:8f8e456e3af25d66063469e3b4722ccf7768b0b1b2d0c37ca897002c5385ef22`;
- operation skill `0.8.0`, digest
  `sha256:b48d014ca89809a1d710a5208036e6df6b3f348a9f86c42586989aaff60fb9c9`.

Implementation:

- added an explicit foreground `aar-supervisor` that exclusively owns the continuity database,
  runtime/dispatcher generation, durable dispatcher, programmable backend, and worker manager;
- changed default `aar-mcp` composition to an ephemeral authenticated private client while retaining
  an explicit embedded compatibility/debug mode;
- added owner-only Unix-socket transport and a declared credentialed loopback-TCP Windows fallback,
  bounded digest-bound private frames, exact supervisor discovery, stale-generation/deadline checks,
  and credential-safe logging surfaces;
- added Linux boot-ID plus `/proc` start ticks and Windows `GetProcessTimes` identity, durable
  supervisor runs and worker bindings, exact-generation heartbeats/terminal receipts, orphan fencing,
  and quarantine instead of PID-only termination;
- added additive SQLite schema v3 and preserved v1-to-v3 bytes/recovery compatibility.

Executed verification:

- the LT2 affected suite passed 99 tests with one Windows platform-guarded identity test skipped;
- the repository run completed with 184 passes, one platform skip, and one stale generated contract
  asset; regenerating contract/MCP/host assets removed the only failure and the exact affected 39-test
  shard passed;
- three real-subprocess integration scenarios passed: replacement/concurrent frontends, hard
  supervisor loss with predecessor/orphan reconciliation, and durable RLM completion across frontend
  exit;
- an isolated Linux exact-wheel environment attached through the owner-only Unix socket and read a
  durable result through a fresh frontend;
- a native Windows 3.13 exact-wheel probe verified `GetProcessTimes` identity, credentialed loopback
  TCP, bad-credential rejection, package `0.2.0a0`, skill `0.8.0`, and all 30 v7 tools;
- a pre-cutover online SQLite backup and complete prior LT1 uv-tool environment were read back with
  the LT1 implementation; the database hash remained unchanged, and the older rollback was retained;
- an operator-selected least-privilege Windows Scheduled Task now keeps the WSL supervisor in the
  foreground. The AAR package does not create that task automatically;
- fresh `hermes mcp test aar` and the current native Hermes session both attached to runtime and
  dispatcher generation 10; an installed durable RLM submission reached succeeded and was read by a
  separate fresh frontend;
- live readback confirmed distinct supervisor/frontend processes, private directory mode `0700`,
  discovery/credential/socket modes `0600`, registry schema v3, no nonterminal operation, and an
  approximately 1% two-second idle CPU sample.

Boundaries:

- the Windows declaration is credentialed loopback TCP, not named-pipe ACL support;
- remote HTTP, arbitrary process-memory resurrection, checkpoint/effect-aware continuation, generic
  exactly-once effects, provider credentials, activation, final delivery, AHC admission, push, and
  publication remain outside this acceptance;
- exact rollback locations and runtime/process identifiers remain operator-local evidence and are not
  tracked in public project files.

---

## 2026-08-11 — AR-LT3 policy-bound RLM successor candidate verified before cutover

**Objective:** freeze the first bounded AR-LT3 continuation slice so a durable `rlm.execute`
operation can create a fenced successor only from an exact, receipt-backed step boundary, without
refreshing budget, deadline, cancellation, generation, lease, input, policy, or environment authority.

**State before:** AR-LT2 package `0.2.0a0` and registry schema v3 were installed behind the durable
supervisor. Interrupted RLM attempts could use the LT2 recovery classifier, but there was no frozen
operation-kind policy or exact policy/boundary digest pair authorizing a new successor attempt.

**Verified source/package candidate:**

- source commit `9b7a1d9c8f8aa64fbd43a42dbfd6a6cc8b25d23a`
  (`feat: add policy-bound RLM recovery successors`);
- package `adaptive-agent-runtime==0.3.0a0`;
- exact wheel `adaptive_agent_runtime-0.3.0a0-py3-none-any.whl`, SHA-256
  `cce45b4f8ac3af83beb43812aad9f8798ad8abd9d43dcf4f8dab399305a507f8`;
- additive registry schema v4, contract schema bundle
  `sha256:de2515a9991827795a48f67f8e3dc2ea2a037c8841a166c0d53a6fc66408303b`;
- MCP surface `aar.mcp-tools.v7`, 30 tools, digest
  `sha256:c3038e6ef49c147560db35c49d02b84f564c9494985b84985e04b931eb5bb090`;
- operation skill `0.8.0`, digest
  `sha256:b48d014ca89809a1d710a5208036e6df6b3f348a9f86c42586989aaff60fb9c9`.

Implemented bounded slice:

- freeze a versioned `rlm.step-boundary.v1` recovery policy before durable dispatch;
- bind a successor boundary to the exact operation, predecessor attempt, runtime/dispatcher
  generations, lease epoch, input/environment/policy digests, committed steps, broker traces and
  receipts, cumulative usage, original deadline, cancellation state, and successor count;
- derive the next RLM action from persisted steps rather than restarting the predecessor attempt;
- reuse an authoritative broker receipt without requiring fresh model/effect budget, while preserving
  all cumulative usage;
- persist unresolved started-without-receipt work as `reconcile_effect` and never replay it blindly;
- atomically reject stale lease/generation races without leaving an orphan decision, boundary, or
  successor;
- quarantine malformed policy/payload/journal evidence per operation so one bad candidate cannot stop
  healthy durable work.

Executed verification:

- repository suite: 199 passed, one Windows platform-guarded test skipped, in 335.32 seconds;
- changed-file Ruff/format, contract generation/verification, MCP assets, host-profile assets, and
  `git diff --check` passed;
- a clean Linux Python 3.11 exact-wheel supervisor/frontend topology passed all ten compatibility
  checks, discovered all 30 tools, and opened schema versions 1 through 4;
- a native Windows Python 3.13 exact-wheel probe passed process-start identity, credentialed loopback
  TCP, bad-credential rejection, fresh frontend attachment, capability readback, and all 30 tools;
- a WAL-consistent online backup of the installed LT2 database migrated from v1-v3 to v1-v4 under the
  exact LT3 wheel; the LT2 reader rejected the migrated copy with `UnsupportedRegistrySchema`;
- a read-only check confirmed the authoritative installed database remained v1-v3 with `quick_check`
  equal to `ok` after rehearsal.

**Pre-cutover boundary:** this entry verifies source, exact package, isolated host topology, and a copied
database migration. It does not claim that `0.3.0a0` is installed or that the serving supervisor has
migrated schema v4. The live runtime remains LT2 until a separately authorized cutover freezes a full
LT2 rollback environment and untouched database copy, installs this exact wheel, restarts the selected
supervisor, and passes fresh installed/session/single-owner/durable-operation readback.

The verified slice does not yet implement arbitrary IPython process resurrection, broad portable
workspace restore, generic external-effect exactly-once execution, activation, final delivery, or AHC
managed-host admission.

### Evidence correction before installed closeout

The MCP tool-surface and canonical contract-bundle digests in the pre-cutover candidate list above were
copied from earlier generated output and are superseded. Fresh checked-in asset readback, the installed
exact wheel, a fresh Hermes frontend, and the current native MCP session agree on the authoritative
`aar.mcp-tools.v7` digest:

`sha256:c3032f942269c8705e6c8e918a6ca934fb392441264194adc0db2d36cc8133c0`

The authoritative canonical contract-bundle digest is:

`sha256:de251fb18ce631a227d58857e3392418a80ae534fae57d2d3ab125eeb8b18171`

This correction changes evidence text only. Package version `0.3.0a0`, exact wheel SHA-256
`cce45b4f8ac3af83beb43812aad9f8798ad8abd9d43dcf4f8dab399305a507f8`, schema v4, tool count,
and runtime behavior remain unchanged.

---

## 2026-08-11 — AR-LT3 bounded RLM successor installed and enabled

**Objective:** replace the verified AR-LT2 installed image with the exact AR-LT3 candidate while
preserving an untouched downgrade point, one runtime owner, durable operation state, and the explicit
boundary between this RLM slice and the still-open workspace/effect packages.

**Installed result:** exact `adaptive-agent-runtime==0.3.0a0` is active behind the existing
host-selected Windows Scheduled Task and WSL durable supervisor. Runtime and dispatcher generation are
12, the authoritative registry contains schema versions 1 through 4, and the current Hermes MCP session
is attached to the LT3 supervisor.

Pre-cutover evidence:

- installed LT2 `0.2.0a0`, generation 11, exact native process identity, executable path, task action,
  schema v3, database integrity, terminal-only operation state, and completed dispatch state were read
  back before mutation;
- the exact predecessor process was stopped only after PID plus boot/start identity matched; discovery
  and the database ownership lock were then absent;
- a complete LT2 uv-tool environment, exact LT2 wheel, scheduled-task XML, safe discovery evidence, and
  untouched v3 database were retained together in operator-local rollback storage;
- the LT2 reader opened a separate readback copy with schema v3 and `quick_check=ok`; the canonical
  rollback database SHA-256 remained unchanged;
- the retained set contains exactly three usable generations: the older v5 point, pre-LT2 LT1 point,
  and pre-LT3 LT2 point.

Cutover and installed verification:

- the candidate wheel hash was rechecked before `uv tool install --force`; installed package readback
  returned `0.3.0a0` and all eight expected console entry points;
- the selected task restarted the installed `aar-supervisor`, published fresh exact process identity,
  and advanced runtime/dispatcher generation from 11 to 12;
- live schema migration produced versions `(1, 2, 3, 4)` with `quick_check=ok` and preserved all prior
  terminal operation rows;
- fresh `hermes mcp test aar` connected and discovered 30 tools; the current native MCP session read
  package `0.3.0a0`, tool-surface digest
  `sha256:c3032f942269c8705e6c8e918a6ca934fb392441264194adc0db2d36cc8133c0`, operation skill `0.8.0`,
  generation 12, and attached-supervisor mode;
- an intentionally incomplete canary grant set was denied before operation creation; a new correctly
  authorized bounded RLM canary was accepted and completed with one model request, one durable receipt,
  and certain terminal state;
- the v4 recovery-policy table contained the canary's admission-time policy binding;
- a duplicate supervisor candidate exited nonzero because discovery still named the exact live
  process; generation stayed 12 and an exact argv scan found one supervisor;
- a two-second idle sample was approximately 0.5% CPU; the task remained running, private supervisor
  files retained owner-only modes, no active/indeterminate operation remained, and the pre-LT3 database
  hash stayed unchanged.

**Acceptance boundary:** this installed result closes the first policy-bound RLM successor slice only.
Portable workspace checkpoint selection and new-generation restore, broad broker/effect reconciliation,
generic exactly-once effects, arbitrary running-cell resurrection, provider credentials, activation,
final delivery, and managed-host AHC admission remain outside the claim.
