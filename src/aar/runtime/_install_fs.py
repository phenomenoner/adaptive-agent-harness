"""Filesystem, target, staging, and publication primitives for clean installs."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import secrets
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from aar.canonical import canonical_sha256
from aar.provider_ready_install_models import (
    CLEAN_INSTALL_DATABASE_IDENTITY_SCHEMA_VERSION,
    CleanInstallDatabaseIdentity,
)
from aar.provider_ready_models import HostActivationIntent

INSTALL_RECEIPT_PATH = "authority/install-candidate-receipt.json"
INSTALL_PREPARATION_PATH = "authority/install-preparation.json"
MIGRATION_ATTESTATION_PATH = "authority/migration-attestation.json"
PROFILE_PATH = "authority/profile.json"
CURRENT_AUTHORITY_PATH = "authority/current.json"
HISTORY_AUTHORITY_DIR = "authority/history"
HISTORY_AUTHORITY_NAME = "00000000000000000001.json"
DATABASE_NAME = "reference.sqlite3"
BACKUP_NAME = ".empty-v5-backup.sqlite3"
RENAME_NOREPLACE = 1

def _no_test_hook(_name: str) -> None:
    return None


_TEST_HOOK: Callable[[str], None] = _no_test_hook


def _test_hook(name: str) -> None:
    """Private deterministic race seam; tests may monkeypatch `_TEST_HOOK`."""

    _TEST_HOOK(name)


class InstallerError(RuntimeError):
    """A deterministic, contained clean-install refusal."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
