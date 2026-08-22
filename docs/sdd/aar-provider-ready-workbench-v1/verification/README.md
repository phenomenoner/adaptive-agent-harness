# Verification Assets

This directory contains specification-phase planning assets only.

- `requirements.json` owns requirement IDs and document owners.
- `acceptance-matrix.json` owns planned discriminating evidence rows.
- `validate_spec.py` verifies structure, IDs, references, local Markdown links, canonical package-version fixtures, and the exact frozen predecessor catalog/capability/caller-work/v6-suspension assumptions reused by this SDD. The receipt binds those predecessor input hashes separately from the successor semantic-tree digest.

A passing spec validator proves documentation consistency only. It proves no runtime behavior, migration safety, host compatibility, provider route, or benchmark readiness.
