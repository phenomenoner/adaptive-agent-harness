# Host Compatibility Plan

**Status:** AR-LT2 durable supervisor/frontend separation verified in the reference host and
installed Hermes AAR surface; Codex AR-2/AR-3 rows remain verified; AHC remains a target
**Updated:** 2026-08-11

## Compatibility record

Every executed row records:

- AAR commit and package version;
- AAR envelope/runtime/workspace schema versions;
- fixture and capability digests;
- `aar-operations` skill version and digest;
- host/client name and exact version;
- operating system and process boundary;
- MCP protocol era/version and transport, if used;
- enabled tools and broker capabilities;
- config digest without secrets;
- exact black-box scenarios and results;
- evidence that the bundled skill and advertised MCP surface agreed during the executed workflow;
- unsupported behavior and evidence gaps.

An installed config/skill, visible server, successful startup, or green health check is only setup evidence. A verified row requires an actual skill-guided operation and exact result readback from a fresh host process.

## Target matrix

| Host | Status | Target adapter | First scope | Required proof before claiming support |
|---|---|---|---|---|
| Reference host | AR-LT2 verified at `38f338255f38b266f0cbe4d87db6f659274d80c6`; prior AR-2/AR-3 and AR-LT1 rows retained | direct SDK + ephemeral MCP stdio frontend over private supervisor IPC | durable brokered RLM, reconnectable events/cancel/reconcile, programmable EXECUTE, canonical assets | full repository/affected suites, frontend and supervisor process loss, exact-wheel Linux/Windows probes, package evidence, and prior rows |
| Codex local clients (CLI-tested) | `c21d595` refresh passed and is included in the audited local acceptance | MCP stdio + bundled `aar-operations` skill | brokered RLM, zero-write status, foreign-session replay denial, plus empty canonical-asset bundle lifecycle | fresh `codex-cli` process, exact skill/tool/protocol readback, RLM result/trace, zero disclosed replay fields, and asset export/import result; five failed correction calls retained |
| Hermes Agent | AR-LT2 AAR v7 installed and current/fresh-process verified at `38f338255f38b266f0cbe4d87db6f659274d80c6`; prior AR-1/AR-LT1 rows retained | ephemeral MCP stdio frontend + bundled `aar-operations` skill over a host-managed WSL supervisor | durable RLM submit/status/events/cancel/reconcile plus prior programmable EXECUTE | exact wheel, fresh `hermes mcp test`, current-session attach, installed durable-operation readback, separate supervisor/frontend identities, private-file permissions, explicit service choice, and rollback readback |
| AHC | planned | shared fixtures + native adapter; MCP parity optional | EXECUTE, later SHADOW and MANAGED | Python/Rust fixtures, exact session/workspace binding, authoritative brokers, restart reconciliation, no duplicate final delivery |

## Executed AR-0D rows

Both rows used package `adaptive-agent-runtime==0.1.0a0`, schema bundle
`sha256:e45f45a5c2df8dfaf4561bcc59410b4a35c51775a9f713211eee6e655e35fa5f`,
fixture set `sha256:18f42e30c9119bd82be81eebe7d5163ef67509e22945168e257dfa1500b91835`,
tool surface `sha256:5b503d1b0600d70a8d1d3f7f8995378533694279dc8b233c7c9342e96d075e5d`,
and `aar-operations` `0.2.2` at
`sha256:8ef484155b31022293596dfb5825aaa66e03d5ac9b82dcaa89226b1d3e54fa68`.

| Field | Codex local client | Hermes Agent CLI |
|---|---|---|
| Host version | `codex-cli 0.146.0` | Hermes Agent `0.19.0` (`2026.7.20`), local `431a588f`, Python `3.11.15` |
| Host OS/process boundary | Windows 11 build `26200`; ephemeral `codex exec` child launched from the Codex App environment | Ubuntu `24.04.4` under WSL2; isolated profile and one-shot CLI process |
| Negotiated MCP revision | `2025-06-18` | `2025-11-25` |
| Transport and tools | local stdio; all ten `aar_*` tools discovered | local stdio; all ten `aar_*` tools discovered |
| Profile config digest | `sha256:94d2e67b919c375a8b03ed2f79014451727c3275ad2d051612cee54b4ea6423c` | `sha256:49d3ef8a0e4e2a1e7b76fec5e2a9f1f8ccb37b89af55427e4151f41bc3639585` |
| Guided result | passed capabilities, create, execute/inspect, status, cancellation, stale-revision rejection, unsupported checkpoint, and content-addressed artifact readback | same scenarios passed; the exported session trace also proves the canonical skill was loaded before the MCP calls |
| Artifact readback | 331-byte receipt; declared and computed `sha256:1dc96f0a0a1be236364eda8b43d53f2dfbff6415851470f9d1902b3b2d63b812` | 332-byte receipt; declared and computed `sha256:c908a0e42a83c97f71e257f45fcaf182f5d82db460955a8672451e6a87ba0422` |