class PublicationIndeterminate(InstallerError):
    """Publication linearized, but a required durability/readback check failed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        target: Path | None = None,
        install_epoch: str | None = None,
    ) -> None:
        self.target = target
        self.install_epoch = install_epoch
        super().__init__(code, message)
CleanInstallError = InstallerError
@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    owner_uid: int
    owner_gid: int

    @classmethod
    def from_stat(cls, observed: os.stat_result) -> Self:
        return cls(
            device=int(observed.st_dev),
            inode=int(observed.st_ino),
            mode=stat.S_IMODE(observed.st_mode),
            owner_uid=int(observed.st_uid),
            owner_gid=int(observed.st_gid),
        )

    def same_inode(self, observed: os.stat_result) -> bool:
        return self.device == observed.st_dev and self.inode == observed.st_ino

    def exact(self, observed: os.stat_result) -> bool:
        return (
            self.same_inode(observed)
            and self.mode == stat.S_IMODE(observed.st_mode)
            and self.owner_uid == observed.st_uid
            and self.owner_gid == observed.st_gid
        )
@dataclass(frozen=True, slots=True)
class TargetIdentity:
    requested_target: Path
    canonical_target: Path
    canonical_parent: Path
    final_name: str
    runtime_home_digest: str
    database_identity: str

    @property
    def database_path(self) -> Path:
        return self.canonical_target / DATABASE_NAME
@dataclass(frozen=True, slots=True)
class RetainedDirectory:
    name: str
    descriptor: int
    identity: FileIdentity

    def verify(self) -> None:
        _verify_directory_descriptor(self.descriptor, self.identity, self.name)
class RetainedDirectoryChain:
    """Open and retain every existing directory from root through parent."""

    def __init__(self, parent: Path) -> None:
        self.parent_path = parent
        self.directories: list[RetainedDirectory] = []
        try:
            self._open(parent)
        except BaseException:
            self.close()
            raise

    @property
    def parent(self) -> RetainedDirectory:
        if not self.directories:
            raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", "parent directory chain is empty")
        return self.directories[-1]

    def _open(self, parent: Path) -> None:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        current_fd = os.open(os.sep, flags)
        root_stat = os.fstat(current_fd)
        self.directories.append(
            RetainedDirectory("/", current_fd, FileIdentity.from_stat(root_stat))
        )
        if parent == Path(os.sep):
            return
        for component in parent.parts[1:]:
            descriptor = os.open(component, flags, dir_fd=current_fd)
            observed = os.fstat(descriptor)
            if not stat.S_ISDIR(observed.st_mode):
                os.close(descriptor)
                raise InstallerError(
                    "FRESH_INSTALL_PATH_UNSAFE",
                    f"ancestor is not a directory: {component}",
                )
            self.directories.append(
                RetainedDirectory(component, descriptor, FileIdentity.from_stat(observed))
            )
            current_fd = descriptor

    def verify(self) -> None:
        for directory in self.directories:
            directory.verify()
        _verify_chain_against_path(self.parent_path, self.directories)

    def close(self) -> None:
        for directory in reversed(self.directories):
            with contextlib.suppress(OSError):
                os.close(directory.descriptor)
        self.directories.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()
@dataclass(frozen=True, slots=True)
class StageHandle:
    name: str
    descriptor: int
    identity: FileIdentity
    parent: RetainedDirectory
    owned_entries: dict[tuple[str, ...], FileIdentity] = field(default_factory=dict)

    @property
    def proc_root(self) -> Path:
        return Path(f"/proc/self/fd/{self.descriptor}")

    def verify(self) -> None:
        _verify_directory_descriptor(self.descriptor, self.identity, "staging")
        observed = os.stat(self.name, dir_fd=self.parent.descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(observed.st_mode) or not self.identity.exact(observed):
            raise InstallerError(
                "FRESH_INSTALL_STAGE_REPLACED",
                "staging directory entry no longer names the retained staging inode",
            )
@dataclass(slots=True)
class PublicationState:
    """One-way local record of the renameat2 publication linearization point."""

    renamed: bool = False
def _path_text(value: os.PathLike[str] | str, *, label: str) -> str:
    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", f"{label} is not a path") from error
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", f"{label} is not UTF-8") from error
    if not isinstance(raw, str) or "\x00" in raw:
        raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", f"{label} contains an invalid NUL")
    try:
        raw.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", f"{label} is not UTF-8") from error
    return raw
def _require_absolute_file_path(value: os.PathLike[str] | str, *, label: str) -> Path:
    text = _path_text(value, label=label)
    path = Path(text)
    if not path.is_absolute():
        raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", f"{label} must be absolute")
    return path
def _existing_ancestor_paths(parent: Path) -> tuple[Path, ...]:
    parts = parent.parts
    if not parts or parts[0] != os.sep:
        raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", "parent is not an absolute POSIX path")
    paths = [Path(os.sep)]
    current = Path(os.sep)
    for component in parts[1:]:
        current /= component
        paths.append(current)
    return tuple(paths)
def _preflight_parent(parent: Path) -> Path:
    for ancestor in _existing_ancestor_paths(parent):
        try:
            observed = os.lstat(ancestor)
        except (FileNotFoundError, OSError) as error:
            raise InstallerError(
                "FRESH_INSTALL_PATH_UNSAFE",
                f"existing parent/ancestor is unavailable: {ancestor}",
            ) from error
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
            raise InstallerError(
                "FRESH_INSTALL_PATH_UNSAFE",
                f"parent/ancestor is not a non-symlink directory: {ancestor}",
            )
    try:
        return parent.resolve(strict=True)
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_PATH_UNSAFE", "parent cannot be strictly resolved"
        ) from error
def compute_runtime_home_digest(canonical_target: Path) -> str:
    """Compute the exact canonical runtime-home object digest."""

    target = canonical_target.as_posix()
    return canonical_sha256(
        {
            "database": f"{target}/{DATABASE_NAME}",
            "runtime_home": target,
        }
    )
def compute_database_identity(runtime_home_digest: str) -> str:
    """Compute the canonical semantic database identity (never inode/dev)."""

    material = CleanInstallDatabaseIdentity(
        schema_version=CLEAN_INSTALL_DATABASE_IDENTITY_SCHEMA_VERSION,
        runtime_home_digest=runtime_home_digest,
        database_name=DATABASE_NAME,
    )
    return "db-" + canonical_sha256(material.model_dump(mode="json")).removeprefix("sha256:")
def _preflight_target_with_chain(
    runtime_home: os.PathLike[str] | str,
) -> tuple[TargetIdentity, RetainedDirectoryChain]:
    """Retain the checked ancestor chain before classifying the final component."""

    text = _path_text(runtime_home, label="runtime_home")
    path = Path(text)
    if not path.is_absolute() or text.endswith(os.sep) or path.name in {"", ".", ".."}:
        raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", "runtime_home must be an absolute target")
    final_name = path.name
    try:
        chain = RetainedDirectoryChain(path.parent)
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_PATH_UNSAFE", "existing parent/ancestor is unavailable"
        ) from error
    try:
        # Resolve only for the semantic identity; all effects remain descriptor-bound.
        parent = path.parent.resolve(strict=True)
        chain.verify()
    except OSError as error:
        chain.close()
        raise InstallerError(
            "FRESH_INSTALL_PATH_UNSAFE", "existing parent/ancestor is unavailable"
        ) from error
    _test_hook("preflight_chain_retained")
    try:
        observed = os.stat(final_name, dir_fd=chain.parent.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as error:
        chain.close()
        raise InstallerError(
            "FRESH_INSTALL_PATH_UNSAFE", "target could not be classified"
        ) from error
    else:
        _ = observed
        chain.close()
        raise InstallerError(
            "FRESH_INSTALL_TARGET_EXISTS",
            "the final target component already exists in any filesystem form",
        )
    canonical_target = parent / final_name
    runtime_home_digest = compute_runtime_home_digest(canonical_target)
    return (
        TargetIdentity(
            requested_target=path,
            canonical_target=canonical_target,
            canonical_parent=parent,
            final_name=final_name,
            runtime_home_digest=runtime_home_digest,
            database_identity=compute_database_identity(runtime_home_digest),
        ),
        chain,
    )


def preflight_target(runtime_home: os.PathLike[str] | str) -> TargetIdentity:
    """Validate an absent target and return its canonical semantic identity."""

    target, chain = _preflight_target_with_chain(runtime_home)
    chain.close()
    return target
def _verify_directory_descriptor(descriptor: int, identity: FileIdentity, label: str) -> None:
    try:
        observed = os.fstat(descriptor)
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_PARENT_REPLACED", f"{label} descriptor is unavailable"
        ) from error
    if not stat.S_ISDIR(observed.st_mode) or not identity.exact(observed):
        raise InstallerError(
            "FRESH_INSTALL_PARENT_REPLACED", f"{label} descriptor identity changed"
        )
def _verify_chain_against_path(parent: Path, retained: list[RetainedDirectory]) -> None:
    try:
        probe = RetainedDirectoryChain(parent)
    except (InstallerError, OSError) as error:
        raise InstallerError(
            "FRESH_INSTALL_PARENT_REPLACED", "parent/ancestor identity chain changed"
        ) from error
    try:
        if len(probe.directories) != len(retained) or any(
            not old.identity.exact(os.fstat(new.descriptor))
            for old, new in zip(retained, probe.directories, strict=True)
        ):
            raise InstallerError(
                "FRESH_INSTALL_PARENT_REPLACED", "parent/ancestor identity chain changed"
            )
    finally:
        probe.close()
def _create_stage(
    chain: RetainedDirectoryChain, final_name: str, install_epoch: str
) -> StageHandle:
    stage_name = f".{final_name}.install-{install_epoch.removeprefix('install-')}"
    parent = chain.parent
    chain.verify()
    try:
        os.mkdir(stage_name, 0o700, dir_fd=parent.descriptor)
    except FileExistsError as error:
        raise InstallerError(
            "FRESH_INSTALL_STAGE_EXISTS", "stale staging is never adopted"
        ) from error
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_STAGING_FAILED", "cannot create staging directory"
        ) from error
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    created_identity: FileIdentity | None = None
    try:
        created = os.stat(stage_name, dir_fd=parent.descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(created.st_mode):
            raise InstallerError(
                "FRESH_INSTALL_STAGE_REPLACED", "created staging entry is no longer a directory"
            )
        created_identity = FileIdentity.from_stat(created)
        _test_hook("stage_mkdir_before_open")
        descriptor = os.open(stage_name, flags, dir_fd=parent.descriptor)
        observed = os.fstat(descriptor)
        if not created_identity.same_inode(observed):
            os.close(descriptor)
            raise InstallerError(
                "FRESH_INSTALL_STAGE_REPLACED",
                "staging directory was replaced before it could be retained",
            )
        os.fchmod(descriptor, 0o700)
        observed = os.fstat(descriptor)
        identity = FileIdentity.from_stat(observed)
        if identity.mode != 0o700 or identity.device != parent.identity.device:
            os.close(descriptor)
            raise InstallerError(
                "FRESH_INSTALL_STAGING_FAILED", "staging mode or filesystem is invalid"
            )
        return StageHandle(stage_name, descriptor, identity, parent)
    except InstallerError:
        if created_identity is not None:
            with contextlib.suppress(OSError):
                current = os.stat(stage_name, dir_fd=parent.descriptor, follow_symlinks=False)
                if created_identity.same_inode(current):
                    os.rmdir(stage_name, dir_fd=parent.descriptor)
        raise
    except OSError as error:
        if created_identity is not None:
            with contextlib.suppress(OSError):
                current = os.stat(stage_name, dir_fd=parent.descriptor, follow_symlinks=False)
                if created_identity.same_inode(current):
                    os.rmdir(stage_name, dir_fd=parent.descriptor)
        raise InstallerError(
            "FRESH_INSTALL_STAGING_FAILED", "cannot retain staging directory"
        ) from error
def _fixed_child_path(stage: StageHandle, name: str) -> Path:
    if name not in {DATABASE_NAME, BACKUP_NAME}:
        raise InstallerError(
            "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "SQLite child name is not fixed"
        )
    return stage.proc_root / name
def _entry_stat(stage_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_SQLITE_FAILED", f"cannot inspect staging entry {name}"
        ) from error
def _assert_regular_nofollow(stage_fd: int, name: str, *, positive: bool = False) -> os.stat_result:
    observed = _entry_stat(stage_fd, name)
    if observed is None or stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise InstallerError(
            "FRESH_INSTALL_SQLITE_FAILED", f"entry is not a regular no-follow file: {name}"
        )
    if positive and observed.st_size <= 0:
        raise InstallerError("FRESH_INSTALL_SQLITE_FAILED", f"entry is empty: {name}")
    return observed
def _stage_entries(stage_fd: int, allowed: set[str]) -> dict[str, FileIdentity]:
    try:
        names = os.listdir(stage_fd)
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_SQLITE_FAILED", "cannot enumerate staging fd"
        ) from error
    result: dict[str, FileIdentity] = {}
    for name in names:
        if name not in allowed:
            raise InstallerError("FRESH_INSTALL_SQLITE_RESIDUE", f"unknown staging entry: {name}")
        observed = os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
        if stat.S_ISLNK(observed.st_mode):
            raise InstallerError("FRESH_INSTALL_SQLITE_RESIDUE", f"symlink staging entry: {name}")
        result[name] = FileIdentity.from_stat(observed)
    return result
def _assert_identity_unchanged(
    before: Mapping[str, FileIdentity],
    after: Mapping[str, FileIdentity],
    *names: str,
) -> None:
    for name in names:
        if name not in before or name not in after or before[name] != after[name]:
            raise InstallerError(
                "FRESH_INSTALL_SQLITE_REPLACED",
                f"staged SQLite entry identity changed: {name}",
            )
def _fsync_fd(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise InstallerError("FRESH_INSTALL_FSYNC_FAILED", "fsync failed") from error
def _fsync_child(stage_fd: int, name: str) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=stage_fd)
    try:
        _fsync_fd(descriptor)
    finally:
        os.close(descriptor)
def sys_platform_linux() -> bool:
    return os.uname().sysname.lower() == "linux"
def _renameat2_function() -> Any:
    if os.name != "posix" or not sys_platform_linux():
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.renameat2
    except (AttributeError, OSError):
        return None
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    return function
def _require_publication_support() -> None:
    if _renameat2_function() is None:
        raise InstallerError(
            "FRESH_INSTALL_PUBLICATION_UNSUPPORTED",
            "Linux renameat2(RENAME_NOREPLACE) is unavailable",
        )
def _rename_noreplace(parent_fd: int, stage_name: str, target_name: str) -> None:
    function = _renameat2_function()
    if function is None:
        raise InstallerError("FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "renameat2 is unavailable")
    result = function(
        parent_fd,
        os.fsencode(stage_name),
        parent_fd,
        os.fsencode(target_name),
        RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise InstallerError("FRESH_INSTALL_TARGET_RACE", "target appeared before publication")
        if error_number in {errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL}:
            raise InstallerError(
                "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "renameat2 no-replace failed"
            )
        raise InstallerError("FRESH_INSTALL_PUBLICATION_FAILED", os.strerror(error_number))
def _snapshot_tree_fd(
    directory_fd: int, relative: tuple[str, ...] = ()
) -> dict[tuple[str, ...], FileIdentity]:
    """Read a no-follow recursive identity snapshot without granting ownership."""

    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as error:
        raise InstallerError("FRESH_INSTALL_CLEANUP_REFUSED", "cannot enumerate staging") from error
    snapshot: dict[tuple[str, ...], FileIdentity] = {}
    for name in names:
        try:
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as error:
            raise InstallerError(
                "FRESH_INSTALL_CLEANUP_REFUSED", f"cannot inspect staged entry: {name}"
            ) from error
        path = (*relative, name)
        if stat.S_ISLNK(observed.st_mode) or not (
            stat.S_ISREG(observed.st_mode) or stat.S_ISDIR(observed.st_mode)
        ):
            raise InstallerError(
                "FRESH_INSTALL_CLEANUP_REFUSED", f"staged entry has unsafe type: {'/'.join(path)}"
            )
        identity = FileIdentity.from_stat(observed)
        snapshot[path] = identity
        if stat.S_ISDIR(observed.st_mode):
            try:
                child_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise InstallerError(
                    "FRESH_INSTALL_CLEANUP_REFUSED",
                    f"cannot retain staged directory: {'/'.join(path)}",
                ) from error
            try:
                if not identity.exact(os.fstat(child_fd)):
                    raise InstallerError(
                        "FRESH_INSTALL_CLEANUP_REFUSED",
                        f"staged directory changed while being inspected: {'/'.join(path)}",
                    )
                snapshot.update(_snapshot_tree_fd(child_fd, path))
            finally:
                os.close(child_fd)
    return snapshot


def record_stage_ownership(stage: StageHandle) -> None:
    """Record identities created by the completed current invocation phase."""

    stage.verify()
    stage.owned_entries.clear()
    stage.owned_entries.update(_snapshot_tree_fd(stage.descriptor))


def reconcile_removed_stage_entries(stage: StageHandle) -> None:
    """Accept only disappearance of previously owned entries after a failed phase."""

    stage.verify()
    observed = _snapshot_tree_fd(stage.descriptor)
    if any(
        path not in stage.owned_entries or stage.owned_entries[path] != identity
        for path, identity in observed.items()
    ):
        raise InstallerError(
            "FRESH_INSTALL_CLEANUP_REFUSED",
            "failed phase added or replaced an entry outside its ownership manifest",
        )
    stage.owned_entries.clear()
    stage.owned_entries.update(observed)


def validate_stage_ownership(stage: StageHandle) -> None:
    """Refuse rather than adopt an entry absent from this invocation's manifest."""

    stage.verify()
    if _snapshot_tree_fd(stage.descriptor) != stage.owned_entries:
        raise InstallerError(
            "FRESH_INSTALL_CLEANUP_REFUSED",
            "staging inventory is not the current invocation ownership manifest",
        )


