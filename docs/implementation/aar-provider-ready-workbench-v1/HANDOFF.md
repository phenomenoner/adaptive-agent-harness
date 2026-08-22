# AR-PRW Implementation Handoff

**Status:** `a1a_focused_green_a1b_packet_pending`

**Control epoch:** `2026-08-22T19:17:55Z`

## State

- Historical reviewed SDD generation remains structurally valid: complete custody-tree digest `sha256:bb0287e1718033c773e763096b202591307458e1524ab8de9ea8d5a29039afee`; validator semantic-artifact digest `sha256:30dbc6ffb52a289792d7b37d782ce8b537fd0741957af9854b9538a863a01047`.
- Generation-5 exact bytes are historical and rejected. Dual batch `deleg_b7d68ad4` verified staged tree `eed8ab17a6436f47d9bf3df9fc26ada6a129533a`, S0 semantic tree `sha256:932e362b368bc66eee77b8d9c9e270c106299011b3aaffa315d918b4b3671082`, receipt `sha256:fa22511d3627c96509a4625bad24ef42a148bf3fbec0adf3c35c65c4c51a919e` and A1a packet `sha256:649b3a13801842bc52889a299180c1dec9225d637e4e827238b9cc0845716e6f`, then returned S0 `BLOCKED` and packet `INCOMPLETE`; those bytes were unstaged before amendment.
- Generation-6 exact staged tree `327bc7357ced82909e50cbefcb82be427a051fe2` is historical and rejected. Independent batch `deleg_9d1b67c8` returned S0 `BLOCKED / BATCH_COMPLETE` with eight blockers and A1a `BLOCKED / BATCH_COMPLETE` with one planner-field collision; the index was unstaged without deleting worktree bytes.
- Generation-7 exact staged tree `26761c923883088c97e9b75f02f26fc4cdc17ec0` is historical and rejected. Independent batch `deleg_5f4ad782` returned identity PASS and A1a `PASS / BATCH_COMPLETE`, but S0 `BLOCKED / BATCH_COMPLETE` with three findings; the packet PASS is non-transferable and the index was unstaged without deleting worktree bytes.
- Generation-8 exact staged tree `1e5d511fb70fd80967cf147cd7d7ec81347f71fd` is historical and rejected. Independent batch `deleg_e9f44138` returned identity PASS and A1a `PASS / BATCH_COMPLETE`, but S0 `BLOCKED / BATCH_COMPLETE` with two acceptance contradictions; the packet PASS is non-transferable and the index was unstaged without deleting worktree bytes.
- Generation-9 exact staged tree `866e05547ec8ddc56f6370e1d05a7b0dbbce2a15` is historical and rejected. Independent batch `deleg_b25672f4` returned identity PASS and A1a `PASS / BATCH_COMPLETE`; S0 returned `BLOCKED / BATCH_COMPLETE` solely because four promised A-ADM-004 validator axes were not independently enforced. The semantic row itself and all prior closures passed; the packet PASS is non-transferable.
- Generation-10 S0 at semantic tree `sha256:cf6874ee870a1e2bcca3fb83b785213ea8b2f0205e4a54afaf9161b609925ab1` received independent `PASS / BATCH_COMPLETE`, `findings=[]`, with no scope expansion. A1a rev2 packet `sha256:6d2326a68cdbaf7bad0326cd5d28207ffefa9a7f3ceb855b6ecf3d85912798ba` then received fresh independent `PASS / BATCH_COMPLETE`, collection-schema discriminator PASS, collision PASS and `findings=[]` in `deleg_b45d4517`. Reviewed candidate commit is `06bfdc5702c21d3e81d53cce26b49bd38779870e`; only terminal evidence commit and task-start/collision freeze remain before writer dispatch.
- A1a product slice is focused-green at commit `3ca321fd0df5a20a969965a0c1705fdd02dc83b6`: exactly `src/aar/provider_ready_models.py` and `tests/test_provider_ready_models.py`, assertion-level and behavioral RED evidence, 15 focused tests PASS, full Ruff PASS and W291/W293 PASS. PMO repaired a test-only strict-list false positive and added explicit min/max/max+1 collection checks before final green; production model bytes were unchanged by that central review. This prerequisite slice closes no acceptance row alone.
- Active implementation worktree is on `codex/aar-provider-ready-workbench-v1-impl`; product HEAD is `3ca321fd0df5a20a969965a0c1705fdd02dc83b6`, descended from accepted product-source ancestor `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1`, with A1a limited to its exact two-file ownership.
- Exact-base Linux-native baseline is terminal: `743 passed, 5 skipped, 1 warning`; Ruff `All checks passed!`; sanitized digest-bound receipt `evidence/baseline-linux-native-summary.json`.
- DrvFS attempt is separately classified `ABORTED_ENVIRONMENT_DIAGNOSTIC`; its launcher/supervisor T3/ENV-5 risk remains open and was not washed out by the Linux-native PASS.
- Luna A0 seam map completed and PMO source-check confirmed A1a's two-path slice does not collide with shared generators/registries/assets.

## Governing discovery

Generation-9 closed the substantive acceptance contradictions without expansion. Independent review confirmed the row semantics, crash boundary, outbox/T5 closure and prior authority seams, but counterfactual probes showed the validator did not independently enforce four A-ADM-004 axes: both separately digested planner profiles, nonzero budgets, retired factory/profile/digest and same-generation instantiated-adapter health.

Generation-10 changes only `validate_spec.py` by adding those four exact discriminators. The acceptance matrix and every product/test authority remain byte-identical. Normal validator runs PASS twice with byte-identical receipt; four isolated mutation probes that also update the expected matrix digest each fail only on the removed marker. No row, requirement, daemon, DB, provider lane, MCP surface, product source, test lane or writable path was added.

Generation-10 A1a rev1 review found one packet-only gap: empty adapters and seven adapters can be rejected by planner/uniqueness invariants even when emitted collection bounds are missing. Rev2 therefore requires direct `model_json_schema()` assertions after local `$defs/$ref` resolution for all four bounded collections: adapters `1..6`; route profile IDs, principal patterns and capabilities `1..64`. Generic rejection or error typing is explicitly insufficient. Product ownership and focused T0/T1 scope remain unchanged.

The A1a packet still owns exactly `src/aar/provider_ready_models.py` and `tests/test_provider_ready_models.py`. S0 and rev2 packet have independently PASSed; no writer may start until this terminal evidence is committed and the resulting exact task-start parent passes the final allowlist/collision check.

## Next exact actions

1. Derive and independently review a bounded A1b packet from the frozen SDD; do not invent shared registry/generator/package authority.
2. Freeze an exact A1b task-start parent and exclusive source/test ownership.
3. Dispatch one Luna/max A1b writer only after packet completeness and collision gates pass.

## Release boundary

No intermediate phase may install, push, tag, publish, or release. After **all non-live implementation phases** and mandatory exact-candidate gates converge, PMO will:

1. install the completed successor candidate;
2. dispatch Luna/max installed-candidate verification;
3. update final docs and evidence;
4. verify the configured remote is `https://github.com/phenomenoner/adaptive-agent-harness`;
5. prepare the external GO/HOLD packet; push, PR, tag and GitHub release still require CK's separate authorization.

T4 live provider calls and production cutover remain separately gated and are not inferred from the all-phase release instruction. T5 paired execution is absent from this release authority.

## Resume boundary

Do not dispatch A1b until its bounded packet, exact task-start parent and exclusive path collision check pass. A1a evidence may be reused while commit `3ca321f` and its two files remain unchanged; any shared-registry/schema/generator/package need is a stop-and-reopen condition.
