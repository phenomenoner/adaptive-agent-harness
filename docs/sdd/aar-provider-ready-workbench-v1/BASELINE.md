# Evidence Baseline

## 1. Custody

This public SDD successor is grounded in exact source identities while intentionally omitting machine-local filesystem paths.

| Role | Public identity | State |
|---|---|---|
| Exact implemented baseline | `adaptive-agent-runtime 0.5.0a0`, commit `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1` | clean predecessor source |
| Active successor specification | branch `codex/aar-provider-ready-workbench-v1-impl` rooted at the exact baseline | S0 review candidate; product source unchanged |
| Preserved runtime class | user-owned registry v1-v5 state | no reviewed v6 operator cutover performed |
| Admission evidence class | protocol-v5 RLM-workbench evaluation receipt | provider calls `0`; formal attempts `0`; AAR arm blocked |

The user-owned dirty canonical worktree remains preserved outside the successor branch. Before product code work, PMO must freeze this successor candidate and dispatch only bounded Luna/max packets against the clean accepted branch.

## 2. Exact source seams

Hashes below are SHA-256 over the exact v0.5 file bytes.

| Seam | Exact source evidence | File SHA-256 | Consequence |
|---|---|---|---|
| v6 primitives | `src/aar/runtime/migrations.py:50-97` implements verified SQLite backup; `:100-174` begins transactional `apply_registry_v6` with strict attestation and replay identity | `e7102275a5c61b608fe00a03adfc1773a7629d94d061e200372c3d3eb655884f` | Reuse these primitives; add an operator-owned plan/apply/status/reconcile path instead of another migrator. |
| Supervisor CLI | `src/aar/runtime/supervisor.py:668-691` accepts runtime home, database, programmable backend, transport, and concurrency only | `dc4365d4433f35088cc401fba9ba95ca93cd0bfe818f762f5cc30d38faed4187` | No official activation-profile or reviewed cutover binding reaches `SupervisorService`. |
| Host injection seam | `src/aar/runtime/reference_host.py:164-177` accepts optional model registry, route, planner, and backend availability; `:210-243` constructs caller work only on v6 and copies caller-provided availability | `977079d7e4f58caeaa3437d2c935ce7db79ca4b4a324b46af0b03772bbd38e7f` | The seam exists, but profile verification and component-derived capability truth are absent. |
| Planner path | `src/aar/runtime/rlm_workbench.py:857-864` rejects a missing planner; `:901-906` calls synchronous recovery planning; `:933-946` calls synchronous normal planning | `9baa5ded563cd4c3611e456b2fd6e0c1350ce12dd1747906f06293d33cad9024` | Caller-delegated root planning is not durable ticket work and can hold the attempt across a physical call. |
| Admission rule | `src/aar/rlm_workbench_models.py:232-244` rejects the full journey if any broker method lacks a qualified backend | `b8b1fb20ef60fef28cfad113cc161c2b71c6cc2e0a12893eebdecc16de34c08a` | A model-only bounded job cannot be admitted without unrelated adapters; admission must derive required methods from the job. |
| Grant publication | `src/aar/mcp/server.py:174-255` defines published reference grants without workbench execute; `:258-260` adds only the successor grant-ID acceptance mapping | `b290a614c678c35df7be0ac40025b811b55be6312547689583a90d5845d61742` | The tool can recognize the grant ID, but normal reference-context publication does not issue it. |
| Test-only availability | `tests/test_mcp_workbench_tools.py:112-123` manufactures `caller_driver` availability for tests | `95b2f88ed317e2d4a11051bcfd74bfba444d0675acba98bb8746aa6e72bbb010` | Component tests prove contract handling, not an installed production adapter. |

## 3. Live admission observations

The installed v0.5 host was probed through native MCP and yielded:

- registry schema versions `1` through `5` only;
- `rlm.workbench.execute` reference context denied as `GRANT_DENIED`;
- all six broker method rows reported `backend_kind=unconfigured`, `configured=false`, and `evidence_tier=unknown`;
- exact source suite result `743 passed, 5 skipped, 1 warning`;
- provider-backed contender calls `0`.

The component suite is therefore PASS while live provider-backed workbench admission is BLOCKED. These are different claims.

## 4. Baseline gap register

| Gap | Exists in v0.5 | Missing product closure |
|---|---|---|
| Registry v6 | DDL, schema, migration helper, attestation model | reviewed operator command, exclusive-owner proof, durable cutover state, readback, package profile |
| Planner | synchronous injectable test/native protocol | durable root-planner ticket and successor resume path for caller delegation |
| Caller work | claim, send-start, cancel-before-send, commit, reconcile | production activation and root-planner integration |
| Backend capability | strict capability projection | verified profile-to-component binding and job-scoped required-method admission |
| Authority | successor grant-ID recognition | principal/session/budget-scoped publication after activation |
| Route evidence | AR-MB route and usage receipts | exact workbench receipt closure proving requested and effective Luna/max treatment |
| Operator UX | ordinary supervisor/MCP launchers | `aar-admin` plan/apply/status/reconcile plus activation-profile launcher binding |

## 5. Claim boundary

This baseline is evidence for planning, not proof that AR-PRW exists. The first implementation task must re-run these source observations against its clean base and mark every changed citation as superseded or still applicable.