The Codex final receipt and JSONL event stream have SHA-256
`badbb5f4ea4b527cfbc349c9ae6f2c9968309702e2c366cf7d4f9570c9314762` and
`f99d01e7b6a77c0e3f183aef3d36490e01a9016171e280dc61138097be6dc9b7`.
The Hermes final receipt, usage receipt, and redacted trace have SHA-256
`0671cae2aea42873460c11c87332fb9f21f95c7faa0f49aa97c14a0b1d75384e`,
`cfdb6af869c85b3802054bca5010812e8fb6d4902c2ad0f458e662c5cb38dd94`, and
`2dc32a0044268c422eda4624227746d6f5ac1099438f59e7e17605f5b93434b2`.
These are local acceptance artifacts, not published release assets.

Known host boundaries:

- The Codex row exercises the shared local plugin/MCP surface through a fresh CLI process. A new
  desktop-task UI pickup was not separately replayed.
- Both hosts start a fresh MCP child, but neither forwards the parent process's ad hoc
  `AAR_DATABASE` override through its managed MCP launcher. The executed rows therefore reused the
  default repository-local recovery database and observed runtime generations `5` and `7`.
- The Codex verification used the host's explicit MCP approval override and
  `danger-full-access` sandbox because its Windows read-only shell could not run the deadline-only
  PowerShell probe. This is host policy, not an AAR grant.
- The Hermes distribution intentionally owns `config.yaml` and supplies MCP configuration only.
  The verification selected `openai-codex` and `gpt-5.6-sol` explicitly; gateway parity was not
  claimed.
- Portable checkpoints, arbitrary Python workspaces, provider credentials, direct effects,
  activation, and final delivery remain unsupported in AR-0.

## Executed AR-1 rows

Both rows used executable candidate `6aa8fc7f7598ebc77560defe1d31d30719a64339`, package
`adaptive-agent-runtime==0.1.0a0`, schema bundle
`sha256:e45f45a5c2df8dfaf4561bcc59410b4a35c51775a9f713211eee6e655e35fa5f`,
fixture set `sha256:18f42e30c9119bd82be81eebe7d5163ef67509e22945168e257dfa1500b91835`,
tool surface `aar.mcp-tools.v2` with 20 public tools at
`sha256:71a42a4b8dd554c717bfd0a5e36f601234da3016d6fa96138548c2291747d44b`, and
`aar-operations` `0.3.1` at
`sha256:bc83747e71ec7ae9e9bbf8c35661bfad770f282e035fae8ffa675f91bc5ca7d3`.

| Field | Codex local client | Hermes Agent CLI |
|---|---|---|
| Host version | `codex-cli 0.146.0` | Hermes Agent `0.19.0` (`2026.7.20`), local `431a588f`, Python `3.11.15` |
| Host OS/process boundary | Windows 11 build `26200`; fresh ephemeral `codex exec` child | Ubuntu `24.04.4` under WSL2; fresh isolated-profile one-shot process |
| Negotiated MCP revision | `2025-06-18` | `2025-11-25` |
| Runtime and backend | generation `10`; IPython `9.16.1` | generation `1`; IPython `9.16.1` |
| Backend capability digest | `sha256:0a9e7aeef5b6c086e329d3822e560910c76ce26ee15878093e76bf9d34691be6` | same |
| Guided result | created generation 1; executed and inspected `answer=42` at revision 1; reconciled completed; checkpointed with bound provenance; restored generation 2/revision 0; health ready; closed succeeded | same lifecycle through 15 AAR MCP calls; 13 model API calls; no shell, file, browser, Python, or tool-search call |
| Profile/config evidence | installed Codex plugin `0.1.0+codex.20260808172114`; generated config digest `sha256:94d2e67b919c375a8b03ed2f79014451727c3275ad2d051612cee54b4ea6423c` | generated profile config digest `sha256:49d3ef8a0e4e2a1e7b76fec5e2a9f1f8ccb37b89af55427e4151f41bc3639585`; accepted isolated override digest `sha256:07b25637a25a5ab44ec1b1a09206791cdbc0e562cf638c316cc0edbd9430e17a` |

The Codex final receipt has SHA-256
`c45f1220f2e63b3091dc18d26a499d959061feb1bdf02c0cd0bfea5673cdd0c4`.
The Hermes final receipt, usage receipt, and redacted trace have SHA-256
`6a87db832a7ae0e385643906feb7268c9ac1974e05130e78128943ed96d916c8`,
`66eaeb207ac3908cb318705d80f329a1283c80a3e9c73a189491528e4edcfa98`, and
`05d1119895d5555863f386c2e3f507f098feb9a76bd13d5d359b6c340bde2d71`.
These are local acceptance artifacts, not published release assets.

AR-1 host boundaries and observed failures:

- The Codex agent read the installed `aar-operations` skill once through its shell before using
  AAR MCP tools. The subsequent programmable lifecycle is real, but the run is not described as a
  tool-exclusive MCP trace.
- Earlier Codex probes failed closed before or at mutation because `aar-mcp` was absent from PATH,
  the agent used schema-invalid nested context fields, and a prompt-fixed deadline expired during
  startup. Installing the exact wheel, publishing the flat context field names, and exposing
  `server_now_unix_ms` addressed those failures.