def _owned_child_names(
    manifest: dict[tuple[str, ...], FileIdentity], relative: tuple[str, ...]
) -> set[str]:
    depth = len(relative) + 1
    return {
        path[-1]
        for path in manifest
        if len(path) == depth and path[:-1] == relative
    }


def _remove_owned_tree_fd(
    parent_fd: int,
    relative: tuple[str, ...],
    manifest: dict[tuple[str, ...], FileIdentity],
) -> None:
    name = relative[-1]
    expected = manifest[relative]
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_CLEANUP_REFUSED", f"owned staged entry disappeared: {'/'.join(relative)}"
        ) from error
    if not expected.exact(observed):
        raise InstallerError(
            "FRESH_INSTALL_CLEANUP_REFUSED", f"owned staged entry changed: {'/'.join(relative)}"
        )
    _test_hook(f"cleanup_before_remove:{'/'.join(relative)}")
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not expected.exact(current):
        raise InstallerError(
            "FRESH_INSTALL_CLEANUP_REFUSED", f"owned staged entry changed: {'/'.join(relative)}"
        )
    if stat.S_ISDIR(current.st_mode):
        child_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            if not expected.exact(os.fstat(child_fd)):
                raise InstallerError(
                    "FRESH_INSTALL_CLEANUP_REFUSED",
                    f"owned staged directory changed: {'/'.join(relative)}",
                )
            names = set(os.listdir(child_fd))
            expected_names = _owned_child_names(manifest, relative)
            if names != expected_names:
                raise InstallerError(
                    "FRESH_INSTALL_CLEANUP_REFUSED",
                    f"owned staged directory inventory changed: {'/'.join(relative)}",
                )
            for child in sorted(expected_names):
                _remove_owned_tree_fd(child_fd, (*relative, child), manifest)
            if not expected.exact(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)):
                raise InstallerError(
                    "FRESH_INSTALL_CLEANUP_REFUSED",
                    f"owned staged directory changed: {'/'.join(relative)}",
                )
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)
        return
    if not stat.S_ISREG(current.st_mode):
        raise InstallerError(
            "FRESH_INSTALL_CLEANUP_REFUSED", f"owned entry has unsafe type: {'/'.join(relative)}"
        )
    os.unlink(name, dir_fd=parent_fd)


