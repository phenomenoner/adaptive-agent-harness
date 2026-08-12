#!/usr/bin/env python3
"""Fail closed on public-release hygiene and documentation portability."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TRANSLATIONS = {
    "README.ar.md",
    "README.cs.md",
    "README.de.md",
    "README.es.md",
    "README.fi.md",
    "README.fr.md",
    "README.it.md",
    "README.ja.md",
    "README.ko.md",
    "README.lt.md",
    "README.no.md",
    "README.pt-BR.md",
    "README.ru.md",
    "README.th.md",
    "README.vi.md",
    "README.zh-CN.md",
    "README.zh-TW.md",
}

BYTE_PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "openai-style-key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
    "aws-access-key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "authorization-header": re.compile(
        rb"authorization\s*:\s*(?:bearer|token)\s+\S+", re.IGNORECASE
    ),
    "machine-local-path": re.compile(
        rb"(?:/" + rb"home/(?!user(?:/|$)|example(?:/|$)|<)[^/\s]+(?:/|$)"
        rb"|/" + rb"mnt/[a-z]/(?!(?:path|project|repo|workspace|example)(?:/|$)|<)"
        rb"[^/\s]+(?:/|$)"
        rb"|[A-Z]:\\Users\\(?!user(?:\\|$)|example(?:\\|$)|<)[^\\/\s]+(?:\\|$)"
        rb"|[A-Z]:\\(?!(?:path|tools|tmp|project|repo|workspace|example|Users|Windows)"
        rb"(?:\\|$)|<)[^\\/\r\n]+(?:\\|$))",
        re.IGNORECASE,
    ),
    "private-message-id": re.compile(
        rb"(?:discord|telegram|message|channel|chat|guild|user)[_-]?(?:id)?"
        rb"\s*[:=]\s*[\"']?\d{15,20}\b",
        re.IGNORECASE,
    ),
    "opaque-task-id": re.compile(
        rb"(?:desktop\s+task|task\s+id)\s+`?[0-9a-f]{8}"
        rb"(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}`?",
        re.IGNORECASE,
    ),
    "local-receipt-path": re.compile(
        rb"(?:^|[\s`])(?:~?/)?\.aar/[A-Za-z0-9._/-]*"
        rb"(?:receipt|summary|trace)[A-Za-z0-9._/-]*",
        re.IGNORECASE,
    ),
}

PROHIBITED_PATH = re.compile(
    r"(^|/)(?:\.env(?:$|\.)|\.data(?:/|$)|\.private(?:/|$)|secrets?(?:/|$)|"
    r"credentials?(?:/|$)|rollback(?:/|$)|runtime-workspace(?:/|$)|"
    r"[^/]*\.(?:sqlite3?|pem|key)$)",
    re.IGNORECASE,
)
MARKDOWN_LINK = re.compile(r"\]\(([^)]+)\)")


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def github_slug(heading: str) -> str:
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", heading)
    heading = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", heading)
    heading = (
        heading.replace("`", "")
        .replace("*", "")
        .replace("_", "")
        .replace("~", "")
        .strip()
        .lower()
    )
    output: list[str] = []
    for character in heading:
        category = unicodedata.category(character)
        if character.isspace():
            output.append("-")
        elif character == "-" or category[0] in {"L", "N", "M"}:
            output.append(character)
    return "".join(output)


def release_identity_failures(
    root: Path, translation_names: set[str]
) -> list[dict[str, str]]:
    """Bind the current public docs to the package version without rewriting history."""
    failures: list[dict[str, str]] = []
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    tag = f"v{version}"
    required_markers = {
        root / "README.md": (
            f"releases/tag/{tag}",
            f"@{tag}",
            f"**`{version}`**",
        ),
        root / "CHANGELOG.md": (
            f"## [{version}]",
            f"releases/tag/{tag}",
        ),
        root / "docs" / f"RELEASE-{tag}.md": (
            f"# Adaptive Agent Harness {tag}",
            f"@{tag}",
        ),
    }
    for name in sorted(translation_names):
        required_markers[root / "docs" / "i18n" / name] = (
            f"releases/tag/{tag}",
            f"@{tag}",
            f"**`{version}`**",
        )
    for path, markers in required_markers.items():
        relative = path.relative_to(root).as_posix()
        if not path.is_file():
            failures.append({"kind": "release-identity-file", "detail": relative})
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                failures.append(
                    {
                        "kind": "release-identity-marker",
                        "detail": f"{relative} -> {marker}",
                    }
                )
    return failures


def main() -> int:
    files = tracked_files()
    failures: list[dict[str, str]] = []

    if len(files) < 150:
        failures.append({"kind": "tracked-file-floor", "detail": str(len(files))})

    for required in ("LICENSE", "README.md", "SECURITY.md", "CONTRIBUTING.md"):
        if not (ROOT / required).is_file():
            failures.append({"kind": "missing-required-file", "detail": required})

    translation_dir = ROOT / "docs" / "i18n"
    actual_translations = {path.name for path in translation_dir.glob("README.*.md")}
    if actual_translations != EXPECTED_TRANSLATIONS:
        failures.append(
            {
                "kind": "translation-set",
                "detail": json.dumps(
                    {
                        "missing": sorted(EXPECTED_TRANSLATIONS - actual_translations),
                        "extra": sorted(actual_translations - EXPECTED_TRANSLATIONS),
                    }
                ),
            }
        )
    failures.extend(release_identity_failures(ROOT, actual_translations))

    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if PROHIBITED_PATH.search(relative):
            failures.append({"kind": "prohibited-path", "detail": relative})
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            failures.append({"kind": "binary-file", "detail": relative})
            continue
        for name, pattern in BYTE_PATTERNS.items():
            if pattern.search(data):
                failures.append({"kind": name, "detail": relative})
        if path.suffix.lower() != ".md":
            continue
        text = data.decode("utf-8")
        slugs: set[str] = set()
        slug_counts: dict[str, int] = {}
        for line in text.splitlines():
            heading = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
            if not heading:
                continue
            base = github_slug(heading.group(1))
            occurrence = slug_counts.get(base, 0)
            slug_counts[base] = occurrence + 1
            slugs.add(base if occurrence == 0 else f"{base}-{occurrence}")
        for target in MARKDOWN_LINK.findall(text):
            target = target.strip()
            if target.startswith("#"):
                if target[1:] not in slugs:
                    failures.append(
                        {
                            "kind": "broken-heading-anchor",
                            "detail": f"{relative} -> {target}",
                        }
                    )
                continue
            target = target.split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                failures.append(
                    {"kind": "broken-relative-link", "detail": f"{relative} -> {target}"}
                )

    report = {
        "tracked_files": len(files),
        "translations": len(actual_translations),
        "failures": failures,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
