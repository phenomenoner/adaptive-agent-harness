# Verification assets — clean-install SDD rev2

This directory contains deterministic specification-phase assets only.

- `requirements.json` owns 61 requirement IDs, tiers, owners, the exact structured product contract, one non-counted summary requirement, and the exact frozen-compatibility manifest.
- `acceptance-matrix.json` owns 214 counted atomic rows: T0=22, T1=91, T2=73, T3=15, T4=13, T5=0. Each row has one operation, one lifecycle phase, one singular variant, one exact derived stimulus, one expected observation, and one concrete validator-derived wrong-effect absence. `A-TEST-001` is non-counted metadata.
- The machine assets bind the existing 14 schema files, provider-ready fixture manifest, and all 64 declared fixture members by sorted path, size, SHA-256, and compatibility bundle digest. They declare only one planned additional receipt schema: `aar.install-candidate-receipt.v1`.
- `validate_spec.py` checks exact product contract, counts, atomicity, clean-install semantics, target identity, temporal ownership, v6 projection, receipt shape, frozen predecessor inputs, local links, and validator self-tests.
- `spec-validation-receipt.json` is generated only by the final validator invocation. It excludes itself from the semantic tree, records sorted per-file hashes and counts, and proves documentation consistency only.

Run diagnostic validation without caches/bytecode:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B verification/validate_spec.py --root .
PYTHONDONTWRITEBYTECODE=1 python -B -m json.tool verification/requirements.json >/dev/null
PYTHONDONTWRITEBYTECODE=1 python -B -m json.tool verification/acceptance-matrix.json >/dev/null
```

The final invocation is required after every edit:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B verification/validate_spec.py \
  --root . --receipt verification/spec-validation-receipt.json
```

Run that exact receipt-bound command twice and require byte-identical receipt files. A passing receipt proves no product implementation, database publication, startup/Ready, wheel provenance, provider route, or benchmark.
