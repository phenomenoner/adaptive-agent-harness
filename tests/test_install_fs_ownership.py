from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

from aar.runtime import _install_fs as fs

EPOCH = "install-" + "1" * 64


def _new_stage(tmp_path: Path) -> tuple[fs.RetainedDirectoryChain, fs.StageHandle]:
    parent = tmp_path / "parent"
    parent.mkdir()
    chain = fs.RetainedDirectoryChain(parent)
    return chain, fs._create_stage(chain, "runtime", EPOCH)


def _write_file(directory_fd: int, name: str, data: bytes = b"owned") -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("replace_ancestor", [False, True])
def test_preflight_retains_checked_chain_and_refuses_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_ancestor: bool
) -> None:
    ancestor = tmp_path / "ancestor"
    parent = ancestor / "parent"
    parent.mkdir(parents=True)
    target = parent / "runtime"
    replaced = False

    def replace_chain(name: str) -> None:
        nonlocal replaced
        if name != "preflight_chain_retained" or replaced:
            return
        replaced = True
        source = ancestor if replace_ancestor else parent
        displaced = tmp_path / f"displaced-{source.name}"
        os.rename(source, displaced)
        if replace_ancestor:
            (ancestor / "parent").mkdir(parents=True)
        else:
            parent.mkdir()

    monkeypatch.setattr(fs, "_TEST_HOOK", replace_chain)
    target_identity, chain = fs._preflight_target_with_chain(target)
    try:
        with pytest.raises(fs.InstallerError, match="FRESH_INSTALL_PARENT_REPLACED"):
            fs._create_stage(chain, target_identity.final_name, EPOCH)
        assert not (parent / "runtime").exists()
    finally:
        chain.close()


def test_stage_open_refuses_replacement_without_touching_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    chain = fs.RetainedDirectoryChain(parent)

    def replace_stage(name: str) -> None:
        if name != "stage_mkdir_before_open":
            return
        created = next(parent.glob(".runtime.install-*"))
        os.rename(created, parent / "created-by-invocation")
        created.mkdir()
        os.chmod(created, 0o755)
        (created / "replacement-sentinel").write_bytes(b"foreign")

    monkeypatch.setattr(fs, "_TEST_HOOK", replace_stage)
    try:
        with pytest.raises(fs.InstallerError, match="FRESH_INSTALL_STAGE_REPLACED"):
            fs._create_stage(chain, "runtime", EPOCH)
        replacement = next(parent.glob(".runtime.install-*"))
        assert (replacement / "replacement-sentinel").read_bytes() == b"foreign"
        assert replacement.stat().st_mode & 0o777 == 0o755
        assert (parent / "created-by-invocation").is_dir()
    finally:
        chain.close()


@pytest.mark.parametrize(
    "relative,is_directory",
    [
        ((fs.DATABASE_NAME,), False),
        ((f"{fs.DATABASE_NAME}-wal",), False),
        ((f"{fs.DATABASE_NAME}-shm",), False),
        ((fs.BACKUP_NAME,), False),
        ((f"{fs.BACKUP_NAME}-wal",), False),
        ((f"{fs.BACKUP_NAME}-shm",), False),
        (("authority",), True),
        (("authority", "install-candidate-receipt.json"), False),
        (("authority", "install-preparation.json"), False),
        (("authority", "migration-attestation.json"), False),
        (("authority", "profile.json"), False),
        (("authority", "current.json"), False),
        (("authority", "history"), True),
        (("authority", "history", fs.HISTORY_AUTHORITY_NAME), False),
    ],
)
def test_cleanup_refuses_known_name_not_owned_by_current_phase(
    tmp_path: Path, relative: tuple[str, ...], is_directory: bool
) -> None:
    chain, stage = _new_stage(tmp_path)
    try:
        parent_fd = stage.descriptor
        for component in relative[:-1]:
            with contextlib.suppress(FileExistsError):
                os.mkdir(component, 0o700, dir_fd=parent_fd)
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            if parent_fd != stage.descriptor:
                os.close(parent_fd)
            parent_fd = child_fd
        fs.record_stage_ownership(stage)
        if is_directory:
            os.mkdir(relative[-1], 0o700, dir_fd=parent_fd)
        else:
            _write_file(parent_fd, relative[-1], b"injected")
        if parent_fd != stage.descriptor:
            os.close(parent_fd)
        with pytest.raises(fs.InstallerError, match="FRESH_INSTALL_CLEANUP_REFUSED"):
            fs.cleanup_stage(stage, chain)
        assert (Path(f"/proc/self/fd/{stage.descriptor}") / Path(*relative)).exists()
    finally:
        os.close(stage.descriptor)
        chain.close()


@pytest.mark.parametrize("directory", [False, True])
def test_cleanup_refuses_known_name_substitution_before_any_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    chain, stage = _new_stage(tmp_path)
    try:
        if directory:
            os.mkdir("authority", 0o700, dir_fd=stage.descriptor)
            authority_fd = os.open(
                "authority",
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=stage.descriptor,
            )
            try:
                _write_file(authority_fd, "current.json")
            finally:
                os.close(authority_fd)
            target = "authority"
        else:
            _write_file(stage.descriptor, fs.DATABASE_NAME)
            target = fs.DATABASE_NAME
        _write_file(stage.descriptor, "other-owned")
        fs.record_stage_ownership(stage)

        def substitute(name: str) -> None:
            if name != "cleanup_after_validation":
                return
            os.rename(
                target,
                f"saved-{target}",
                src_dir_fd=stage.descriptor,
                dst_dir_fd=stage.descriptor,
            )
            if directory:
                os.mkdir(target, 0o700, dir_fd=stage.descriptor)
            else:
                _write_file(stage.descriptor, target, b"replacement")

        monkeypatch.setattr(fs, "_TEST_HOOK", substitute)
        with pytest.raises(fs.InstallerError, match="FRESH_INSTALL_CLEANUP_REFUSED"):
            fs.cleanup_stage(stage, chain)
        assert (Path(f"/proc/self/fd/{stage.descriptor}") / target).exists()
        assert (Path(f"/proc/self/fd/{stage.descriptor}") / "other-owned").exists()
    finally:
        os.close(stage.descriptor)
        chain.close()


def test_cleanup_removes_only_a_complete_owned_manifest(tmp_path: Path) -> None:
    chain, stage = _new_stage(tmp_path)
    stage_path = Path(f"/proc/self/fd/{stage.parent.descriptor}") / stage.name
    try:
        _write_file(stage.descriptor, fs.DATABASE_NAME)
        os.mkdir("authority", 0o700, dir_fd=stage.descriptor)
        authority_fd = os.open(
            "authority",
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=stage.descriptor,
        )
        try:
            _write_file(authority_fd, "current.json")
        finally:
            os.close(authority_fd)
        fs.record_stage_ownership(stage)
        fs.cleanup_stage(stage, chain)
        assert not stage_path.exists()
    finally:
        os.close(stage.descriptor)
        chain.close()
