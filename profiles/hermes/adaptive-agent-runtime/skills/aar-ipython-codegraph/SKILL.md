---
name: aar-ipython-codegraph
description: Organize and navigate source-like artifacts exported from AAR programmable IPython workspaces with an already available external CodeGraph installation. Use when an agent needs structural code navigation across resolved AAR cell, module, traceback, or source artifacts without making CodeGraph an AAR runtime dependency or indexing live runtime state.
---

# AAR IPython CodeGraph

This is an optional external workflow. It is not a workspace backend, broker provider, authority
source, or part of AAR core. Never install CodeGraph, alter host configuration, or initialize an
index merely because this skill is present; use it only when the caller chose the workflow and a
CodeGraph command or tool is already available.

## Establish the artifact boundary

1. Use `$aar-operations` and `aar_capabilities` to confirm the current runtime, tool surface,
   artifact schema, and operation binding.
2. Obtain complete artifact references from a programmable workspace result, checkpoint manifest,
   RLM trace, or operation receipt. Resolve each selected reference through `aar_artifact_resolve`
   with an explicit `max_bytes` limit and `allow_redacted=true` only when disclosure is authorized.
3. Verify artifact ID, SHA-256 digest, media type, size, creating operation, and redaction state
   before decoding `content_base64`. Reject any mismatch.
4. Materialize only source-like, non-secret content into a task-local navigation directory. Keep a
   manifest mapping each safe relative filename to its original AAR artifact reference and digest.

Do not index the live `.aar` database, SQLite WAL files, worker sockets, checkpoint stores,
credential locations, private evidence trees, runtime homes, or an entire user directory. Do not
materialize arbitrary pickle data. A checkpoint exclusion is not permission to disclose it.

## Build or refresh the external graph

- Prefer a current task-local `.codegraph/` index. Use the callable CodeGraph exploration tool when
  the host exposes it; otherwise use the installed `codegraph status`, `codegraph init`,
  `codegraph sync`, and `codegraph explore` commands.
- Initialize only the bounded materialized source root. Refresh after its manifest or source bytes
  change. Keep `.codegraph/` ignored and outside AAR evidence, release, and checkpoint contracts.
- Ask structural questions with exact artifact filenames, symbols, or call paths. Use the graph to
  navigate, then read the referenced artifact bytes before making a correctness claim.
- Preserve artifact digests beside any answer derived from the graph. CodeGraph catalog, status,
  or index presence proves navigation metadata only; it does not prove artifact authenticity,
  runtime activation, execution, authority, or delivery.

## Keep integration optional

Compare this workflow with the same artifact set and queries without CodeGraph. Report retrieval
quality, graph coverage, elapsed time, index cost, unresolved uncertainty, and safety exclusions.
Do not propose an AAR provider package until repeated benchmark evidence shows a material benefit
over the external workflow.

AHC integrations consume the same AAR artifact references and digests. An AHC-native adapter may
automate authorized export or retention, but it must not change the AAR artifact contract. NOOA is
design input only and is not an adapter, backend, or dependency in this workflow.
