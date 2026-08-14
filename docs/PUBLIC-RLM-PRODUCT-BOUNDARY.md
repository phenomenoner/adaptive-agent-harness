# Public caller-delegated RLM product decision

**Status:** accepted architecture for the current public Plugin Directory workstream; implementation
and release evidence are required before the expanded candidate is ready for submission.

**Supersedes:** the earlier assumption that any public RLM capability must be a separate
service-managed provider product.

## Decision

Adaptive Agent Runtime's public plugin contains two tenant-scoped capabilities:

1. bounded durable structured workspaces; and
2. caller-delegated RLM coordination.

The public RLM mode is part of the same plugin because AAR does not own or execute a provider call.
AAR persists the RLM job, exact prompt, call identity, execution ticket, budgets, idempotency state,
bounded caller observation, and deterministic continuation. The authenticated main agent or host
chooses one model and optional reasoning effort when it starts the job, then executes every model
call in that job through the same route it is already authorized to use.

Provider credentials, provider endpoints, provider billing authority, direct provider sends,
provider retry, provider settlement, and final delivery remain outside the public AAR service.

~~~mermaid
sequenceDiagram
    participant U as User
    participant H as Main agent / host
    participant A as Public AAR MCP
    participant M as Host-authorized model

    U->>H: Bounded RLM request
    H->>A: aar_rlm_start(executor, model, effort)
    A-->>H: Exact ModelCallSpec
    H->>A: aar_rlm_claim_model_call
    A-->>H: Immutable execution ticket
    H->>M: Execute exact prompt with tools disabled
    M-->>H: Result and any host receipt
    H->>A: aar_rlm_commit_model_call
    A-->>H: Next ModelCallSpec or terminal result
~~~

This is a caller handoff protocol, not MCP Sampling and not an AAR-owned provider gateway.

## First-principles boundary

| Responsibility | Public AAR | Main agent or host |
|---|---:|---:|
| Persist the RLM strategy and current step | Yes | No |
| Produce the exact next prompt and digest | Yes | No |
| Enforce call count, output, result, and storage bounds | Yes, for issued calls and committed observations | Enforce host/provider limits too |
| Select one model and optional reasoning effort for the job | Persist the immutable choice | Yes, at job start |
| Invoke the model | No | Yes |
| Hold provider credentials or endpoint configuration | No | Yes, under existing host authority |
| Persist the exact ticket/result binding | Yes | Supply the observation |
| Prove the physical provider, model, tokens, or charge | Only when a separately verifiable host receipt exists | Own the authoritative evidence |
| Retry an unknown physical call | No | Only under the host's own authority and reconciliation policy |
| Execute tools, code, effects, publication, or delivery | No | Only through separately authorized host capabilities |

The public MCP request never contains an API key, bearer token, provider endpoint, credential
resolver, or recovery override.

## Why this belongs in the same plugin

Workspace and caller-delegated RLM share the same trust boundary:

- the same OAuth-derived tenant identity and private database;
- the same bounded retained-data and quota policy;
- no AAR-owned provider credential or provider spend;
- no direct external effect or final delivery;
- no background provider process while a caller is thinking or executing a model;
- the same exact-reference, idempotency, restart, and tenant-isolation requirements.

The RLM capability should use a distinct `aar:rlm` OAuth scope because prompts and model outputs can
have different retention sensitivity from small structured workspace values. Both scopes can be
requested by the same plugin installation.

A separate product becomes justified only if AAR later owns provider execution, credentials,
billing, lookup, retry, or settlement. That is the service-managed mode described below, not the
current public contract.

## Implementation separation

Caller-delegated public RLM is intentionally implemented as a small public coordinator, not as a
new execution mode inside the existing service-managed `RlmEngine` or durable dispatcher. The
tenant database owns additive `public_rlm_jobs`, `public_rlm_calls`, and `public_rlm_commands`
tables. Each MCP turn performs one short local transaction and returns; no transaction, worker,
attempt lease, provider session, or process remains active while the host executes a model.