def cleanup_stage(stage: StageHandle, chain: RetainedDirectoryChain) -> None:
    """Remove only the exact entries owned by this invocation's completed phases."""

    chain.verify()
    validate_stage_ownership(stage)
    _test_hook("cleanup_after_validation")
    validate_stage_ownership(stage)
    for name in sorted(_owned_child_names(stage.owned_entries, ())):
        _remove_owned_tree_fd(stage.descriptor, (name,), stage.owned_entries)
    _fsync_fd(stage.descriptor)
    stage.verify()
    _test_hook("cleanup_before_stage_rmdir")
    stage.verify()
    os.rmdir(stage.name, dir_fd=stage.parent.descriptor)
    _fsync_fd(stage.parent.descriptor)
def _mkdir_at(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o700)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
            os.close(descriptor)
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", f"directory mode is not 0700: {name}"
            )
        return descriptor
    except FileExistsError as error:
        raise InstallerError(
            "FRESH_INSTALL_PROFILE_INVALID", f"duplicate staged directory: {name}"
        ) from error
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_PROFILE_INVALID", f"cannot create staged directory: {name}"
        ) from error
def _write_at(parent_fd: int, name: str, data: bytes) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        _fsync_fd(descriptor)
    finally:
        os.close(descriptor)
def _issue_epoch(epoch_factory: Callable[[], str] | None) -> str:
    value = epoch_factory() if epoch_factory is not None else f"install-{secrets.token_hex(32)}"
    if not isinstance(value, str) or len(value) != 72 or not value.startswith("install-"):
        raise InstallerError("FRESH_INSTALL_EPOCH_INVALID", "install epoch has the wrong shape")
    try:
        int(value.removeprefix("install-"), 16)
    except ValueError as error:
        raise InstallerError(
            "FRESH_INSTALL_EPOCH_INVALID", "install epoch is not lowercase hexadecimal"
        ) from error
    if value != value.lower():
        raise InstallerError(
            "FRESH_INSTALL_EPOCH_INVALID", "install epoch is not lowercase hexadecimal"
        )
    return value
