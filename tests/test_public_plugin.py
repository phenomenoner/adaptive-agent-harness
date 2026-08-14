from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from aar.compat import public_plugin
from aar.compat.public_plugin import (
    PUBLIC_PLUGIN_NAME,
    PUBLIC_PLUGIN_VERSION,
    PublicPluginBuildSettings,
    build_public_plugin,
)
from aar.versions import PACKAGE_VERSION

ROOT = Path(__file__).resolve().parents[1]


def _test_cases_bytes() -> bytes:
    document = {
        "positive": [
            {
                "name": f"positive-{index}",
                "prompt": f"read structured value {index}",
                "expected_behavior": "use the authenticated tenant workspace",
                "expected_result": "a bounded structured response",
            }
            for index in range(5)
        ],
        "negative": [
            {
                "name": f"negative-{index}",
                "prompt": f"attempt unsupported action {index}",
                "expected_behavior": "reject the unsupported request",
                "expected_result": "a clear bounded failure",
            }
            for index in range(3)
        ],
    }
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _source_fixture(tmp_path: Path) -> tuple[Path, bytes]:
    source_root = tmp_path / "source"
    test_cases = _test_cases_bytes()
    files = {
        "skills/aar-public-runtime/SKILL.md": (
            b"# Adaptive Agent Runtime\n\n"
            b"Use tenant workspaces and caller-delegated RLM coordination.\n"
        ),
        "skills/aar-public-runtime/agents/openai.yaml": b"""interface:
  display_name: runtime
""",
        "assets/icon.png": b"icon-bytes",
        "assets/logo.png": b"logo-bytes",
        "assets/logo-dark.png": b"dark-logo-bytes",
        "submission/test-cases.json": test_cases,
    }
    for relative, content in files.items():
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return source_root, test_cases