- Hermes `mcp test` discovered all 20 public tools with the generated bare `aar-mcp` command, but
  the agent path's parent-death watchdog intermittently failed to execute that bare command with
  `PermissionError`. The accepted isolated profile bound the already installed executable at
  `<user-home>/.local/bin/aar-mcp`; this is a host-launcher workaround, not a portable
  generated-profile success claim.
- Hermes exposes AAR calls under `mcp__aar__aar_*` wrapper names and requires the `aar` toolset for
  the one-shot invocation. These are host presentation details, not new AAR tool names.
- Neither row proves a Python security sandbox, Codex desktop-task UI pickup, Hermes gateway or
  messaging parity, provider access, external effects, activation, or final delivery.

## 2026-08-09 Codex dependency and setup refresh

This is a post-acceptance working-tree refresh based on repository HEAD
`479b2ec8458817690482eb27b2f8c30b9da5fab2`. It does not replace the audited AR-2/AR-3 candidate
or claim a published release.

- The default wheel now declares IPython, NumPy, and pandas. The compatibility smoke imports all
  three in the supervised worker, computes a three-row DataFrame sum, inspects the resulting live
  values, and reports their checkpoint exclusions.
- `aar-codex-setup` is the public two-command installation companion. Its default preflight uses a
  temporary database and a real MCP worker, then installs the bundled plugin and registers the
  exact tool-environment `aar-mcp` path in Codex user configuration. A source-checkout invocation
  completed in about eight seconds after dependencies were present.
- Installer/package gates passed on Python 3.11, 3.12, 3.13, and 3.14 (4 tests each). Ruff passed;
  the final Python 3.14 repository suite passed 116 tests in 83.26 seconds.
- Final exact wheel:
  `dist/candidate-20260809-public-setup-final/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`,
  171,614 bytes, 84 ZIP members, SHA-256
  `f1eae3501a820063a0bc925b6c70c40abe626d3f94272a82dd5fa646477ccaa5`. ZIP integrity,
  `aar-codex-setup`, the bundled install guide, and all declared dependencies read back. An
  isolated Python 3.14 install ran `aar-codex-setup --preflight-only` successfully with IPython
  `9.16.1`, NumPy `2.5.1`, pandas `3.0.5`, 28 tools, result `18`, and a closed workspace.
- At the accepted dependency fresh-host probe, the tool environment was bound to that exact wheel
  and the enabled plugin was `0.1.0+codex.20260809045736`. Its full `aar-codex-setup` receipt passed
  with the exact `<user-home>\AppData\Roaming\uv\tools\adaptive-agent-runtime\Scripts\aar-mcp.exe`
  launcher.
- A fresh `codex-cli 0.146.0` process called Context Canvas `canvas_list`, discovered AAR v4,
  executed the same dependency calculation, successfully inspected
  `IPython/answer/df/numpy/pandas/rows/versions`, and closed the workspace. Rejected schema and
  stale-handle corrections in that agent trace are retained as failures and are not counted as
  passing operations.
- After the desktop restart, the previously blocked `uv tool install --force` completed with exit
  zero. A focused installer refinement then made repeated setup idempotent: current plugin and MCP
  bindings are read before writing, and `restart_required` follows actual configuration changes.
  The fail-first installer slice was 2 failed/2 passed; the repaired focused package/profile slice
  passed 11 tests, Ruff, host-profile byte verification, and diff checks. The prior dependency and
  MCP-worker evidence was reused because those executable contracts did not change; the full suite
  and four-interpreter matrix were not repeated.
- Current local exact wheel:
  `dist/candidate-20260809-lean-setup-final/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`,
  172,232 bytes, 84 ZIP members, SHA-256
  `519b8e1afd9574551055124b4251dd9131f18beba3b6f17934960384946934f2`. ZIP integrity and the
  exact entrypoint, bundled installer source/guide, and plugin version read back. The installed
  plugin is `0.1.0+codex.20260809134104`; MCP remains bound to the same exact tool-environment
  launcher. The first configuration run changed only the plugin and requested a restart; an
  immediate repeat was a true no-op with all change flags and `restart_required` false.
- After the second App restart, `codex-cli 0.146.0` loaded the new plugin and its operation skill.
  A `read-only` child sandbox exposed no AAR tools and performed no mutation; the same bounded probe
  under `workspace-write` exposed all 28 tools. The accepted fresh-host calls negotiated
  `2025-06-18`, loaded operation skill `0.5.0`, created a real IPython workspace, executed the
  dependency DataFrame result (`18`, three rows; IPython `9.16.1`, NumPy `2.5.1`, pandas `3.0.5`),
  and inspected `IPython`, `answer`, `frame`, `np`, and `pd` at revision 1.
