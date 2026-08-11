# Security Policy

## Public-alpha scope

Adaptive Agent Harness executes model-authored Python and project operations with the permissions of the account or container that runs it. **It is not a security sandbox.** Use disposable workspaces or an external isolation boundary for untrusted code.

The runtime intentionally does not own provider credentials, final delivery, or general external-effect execution. Hosts and adapters must preserve those boundaries.

## Supported version

Security fixes currently target the latest tagged public alpha only.

| Version | Supported |
|---|---|
| `0.3.x` alpha | Yes |
| Earlier untagged snapshots | No |

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

## Operational guidance

- pin a release tag or exact wheel digest;
- keep runtime homes and attachment credentials private;
- run one authoritative supervisor per registry;
- treat missing receipts as uncertainty, not proof of failure;
- reconcile before replaying effect-shaped work;
- keep provider credentials and final delivery in the host;
- use OS/container isolation when model-authored code is not trusted.
