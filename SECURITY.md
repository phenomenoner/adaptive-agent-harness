# Security Policy

## Public-alpha scope

Adaptive Agent Harness executes model-authored Python and project operations with the permissions of the account or container that runs it. **It is not a security sandbox.** Use disposable workspaces or an external isolation boundary for untrusted code.

The runtime intentionally does not own provider credentials, final delivery, or general external-effect execution. Hosts and adapters must preserve those boundaries.

## Supported version

Security fixes currently target the latest tagged public alpha only.

| Version | Supported |
|---|---|
| `0.6.x` alpha | Yes |
| `0.5.x` and `0.4.x` alpha | Security fixes only when explicitly backported |
| `0.3.x` and earlier untagged snapshots | No |

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose credentials, private data, host execution, or a bypass of generation/grant/receipt fencing.

Use GitHub's **Report a vulnerability** private advisory flow for this repository. Include:

- affected version or commit;
- host and operating system;
- minimal reproduction;
- expected and observed authority boundary;
- whether credentials, external effects, or user data were exposed;
- suggested mitigation, if known.

Do not attach real secrets, private runtime databases, or production receipts. Use synthetic fixtures and redact identifiers.

## Public remote plugin boundary

The public Plugin Directory profile is deliberately narrower than the local `aar-mcp` developer
surface. `aar-mcp-public` exposes six structured-workspace tools and five caller-delegated RLM
coordination tools. It does not expose arbitrary Python, accept provider credentials or endpoints,
perform the actual model call, execute external effects, activate artifacts, publish, or deliver
messages.

- Access tokens must be asymmetrically signed JWTs bound to the configured issuer, exact MCP
  audience/resource, expiry, non-empty subject, and required scopes. Tokens are request
  credentials and are not persisted in AAR runtime databases.
- The exact issuer and verified subject are hashed into an opaque tenant key. Each tenant owns a
  separate directory, SQLite database, runtime lock, principal, and session.
- Workspace mutations require exact generation and revision values plus a content-bound
  idempotency key. Cross-tenant operation, artifact, and RLM handles fail without disclosing
  another tenant's data.
- Every RLM job binds one host-selected executor, model, and optional reasoning effort. AAR issues
  a pre-spend claim ticket; only the host executes the ticketed prompt, and the commit must match
  its exact job, call, route, prompt digest, revision, and bounded output.
- Claim, commit, cancel, request, active-runtime, workspace, key, value, state, artifact, and RLM
  limits fail closed. Terminal retries must reuse the original idempotency key; retained command
  markers are digest-only and bounded per job.
- The v1 SQLite topology requires one authoritative process for a data root, or deterministic
  tenant routing to exclusive owners and persistent volumes.
- Production operators remain responsible for HTTPS ingress, OAuth metadata and key rotation,
  rate limiting, abuse detection, volume quotas, encryption and backup, retention and account
  deletion, purge verification, monitoring, incident response, and accurate public policies.

The optional domain-verification token must enter only through the deployment secret mechanism.
Configuration readback reports only whether it exists; the token must not enter source, logs,
plugin artifacts, or test fixtures.

## Operational guidance

- pin a release tag or exact wheel digest;
- keep runtime homes and attachment credentials private;
- run one authoritative supervisor per registry;
- treat missing receipts as uncertainty, not proof of failure;
- reconcile before replaying effect-shaped work;
- keep provider credentials and final delivery in the host;
- use OS/container isolation when model-authored code is not trusted.