- The child CLI then cancelled `aar_program_workspace_close` at its non-interactive host-approval
  layer. A new server reported the prior workspace absent, so no live worker remained. An initial
  old-style nested-context call and a later closure-only call with the wrong outer context key were
  schema failures before mutation; they are retained as failed probe calls, not AAR failures.
  Current direct exact-wheel preflight still closed its workspace successfully. Exact member
  comparison between the prior and current wheels found changes only in the installer, setup guide,
  generated Codex profile metadata/README, and wheel metadata; `aar/mcp/server.py`, the workspace
  runtime/worker modules, and `aar-operations` bytes are unchanged. The previously accepted
  fresh-host close evidence therefore remains applicable to the unchanged close path; the new
  post-restart host row adds current plugin pickup plus create/execute/inspect evidence without
  claiming that the cancelled close call passed.

### Reference-context UX closeout

- The first schema-guidance repair made the required outer `context` name and every flat field
  source visible in the MCP input schema, retained strict unknown-field rejection, and updated the
  bundled skill. A fresh `codex-cli 0.146.0` probe then used the correct outer key and no nested
  grant, but still omitted principal/session/request identity and inserted an obsolete revision
  field. That call failed schema validation before mutation and is retained as a failed probe.
- MCP surface `aar.mcp-tools.v5` adds the read-only `aar_reference_context` helper. For one exact
  reference-host mutation it returns a complete flat mutation `context` plus the corresponding
  `read_context`; callers still pass the explicit generation, capability digest, identity,
  deadline, grant ID, budget, request ID, and idempotency key to the mutation. No production-host
  authority is inferred, and the strict mutation models still reject nested or extra fields.
- The v5 surface exposes 29 tools at
  `sha256:1ba64cb622ee9c75f3302c9d90cdabcb08804c56bd0d9b9138080b8772911a2c`.
  `aar-operations` `0.6.0` is
  `sha256:4e3b8387f0d790f6eaa81d03ac881ce09c727732eb8ec69da618c99380f9e262`.
  Eighteen focused schema, helper lifecycle, skill-guided lifecycle, installer, package, smoke, and
  generated-profile tests passed; Ruff and both skill/plugin validators passed.
- Final exact wheel:
  `dist/candidate-20260809-context-helper-final2/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`,
  196,291 bytes, 85 ZIP members, SHA-256
  `36eee122eb3befdb18db944e6d68eb077e0977c5942eb7ad3c879cec1e211e7d`. Exact-wheel readback
  matched both versioned MCP schemas plus the canonical skill, skill metadata, and Codex plugin
  manifest. Compared with the fresh-probed predecessor, only the restored archived v4 schema and
  wheel `RECORD` differ; active v5/runtime/skill/plugin bytes are identical.
- Exact final2 wheel installation and `aar-codex-setup --preflight-only` passed. The preflight saw
  29 tools, v5, IPython `9.16.1`, NumPy `2.5.1`, pandas `3.0.5`, result `18`/three rows, and a
  closed workspace. The full setup on its active-byte-equivalent predecessor installed plugin
  `0.1.0+codex.20260809064045`; the exact AAR launcher remains
  `<user-home>\AppData\Roaming\uv\tools\adaptive-agent-runtime\Scripts\aar-mcp.exe`.
- A fresh ephemeral `gpt-5.6-sol`/max Codex process under `workspace-write` with approval policy
  `never` received only the ordinary request to create a scalar workspace, set `answer` to `18`,
  and inspect it. Without context-field hints, it called `aar_capabilities`,
  `aar_reference_context`, `aar_workspace_create`, `aar_reference_context`,
  `aar_workspace_execute`, and `aar_workspace_inspect`; every call completed and the inspected
  revision 1 value was `18`. It used no shell and edited no files. The desktop App still requires
  one restart to pick up this final plugin cachebuster; the fresh child process is the v5 host
  evidence at this boundary.

Observed install boundaries:

- Before repair, the already-running desktop task exposed all 28 AAR tools but a pandas cell failed
  with `No module named 'pandas'`. After other acceptance processes advanced the shared recovery
  database, that old MCP process correctly rejected mutation as `STALE_RUNTIME_STATE`; it was not
  treated as hot-reloaded.
- The portable plugin config uses the cross-host command name `aar-mcp`. Codex user configuration
  now overrides it with the exact tool-environment executable. Context Canvas likewise uses an
  exact Python executable and exact installed server script, avoiding dependence on a GUI PATH
  snapshot.
- Updating the uv tool while existing App tasks still held the old global shim returned exit 2 at
  entrypoint replacement even though the package environment and new setup entrypoint were
  installed. After those tasks were closed and the App restarted, clean `uv tool install --force`
  receipts passed for both the original setup wheel and the final idempotent-installer wheel. The
  public guide retains the required Windows upgrade order.
- This evidence proves local programmable analysis and host bootstrap only. It does not prove a
  Python security sandbox, provider credentials, external effects, activation, final delivery,
  Hermes v4, a Rust AHC adapter, publication, or release.

### Desktop plugin-only discovery closeout

- Restarted desktop task `019fe562-4e4d-7623-8823-519e69526190` and a fresh-context readonly
  subagent initially exposed zero native AAR tools and zero Context Canvas tools while both servers
  had a plugin registration plus a same-name global transport. Direct setup preflight still passed,
  but was not counted as desktop discovery.