This separation is a product invariant:

- the public coordinator MUST NOT call `ReferenceHost.submit_rlm`, `submit_rlm_durable`,
  `run_claimed_rlm`, a model-broker registry, `FakeModelBroker`, or `McpSamplingGatewayTransport`;
- a public caller-delegated result exists only after an exact execution ticket and caller commit;
- each job retains at most `2 * max_model_calls + 1` claim/commit/cancel command markers; markers
  contain only a response digest, exact retries reuse the original idempotency key, and a fresh key
  after the same terminal commit or cancellation conflicts;
- SQLite writes start with `BEGIN IMMEDIATE` and terminal/cancel transitions also predicate the
  expected revision and phase, so a stale writer cannot overwrite newer authority;
- the existing service-managed RLM engine, broker journal, dispatcher, and recovery policy remain
  a different compatibility/developer path and do not supply public readiness evidence.

Future work must not merge these modes merely to reuse code. Shared canonical hashing or bounded
model types are acceptable; shared provider execution, lease, recovery, or credential authority is
not.

## Why the claim step is necessary

A superficially smaller `start -> commit` protocol is unsafe. Two restored main-agent tasks could
both read the same pending prompt, each execute a model, and only discover the collision after both
calls consumed capacity. A commit-time compare-and-set cannot undo the duplicate calls.

`aar_rlm_claim_model_call` performs the missing pre-spend transition:

1. compare the current job revision and exact call-spec digest;
2. atomically claim that exact pending call;
3. issue one immutable ticket that inherits the job's executor, model, reasoning effort, and output
   bound;
4. reject a second claim before another restored caller can safely execute the call.

The claim step neither selects nor authorizes a provider. The caller already selected the job route
at `start`; claim only closes the duplicate-spend race and content-binds the next execution.

## Public MCP contract

The public RLM surface adds five tools to the workspace surface.

### `aar_rlm_start`

Creates or exactly replays one tenant-private bounded job. The start request includes one executor,
requested model, optional reasoning effort, and one output bound applied to every call; all remain
fixed for the
whole job. It returns either a terminal result or a `pending_call` containing:

- job, call, step, and strategy-version identities;
- exact prompt, prompt digest, and call-spec digest;
- call-count, output-token, and result-byte bounds;
- `tools_allowed=false`;
- `model_selection_owner=caller`.

The initial public strategies are:

- `single_call`: one host-executed model call;
- `iterative_refinement`: two to eight sequential calls, each based only on the original query and
  previous committed answer. Every step inherits the job's one model and reasoning effort.

### `aar_rlm_claim_model_call`

Atomically claims the exact pending call and returns an immutable content-bound execution ticket.
The ticket repeats the fixed job route and binds the job-spec digest; claim accepts no executor,
model, effort, or output-bound override.

At start, the caller may select only a route that the current host can actually invoke. If the host
exposes no model selector, it may use its current model and report `host-current` without inventing
an effort. To assign a different route, the main agent starts a separate job.

### `aar_rlm_commit_model_call`

Commits one ticket-bound caller observation and advances the strategy. Outcomes are:

- `succeeded` with bounded output;
- `failed_certain` with a bounded failure code; or
- `outcome_unknown`, which becomes indeterminate and produces no successor call.

Exact replays verify the original request binding and return the same job's latest durable view, so
an old command never reissues a stale pending call or ticket after the job has advanced. A
conflicting output, route, ticket, or idempotency binding fails closed. Reported route drift,
reported usage beyond the ticket, and an oversized result terminate the job rather than silently
continuing. An oversized observation retains only its digest and byte count.

### `aar_rlm_status`

Reads the durable job revision, phase, pending call, active ticket, bounded step summaries,
provenance level, and terminal result. Status is the recovery entry point after a task or transport
restart.

### `aar_rlm_cancel`

Cancellation before a call is claimed is certain. After a ticket is issued, AAR records
`cancel_requested` as indeterminate because it cannot stop or prove the outcome of a model call
executing under host authority. A late observation may be retained, but it never creates another
RLM step after cancellation.

