# Adaptive Agent Runtime public plugin

**Repository status:** the `0.6.0a2` release contract includes the remote MCP server, public skill
source, deterministic plugin builder, container recipe, and local verification scenarios. Its
in-tree contract is [`profiles/release-status-v1.json`](../profiles/release-status-v1.json). This
source does not establish that the target tag, GitHub prerelease, or
`adaptive-agent-runtime-v0.6.0a2-release-receipt.json` exists; publication and exact source,
artifact, CI, install, host, review, tag, and asset-readback evidence require external readback. It
also does not establish a production endpoint, reviewer account, OpenAI approval, or official
Plugin Directory publication; those require separate external authority.

Adaptive Agent Runtime is AAR's curated public-directory product for ChatGPT and Codex. It combines
tenant-private structured workspaces with bounded caller-delegated RLM coordination. The host that
invokes the plugin retains model, reasoning-effort, authorization, and user-facing effect authority;
AAR retains durable job state, exact model-call specifications, execution tickets, idempotency,
budgets, compare-and-set receipt commits, and deterministic continuation.

This product is intentionally separate from the local `aar-mcp` developer surface. It does not
accept provider credentials and does not send requests to model providers. The canonical
responsibility split, state machine, recovery rules, and compatibility policy are specified in
[`PUBLIC-RLM-PRODUCT-BOUNDARY.md`](PUBLIC-RLM-PRODUCT-BOUNDARY.md).

