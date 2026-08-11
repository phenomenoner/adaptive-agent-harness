# Project Agent Instructions

## Source of truth

- If `README.local.md` exists, read it first for local paths, repository bindings, and operator workflow. It is intentionally untracked.
- For runtime, compatibility, package-contract, or status work, read the relevant sections of
  `README.md`, `DEVELOPMENT-PLAN.md`, and the active WAL entry. A bounded documentation or test-tool
  change does not require rereading unrelated historical evidence.
- Treat current source and executed evidence as authoritative. Planning text does not prove implementation or compatibility.
- Preserve unrelated and dirty user work. Do not reset, clean, rebase, overwrite, or publish without explicit authority.

## Repository exploration

- Prefer a current repository-local CodeGraph index before broad text search once indexable source exists.
- Keep `.codegraph/` local and ignored. Index state is navigation metadata, not verification evidence.
- If CodeGraph is unavailable or supports none of the relevant files, fall back to focused repository inspection and record only material gaps.

## Context Canvas and local roadmap

- During implementation, use Context Canvas navigation at the first useful boundary when the current task has a trusted hook-provided opaque ID and the Canvas tools are available.
- Never store, guess, derive, or reuse a Canvas ID from this repository or its path. If no checkpoint exists, continue normally; create one only at an intentional implementation, milestone, review-freeze, compaction, or handoff boundary.
- Keep `WAL.md` and executed repository evidence authoritative. Canvas stores a bounded semantic map and hash-bound evidence pointers, not raw receipts or secrets.
- If `roadmap.local.html` exists, update it in the same batch when a durable phase, blocker, gate,
  compatibility row, or tracked project/coordination binding changes. Do not update it for a
  transient task objective or a process-only refinement that leaves those states unchanged.

## Delegation

- Apply `baton-fanout-skill` before any subagent or CLI compatibility worker.
- Prefer direct work for coupled architecture, authority, security, integration judgment, and final synthesis.
- After the Baton brake selects delegation, prefer an exposed Luna lane at `max` for deterministic, cheaply falsifiable inventory or stable code generation with exact paths.
- When native Luna is unavailable, use the Luna CLI bridge only for eligible, bounded code generation. Do not use that bridge for exploratory scouting, architecture, security, release judgment, or live operations.
- Give each writer exclusive paths. The main agent reviews artifacts, runs shared verification, and owns the final claim.

## Verification

- During implementation, run the smallest check that can falsify the changed contract.
- Reuse fresh results while executable bytes, relevant tests/contracts, toolchain, and required
  environment are unchanged. Documentation, WAL, formatting, and receipt-only edits do not trigger
  code or host reruns.
- For dependency, installer, profile, or packaging work: use focused current-interpreter checks in
  the edit loop; defer any required supported-Python matrix and full suite until executable bytes
  stabilize; freeze packaged docs/profile versions before one final wheel build; then run only the
  exact-wheel and fresh-host probes needed by the claim.
- Bind host compatibility to exact commits, schema/fixture digests, client versions, configurations, and executed operations.
- MCP startup, configuration, catalog visibility, or health alone does not prove tool behavior, restart recovery, authority, or final delivery.
- Do not claim Codex App, Hermes, or AHC support until its row in `HOST-COMPATIBILITY.md` has the required fresh-host evidence.