## Durable state machine

~~~text
pending_model_call
    -> awaiting_caller_result        claim exact ticket
    -> cancelled                     cancel before claim

awaiting_caller_result
    -> pending_model_call            successful non-final commit
    -> succeeded                     successful final commit
    -> failed                        certain failure, route drift, quota violation
    -> indeterminate                 caller outcome unknown
    -> cancel_requested              cancel after claim

cancel_requested
    -> cancel_requested              retain at most one late observation; no successor
~~~

Required invariants:

- one canonical call specification per job and step;
- at most one ticket per call;
- the ticket binds job, job spec, step, call spec, fixed model/effort/executor route, output bound,
  and claim identity;
- caller observation is stored through one monotonic compare-and-set;
- exact start/claim/commit replays are idempotent and return the latest durable view; different bytes
  conflict;
- a claimed or unknown call is never automatically reissued;
- no dispatcher lease or background thread is held while the caller executes a model;
- tenant runtime eviction and process restart preserve the same job, call, ticket, and revision;
- no RLM terminal result can authorize an external effect or final delivery.

## Model and reasoning-effort semantics

Model and effort are per-job caller choices, not server startup configuration and not per-step
strategy controls. This matches main-agent task assignment: one assigned task has one execution
route, while another task can use another route.

Examples:

- one iterative job may use a fast model for both draft and refinement steps;
- a separate review job may use a stronger model with higher effort;
- another host may expose only its current model and no effort selector;
- a host may reject a requested route before starting because it is unavailable or unauthorized.

AAR records the requested route. It does not infer availability from a model name and does not
accept arbitrary provider configuration. An effective route may be reported after execution. If it
differs from the ticket, the job fails closed instead of presenting the drift as the requested call.

## Receipt assurance

The public protocol deliberately separates durable binding from physical provider proof.

| Assurance | Meaning | What it does not prove |
|---|---|---|
| `caller_reported` | AAR bound and retained the caller's structured route/result/usage statement | physical provider, exact effective model, tokens, cost, or charge |
| `host_receipt_bound` | AAR also content-bound an opaque host receipt ID and digest | that AAR verified the receipt, or that it is a provider-signed receipt |

A future adapter may add a separately verified host-receipt assurance level only when it has an
actual verifier. The caller cannot promote its own assertion to provider-verified evidence.

## Retained data and deletion boundary

The tenant database retains the exact bounded query, generated call prompts, tickets, in-bound model
outputs, caller-reported route and usage fields, and optional opaque host-receipt identity/digest.
Oversized outputs retain only a digest and UTF-8 byte count. The default source candidate caps each
query, call, result, number of calls, and retained job count; it does not claim that a count limit is
a retention or deletion policy.

The current MCP surface has no per-job erase tool. Before public submission, the production operator
must provide and verify an authenticated account deletion/retention path, publish the actual
retention window and backup/purge behavior, and ensure the privacy policy describes prompt and
completion transit and storage. If product or legal review requires in-product per-job deletion,
that is a separately designed destructive tool and changes the public surface; agents must not
invent deletion by editing SQLite or reusing workspace operations.

## Host compatibility

The contract is host-neutral, but the strongest supported claim is host-specific:

- a Codex host that exposes native child/model dispatch can choose a model and effort per job;
- a host without such a selector can use only its current model and must omit unknown effort and
  effective-route claims;
- if a host cannot make a bounded no-tools call or safely use the current model as the executor, it
  must report the capability as unavailable rather than fabricate a result;
- public ChatGPT and Codex compatibility require separate fresh-host execution evidence. Plugin
  installation, tool discovery, and schema visibility are not execution proof.

## Relationship to the fixed Hermes Sampling route

The existing `--hermes-mcp-sampling-luna-max` route is deprecated compatibility behavior. It is a
server-selected, session-bound route fixed to one model and effort. It is not the public product's
model-selection mechanism.

