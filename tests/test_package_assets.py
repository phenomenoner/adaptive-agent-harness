from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_declares_portable_contract_and_evidence_assets() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"]["aar-codex-setup"] == (
        "aar.compat.codex_setup:main"
    )
    assert project["project"]["dependencies"] == [
        "ipython>=9.16,<10",
        "mcp==2.0.0",
        "numpy>=2,<3",
        "pandas>=2.2,<4",
        "pydantic>=2.12,<3",
    ]
    force_include = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include == {
        "benchmarks": "aar/bundled/benchmarks",
        "docs/CODEX-INSTALL.md": "aar/bundled/docs/CODEX-INSTALL.md",
        "integration": "aar/bundled/integration",
        "profiles": "aar/bundled/profiles",
        "schemas": "aar/bundled/schemas",
        "skills/aar-operations": "aar/bundled/aar-operations",
        "tests/fixtures": "aar/bundled/fixtures",
    }
    required = (
        ROOT / "schemas/aar-schemas-v1.json",
        ROOT / "schemas/aar-mcp-tools-v5.json",
        ROOT / "schemas/aar-mcp-tools-v7.json",
        ROOT / "tests/fixtures/manifest.json",
        ROOT / "benchmarks/rlm-evidence-v1.json",
        ROOT / "docs/CODEX-INSTALL.md",
        ROOT / "integration/ahc/rlm-contract-v1.json",
        ROOT / "integration/ahc/continuity-contract-v1.json",
    )
    assert all(path.is_file() for path in required)