- The plugin-resolved `<user-home>\.local\bin\aar-mcp.exe` completed the real-worker preflight
  with 29 tools, v5, the dependency calculation, and a closed workspace. Its launcher SHA-256
  matched the exact uv-tool launcher. The matching global `aar` registration and the stale global
  Context Canvas registration were then removed; installed plugins and hooks were left enabled.
- Fresh desktop task `019fe5a8-445c-7db1-832e-acad8a95b688` natively exposed 29 AAR tools and 12
  Context Canvas tools. Its native `aar_capabilities` call returned `aar-mcp`,
  `aar.mcp-tools.v5`, runtime generation 1, and `aar-operations` `0.6.1` at
  `sha256:f4c47ebee6e114de990d2be6339698584809c10d1ab1b10bf799f69c1bc5c045`.
  The Context Canvas hook also injected an opaque identity. No mutation was attempted.
- Final configuration readback lists AAR and Context Canvas once each through their plugin-owned
  stdio commands. The AAR plugin is `0.1.0+codex.20260809083709`; Context Canvas is
  `0.4.0+codex.20260809075929`.
- `aar-codex-setup` schema `aar.codex-setup.v2` now installs the plugin as the sole MCP transport
  authority. It removes only a legacy global `aar` entry that names the same preflighted launcher
  and fails closed when a different global server owns that name. It reads the actual
  `[mcp_servers.aar]` table rather than treating plugin-inclusive `codex mcp get` output as global
  configuration. Two fail-first passes each produced four expected failures before their repairs;
  the focused installer slice then passed 6/6. The final repository suite passed 121 tests, Ruff
  passed, generated profile bytes verified, and plugin validation passed.
- Exact wheel
  `dist/candidate-20260809-plugin-authority-final2/adaptive_agent_runtime-0.1.0a0-py3-none-any.whl`
  is 198,995 bytes with SHA-256
  `cd8d9d78f12d05645ae24f9ba7f203e1446a07fe90c78eccac5225a196b7aa27`. Installed
  `--preflight-only` returned setup v2, 29 tools, v5, result 18/three rows, and a closed workspace.
  Installed configuration-only setup was a true no-op with plugin authority, no legacy global
  entry, and `restart_required: false`.
- After intentionally terminating the first verification task's read-only MCP child, the same task
  returned `Transport closed` instead of reconnecting. Fresh task
  `019fe5b4-38d1-7a61-a2a9-3f87c9397c06` then natively started the updated installed runtime and
  reproduced the 29-tool capability result plus 12 Canvas tools. The compatibility claim therefore
  includes fresh-task restart recovery, but does not claim transparent same-task stdio reconnect.
  The final wheel changed only setup/install documentation, `aar/compat/codex_setup.py`, and wheel
  `RECORD` from that native-probed wheel; AAR MCP server and operation-skill bytes are identical, so
  the native proof remains bound.
- This closes current desktop native discovery for the plugin-only registration. It does not prove
  provider credentials, external effects, activation, final delivery, a published package, Hermes
  v5, or a separately supervised always-on AAR service.

## Executed AR-2 and AR-3 rows

The retained AR-2/AR-3 acceptance rows use source repair candidate `c21d595` and package
`adaptive-agent-runtime==0.1.0a0`, schema bundle
`sha256:6e2aee5ba92c07fb18b7cb074712679617d64b2c895ebf4fbd167f3f31f112ab`,
fixture set `sha256:5d07a6dac808b44339bfd93a37257fb74ea575a64b40b6c1cda26cb0e6add1e4`,
MCP surface `aar.mcp-tools.v4` with 28 public tools at
`sha256:25a4d4e14d62fb5bd934382b05f8cc2228b6f7d17cd3b08053161c71cda810fb`, and
`aar-operations` `0.5.0` at
`sha256:59b2e32427cd4b6c84b2c17ebac540fa11dbc6bf49e56118d8b92cfa3037278a`.

| Field | Reference host and package | Codex local client |
|---|---|---|
| Host version | CPython 3.11, 3.12, 3.13, and 3.14 isolated uv environments | `codex-cli 0.146.0`; fresh ephemeral `gpt-5.6-sol` high-effort process on Windows 11 build `26200` |
| Transport | direct SDK and real MCP client/server tests; isolated wheel readback on Python 3.11 and 3.14 | local stdio through the installed personal plugin and bundled operation skill |
| Verified RLM result | persisted jobs, typed broker calls, budgets/grants, retained handles, deterministic benchmark, cancellation, stale identity, process loss, and indeterminate reconciliation | one evidence-query/model-request strategy succeeded with a certain terminal result; one model request; exact broker methods and contract digest `sha256:bd06715bade9ef377400fa145484d7a330fdfc4f6cb554d43c39ed4ca62058b7` |
| Verified asset result | contentful dependency-closed round-trip, all five lifecycle event kinds, replay, unknown outcome, conflicting/missing refs, nondeterministic migration rejection, prepare-only materializer, and process loss | canonical empty export/import succeeded with certain outer operations and bundle digest `sha256:f6ece0928b3dae780d60af332d1c1858b2d34cf30f1c86c822ee2f2d9c8c23bd`; active serving remained false |
| Package/plugin evidence | wheel contains schemas, fixtures, benchmarks, integration fixture, operation skill, and optional CodeGraph workflow skill; Python 3.11 and 3.14 isolated installs passed `aar-compat-smoke` with all nine checks | installed plugin `0.1.0+codex.20260808192807`; selected installed/source plugin bytes are identical |
| Authority ceiling | deterministic local brokers; Effect remains proposal-only; materializer remains prepare-only | no provider credentials, external effects, activation, serving mutation, or final delivery |