OpenAI publishes approved plugins once to the universal Plugins Directory shared by ChatGPT and
Codex. The submission portal scans a production MCP URL and collects the listing, skills, prompts,
test cases, authentication details, policies, and release notes. See the official
[packaging guide](https://developers.openai.com/plugins/build/plugins) and
[submission guide](https://developers.openai.com/plugins/deploy/submission).

## Product boundary

The public server exposes eleven tools:

| Tool | State change | Open-world effect | Purpose |
|---|---:|---:|---|
| `aar_public_capabilities` | No | No | Read the exact public surface, limits, and unsupported capabilities. |
| `aar_workspace_open` | Maybe | No | Create or reopen one named tenant-private workspace. |
| `aar_workspace_update` | Yes | No | Apply an idempotent set, delete, or integer increment at an exact revision. |
| `aar_workspace_inspect` | No | No | Read current values and the exact workspace handle. |
| `aar_operation_status` | No | No | Recover one durable workspace-operation receipt. |
| `aar_artifact_resolve` | No | No | Resolve one bounded content-addressed workspace receipt artifact. |
| `aar_rlm_start` | Yes | No | Create an idempotent bounded RLM job with one fixed host-selected route and return its first exact model-call specification. |
| `aar_rlm_claim_model_call` | Yes | No | Atomically claim one pending call and issue its fixed-route ticket before model spend. |
| `aar_rlm_commit_model_call` | Yes | No | Compare-and-set one bounded caller result and receipt against the exact ticket. |
| `aar_rlm_status` | No | No | Recover the durable RLM phase, pending call, active ticket, steps, or terminal result. |
| `aar_rlm_cancel` | Yes | No | Cancel an unclaimed job, or record cancellation while a ticketed outcome remains indeterminate. |

The public profile does not expose arbitrary Python, programmable workspaces, provider credentials,
service-managed model calls, external effects, publication, message delivery, or activation. It is
not a security sandbox. Its narrower risk boundary comes from the curated tool surface, verified
OAuth identity, physically separate tenant databases, optimistic revisions, exact ticket binding,
idempotency, compare-and-set commits, and explicit size/count budgets.

The local `aar-mcp` stdio profile remains available for trusted developer environments and keeps
its full developer surface. Installing or publishing this public plugin must not replace that local
profile or imply that their capabilities are equivalent.

## Caller-delegated RLM flow

One main-agent-assigned RLM job uses this protocol:

1. Choose one model and optional reasoning effort that the current host is actually authorized to
   invoke for the whole job.
2. Call `aar_rlm_start` with that route, or read `aar_rlm_status`, and obtain the exact pending call
   specification.
3. Call `aar_rlm_claim_model_call` before executing the model. The returned ticket binds the job,
   job spec, call, prompt digest, fixed route, executor, bounds, and expected revision.
4. Execute exactly the ticketed prompt through the host with tools disabled and without adding
   unrelated instructions.
5. Call `aar_rlm_commit_model_call` with the exact ticket, bounded output, actual route fields, and
   only the usage or receipt identifiers the host really returned.
6. If the response contains another pending call, repeat from step 3 with the same job route.
   Otherwise consume the terminal result.

All steps in one job use the start-time model and effort. To assign a different route, the main
agent starts a separate job. AAR does not assume a provider catalog and never fabricates a model
name, effort, usage number, or provider receipt. When a host cannot select a named route, it may
honestly start with the current callable route as `host-current` and omit an effort it cannot
observe.

Claiming happens before model spend so that two restored callers cannot both execute the same
pending call and discover the conflict only at commit time. A ticket that may have executed but has
no authoritative result remains indeterminate and must not be blindly replayed. Cancellation,
restart, reconnect, or timeout cannot silently create a second model execution.

Caller-reported result and usage fields are integrity-bound to the ticket and durable state, but
they are not independently verified provider telemetry. A deployment that needs provider-authentic
receipts must add a trusted host attestation adapter; that is distinct from making AAR a credentialed
provider client.

The legacy fixed Hermes MCP Sampling route remains temporarily available only for compatibility and
emits a deprecation warning when explicitly enabled. It is not the public product path. Removal
requires migration and regression proof that supported Hermes workflows can complete through the
caller-delegated protocol without losing restart, cancellation, or receipt semantics.

## Authentication and tenant identity

`aar-mcp-public` is an OAuth 2.1 resource server. It does not implement an authorization server.
The configured identity provider must:

- publish OAuth authorization-server metadata;
- support PKCE and one OpenAI-compatible client identification or registration route such as
  Client ID Metadata Documents, dynamic client registration, or an explicitly configured client;
- echo and bind the MCP `resource` value throughout authorization;
- issue asymmetric signed JWT access tokens with exact `iss`, `aud`, `exp`, and non-empty `sub`
  claims plus the `aar:rlm` and `aar:workspace` scopes;
- publish the matching JWKS over HTTPS.

The server accepts only configured RSA, ECDSA, or EdDSA algorithms and rejects symmetric JWT
algorithms. The configured issuer string is compared exactly with the token claim. The audience is
required to exactly equal the canonical MCP resource URL. Public configuration URLs reject embedded
credentials, queries, and fragments. Follow OpenAI's current
[plugin authentication requirements](https://developers.openai.com/plugins/build/auth), including
the UserInfo and verified-email requirements if workspace-domain restrictions will be offered.

After verification, AAR hashes the exact issuer and subject into an opaque path-safe tenant key. It
does not place the raw subject or bearer token in the SQLite runtime. Each tenant has a separate
database, runtime owner, principal, and session. Workspace operation, workspace artifact, and RLM job
lookups occur only inside that tenant database.

The MCP SDK publishes path-bound protected-resource metadata at
`/.well-known/oauth-protected-resource/<mcp-path>`. The response includes the resource identifier,
authorization server, required scopes, resource name, documentation URL, and bearer-header method.
Unauthenticated MCP requests return `401` with a Bearer challenge.

## Bounded storage and runtime ownership

Default server limits are explicit and appear in `aar_public_capabilities`:

- 1,048,576 bytes per MCP request body;
- 64 workspaces per tenant;
- 256 keys per workspace;
- 65,536 canonical JSON bytes per value;
- 262,144 canonical JSON bytes per workspace;
- 1,048,576 bytes per disclosed workspace receipt artifact;
- 256 retained RLM jobs per tenant;
- 16,384 UTF-8 bytes per RLM query;
- 8 model calls per RLM job;
- at most `2 * max_model_calls + 1` retained claim/commit/cancel command markers per RLM job;
- 32,768 requested output tokens per call;
- 262,144 caller-result bytes per call;
- 128 simultaneously open tenant runtimes in one process.

The runtime pool leases a tenant while a tool executes. At capacity it evicts only an idle
least-recently-used runtime, closes its databases and ownership lock, and reopens persisted state on
the next request. It never evicts a leased runtime. These process limits do not replace deployment
rate limits, volume quotas, backups, retention, account deletion, abuse controls, or operational
monitoring; the production operator must define and publish those controls.

RLM storage includes the bounded query, generated prompts, tickets, in-bound completions,
caller-reported route/usage fields, and optional opaque host-receipt identity/digest. Claim, commit,
and cancel idempotency rows store a fixed response digest marker rather than another full job view,
and their count is derived from the immutable job call budget. The source candidate has no per-job
erase tool. Public submission therefore requires a verified authenticated account deletion/retention
path plus privacy and retention pages that describe this data accurately.

The current persistence topology permits one authoritative process for a given data root. A
production deployment must therefore run one active service owner, or route each tenant consistently
to one exclusive owner and persistent volume. Do not place replicas with independent data roots
behind a non-sticky load balancer, and do not assume that a shared filesystem turns SQLite ownership
into a distributed store. A horizontally shared database/backend requires a separately designed and
verified adapter.

## Build and run the container

Build from the repository root:

```bash
  docker build -f deploy/public/Dockerfile -t adaptive-agent-runtime:0.6.0a2 .
```

The default image runs as UID/GID `10001`, owns `/var/lib/aar` with mode `0700`, exposes port
`8000`, uses `/healthz` for its container health check, and installs only locked production
dependencies with a non-editable `uv sync --locked`. Override both the `PYTHON_IMAGE` and `UV_IMAGE`
build arguments with organization-approved digest-pinned images for a release build.

Copy `deploy/public/public.env.example` to a private deployment environment and replace every
reserved `example.com` URL. Required settings are:

- `AAR_PUBLIC_DATA_ROOT`
- `AAR_PUBLIC_ISSUER_URL`
- `AAR_PUBLIC_RESOURCE_URL`
- `AAR_PUBLIC_AUDIENCE`
- `AAR_PUBLIC_JWKS_URL`
- `AAR_PUBLIC_DOCUMENTATION_URL`
- `AAR_PUBLIC_REQUIRED_SCOPES=aar:rlm,aar:workspace`

Terminate public HTTPS at a trusted ingress and forward only the configured MCP path plus the
well-known and health routes. Keep the service data volume private and persistent. Do not enable
`AAR_PUBLIC_ALLOW_INSECURE_DEV` in a public deployment.

Before starting, read back non-secret configuration:

```bash
  docker run --rm --env-file deploy.env adaptive-agent-runtime:0.6.0a2 --check-config
```

The readback reports only whether a domain-challenge token is configured; it never prints the token.
The production process starts with the same environment and no command arguments.

## Domain verification

When the OpenAI portal issues a domain challenge, inject its exact value as
`AAR_PUBLIC_OPENAI_CHALLENGE_TOKEN` through the deployment secret mechanism. The server then returns
only that value, without JSON or a newline, from:

```text
/.well-known/openai-apps-challenge
```

Remove or rotate the value after the portal workflow according to the operator's release policy.
The repository contains no real challenge token.

## Build the plugin and submission packet

Use real public URLs only. The builder rejects HTTP, localhost, loopback, local-only hostnames,
userinfo, query strings, fragments, and an MCP URL without a non-root path. It refuses to overwrite
an output directory.

```bash
aar-public-plugin-build \
  --output-root ./dist/public-plugin-candidate \
  --mcp-url "$AAR_PUBLIC_MCP_URL" \
  --website-url "$AAR_PUBLIC_WEBSITE_URL" \
  --support-url "$AAR_PUBLIC_SUPPORT_URL" \
  --privacy-policy-url "$AAR_PUBLIC_PRIVACY_URL" \
  --terms-of-service-url "$AAR_PUBLIC_TERMS_URL" \
  --developer-name "Verified publisher name"
```

Set those variables to publisher-controlled public HTTPS URLs first. The builder rejects known
documentation/special-use domains and non-global IP addresses; domain control and reachability still
require the portal and deployed checks. The output contains:

- a repo-local marketplace and installable plugin for Codex/ChatGPT desktop testing;
- `.codex-plugin/plugin.json` and one remote `.mcp.json` connection;
- the scoped `aar-public-runtime` skill and brand assets;
- a deterministic skill ZIP for the portal;
- five positive and three negative reviewer test cases;
- a submission field packet with exact runtime/plugin versions and unresolved external gates;
- a SHA-256 manifest covering every generated file.

The skill ZIP uses sorted paths, fixed timestamps, fixed permissions, and stored entries. Building
the same source and settings into two fresh roots must produce byte-identical trees.

Validate the generated plugin with the current `plugin-creator` validator, then install it only as a
local candidate for fresh-task testing. A local marketplace install or successful scan is not public
publication.

The retained Codex acceptance built an exact wheel from commit
`0ccfe81fd1e5682071de7ef5c8fd4474283dd9c2`, generated two byte-identical 13-file plugin trees,
and installed a local derivative that changed only the cachebuster and authenticated MCP URL. A
fresh ephemeral `gpt-5.6-sol` / `max` task then made ten successful public AAR calls, persisted a
workspace value, and completed one exact start/claim/host-execute/commit/status RLM lifecycle. The
final caller provenance is deliberately `caller_reported`; this is not provider-signed evidence.
Because the write tools carry destructive annotations, a headless run without approval stopped at
the host boundary. The passing run used Codex `--approve-for-me`, which selects the
`workspace-write` sandbox; it did not bypass approvals or sandboxing. Interactive clients retain
their own approval policy.

An independent final review subsequently bound the exact candidate commit, wheel/plugin evidence,
and local acceptance receipt. It covered all 77 required cells and passed with no actionable
findings. That is a local release-candidate verdict, not evidence for any external gate below.

## Submission gates outside this repository

Do not submit until all of the following are true:

- the production HTTPS endpoint and OAuth flow work from the public internet;
- the publisher controls and verifies the MCP domain;
- a reviewer account works without MFA, SMS, email confirmation, or private-network access;
- public website, support, privacy, retention/deletion, and terms pages match actual behavior;
- production rate limits, abuse controls, storage quotas, backup, purge, and incident response are
  operating;
- the deployed routing topology preserves one authoritative owner and persistent database per
  tenant;
- the publisher identity is verified in the same OpenAI organization used for submission;
- the submitter has Apps Management Write permission;
- all five positive and three negative cases pass against the deployed endpoint;
- a fresh supported Codex host against the deployed endpoint completes an actual model call through
  the caller-delegated loop, survives process restart, and demonstrates ticket-conflict and
  unknown-outcome fail-closed paths;
- a separate fresh ChatGPT host completes the same public contract before ChatGPT compatibility is
  claimed;
- regional availability and legal attestations are approved;
- the portal scan reports the expected eleven tools, output schemas, annotations, and skill snapshot.

This plugin has no custom UI. A UI content security policy and screenshots are therefore not part of
this candidate. If UI is added later, its domains, CSP, screenshots, privacy behavior, and tests
require a new reviewed plugin version.

Submission starts OpenAI review; approval and the publisher's later publish action are separate
events. Do not describe a draft, scan, or approval as public availability.

## Maintainer verification

Run the focused public checks during development:

```bash
uv run --locked ruff check src/aar/mcp/public_*.py src/aar/compat/public_plugin.py tests/test_public_*.py
uv run --locked pytest -q tests/test_public_auth.py tests/test_public_runtime.py tests/test_public_rlm.py tests/test_public_mcp_server.py tests/test_public_plugin.py
```

The HTTP scenario uses a real loopback TCP listener and a Bearer-authenticated Streamable HTTP MCP
client. It exercises workspace persistence and the caller-delegated ticket/commit lifecycle with a
bounded synthetic caller observation across a server restart; that scenario does not itself invoke
an LLM. Physical model execution requires a separate fresh-host receipt, whose route provenance is
no stronger than the host evidence it binds. Only a deployed endpoint plus portal/reviewer execution
can supply the remaining live end-to-end evidence.