def _verify_target_binding(intent: HostActivationIntent, target: TargetIdentity) -> None:
    if intent.runtime.runtime_home_digest != target.runtime_home_digest:
        raise InstallerError(
            "FRESH_INSTALL_IDENTITY_MISMATCH", "intent runtime_home_digest differs from target"
        )
    if intent.runtime.database_identity != target.database_identity:
        raise InstallerError(
            "FRESH_INSTALL_IDENTITY_MISMATCH", "intent database_identity differs from target"
        )
def _read_at(parent_fd: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)
def _publish(
    stage: StageHandle,
    chain: RetainedDirectoryChain,
    target: TargetIdentity,
    state: PublicationState,
) -> None:
    _test_hook("before_publish_fence")
    chain.verify()
    stage.verify()
    _test_hook("before_publish")
    _rename_noreplace(stage.parent.descriptor, stage.name, target.final_name)
    state.renamed = True
    _test_hook("after_publish")
def _verify_publication(
    stage: StageHandle,
    chain: RetainedDirectoryChain,
    target: TargetIdentity,
) -> None:
    try:
        published = os.stat(
            target.final_name, dir_fd=stage.parent.descriptor, follow_symlinks=False
        )
    except OSError as error:
        raise PublicationIndeterminate(
            "FRESH_INSTALL_PUBLICATION_FAILED", "published target cannot be read back"
        ) from error
    if not stat.S_ISDIR(published.st_mode) or not stage.identity.same_inode(published):
        raise PublicationIndeterminate(
            "FRESH_INSTALL_PUBLICATION_IDENTITY_MISMATCH", "target inode differs from staged inode"
        )
    try:
        _test_hook("before_parent_fsync")
        _fsync_fd(stage.parent.descriptor)
        _test_hook("after_parent_fsync")
    except InstallerError as error:
        raise PublicationIndeterminate(
            "FRESH_INSTALL_PUBLICATION_FAILED",
            "published parent durability is indeterminate",
        ) from error
    try:
        chain.verify()
    except InstallerError as error:
        raise PublicationIndeterminate(
            "FRESH_INSTALL_PARENT_REPLACED", "parent/ancestor changed during publication"
        ) from error
