# AAR v0.6.0a1 Hermes host — frozen acceptance plan

Status: **FROZEN BEFORE REMAINING IMPLEMENTATION**

This plan is the release claim budget for GitHub issues #2, #3, and #4 plus the standalone Hermes
host/session-grant milestone. A green subset does not imply release, installed-host, or live-cutover
acceptance.

## Scope guard

AAR core and the provider-ready/caller-work contracts remain host-neutral: any compatible agent
harness may install and operate them. `aar-hermes-mcp` and `aar-hermes-authority` are one optional
host adapter used for the local acceptance/cutover in this milestone; they are not the provider,
package core, or required path for other agent hosts.

In scope:

- public, credential-free operator commands that deterministically derive an exact install-candidate
  receipt from the released wheel and issue a target-bound host activation intent from explicit
  host-owned inputs;
- one complete documented path from release assets to a fresh absent v6 runtime, provider-ready
  startup, Hermes attachment, explicit authority, real caller-delegated provider execution, restart,
  durable reconciliation, and live cutover;
- current `0.6.0a1` status/compatibility/install documentation and anti-drift tests;
- the standalone Hermes launcher, private authority protocol, current memory-grant admission, package,
  generated assets, release, persistent clean install, and local Hermes cutover.

Out of scope:

- any new public MCP tool/method/schema, durable session-grant store, activation schema, provider
  credential path, external-effect executor, automatic grant policy, old-root migration/adoption,
  generic daemon/service installer, PyPI publication, or official plugin-directory claim;
- modification, promotion, archival, removal, cleanup, or replacement of any old runtime root.

Reopen trigger: only an executable contradiction showing that one required acceptance row cannot be
satisfied through the existing installer, provider-ready startup, caller-work, private supervisor,
release, or Hermes MCP seams may amend product scope. Test altitude never authorizes a new product
mechanism.

## Acceptance matrix

| ID | Claim / old defect that must fail | Tier | Required evidence before release |
|---|---|---:|---|
| AC-01 | Package identity is exactly `0.6.0a1`; `aar-hermes-mcp`, `aar-hermes-authority`, and required operator commands install from the wheel. | T0/T2 | metadata/version tests; build wheel/sdist; isolated non-editable install; every entry point `--help` and representative call. |
| AC-02 | Frozen public MCP v8 names, descriptions, input/output schemas, tool count, and compatibility bytes do not drift. | T0/T2 | canonical MCP/host generate+verify; cross-surface digest join; real SDK list/serialization; public compatibility tests. |
| AC-03 | Every public provider-ready mutation, including all five caller-work successors, rejects absent/static/wrong/stale/foreign/expired/over-budget grants before its first durable write. | T1/T2 | complete mutating-handler inventory; common-seam tests; provider-ready integration negatives; static `aar_reference_context` denial; pre/post durable-state equality. |
| AC-04 | Install, startup, Ready, attach, capabilities, reference context, and ordinary requests never auto-issue a session grant. | T1/T3 | zero-grant lifecycle assertions across install/start/attach and generation-1 native probe. |
| AC-05 | Private issue/revoke is same-owner authenticated, generation/process/credential/deadline/policy/budget/TTL fenced; only supervisor clock/policy can approve. | T1/T2 | strict protocol round trips; wrong credential/UID/generation/digest/deadline/principal/capability/TTL/duplicate tests; authenticated integration success. |
| AC-06 | A post-send lost/malformed/mismatched control response is `INDETERMINATE`, preserves the grant ID, and is never silently retried. | T1 | sent-then-lost and mismatched-receipt tests plus CLI exit-code/readback tests. |
| AC-07 | Restart creates a successor generation with an empty grant map; old IDs fail while durable activation and RLM state remain. | T2/T3 | supervisor restart lifecycle: old grant denial, new explicit issue, activation readback unchanged, durable job reconciliation. |
| AC-08 | Launcher reuse accepts only the exact package/process/protocol/runtime/dispatcher/capability/activation/route owner and never replaces an unknown or mismatched live owner. | T1/T2 | one mismatch negative per binding family; concurrency convergence; live capabilities/readback equality. |
| AC-09 | `reference-driver` is deterministic-only; `host-caller-driver-v1` cannot perform a service-owned provider send. | T1 | exact catalog binding positives; unknown driver negative; direct caller-driver broker send fails closed. |
| AC-10 | Real host-provider work is agent-harness-neutral and uses durable claim → mark-send-started → host physical provider call → exact response/usage receipt commit; no provider credential enters AAR. | T3/T4 | fresh installed caller-delegated RLM job through the selected compatible host adapter, exact ticket/revision/route/receipt lineage, successful terminal status, sanitized evidence. |
| AC-11 / #2 | An external operator can derive the exact `aar.install-candidate-receipt.v1` from the released wheel and a caller-declared source commit without private tooling. The receipt deterministically binds exact wheel bytes to that declaration but does not claim the wheel proves Git source; malformed source identity or wheel/member tamper fails closed. | T1/T2 | public CLI positive fixture; one-axis malformed-source and wheel/member negatives; repeated exact input is byte-identical; documentation and release status identify the source as declared and not Git-verified. |
| AC-12 / #2 | An external operator can issue a self-digested target-bound `aar.host-activation-intent.v1` only from explicit host-owned inputs; the command does not install or activate. | T1/T2 | public CLI positive fixture; missing/unknown/contradictory host-input negatives; target/route/policy/candidate digest assertions; target remains absent. |
| AC-13 / #2 | Release asset → absent root → install → activation verify → provider-ready supervisor → Hermes attach is one documented reproducible sequence. | T3 | isolated exact-wheel rehearsal in a fresh persistent root; canonical receipts and Ready/capabilities readback; no implicit service or hidden input. |
| AC-14 / #3 | Shipped Hermes CLI reaches configured provider-ready capability rows and explicit generation-bound mutation authority. | T3 | native MCP `aar_capabilities` and `aar_rlm_workbench_capabilities` show exact configured caller route/method evidence; explicitly granted mutation succeeds. |
| AC-15 / #4 | Current README/status/compatibility/install/release surfaces name `0.6.0a1`, 38 tools, current authority and route boundaries; `0.4.0a6` pages are explicitly historical rather than current guidance. | T0 | current-release consistency test driven from package metadata/tool manifest; link/path inventory; stale-current-version negative fixture; localized README parity check. |
| AC-16 | Exact candidate passes repository lint, full tests, packaging, generated/public/spec/lock/hygiene gates with no credential/private-path leakage. | T0–T2 | final-byte gate ledger with raw result paths, exact counts, skips and warnings; clean diff check and scoped secret/path scan. |
| AC-17 | Exact candidate receives a fresh independent Claude CLI `claude-opus-5`, effort `high`, read-only PASS bound to candidate/manifest/evidence hashes. | Review | model probe must report canonical `claude-opus-5`; safe/plan mode with Read/Glob/Grep only; structured PASS/BLOCKED report; main-agent hash and finding verification. |
| AC-18 | PR head equals reviewed candidate, remote CI is green, and the external release receipt is the sole official source-identity authority: merge/tag target, source commit/tree, wheel/sdist/candidate-receipt digests and downloaded assets all bind the exact released bytes; issues #2/#3/#4 close traceably. | Release | PR/check/merge readback; annotated tag target; GitHub prerelease; release-receipt positive; one-axis tag-target, source, wheel, sdist, candidate-receipt and asset-readback mismatch negatives; downloaded wheel/sdist/candidate/release receipt byte readback; issue state/comments. |
| AC-19 | A new persistent canonical v6 root—not `/tmp` and not any old root—passes G0, real IPython, caller-delegated provider, restart, durable RLM, and provenance checks. | T3/T4 | exact release-asset install receipt; process/generation; native calls before/after restart; old-root before/after identity unchanged. |
| AC-20 | Hermes cutover changes only the intended MCP integration pointer, then fresh native readback and one explicit mutation succeed; retained v0.5 rollback remains usable and untouched. | T4 | sanitized before/after config digest and pointer fields; Hermes restart/readback; current generation; mutation receipt; rollback command/root readability; no rollback execution unless needed. |