The retained fresh-host summary is
`.aar/codex-ar23-c21d595-summary.json`, SHA-256
`95e3119e85aa2cd8f80c1ccea67585acb404ac336e6316040bf279c4090db252`. It records
the accepted outputs and that the raw trace was not retained. The host made five rejected
schema, budget, and deadline correction calls before the accepted lifecycle; the summary retains
each failed tool, failure class, and correction. They were not counted as passing operations or
erased from the compatibility assessment. The accepted capability, RLM, status, replay-denial,
broker, and asset calls passed.

AR-2/AR-3 boundaries and open host rows:

- The Codex asset lifecycle used an empty canonical bundle. Contentful assets are covered by direct
  and MCP integration tests, not by this fresh-host row.
- The current desktop task was already running when the plugin cachebuster was installed. A fresh
  task or application restart is required for reliable desktop pickup; this row used a genuinely
  fresh CLI process.
- The AR-1 Hermes row was not replayed for the v4 RLM/asset surface. No AR-2/AR-3 Hermes claim is
  made.
- The AHC fixture is transport-neutral Python contract evidence. No Rust consumer, native adapter,
  managed-host authority, activation, or delivery row has passed.
- CodeGraph remains a separate optional skill-guided workflow for IPython artifacts; NOOA remains
  design input only. Neither is an AAR runtime backend or dependency.

## Executed AR-LT1 durable continuity row

The AR-LT1 candidate is source commit
`04238e83dd5702ee92f49fad2da425e06fd8dc57`, package
`adaptive-agent-runtime==0.1.0a0`, and final local wheel SHA-256
`f3748ddfc444478407a9258018f711a363210cc7d6a4266070718c3ed2a507b5`.

Bound identities:

- MCP surface `aar.mcp-tools.v6`, 30 public tools, digest
  `sha256:e3d319b447ebbdf8b055ef8f710e2889184bc425fc88078caaf5de0a205081e4`;
- schema bundle
  `sha256:e69ff34ab4b74b514f69919ec5244fb6d4d994dce16af3e47965348b0b3a892d`;
- fixture set
  `sha256:d9fe836b884fcae09fed9377042664f3dc9df3f5bde6f8dc78f424df389c1820`;
- operation skill `0.7.0`, digest
  `sha256:1dbf36ff6be3651d95f777e008bcd8ad1341df1dcaecc00994414b4c4fb9303a`.

Executed claims:

- the repository suite passed 163 tests; Ruff and all contract/profile asset verifiers passed;
- an isolated exact-wheel environment passed the 12 process-recovery and runtime-ownership tests;
- real process scenarios covered accepted-before-claim restart, claimed successor attempts, stale
  owner/late-write rejection, durable cancellation, broker-receipt reuse, unresolved-broker
  indeterminate state, strictly increasing events, and duplicate-start rejection;
- a live installed Hermes generation completed a durable `start_only` RLM operation after frontend
  submission and returned its terminal trace through the native AAR tools;
- a second process for the same live database failed before generation allocation, while idle
  dispatcher CPU fell from the observed busy-loop level to approximately 0.5%;
- the final wheel was installed through the existing Hermes MCP binding, and a fresh
  `hermes mcp test aar` process connected and discovered all 30 tools.

Boundaries:

- AR-LT1 remains a combined frontend/runtime process. Durable supervisor separation is AR-LT2;
- replacing the MCP child closes already-open host transports. Existing gateway sessions require an
  MCP reload or gateway restart; fresh processes use the installed wheel immediately;
- the verified v5 tool environment and pre-v6 database copy remain available as a local rollback
  point, but no in-place downgrade of a v2 continuity database is claimed;
- no provider credentials, external effect execution, activation, final delivery, arbitrary worker
  resurrection, OS service install, push, or published release is included.

## Executed AR-LT2 durable supervisor row

The AR-LT2 candidate is source commit
`38f338255f38b266f0cbe4d87db6f659274d80c6`, package
`adaptive-agent-runtime==0.2.0a0`, and exact local wheel SHA-256
`e867c69c82139d567303591a8c94a204d9b779366e6e22927ad184ae6a6ad624`.

Bound identities:

- MCP surface `aar.mcp-tools.v7`, 30 public tools, digest
  `sha256:370d8a3177e80e94523c07537fc6b3107beacd52956add61b0e31b9d32bf2555`;