MCP Sampling was deprecated in the MCP `2026-07-28` specification era. New public RLM behavior must
not depend on it. See [SEP-2577](https://modelcontextprotocol.io/seps/2577-deprecate-roots-sampling-and-logging).

The fixed route may be removed only after all of the following are true:

1. the caller-delegated flow is implemented and tested from exact packaged bytes;
2. the Hermes/OpenClaw consumer migrates to start, claim, host execution, commit, and status;
3. a fresh installed-host scenario proves one actual model call, exact route readback, result
   commit, restart/status recovery, and no duplicate execution;
4. no active configuration, profile, test, or recovery procedure requires the flag;
5. the prior installed generation remains recoverable during the migration window.

Until those gates pass, the flag remains present but documented as deprecated, non-public, and not
eligible for a durable provider-recovery claim.

## Incident-derived classification

The known blocking-provider incident produced four distinct findings. Their relevance changes under
caller-delegated execution:

| Finding | Public caller-delegated mode | Legacy/service-managed mode |
|---|---|---|
| Attempt lease expires during blocking provider I/O | Unreachable by design: AAR returns the ticket and holds no provider lease while the host executes | Still a blocker for durable synchronous provider execution |
| Late provider writer overwrites recovery quarantine | The original writer is absent; the reusable lesson is mandatory monotonic CAS for claim/commit/cancel races | Still a confirmed implementation defect |
| MCP Sampling has no provider receipt lookup | AAR did not perform the provider call; an unknown caller outcome remains indeterminate and is never replayed | Still a recovery/admission blocker |
| Nested runner failure loses diagnostics | The benchmark runner is not on the public path; public commit failures still require bounded failure receipts and honest certainty | Still an observability blocker for that runner |

Therefore the public feature must include the race and observability lessons, but it must not import
provider lease keepers, Sampling lookup, provider reconciliation, or operator quarantine tools.

## Separate future service-managed mode

A service-managed RLM product would have a different trust and operating boundary:

- AAR or its service chooses and invokes a provider;
- an operator owns credential resolution and spend policy;
- provider request identity, lookup, settlement grace, retry prohibition, and billing receipts become
  server responsibilities;
- continuous attempt leases and late-writer reconciliation become reachable;
- background operations, abuse controls, pricing, and incident response expand materially.

That mode requires a separate product decision, threat model, deployment profile, and evidence set.
It is not a hidden fallback for the caller-delegated public plugin.

## Verification gates for the expanded candidate

Before the public RLM claim can replace the reviewed workspace-only baseline, evidence must cover:

1. exact start replay and conflicting start rejection;
2. concurrent claims where only one ticket is issued for the fixed job route;
3. exact claim, commit, and cancel replay plus conflicting request/result/fresh-terminal-key
   rejection and transactional command-history capacity rollback;
4. different routes across separate jobs and one fixed model/effort route across iterative steps;
5. wrong tenant, job, revision, call, spec digest, and ticket denial;
6. oversized output redaction, route drift, usage overflow, and unknown-outcome termination;
7. cancel before claim, cancel after claim, and late-result behavior;
8. process restart and tenant-LRU eviction between start, claim, and commit;
9. one skill-guided fresh Codex execution that performs the actual host model call;
10. a separate fresh ChatGPT execution before making a ChatGPT-specific model-selection claim;
11. exact-wheel HTTP, OAuth scope, catalog, annotations, deterministic plugin build, and public
    reviewer scenarios;
12. an independent review of the newly frozen expanded candidate.

The earlier independent PASS for the workspace-only surface remains valid evidence for those six
tools, but it is not a release verdict for the expanded caller-delegated product.

## Non-goals

The current public work does not add:

- arbitrary Python or programmable workspaces;
- provider API keys, endpoints, catalogs, gateways, or billing controls;
- AAR-owned provider calls, retries, lookup, settlement, or quarantine controls;
- hidden child agents or automatic tool use inside a model call;
- external effects, publication, activation, message delivery, or outbox ownership;
- claims that caller-reported route or usage fields are provider verified.

This boundary keeps the product useful without transferring host authority into AAR.