## Ordered gates and stop rules

1. **Contract gate:** this plan and the normative SDD are internally consistent; no implementation
   may weaken a row to obtain green output.
2. **Focused source gate:** AC-03 through AC-12 discriminating tests and touched Ruff/type/static checks.
3. **Public/generated gate:** AC-02 and AC-15 regenerate in dependency order and cross-join exact bytes.
4. **Whole-source gate:** AC-16 full repository tests/lint/build/hygiene on stabilized bytes.
5. **Candidate freeze:** commit exact source; build immutable review manifest and evidence index; no
   release/install/cutover actions yet.
6. **Claude independent review:** one batch-complete full review wave. Limits:
   `maxFullReviewWaves=1`, `maxSameCauseAttempts=2`, `maxPrimaryReviewers=1`,
   `maxNarrowAuditors=0`. A material finding blocks release. If repaired, one finding-scoped
   exact-hash closure invocation is allowed; it is not a second whole review wave. Any unreviewed
   dependent byte remains blocked.
7. **PR/CI/release:** only an exact reviewed PASS candidate may be pushed, merged, tagged, or released.
8. **Installed pickup:** clean-install the exact release artifact once into a new persistent root and
   execute AC-13/14/19. `/tmp` qualification is never persistent acceptance evidence.
9. **Cutover:** only after installed pickup passes; preserve rollback evidence and perform AC-20.

Fail closed and stop advancement on public MCP/schema drift, implicit grant issuance, current-grant
bypass, fabricated provider receipt/usage, hidden/private install input, required old-root mutation,
indeterminate control presented as success, candidate hash drift after review, red/unknown required CI,
or inability to prove exact released/installed/live identity.

## Claude review brief requirements

The review bundle will exclude credentials, `.env`, private runtime data, personal identifiers, and
live receipts. It will contain exact candidate/manifest/evidence hashes, changed-file inventory,
issues #2/#3/#4 acceptance mapping, authority and lifecycle invariants, test commands/counts/raw-result
paths, known limits, rollback/cutover gates, and a structured decision schema requiring:

- `decision`: `PASS` or `BLOCKED`;
- exact candidate and manifest hashes;
- batch-complete findings with severity, scope classification, file/line evidence, sibling paths, and
  required regression;
- verification gaps and concise rationale.

Claude is a read-only reviewer, not implementation, release, deployment, or cutover authority. Main
session independently verifies its model identity, hashes, findings, and final release gates.