- private supervisor protocol `aar.supervisor.protocol.v1`, digest
  `sha256:2ec0e07c390041517632aee6d2962bec55cd974fd31c2a99284ff5e45a2196c8`;
- schema bundle
  `sha256:8f8e456e3af25d66063469e3b4722ccf7768b0b1b2d0c37ca897002c5385ef22`;
- fixture set
  `sha256:d9fe836b884fcae09fed9377042664f3dc9df3f5bde6f8dc78f424df389c1820`;
- operation skill `0.8.0`, digest
  `sha256:b48d014ca89809a1d710a5208036e6df6b3f348a9f86c42586989aaff60fb9c9`.

Executed claims:

- authenticated private frames bind protocol, generations, authority, deadlines, attachment proof,
  payload length and digest; malformed, stale, oversized, and bad-credential cases fail closed;
- exact native process-start identities distinguish supervisor/worker ownership from PID reuse;
- real subprocess tests prove concurrent and replacement frontends, durable RLM continuation after
  frontend exit, hard supervisor loss, predecessor-generation reconciliation, and orphan-worker
  terminal fencing;
- the isolated exact Linux wheel uses an owner-only Unix socket and returns one durable RLM result to
  a fresh frontend; the native Windows 3.13 row verifies `GetProcessTimes` identity and the declared
  credentialed loopback-TCP fallback, including invalid-credential rejection;
- Hermes runs the WSL supervisor through an explicitly installed, least-privilege Windows Scheduled
  Task. Package installation alone does not create this task;
- fresh `hermes mcp test aar` and the existing native Hermes session both attached to runtime and
  dispatcher generation 10. An installed durable RLM operation progressed from accepted to
  succeeded and was read through a separate fresh frontend;
- live supervisor and MCP frontend PIDs are separate. The private directory is `0700`; discovery,
  attachment credential, and Unix socket are `0600`; registry schema v3 has no nonterminal operation.

Rollback and boundaries:

- the complete prior LT1 tool tree and pre-v3 SQLite backup are retained in operator-local rollback
  evidence; the older v5 rollback is also retained;
- Windows named-pipe ACLs, remote HTTP, arbitrary memory resurrection, checkpoint/effect-aware
  continuation, external effect execution, provider credentials, activation, final delivery, AHC
  admission, push, and publication are not claimed.

## Executed bounded AR-LT3 RLM recovery row

The first bounded AR-LT3 candidate is source commit
`9b7a1d9c8f8aa64fbd43a42dbfd6a6cc8b25d23a`, package
`adaptive-agent-runtime==0.3.0a0`, and exact local wheel SHA-256
`cce45b4f8ac3af83beb43812aad9f8798ad8abd9d43dcf4f8dab399305a507f8`.
The wheel is 300,806 bytes with 113 ZIP members and no corrupt member.

Bound identities:

- MCP surface `aar.mcp-tools.v7`, 30 public tools, digest
  `sha256:c3032f942269c8705e6c8e918a6ca934fb392441264194adc0db2d36cc8133c0`;
- canonical contract schema bundle
  `sha256:de251fb18ce631a227d58857e3392418a80ae534fae57d2d3ab125eeb8b18171`;
- operation skill `0.8.0`, digest
  `sha256:b48d014ca89809a1d710a5208036e6df6b3f348a9f86c42586989aaff60fb9c9`;
- additive registry migration `(1, 2, 3) -> (1, 2, 3, 4)`.

Executed claims:

- durable `rlm.execute` binds `rlm.step-boundary.v1` before dispatch and cannot silently adopt a
  different policy;
- successor decisions bind predecessor attempt, runtime and dispatcher generations, lease epoch,
  input/environment/policy digests, committed steps, broker receipts, cumulative usage, deadline,
  and cancellation state;
- a crash after an authoritative broker receipt reuses that one receipt without a second broker
  request, even when the original model-request budget is fully consumed;
- unresolved calls persist `reconcile_effect` and remain indeterminate; malformed/tampered policy,
  payload, broker grant, sequence, response, or digest evidence quarantines rather than replaying;
- stale-boundary rejection is atomic: no decision, boundary, or accepted successor survives the
  rejected transaction;
- the full suite passed `199 passed, 1 skipped in 335.32s`; the guarded Windows process-time path then
  passed in a fresh native Windows Python 3.13 exact-wheel probe;
- a fresh Linux Python 3.11 exact-wheel supervisor/frontend pair passed all ten compatibility-smoke
  checks and reported registry schema v4; the Windows probe reported native process identity,
  bad-credential rejection, loopback TCP, 30 tools, and attached-supervisor mode;
- an online backup of the installed LT2 schema-v3 database passed integrity checking, migrated to v4
  under the LT3 exact wheel, and was rejected by LT2 as newer schema. A separate pre-cutover readback
  confirmed the authoritative database was still schema v3 with `quick_check=ok`;
- before replacement, installed LT2 `0.2.0a0`, generation 11, exact process identity, task action,
  terminal-only operation state, completed dispatch rows, and database integrity were read back;
