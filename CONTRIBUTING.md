# Contributing

Thank you for helping improve Adaptive Agent Harness.

## Before opening a change

1. Search existing issues and pull requests.
2. Keep the change focused on one contract, behavior, host profile, or documentation concern.
3. Do not include credentials, runtime databases, local receipts, rollback archives, private identifiers, or machine-specific paths.
4. Preserve the authority boundary: the harness computes and proposes; the host authorizes effects and delivery.

## Development setup

```bash
uv sync --locked
uv run aar-contract verify
uv run pytest -q
```

Use `uv run ruff check` and `uv run ruff format --check` for changed Python files. See [docs/TESTING.md](docs/TESTING.md) for claim-driven verification tiers.

## Pull requests

A good pull request includes:

- the user-visible or contract problem;
- affected invariants and compatibility surface;
- exact tests or fixtures added;
- fresh verification output;
- explicit unsupported or unverified boundaries;
- documentation updates when public behavior changes.

Behavior-changing work should update the relevant schema or fixture, tests, architecture or roadmap document, and public release notes when it changes a supported contract.

## Translations

The English `README.md` is canonical. Translation fixes are welcome; keep commands, identifiers, links, version numbers, and safety boundaries exact. Do not translate product names, package names, command names, schema names, or code.

## Code of conduct

Be respectful, technical, and evidence-focused. Disagreement is welcome; harassment and personal attacks are not.
