"""Durable generation-bound grant-set authority for provider-ready activation.

This module owns only the post-generation grant-set file and its durable
current-generation readback. It deliberately does not import or mutate the
registry, migration, supervisor, listener, transport, or provider layers.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from aar.canonical import canonical_json_bytes
from aar.provider_ready_runtime_models import WorkbenchGrantSet

_MAX_COUNTER: Final = 9_223_372_036_854_775_807
_GRANT_SET_FILE: Final = "workbench-grant-set.json"
_GENERATION_RE: Final = re.compile(r"[0-9]{20}\Z")
_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700
_MAX_GRANT_SET_BYTES: Final = 1_048_576

_DIR_OPEN_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_OPEN_FLAGS: Final = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_WRITE_OPEN_FLAGS: Final = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
)


class ProviderReadyActivationError(RuntimeError):
    """Base class for fail-closed activation and grant-store errors."""

    code = "ACTIVATION_STORE_ERROR"


class ActivationStoreError(ProviderReadyActivationError):
    """The authority tree or canonical grant-set file is malformed or unsafe."""

    code = "ACTIVATION_STORE_INVALID"


class ActivationGenerationConflict(ProviderReadyActivationError):
    """A generation cannot be published without replacing existing authority."""

    code = "ACTIVATION_GENERATION_CONFLICT"


class ActivationBindingMismatch(ProviderReadyActivationError):
    """The supplied grant set is not bound to the requested current identity."""

    code = "ACTIVATION_BINDING_MISMATCH"



@dataclass(frozen=True, slots=True)
class ProviderReadyActivationResult:
    """Immutable result of one generation-bound grant-set publication."""

    grant_set: WorkbenchGrantSet
    path: Path
    runtime_generation: int
    replayed: bool

    @property
    def grant_set_digest(self) -> str:
        return self.grant_set.grant_set_digest


@dataclass(frozen=True, slots=True)
class _DirectoryHandle:
    fd: int
    created: bool


class ActivationGenerationStore:
    """Own the exact durable generation grant-set file and current readback.

    ``authority_root`` is the already-installed ``authority`` directory.  The
    store may create ``runtime-generations`` and a new generation directory
    below it; the authority root itself is never created, replaced, adopted,
    or repaired by C2.
    """

    def __init__(
        self,
        authority_root: str | os.PathLike[str],
        *,
        current_runtime_generation: int | None = None,
        lock: object | None = None,
    ) -> None:
        self._authority_root = _absolute_path(authority_root, "authority_root")
        if current_runtime_generation is not None:
            _validate_generation(current_runtime_generation)
        self._current_runtime_generation = current_runtime_generation
        self._current_grant_set: WorkbenchGrantSet | None = None
        self._lock = lock if lock is not None else threading.RLock()

    @property
    def authority_root(self) -> Path:
        """Return the caller-owned authority root without resolving it."""

        return self._authority_root

    @property
    def current_runtime_generation(self) -> int | None:
        """Return the current in-process generation, if this store has activated one."""

        with self._lock:
            return self._current_runtime_generation

    @property
    def current_grant_set(self) -> WorkbenchGrantSet | None:
        """Return the immutable current grant set, if this process has activated one."""

        with self._lock:
            return self._current_grant_set

    def path_for_generation(self, runtime_generation: int) -> Path:
        """Return the exact non-resolved canonical path for a generation."""

        generation = _validate_generation(runtime_generation)
        return self._generation_path(generation)

    # A descriptive alias for integration code that prefers a readback name.
    grant_set_path = path_for_generation

    def publish(
        self,
        grant_set: WorkbenchGrantSet,
        *,
        runtime_generation: int | None = None,
        activation_generation: int | None = None,
        profile_id: str | None = None,
        profile_digest: str | None = None,
        capability_digest: str | None = None,
        activation_authority_digest: str | None = None,
        route_catalog_digest: str | None = None,
    ) -> ProviderReadyActivationResult:
        """Publish one canonical grant set with no replacement semantics.

        Optional expected bindings are intended for the narrow supervisor seam.
        When supplied, every expected value must equal the corresponding field
        in ``grant_set``.  A same-generation replay is accepted only when its
        persisted bytes are byte-identical to the requested canonical bytes.
        """

        if not isinstance(grant_set, WorkbenchGrantSet):
            raise ActivationBindingMismatch("grant_set must be a WorkbenchGrantSet")
        (
            generation,
            expected_activation_generation,
            expected_profile_id,
            expected_profile_digest,
            expected_capability_digest,
            expected_activation_authority_digest,
            expected_route_catalog_digest,
        ) = _validate_bindings(
            grant_set,
            runtime_generation=runtime_generation,
            activation_generation=activation_generation,
            profile_id=profile_id,
            profile_digest=profile_digest,
            capability_digest=capability_digest,
            activation_authority_digest=activation_authority_digest,
            route_catalog_digest=route_catalog_digest,
        )
        expected_bytes = canonical_json_bytes(grant_set)

        with self._lock:
            current = self._current_runtime_generation
            if current is not None and generation < current:
                raise ActivationGenerationConflict(
                    f"runtime generation {generation} is older than current "
                    f"generation {current}"
                )

            authority = runtime_root = generation_dir = -1
            generation_created = False
            runtime_root_created = False
            temp_name: str | None = None
            published = False
            try:
                authority_handle = self._open_authority()
                authority = authority_handle.fd
                runtime_handle = self._open_child_directory(
                    authority,
                    "runtime-generations",
                    create=True,
                    role="runtime-generations",
                )
                runtime_root = runtime_handle.fd
                runtime_root_created = runtime_handle.created
                durable_generations = self._scan_durable_generations(runtime_root)
                self._establish_durable_fence(durable_generations)
                current = self._current_runtime_generation
                if current is not None and generation < current:
                    raise ActivationGenerationConflict(
                        f"runtime generation {generation} is older than current "
                        f"generation {current}"
                    )

                generation_name = _generation_name(generation)
                generation_handle = self._open_child_directory(
                    runtime_root,
                    generation_name,
                    create=True,
                    role=f"runtime generation {generation}",
                )
                generation_dir = generation_handle.fd
                generation_created = generation_handle.created
                existing = self._inspect_generation(
                    generation_dir,
                    generation_created=generation_created,
                    generation=generation,
                )
                if existing is not None:
                    existing_set, existing_bytes = existing
                    _check_persisted_binding(
                        existing_set,
                        runtime_generation=generation,
                        activation_generation=expected_activation_generation,
                        profile_id=expected_profile_id,
                        profile_digest=expected_profile_digest,
                        capability_digest=expected_capability_digest,
                        activation_authority_digest=expected_activation_authority_digest,
                        route_catalog_digest=expected_route_catalog_digest,
                    )
                    if existing_bytes != expected_bytes or existing_set != grant_set:
                        raise ActivationGenerationConflict(
                            "runtime generation "
                            f"{generation} already contains different grant-set bytes"
                        )
                    self._set_current(generation, existing_set)
                    return ProviderReadyActivationResult(
                        grant_set=existing_set,
                        path=self._generation_path(generation),
                        runtime_generation=generation,
                        replayed=True,
                    )

                # Persist the new generation directory entry before its file,
                # then make the exact file durable before exposing its name.
                if generation_created:
                    _fsync_fd(runtime_root, "runtime-generations directory")
                if runtime_root_created:
                    _fsync_fd(authority, "authority directory")

                temp_name = self._create_and_write_temp(generation_dir, expected_bytes)
                try:
                    self._publish_noreplace(
                        generation_dir,
                        temp_name,
                        expected_bytes,
                        grant_set,
                        generation,
                    )
                    published = True
                finally:
                    # The hard-link publication leaves the private temporary
                    # name in place until the canonical name is read back.
                    self._unlink_temp(generation_dir, temp_name)
                    temp_name = None

                _fsync_fd(generation_dir, f"runtime generation {generation} directory")
                _fsync_fd(runtime_root, "runtime-generations directory")
                readback_set, readback_bytes = self._read_persisted_file(
                    generation_dir,
                    generation=generation,
                    allow_multiple_links=False,
                )
                if readback_bytes != expected_bytes or readback_set != grant_set:
                    raise ActivationStoreError(
                        "published grant-set readback is not byte-identical to requested bytes"
                    )
                self._set_current(generation, readback_set)
                return ProviderReadyActivationResult(
                    grant_set=readback_set,
                    path=self._generation_path(generation),
                    runtime_generation=generation,
                    replayed=False,
                )
            except ProviderReadyActivationError:
                raise
            except (OSError, TypeError, ValueError) as error:
                raise ActivationStoreError(f"grant-set publication failed: {error}") from error
            finally:
                if temp_name is not None and generation_dir >= 0:
                    self._unlink_temp(generation_dir, temp_name)
                if generation_created and not published and runtime_root >= 0:
                    self._remove_new_empty_generation(runtime_root, generation)
                for fd in (generation_dir, runtime_root, authority):
                    if fd >= 0:
                        _close_fd(fd)

    # This is the narrow activation seam; it intentionally delegates all
    # factory/capability projection to the caller and owns only publication.
    def activate(
        self,
        *,
        runtime_generation: int,
        grant_set: WorkbenchGrantSet,
        activation_generation: int,
        profile_id: str,
        profile_digest: str,
        capability_digest: str,
        activation_authority_digest: str | None = None,
        route_catalog_digest: str | None = None,
    ) -> ProviderReadyActivationResult:
        return self.publish(
            grant_set,
            runtime_generation=runtime_generation,
            activation_generation=activation_generation,
            profile_id=profile_id,
            profile_digest=profile_digest,
            capability_digest=capability_digest,
            activation_authority_digest=activation_authority_digest,
            route_catalog_digest=route_catalog_digest,
        )

    publish_grant_set = publish

    def read(self, runtime_generation: int) -> WorkbenchGrantSet:
        """Read and independently validate one immutable generation file."""

        generation = _validate_generation(runtime_generation)
        with self._lock:
            authority = runtime_root = generation_dir = -1
            try:
                authority = self._open_authority().fd
                runtime_root = self._open_child_directory(
                    authority,
                    "runtime-generations",
                    create=False,
                    role="runtime-generations",
                ).fd
                self._scan_durable_generations(runtime_root)
                generation_dir = self._open_child_directory(
                    runtime_root,
                    _generation_name(generation),
                    create=False,
                    role=f"runtime generation {generation}",
                ).fd
                existing = self._inspect_generation(
                    generation_dir,
                    generation_created=False,
                    generation=generation,
                )
                if existing is None:
                    raise ActivationStoreError(
                        f"runtime generation {generation} has no canonical grant-set file"
                    )
                return existing[0]
            except ProviderReadyActivationError:
                raise
            except (OSError, TypeError, ValueError) as error:
                raise ActivationStoreError(f"grant-set read failed: {error}") from error
            finally:
                for fd in (generation_dir, runtime_root, authority):
                    if fd >= 0:
                        _close_fd(fd)

    readback = read

    def _set_current(
        self,
        generation: int,
        grant_set: WorkbenchGrantSet,
    ) -> None:
        if (
            self._current_runtime_generation is not None
            and generation < self._current_runtime_generation
        ):
            raise ActivationGenerationConflict(
                f"runtime generation {generation} is older than current generation "
                f"{self._current_runtime_generation}"
            )
        if (
            self._current_runtime_generation is not None
            and generation == self._current_runtime_generation
            and self._current_grant_set is not None
            and self._current_grant_set != grant_set
        ):
            raise ActivationGenerationConflict("current generation grant-set bytes changed")
        self._current_runtime_generation = generation
        self._current_grant_set = grant_set

    def verify_persisted(self, current_set: WorkbenchGrantSet) -> None:
        with self._lock:
            self._refresh_durable_fence()
            if (
                self._current_runtime_generation != current_set.runtime_generation
                or self._current_grant_set != current_set
            ):
                raise ActivationStoreError("current grant-set authority changed")
            persisted = self.read(current_set.runtime_generation)
            if persisted != current_set:
                raise ActivationStoreError("current grant-set authority changed")

    def _refresh_durable_fence(self) -> None:
        authority = runtime_root = -1
        try:
            with self._lock:
                authority = self._open_authority().fd
                runtime_root = self._open_child_directory(
                    authority,
                    "runtime-generations",
                    create=False,
                    role="runtime-generations",
                ).fd
                durable_generations = self._scan_durable_generations(runtime_root)
                self._establish_durable_fence(durable_generations)
        except ProviderReadyActivationError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise ActivationStoreError("current grant-set authority cannot be read back") from error
        finally:
            for fd in (runtime_root, authority):
                if fd >= 0:
                    _close_fd(fd)

    def _open_authority(self) -> _DirectoryHandle:
        parts = self._authority_root.parts
        fd = -1
        try:
            fd = os.open(os.sep, _DIR_OPEN_FLAGS)
            for component in parts[1:]:
                child = -1
                try:
                    child = os.open(component, _DIR_OPEN_FLAGS, dir_fd=fd)
                except FileNotFoundError as error:
                    raise ActivationStoreError(
                        f"authority path component {component!r} is missing"
                    ) from error
                except OSError as error:
                    raise ActivationStoreError(
                        f"authority path component {component!r} is not a safe directory"
                    ) from error
                _close_fd(fd)
                fd = child
            _check_directory_fd(fd, mode=_DIRECTORY_MODE, role="authority")
            return _DirectoryHandle(fd=fd, created=False)
        except ProviderReadyActivationError:
            if fd >= 0:
                _close_fd(fd)
            raise
        except OSError as error:
            if fd >= 0:
                _close_fd(fd)
            raise ActivationStoreError(f"authority path cannot be opened: {error}") from error

    def _open_child_directory(
        self,
        parent_fd: int,
        name: str,
        *,
        create: bool,
        role: str,
    ) -> _DirectoryHandle:
        created = False
        try:
            try:
                fd = os.open(name, _DIR_OPEN_FLAGS, dir_fd=parent_fd)
            except FileNotFoundError as error:
                if not create:
                    raise ActivationStoreError(f"{role} is missing") from error
                try:
                    os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent_fd)
                    created = True
                except FileExistsError:
                    pass
                fd = os.open(name, _DIR_OPEN_FLAGS, dir_fd=parent_fd)
            _check_directory_fd(fd, mode=_DIRECTORY_MODE, role=role)
            return _DirectoryHandle(fd=fd, created=created)
        except ProviderReadyActivationError:
            raise
        except OSError as error:
            raise ActivationStoreError(f"{role} is not a safe directory") from error

    def _scan_durable_generations(
        self,
        runtime_root_fd: int,
    ) -> dict[int, tuple[WorkbenchGrantSet, bytes]]:
        """Validate and read every durable generation before activation.

        The generation directory names are the durable successor history.  A
        current pointer is intentionally not persisted here: every activation
        scans the exact validated history and fences requests against its
        highest generation.  Gaps are valid, but every present generation must
        contain exactly one valid canonical grant-set file.
        """

        generations: dict[int, tuple[WorkbenchGrantSet, bytes]] = {}
        for name in self._validate_runtime_root_entries(runtime_root_fd):
            generation = int(name)
            generation_fd = -1
            try:
                generation_fd = self._open_child_directory(
                    runtime_root_fd,
                    name,
                    create=False,
                    role=f"runtime generation {generation}",
                ).fd
                existing = self._inspect_generation(
                    generation_fd,
                    generation_created=False,
                    generation=generation,
                )
                if existing is None:
                    raise ActivationStoreError(
                        f"runtime generation {generation} has no canonical grant-set file"
                    )
                generations[generation] = existing
            except ProviderReadyActivationError:
                raise
            except OSError as error:
                raise ActivationStoreError(
                    f"runtime generation {generation} cannot be scanned"
                ) from error
            finally:
                if generation_fd >= 0:
                    _close_fd(generation_fd)
        return generations

    def _establish_durable_fence(
        self,
        generations: dict[int, tuple[WorkbenchGrantSet, bytes]],
    ) -> None:
        """Adopt the highest validated durable generation as the local fence."""

        if not generations:
            return
        highest = max(generations)
        highest_set = generations[highest][0]
        current = self._current_runtime_generation
        if current is None or current < highest:
            self._current_runtime_generation = highest
            self._current_grant_set = highest_set
            return
        if current == highest:
            if self._current_grant_set is None:
                self._current_grant_set = highest_set
            elif self._current_grant_set != highest_set:
                raise ActivationGenerationConflict(
                    "current generation grant-set bytes changed"
                )

    def _validate_runtime_root_entries(self, runtime_root_fd: int) -> tuple[str, ...]:
        try:
            names = os.listdir(runtime_root_fd)
        except OSError as error:
            raise ActivationStoreError("runtime-generations directory cannot be listed") from error
        valid_names: list[str] = []
        for name in names:
            if _GENERATION_RE.fullmatch(name) is None:
                raise ActivationStoreError(
                    f"unknown runtime-generations sibling {name!r} refuses activation"
                )
            generation = int(name)
            _validate_generation(generation)
            try:
                entry = os.stat(name, dir_fd=runtime_root_fd, follow_symlinks=False)
            except OSError as error:
                raise ActivationStoreError(
                    f"runtime generation entry {name!r} cannot be inspected"
                ) from error
            if not stat.S_ISDIR(entry.st_mode):
                raise ActivationStoreError(f"runtime generation entry {name!r} is not a directory")
            if entry.st_uid != _owner_uid():
                raise ActivationStoreError(f"runtime generation entry {name!r} has the wrong owner")
            if stat.S_IMODE(entry.st_mode) != _DIRECTORY_MODE:
                raise ActivationStoreError(f"runtime generation entry {name!r} has the wrong mode")
            valid_names.append(name)
        return tuple(sorted(valid_names))

    def _inspect_generation(
        self,
        generation_fd: int,
        *,
        generation_created: bool,
        generation: int,
    ) -> tuple[WorkbenchGrantSet, bytes] | None:
        try:
            names = os.listdir(generation_fd)
        except OSError as error:
            raise ActivationStoreError(
                f"runtime generation {generation} directory cannot be listed"
            ) from error
        if not names:
            if generation_created:
                return None
            raise ActivationGenerationConflict(
                f"runtime generation {generation} is a partial directory"
            )
        if any(name != _GRANT_SET_FILE for name in names):
            raise ActivationGenerationConflict(
                f"runtime generation {generation} contains unknown siblings"
            )
        return self._read_persisted_file(
            generation_fd,
            generation=generation,
            allow_multiple_links=False,
        )

    def _create_and_write_temp(self, generation_fd: int, content: bytes) -> str:
        for _ in range(16):
            name = f".{_GRANT_SET_FILE}.tmp-{uuid.uuid4().hex}"
            fd = -1
            try:
                fd = os.open(name, _WRITE_OPEN_FLAGS, _FILE_MODE, dir_fd=generation_fd)
                os.fchmod(fd, _FILE_MODE)
                _write_all(fd, content)
                os.fsync(fd)
                observed = os.fstat(fd)
                if not stat.S_ISREG(observed.st_mode):
                    raise ActivationStoreError("temporary grant-set entry is not a regular file")
                if observed.st_uid != _owner_uid() or stat.S_IMODE(observed.st_mode) != _FILE_MODE:
                    raise ActivationStoreError("temporary grant-set entry has unsafe identity")
                return name
            except FileExistsError:
                continue
            except ProviderReadyActivationError:
                raise
            except OSError as error:
                raise ActivationStoreError("temporary grant-set file cannot be written") from error
            finally:
                if fd >= 0:
                    _close_fd(fd)
        raise ActivationStoreError("could not allocate an exclusive temporary grant-set name")

    def _publish_noreplace(
        self,
        generation_fd: int,
        temp_name: str,
        expected_bytes: bytes,
        expected_set: WorkbenchGrantSet,
        generation: int,
    ) -> None:
        try:
            # linkat(2) with a fresh destination is an atomic no-replace
            # publication on the same directory/filesystem.  The private
            # temporary name is removed only after exact readback.
            os.link(
                temp_name,
                _GRANT_SET_FILE,
                src_dir_fd=generation_fd,
                dst_dir_fd=generation_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            existing = self._read_persisted_file(
                generation_fd,
                generation=generation,
                allow_multiple_links=False,
            )
            if existing[1] == expected_bytes and existing[0] == expected_set:
                return
            raise ActivationGenerationConflict(
                f"runtime generation {generation} was published with different bytes"
            ) from None
        except OSError as error:
            raise ActivationStoreError("atomic no-replace grant-set publication failed") from error

        readback = self._read_persisted_file(
            generation_fd,
            generation=generation,
            allow_multiple_links=True,
        )
        if readback[1] != expected_bytes or readback[0] != expected_set:
            raise ActivationStoreError("grant-set bytes changed during publication readback")

    def _read_persisted_file(
        self,
        generation_fd: int,
        *,
        generation: int,
        allow_multiple_links: bool,
    ) -> tuple[WorkbenchGrantSet, bytes]:
        try:
            entry = os.stat(
                _GRANT_SET_FILE,
                dir_fd=generation_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ActivationStoreError(
                f"runtime generation {generation} canonical grant-set file is missing"
            ) from error
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            raise ActivationStoreError("canonical grant-set path is not a regular non-symlink file")
        if entry.st_uid != _owner_uid():
            raise ActivationStoreError("canonical grant-set file has the wrong owner")
        if stat.S_IMODE(entry.st_mode) != _FILE_MODE:
            raise ActivationStoreError("canonical grant-set file has the wrong mode")
        if (not allow_multiple_links and entry.st_nlink != 1) or (
            allow_multiple_links and entry.st_nlink < 1
        ):
            raise ActivationStoreError("canonical grant-set file has an unsafe link count")

        fd = -1
        try:
            fd = os.open(_GRANT_SET_FILE, _READ_OPEN_FLAGS, dir_fd=generation_fd)
            opened = os.fstat(fd)
            if stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode):
                raise ActivationStoreError("canonical grant-set readback is not regular")
            if opened.st_uid != _owner_uid() or stat.S_IMODE(opened.st_mode) != _FILE_MODE:
                raise ActivationStoreError("canonical grant-set readback identity changed")
            content = _read_limited(fd, _MAX_GRANT_SET_BYTES)
            if opened.st_size != len(content):
                raise ActivationStoreError("canonical grant-set file changed during readback")
        except ProviderReadyActivationError:
            raise
        except OSError as error:
            raise ActivationStoreError("canonical grant-set file cannot be read") from error
        finally:
            if fd >= 0:
                _close_fd(fd)

        try:
            parsed = WorkbenchGrantSet.model_validate_json(content, strict=True)
        except (TypeError, ValueError) as error:
            raise ActivationStoreError("canonical grant-set file is malformed") from error
        if canonical_json_bytes(parsed) != content:
            raise ActivationStoreError("canonical grant-set file is not canonical bytes")
        if parsed.runtime_generation != generation:
            raise ActivationStoreError(
                "canonical grant-set runtime generation does not match its directory"
            )
        return parsed, content

    def _unlink_temp(self, generation_fd: int, name: str) -> None:
        try:
            os.unlink(name, dir_fd=generation_fd)
        except FileNotFoundError:
            return
        except OSError:
            # Leave evidence rather than deleting an entry selected by a path
            # that no longer names this invocation's private file.
            return

    def _remove_new_empty_generation(self, runtime_root_fd: int, generation: int) -> None:
        name = _generation_name(generation)
        generation_fd = -1
        try:
            generation_fd = os.open(name, _DIR_OPEN_FLAGS, dir_fd=runtime_root_fd)
            if os.listdir(generation_fd):
                return
        except OSError:
            return
        finally:
            if generation_fd >= 0:
                _close_fd(generation_fd)
        with contextlib.suppress(OSError):
            os.rmdir(name, dir_fd=runtime_root_fd)

    def _generation_path(self, runtime_generation: int) -> Path:
        return (
            self._authority_root
            / "runtime-generations"
            / _generation_name(runtime_generation)
            / _GRANT_SET_FILE
        )


def _absolute_path(value: str | os.PathLike[str], field_name: str) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise ActivationStoreError(f"{field_name} must be a path") from error
    if isinstance(raw, bytes):
        raise ActivationStoreError(f"{field_name} must use text path components")
    if "\x00" in raw:
        raise ActivationStoreError(f"{field_name} contains NUL")
    path = Path(raw)
    if not path.is_absolute() or len(path.parts) < 2:
        raise ActivationStoreError(f"{field_name} must be an absolute non-root path")
    if any(part in {".", ".."} for part in path.parts[1:]):
        raise ActivationStoreError(f"{field_name} contains a traversal component")
    return path


def _validate_generation(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_COUNTER:
        raise ActivationBindingMismatch("runtime_generation must be a positive strict integer")
    return value


def _generation_name(runtime_generation: int) -> str:
    return f"{_validate_generation(runtime_generation):020d}"


def _validate_bindings(
    grant_set: WorkbenchGrantSet,
    *,
    runtime_generation: int | None,
    activation_generation: int | None,
    profile_id: str | None,
    profile_digest: str | None,
    capability_digest: str | None,
    activation_authority_digest: str | None,
    route_catalog_digest: str | None,
) -> tuple[int, int, str, str, str, str, str]:
    generation = grant_set.runtime_generation if runtime_generation is None else runtime_generation
    generation = _validate_generation(generation)
    expected = (
        grant_set.activation_generation if activation_generation is None else activation_generation,
        grant_set.profile_id if profile_id is None else profile_id,
        grant_set.profile_digest if profile_digest is None else profile_digest,
        grant_set.capability_digest if capability_digest is None else capability_digest,
        (
            grant_set.activation_authority_digest
            if activation_authority_digest is None
            else activation_authority_digest
        ),
        grant_set.route_catalog_digest if route_catalog_digest is None else route_catalog_digest,
    )
    _check_persisted_binding(
        grant_set,
        runtime_generation=generation,
        activation_generation=expected[0],
        profile_id=expected[1],
        profile_digest=expected[2],
        capability_digest=expected[3],
        activation_authority_digest=expected[4],
        route_catalog_digest=expected[5],
    )
    return (generation, *expected)


def _check_persisted_binding(
    grant_set: WorkbenchGrantSet,
    *,
    runtime_generation: int,
    activation_generation: int,
    profile_id: str,
    profile_digest: str,
    capability_digest: str,
    activation_authority_digest: str,
    route_catalog_digest: str,
) -> None:
    checks = (
        ("runtime_generation", runtime_generation, grant_set.runtime_generation),
        ("activation_generation", activation_generation, grant_set.activation_generation),
        ("profile_id", profile_id, grant_set.profile_id),
        ("profile_digest", profile_digest, grant_set.profile_digest),
        ("capability_digest", capability_digest, grant_set.capability_digest),
        (
            "activation_authority_digest",
            activation_authority_digest,
            grant_set.activation_authority_digest,
        ),
        ("route_catalog_digest", route_catalog_digest, grant_set.route_catalog_digest),
    )
    for field_name, expected, actual in checks:
        if expected != actual:
            raise ActivationBindingMismatch(
                f"grant-set {field_name} does not match the current activation binding"
            )


def _owner_uid() -> int:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None:
        return int(geteuid())
    return int(os.stat(".").st_uid)


def _check_directory_fd(fd: int, *, mode: int | None, role: str) -> None:
    try:
        observed = os.fstat(fd)
    except OSError as error:
        raise ActivationStoreError(f"{role} cannot be inspected") from error
    if not stat.S_ISDIR(observed.st_mode):
        raise ActivationStoreError(f"{role} is not a directory")
    if observed.st_uid != _owner_uid():
        raise ActivationStoreError(f"{role} has the wrong owner")
    actual_mode = stat.S_IMODE(observed.st_mode)
    if mode is not None and actual_mode != mode:
        raise ActivationStoreError(f"{role} has mode {actual_mode:o}, expected {mode:o}")
    if mode is None and actual_mode & 0o022:
        raise ActivationStoreError(f"{role} is group/world writable")


def _fsync_fd(fd: int, role: str) -> None:
    try:
        os.fsync(fd)
    except OSError as error:
        raise ActivationStoreError(f"{role} could not be fsynced") from error


def _write_all(fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise ActivationStoreError("short write while publishing grant-set bytes")
        offset += written


def _read_limited(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65_536, limit - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise ActivationStoreError("canonical grant-set file exceeds the bounded size")
        chunks.append(chunk)


def _close_fd(fd: int) -> None:
    with contextlib.suppress(OSError):
        os.close(fd)


__all__ = [
    "ActivationBindingMismatch",
    "ActivationGenerationConflict",
    "ActivationGenerationStore",
    "ActivationStoreError",
    "ProviderReadyActivationError",
    "ProviderReadyActivationResult",
]