- the complete LT2 uv-tool environment, exact wheel, scheduled-task XML, and untouched v3 database
  were retained together. LT2 opened a readback copy and the canonical rollback digest stayed fixed;
- the host-selected Scheduled Task then started the installed LT3 executable. Fresh discovery named
  one exact supervisor process, runtime and dispatcher generation 12, and schema v4 with
  `quick_check=ok`;
- fresh `hermes mcp test aar` and the current native MCP session both attached. They reported package
  `0.3.0a0`, 30 v7 tools, the `0.8.0` skill, and attached-supervisor mode;
- one bounded durable RLM canary completed with an authoritative model receipt, and its admission-time
  recovery policy binding was present in the v4 registry;
- a duplicate supervisor candidate exited nonzero with exact-live-discovery rejection, generation
  stayed 12, and a process scan found one exact supervisor. A two-second idle sample was approximately
  0.5% CPU;
- the task remained running, no active or indeterminate operation remained, and exactly three usable
  rollback generations were retained.

Boundaries:

- this row verifies only the first policy-bound RLM successor slice; workspace checkpoint selection,
  new-generation restore, general broker/effect reconciliation adapters, and the remaining AR-LT3
  fault matrix remain open;
- exact `0.3.0a0` is installed and active for Hermes behind the existing host-managed supervisor;
- no provider credentials, external effects, activation, final delivery, managed-host admission,
  package-registry publication, or managed AHC release is claimed by this row.

## Codex App profile

Current official OpenAI documentation says local Codex clients can connect to MCP servers through stdio or Streamable HTTP and that the ChatGPT desktop app, Codex CLI, and IDE extension share the same MCP configuration for a Codex host. The first AAR profile therefore uses stdio and tests against a fresh Codex host.

The profile must record rather than assume:

- the MCP protocol version/era the tested client actually speaks;
- server instructions and tool schemas read by the client;
- canonical operation-skill delivery/activation and exact digest;
- startup and tool timeouts;
- project/user config ownership and exact generated entry;
- enabled/disabled tool policy and approval behavior;
- progress, cancellation, task, and resource-link support observed in that version;
- what happens when the server exits or restarts.

The Codex bundle must keep the full workflow in `aar-operations`; MCP server instructions stay concise and server-wide. A fresh-host scenario must execute the workflow through real MCP tools, not merely show that the skill was installed.

Codex tool approval configuration is host policy. It is not an AAR grant or proof that an external effect occurred.

Official reference: <https://learn.chatgpt.com/docs/extend/mcp?surface=cli>

## Hermes Agent profile

Hermes is a second MCP adopter because its Python harness, central tool registry, MCP integration, sessions, plugins, and gateway surfaces differ materially from Codex. Passing both profiles prevents an accidental Codex-only contract.

Before implementation, refresh the Hermes source and official docs because the earlier architecture review is a pinned snapshot, not current truth. The profile must determine:

- how MCP servers are registered and scoped;
- which client protocol version/era is used;
- process lifetime and restart ownership;
- session/workspace identity mapping;
- tool permission, timeout, and cancellation behavior;
- whether artifacts/resources are retained or only shown in conversation context;
- whether the gateway and CLI behave identically enough to share one row.

The Hermes package must deliver the same canonical operation workflow or record an explicit semantic translation and digest. Hermes-specific installation metadata must not introduce new core tool semantics.

The original CLI row satisfies the AR-0 portability gate without adding a Hermes-specific core type.
The AR-LT1 row adds installed gateway operation/restart evidence; messaging and final delivery remain
separate unsupported claims.

## AHC profile

AHC is the first managed host, not the portability oracle. It owns exact identity, durable admission, budgets, effects, child work, evidence, activation, rollback, and final delivery.

The AHC adapter may use a native process protocol where MCP does not provide sufficient authority or recovery semantics. It must still:

- consume the same canonical schemas and fixtures;
- preserve operation, generation, revision, idempotency, cancellation, and reconciliation meaning;
- keep AAR optional so ordinary AHC operation survives AAR absence;
- prevent AAR from directly mutating serving state or delivering final replies;
- expose compatibility as an additional matrix row, not as the definition of AAR core.

## MCP protocol baseline

The latest MCP specification observed during planning is revision `2026-07-28`, which uses per-request protocol and capability metadata plus `server/discover`. Legacy revision `2025-11-25` and earlier use an initialization handshake. Target hosts may lag the latest specification.

AR-0C selected `mcp==2.0.0`. The server declares every revision that SDK actually serves:
`2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`, and `2024-11-05`. Reference child-process
probes negotiated `2026-07-28` in modern mode and `2025-11-25` in legacy mode against the original
ten-tool surface and operation state machine. AR-LT1 preserves those revisions while extending the
surface to v6/30 tools. AR-0D request-context telemetry measured Codex at `2025-06-18`; installed
Hermes AR-LT1 calls negotiated `2025-11-25`. The negotiated value must occur in the advertised
supported set. Silent fallback or prompt-supplied protocol claims are not acceptance evidence.

Official references:

- <https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>
- <https://modelcontextprotocol.io/specification/2026-07-28/server/discover>
- <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio>