def _settings(source_root: Path, output_root: Path) -> PublicPluginBuildSettings:
    return PublicPluginBuildSettings(
        output_root=output_root,
        mcp_url="https://aar-mcp.vendor.dev/v1/mcp",
        website_url="https://runtime.vendor.dev/adaptive-agent-runtime",
        support_url="https://support.vendor.dev/adaptive-agent-runtime",
        privacy_policy_url="https://runtime.vendor.dev/privacy",
        terms_of_service_url="https://runtime.vendor.dev/terms",
        developer_name="AAR Maintainers",
        source_root=source_root,
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_identical_builds_have_identical_trees_and_required_documents(tmp_path: Path) -> None:
    source_root, test_cases = _source_fixture(tmp_path)
    first = build_public_plugin(_settings(source_root, tmp_path / "candidate-one"))
    second = build_public_plugin(_settings(source_root, tmp_path / "candidate-two"))
    first_root = Path(str(first["output_root"]))
    second_root = Path(str(second["output_root"]))
    first_tree = _tree(first_root)
    second_tree = _tree(second_root)

    assert first_tree == second_tree
    assert first["file_count"] == len(first_tree)
    assert first["package_version"] == PACKAGE_VERSION
    assert first["plugin_name"] == PUBLIC_PLUGIN_NAME
    assert first["plugin_version"] == PUBLIC_PLUGIN_VERSION
    assert first["status"] == "built"

    manifest_path = first_root / "submission/manifest.sha256.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == set(first_tree) - {"submission/manifest.sha256.json"}
    for relative, digest in manifest.items():
        assert digest == hashlib.sha256((first_root / relative).read_bytes()).hexdigest()
    assert first["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    mcp = json.loads(
        (first_root / "plugins/adaptive-agent-runtime/.mcp.json").read_text(
            encoding="utf-8"
        )
    )
    assert mcp == {
        "mcpServers": {
            "aar-public": {
                "type": "http",
                "url": "https://aar-mcp.vendor.dev/v1/mcp",
            }
        }
    }

    plugin_manifest = json.loads(
        (
            first_root
            / "plugins/adaptive-agent-runtime/.codex-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )
    assert plugin_manifest["name"] == PUBLIC_PLUGIN_NAME
    assert plugin_manifest["version"] == PUBLIC_PLUGIN_VERSION
    assert plugin_manifest["author"] == {"name": "AAR Maintainers"}
    assert plugin_manifest["license"] == "MIT"
    assert (
        plugin_manifest["repository"]
        == "https://github.com/phenomenoner/adaptive-agent-harness"
    )
    assert plugin_manifest["skills"] == "./skills/"
    assert plugin_manifest["mcpServers"] == "./.mcp.json"
    assert plugin_manifest["interface"]["category"] == "Developer Tools"
    assert plugin_manifest["interface"]["websiteURL"] == (
        "https://runtime.vendor.dev/adaptive-agent-runtime"
    )
    assert plugin_manifest["interface"]["privacyPolicyURL"] == (
        "https://runtime.vendor.dev/privacy"
    )
    assert plugin_manifest["interface"]["termsOfServiceURL"] == (
        "https://runtime.vendor.dev/terms"
    )
    assert plugin_manifest["interface"]["composerIcon"] == "./assets/icon.png"
    assert "supportUrl" not in plugin_manifest["interface"]
    assert len(plugin_manifest["interface"]["defaultPrompt"]) <= 3
    assert all(
        1 <= len(prompt) <= 128
        for prompt in plugin_manifest["interface"]["defaultPrompt"]
    )

    copied_cases = (first_root / "submission/test-cases.json").read_bytes()
    assert copied_cases == test_cases
    parsed_cases = json.loads(copied_cases)
    assert len(parsed_cases["positive"]) == 5
    assert len(parsed_cases["negative"]) == 3

    submission = json.loads(
        (first_root / "submission/submission.json").read_text(encoding="utf-8")
    )
    assert submission["listing"]["license"] == "MIT"
    assert submission["universal_mcp"]["url"] == "https://aar-mcp.vendor.dev/v1/mcp"
    assert submission["universal_mcp"]["authentication"] == {
        "protocol": "OAuth 2.1",
        "required_scopes": ["aar:rlm", "aar:workspace"],
        "type": "OAuth 2.1",
    }
    assert submission["plugin"]["skill_path"] == "./skills/"
    assert submission["plugin"]["starter_prompts"] == plugin_manifest["interface"]["defaultPrompt"]
    assert submission["release"]["runtime_package_version"] == PACKAGE_VERSION
    assert submission["release"]["public_plugin_version"] == PUBLIC_PLUGIN_VERSION
    assert submission["external_requirements"] == [
        "production HTTPS endpoint and OAuth flow",
        "domain challenge",
        "reviewer credentials",
        "public website, support, privacy, retention/deletion, and terms pages",
        (
            "production rate limits, abuse controls, storage quotas, backup, purge, and "
            "incident response"
        ),
        "single-owner or tenant-sticky persistent deployment topology",
        "five positive and three negative deployed test passes",
        "portal scan of the expected eleven-tool surface and skill snapshot",
        "fresh-host caller-delegated RLM execution and recovery proof",
        "verified Platform identity",
        "Apps Management Write",
        "final regional/legal approval",
        "OpenAI review approval",
        "publisher publish action",
    ]

    with zipfile.ZipFile(
        first_root / "submission/aar-public-runtime-skill.zip"
    ) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        assert names == sorted(names)
        assert names == [
            "aar-public-runtime/SKILL.md",
            "aar-public-runtime/agents/openai.yaml",
        ]
        for info in infos:
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.extra == b""
            assert info.create_system == 3
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.external_attr >> 16 & 0o777 == 0o644


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("mcp_url", "http://aar-mcp.vendor.dev/v1/mcp"),
        ("website_url", "https://localhost/site"),
        ("support_url", "https://127.0.0.1/support"),
        ("privacy_policy_url", "https://docs.example.local/privacy"),
        ("website_url", "https://sub.example.com/site"),
        ("support_url", "https://support.example.org/help"),
        ("mcp_url", "https://192.168.1.4/mcp"),
        ("mcp_url", "https://169.254.169.254/mcp"),
        ("terms_of_service_url", "https://terms/path"),
        ("mcp_url", "https://aar-mcp.vendor.dev/v1/mcp?debug=true"),
        ("mcp_url", "https://aar-mcp.vendor.dev/"),
    ),
)
def test_rejects_insecure_local_query_and_root_urls(
    tmp_path: Path, field: str, value: str
) -> None:
    source_root, _ = _source_fixture(tmp_path)
    settings = replace(
        _settings(source_root, tmp_path / "rejected"),
        **{field: value},
    )
    with pytest.raises(ValueError):
        build_public_plugin(settings)
    assert not (tmp_path / "rejected").exists()


def test_rejects_existing_output_root(tmp_path: Path) -> None:
    source_root, _ = _source_fixture(tmp_path)
    output_root = tmp_path / "existing"
    output_root.mkdir()
    with pytest.raises(FileExistsError):
        build_public_plugin(_settings(source_root, output_root))
    assert output_root.is_dir()


def test_rejects_missing_source_asset(tmp_path: Path) -> None:
    source_root, _ = _source_fixture(tmp_path)
    (source_root / "assets/icon.png").unlink()
    with pytest.raises(ValueError, match="required source asset"):
        build_public_plugin(_settings(source_root, tmp_path / "missing-asset"))
    assert not (tmp_path / "missing-asset").exists()


def test_rejects_symlink_source_when_supported(tmp_path: Path) -> None:
    source_root, _ = _source_fixture(tmp_path)
    linked_source = tmp_path / "linked-source"
    try:
        linked_source.symlink_to(source_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")
    with pytest.raises(ValueError, match=r"symlink|reparse"):
        build_public_plugin(_settings(linked_source, tmp_path / "symlink-rejected"))
    assert not (tmp_path / "symlink-rejected").exists()


def test_post_creation_failure_removes_only_partial_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, _ = _source_fixture(tmp_path)
    output_root = tmp_path / "partial"
    real_write_bytes = public_plugin._write_bytes

    def fail_on_mcp(path: Path, content: bytes) -> None:
        if path.name == ".mcp.json":
            raise RuntimeError("injected write failure")
        real_write_bytes(path, content)

    monkeypatch.setattr(public_plugin, "_write_bytes", fail_on_mcp)
    with pytest.raises(RuntimeError, match="injected write failure"):
        build_public_plugin(_settings(source_root, output_root))
    assert not output_root.exists()


def test_public_container_recipe_is_non_root_and_secret_free() -> None:
    dockerfile = (ROOT / "deploy/public/Dockerfile").read_text(encoding="utf-8")
    environment = (ROOT / "deploy/public/public.env.example").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["aar-mcp-public"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/healthz" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "COPY pyproject.toml uv.lock README.md LICENSE ./" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "pip install" not in dockerfile
    assert "AAR_PUBLIC_OPENAI_CHALLENGE_TOKEN" not in environment

    values = {
        name: value
        for line in environment.splitlines()
        if line and not line.startswith("#")
        for name, value in (line.split("=", 1),)
    }
    assert values["AAR_PUBLIC_AUDIENCE"] == values["AAR_PUBLIC_RESOURCE_URL"]
    assert {".aar", ".debug", ".git", "WAL.md"}.issubset(dockerignore)


@pytest.mark.parametrize(
    ("name", "expected_size"),
    [
        ("icon.png", (512, 512)),
        ("logo.png", (1024, 1024)),
        ("logo-dark.png", (1024, 1024)),
    ],
)
def test_public_brand_assets_are_complete_square_pngs(
    name: str, expected_size: tuple[int, int]
) -> None:
    content = (
        ROOT / "profiles/codex-public/adaptive-agent-workspace/assets" / name
    ).read_bytes()
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert content[12:16] == b"IHDR"
    assert struct.unpack(">II", content[16:24]) == expected_size
    assert content.endswith(b"IEND\xaeB`\x82")
