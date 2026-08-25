from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aar import admin
from aar.runtime.installer import InstallerError, PublicationIndeterminate


def _argv(tmp_path: Path) -> list[str]:
    return [
        "runtime",
        "install",
        "--runtime-home",
        str(tmp_path / "runtime"),
        "--intent",
        str(tmp_path / "intent.json"),
        "--candidate-receipt",
        str(tmp_path / "receipt.json"),
        "--wheel",
        str(tmp_path / "candidate.whl"),
    ]


def test_runtime_install_is_a_thin_canonical_cli_adapter(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []
    result = SimpleNamespace(
        target=tmp_path / "runtime",
        runtime_home_digest="sha256:" + "1" * 64,
        database_identity="db-" + "2" * 64,
        install_epoch="install-" + "3" * 64,
        snapshot_id="empty-v5-" + "4" * 64,
        attestation_digest="sha256:" + "5" * 64,
        profile_digest="sha256:" + "6" * 64,
        authority_digest="sha256:" + "7" * 64,
        preparation={"schema_version": "aar.clean-install-preparation.v1"},
        attestation={"attestation_digest": "sha256:" + "5" * 64},
        profile={"profile_id": "profile-primary"},
        authority={"runtime_generation": 1},
    )

    def install(*args: Any) -> Any:
        calls.append(args)
        return result

    monkeypatch.setattr(admin, "install_clean_runtime", install)
    assert admin.main(_argv(tmp_path)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert calls == [
        (
            tmp_path / "runtime",
            tmp_path / "intent.json",
            tmp_path / "receipt.json",
            tmp_path / "candidate.whl",
        )
    ]
    raw = captured.out.encode()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert json.loads(raw) == {
        "attestation": {"attestation_digest": "sha256:" + "5" * 64},
        "attestation_digest": "sha256:" + "5" * 64,
        "authority": {"runtime_generation": 1},
        "authority_digest": "sha256:" + "7" * 64,
        "database_identity": "db-" + "2" * 64,
        "install_epoch": "install-" + "3" * 64,
        "preparation": {"schema_version": "aar.clean-install-preparation.v1"},
        "profile": {"profile_id": "profile-primary"},
        "profile_digest": "sha256:" + "6" * 64,
        "runtime_home_digest": "sha256:" + "1" * 64,
        "snapshot_id": "empty-v5-" + "4" * 64,
        "target": str(tmp_path / "runtime"),
    }


def test_runtime_install_surfaces_installer_error_code_without_mutating_adapter_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: Any) -> Any:
        raise InstallerError("FRESH_INSTALL_TARGET_EXISTS", "target already exists")

    monkeypatch.setattr(admin, "install_clean_runtime", refuse)
    assert admin.main(_argv(tmp_path)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FRESH_INSTALL_TARGET_EXISTS" in captured.err
    assert not (tmp_path / "runtime").exists()


def test_runtime_install_emits_machine_distinguishable_indeterminate_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "runtime"
    epoch = "install-" + "8" * 64

    def indeterminate(*_args: Any) -> Any:
        error = PublicationIndeterminate(
            "FRESH_INSTALL_PUBLICATION_FAILED",
            "published parent durability is indeterminate",
            target=target,
            install_epoch=epoch,
        )
        raise error

    monkeypatch.setattr(admin, "install_clean_runtime", indeterminate)
    assert admin.main(_argv(tmp_path)) == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    raw = captured.out.encode()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert json.loads(raw) == {
        "automatic_retry_safe": False,
        "error_code": "FRESH_INSTALL_PUBLICATION_FAILED",
        "install_epoch": epoch,
        "outcome": "indeterminate",
        "publication_state": "rename_linearized",
        "schema_version": "aar.clean-install-publication-indeterminate.v1",
        "target": str(target),
        "target_may_exist": True,
    }
