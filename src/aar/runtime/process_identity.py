"""Fail-closed native process identity and supervisor discovery contracts.

The process identity helpers intentionally do not terminate processes.  They only read the
native start identity needed to distinguish a live owner from a recycled PID.
"""

from __future__ import annotations

import ctypes
import os
import select
import signal
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from aar.canonical import canonical_sha256
from aar.schemas import Digest, PositiveCounter, StrictModel

PROCESS_IDENTITY_SCHEMA_VERSION = "aar.process-identity.v1"
DISCOVERY_SCHEMA_VERSION = "aar.supervisor.discovery.v1"
DEFAULT_SUPERVISOR_VERSION = "aar-supervisor-unknown"
DEFAULT_PROTOCOL_VERSION = "aar.supervisor.protocol.v1"

IdentityText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512, strict=True),
]
EndpointRef = Annotated[
    str,
    StringConstraints(min_length=1, max_length=2048, strict=True),
]
VersionText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, strict=True),
]
EndpointKind = Literal["unix", "tcp"]
IdentityPlatform = Literal["linux", "windows"]


class ProcessIdentityUnavailable(RuntimeError):
    """Native process-start identity could not be obtained safely."""


class ProcessIdentityNotFound(ProcessIdentityUnavailable):
    """The operating system conclusively reports that the PID is absent."""


class ProcessIdentityMismatch(RuntimeError):
    """An exact process handle could not be bound to the recorded identity."""


class ProcessIdentityState(StrEnum):
    """Three-state result for safety-sensitive process ownership decisions."""

    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ProcessIdentityObservation:
    """One native identity observation without collapsing uncertainty into absence."""

    state: ProcessIdentityState
    observed: ProcessStartIdentity | None = None
    error: ProcessIdentityUnavailable | None = None


class ProcessStartIdentity(StrictModel):
    """PID plus the platform-native process-start identity.

    Linux/WSL uses the kernel boot ID and ``/proc/<pid>/stat`` starttime ticks.  Windows
    uses the creation FILETIME returned by ``GetProcessTimes``.  The combination is
    intentionally represented as exact values rather than as a PID-only claim.
    """

    schema_version: Literal["aar.process-identity.v1"] = PROCESS_IDENTITY_SCHEMA_VERSION
    pid: PositiveCounter
    platform: IdentityPlatform
    start_time: PositiveCounter
    boot_id: IdentityText | None = None

    @model_validator(mode="after")
    def native_identity_is_complete(self) -> Self:
        if self.platform == "linux" and not self.boot_id:
            raise ValueError("linux process identity requires boot_id")
        if self.platform == "windows" and self.boot_id is not None:
            raise ValueError("windows process identity must not contain a boot_id")
        return self

    @property
    def native_identity(self) -> str:
        """Return a stable, human-opaque rendering of the native start identity."""

        if self.platform == "linux":
            assert self.boot_id is not None
            return f"linux:{self.boot_id}:{self.start_time}"
        return f"windows:{self.start_time}"

    @property
    def native_start_identity(self) -> str:
        """Compatibility spelling for callers that name the identity explicitly."""

        return self.native_identity

    @property
    def start_time_ticks(self) -> int:
        """Linux starttime ticks, or the native Windows creation-time integer."""

        return self.start_time

    @property
    def native_start_time(self) -> int:
        """Compatibility spelling for the exact native start-time value."""

        return self.start_time


class SupervisorDiscoveryRecord(StrictModel):
    """Digest-bound, credential-free supervisor discovery information."""

    schema_version: Literal["aar.supervisor.discovery.v1"] = DISCOVERY_SCHEMA_VERSION
    endpoint_kind: EndpointKind
    endpoint_ref: EndpointRef
    pid: PositiveCounter
    process_identity: ProcessStartIdentity
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    capability_digest: Digest
    runtime_home_digest: Digest
    ready_at_unix_ms: PositiveCounter
    supervisor_version: VersionText
    protocol_version: VersionText
    attachment_credential_digest: Digest
    discovery_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        endpoint_kind: EndpointKind,
        endpoint_ref: str,
        process_identity: ProcessStartIdentity,
        runtime_generation: int,
        dispatcher_generation: int,
        capability_digest: str,
        runtime_home_digest: str,
        ready_at_unix_ms: int,
        attachment_credential_digest: str,
        supervisor_version: str = DEFAULT_SUPERVISOR_VERSION,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        pid: int | None = None,
    ) -> SupervisorDiscoveryRecord:
        resolved_pid = process_identity.pid if pid is None else pid
        payload = {
            "schema_version": DISCOVERY_SCHEMA_VERSION,
            "endpoint_kind": endpoint_kind,
            "endpoint_ref": endpoint_ref,
            "pid": resolved_pid,
            "process_identity": process_identity.model_dump(mode="json"),
            "runtime_generation": runtime_generation,
            "dispatcher_generation": dispatcher_generation,
            "capability_digest": capability_digest,
            "runtime_home_digest": runtime_home_digest,
            "ready_at_unix_ms": ready_at_unix_ms,
            "supervisor_version": supervisor_version,
            "protocol_version": protocol_version,
            "attachment_credential_digest": attachment_credential_digest,
        }
        return cls(**payload, discovery_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def record_is_bound_and_self_digest_valid(self) -> Self:
        if self.pid != self.process_identity.pid:
            raise ValueError("discovery pid must match process identity pid")
        payload = self.model_dump(mode="json", exclude={"discovery_digest"})
        if self.discovery_digest != canonical_sha256(payload):
            raise ValueError("discovery digest does not match canonical discovery bytes")
        return self

    def canonical_payload(self) -> dict[str, object]:
        """Return the exact field set covered by ``discovery_digest``."""

        return self.model_dump(mode="json", exclude={"discovery_digest"})

    def validate_discovery_digest(self) -> SupervisorDiscoveryRecord:
        """Re-run the self-digest check for callers validating persisted records."""

        if self.discovery_digest != canonical_sha256(self.canonical_payload()):
            raise ValueError("discovery digest does not match canonical discovery bytes")
        return self


# A short name is useful at the private protocol boundary and preserves one canonical model.
DiscoveryRecord = SupervisorDiscoveryRecord
ProcessIdentity = ProcessStartIdentity
NativeProcessIdentity = ProcessStartIdentity


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


def _validate_pid(pid: int | None) -> int:
    target = os.getpid() if pid is None else pid
    if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
        raise ProcessIdentityUnavailable("process identity requires a positive integer PID")
    return target


def _linux_process_identity(pid: int) -> ProcessStartIdentity:
    stat_path = Path("/proc") / str(pid) / "stat"
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    try:
        boot_id = boot_id_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise ProcessIdentityUnavailable("native Linux boot identity is unavailable") from error
    try:
        stat_text = stat_path.read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError) as error:
        raise ProcessIdentityNotFound(f"native Linux process PID {pid} is absent") from error
    except (OSError, UnicodeError) as error:
        raise ProcessIdentityUnavailable(
            f"native Linux process identity unavailable for PID {pid}"
        ) from error

    # The comm field may contain spaces and closing parentheses.  The final ') ' is the
    # delimiter before the state field; starttime is field 22, token 19 after that delimiter.
    delimiter = stat_text.rfind(") ")
    if delimiter < 0:
        raise ProcessIdentityUnavailable(f"malformed /proc/{pid}/stat")
    fields = stat_text[delimiter + 2 :].split()
    if len(fields) <= 19:
        raise ProcessIdentityUnavailable(f"/proc/{pid}/stat has no starttime field")
    try:
        start_time = int(fields[19])
    except ValueError as error:
        raise ProcessIdentityUnavailable(f"invalid /proc/{pid}/stat starttime") from error
    if start_time <= 0 or not boot_id:
        raise ProcessIdentityUnavailable(f"missing native Linux start identity for PID {pid}")

    try:
        return ProcessStartIdentity(
            pid=pid,
            platform="linux",
            start_time=start_time,
            boot_id=boot_id,
        )
    except ValueError as error:
        raise ProcessIdentityUnavailable(
            f"invalid native Linux start identity for PID {pid}"
        ) from error


def _windows_process_start_time(pid: int) -> int:
    """Read a Windows process creation FILETIME through the native API."""

    if os.name != "nt":
        raise ProcessIdentityUnavailable("Windows process identity is unsupported on this platform")

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(process_query_limited_information, 0, pid)
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code in {87, 1168}:
            raise ProcessIdentityNotFound(f"native Windows process PID {pid} is absent")
        raise ProcessIdentityUnavailable(
            f"GetProcessTimes could not open PID {pid} (winerror={error_code})"
        )
    creation = _FILETIME()
    exit_time = _FILETIME()
    kernel_time = _FILETIME()
    user_time = _FILETIME()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            error_code = ctypes.get_last_error()
            raise ProcessIdentityUnavailable(
                f"GetProcessTimes failed for PID {pid} (winerror={error_code})"
            )
        value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        exit_value = (int(exit_time.dwHighDateTime) << 32) | int(exit_time.dwLowDateTime)
        if exit_value:
            raise ProcessIdentityNotFound(
                f"native Windows process PID {pid} has terminated"
            )
        if value <= 0:
            raise ProcessIdentityUnavailable(
                f"GetProcessTimes returned no creation time for PID {pid}"
            )
        return value
    finally:
        kernel32.CloseHandle(handle)


def _windows_process_identity(pid: int) -> ProcessStartIdentity:
    try:
        start_time = _windows_process_start_time(pid)
        return ProcessStartIdentity(pid=pid, platform="windows", start_time=start_time)
    except ProcessIdentityUnavailable:
        raise
    except (OSError, ValueError, AttributeError, ctypes.ArgumentError) as error:
        raise ProcessIdentityUnavailable(
            f"native Windows process identity unavailable for PID {pid}"
        ) from error


def process_identity(pid: int | None = None) -> ProcessStartIdentity:
    """Return the exact native process-start identity, or fail closed.

    Only Linux/WSL and Windows are supported.  Other platforms are deliberately rejected
    rather than substituting a PID, wall-clock timestamp, or best-effort placeholder.
    """

    target = _validate_pid(pid)
    if os.name == "nt":
        return _windows_process_identity(target)
    if sys.platform.startswith("linux"):
        return _linux_process_identity(target)
    raise ProcessIdentityUnavailable(
        f"native process identity is unsupported on platform {sys.platform!r}"
    )


def current_process_identity() -> ProcessStartIdentity:
    """Return the current process's exact native start identity."""

    return process_identity(os.getpid())


def observe_process_identity(identity: ProcessStartIdentity) -> ProcessIdentityObservation:
    """Observe one recorded identity as MATCH, MISMATCH, or UNAVAILABLE."""

    try:
        observed = process_identity(identity.pid)
    except ProcessIdentityNotFound:
        return ProcessIdentityObservation(state=ProcessIdentityState.MISMATCH)
    except ProcessIdentityUnavailable as error:
        return ProcessIdentityObservation(
            state=ProcessIdentityState.UNAVAILABLE,
            error=error,
        )
    return ProcessIdentityObservation(
        state=(
            ProcessIdentityState.MATCH
            if observed == identity
            else ProcessIdentityState.MISMATCH
        ),
        observed=observed,
    )


def process_identity_matches(identity: ProcessStartIdentity) -> bool:
    """Return exact identity equality and raise when observation is unavailable."""

    observation = observe_process_identity(identity)
    if observation.state is ProcessIdentityState.UNAVAILABLE:
        assert observation.error is not None
        raise observation.error
    return observation.state is ProcessIdentityState.MATCH


def _windows_start_time_from_handle(kernel32: object, handle: int, pid: int) -> int:
    creation = _FILETIME()
    exit_time = _FILETIME()
    kernel_time = _FILETIME()
    user_time = _FILETIME()
    if not kernel32.GetProcessTimes(  # type: ignore[attr-defined]
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        error_code = ctypes.get_last_error()
        raise ProcessIdentityUnavailable(
            f"GetProcessTimes failed for PID {pid} (winerror={error_code})"
        )
    value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    exit_value = (int(exit_time.dwHighDateTime) << 32) | int(exit_time.dwLowDateTime)
    if exit_value:
        raise ProcessIdentityNotFound(f"native Windows process PID {pid} has terminated")
    if value <= 0:
        raise ProcessIdentityUnavailable(
            f"GetProcessTimes returned no creation time for PID {pid}"
        )
    return value


class ExactProcessHandle:
    """Race-free signal and terminal-readback handle for one recorded process."""

    def __init__(
        self,
        identity: ProcessStartIdentity,
        *,
        kind: Literal["linux-pidfd", "windows-handle"],
        handle: int,
        kernel32: object | None = None,
    ) -> None:
        self.identity = identity
        self.kind = kind
        self.handle = handle
        self._kernel32 = kernel32
        self.last_error: int | None = None
        self._closed = False

    def __enter__(self) -> ExactProcessHandle:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def send_signal(self, signum: int) -> bool:
        """Signal only the process instance bound to this handle."""

        if self._closed:
            raise ProcessIdentityUnavailable("exact process handle is closed")
        if self.kind == "linux-pidfd":
            sender = getattr(signal, "pidfd_send_signal", None)
            if sender is None:
                raise ProcessIdentityUnavailable("pidfd_send_signal is unavailable")
            try:
                sender(self.handle, signum)
            except ProcessLookupError:
                return False
            except OSError as error:
                raise ProcessIdentityUnavailable(
                    f"pidfd signal failed for PID {self.identity.pid}"
                ) from error
            return True

        assert self._kernel32 is not None
        if self._kernel32.TerminateProcess(self.handle, 0):  # type: ignore[attr-defined]
            return True
        self.last_error = ctypes.get_last_error()
        return False

    def wait(self, timeout_sec: float) -> bool:
        """Return true only after terminal state is observed through this exact handle."""

        if timeout_sec < 0:
            raise ValueError("timeout_sec must be non-negative")
        if self._closed:
            raise ProcessIdentityUnavailable("exact process handle is closed")
        if self.kind == "linux-pidfd":
            poller = select.poll()
            poller.register(self.handle, select.POLLIN)
            timeout_ms = min(int(timeout_sec * 1_000), 2_147_483_647)
            return bool(poller.poll(timeout_ms))

        assert self._kernel32 is not None
        timeout_ms = min(int(timeout_sec * 1_000), 0xFFFFFFFE)
        result = int(
            self._kernel32.WaitForSingleObject(self.handle, timeout_ms)  # type: ignore[attr-defined]
        )
        if result == 0:
            return True
        if result == 0x00000102:
            return False
        error_code = ctypes.get_last_error()
        raise ProcessIdentityUnavailable(
            f"WaitForSingleObject failed for PID {self.identity.pid} "
            f"(winerror={error_code})"
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.kind == "linux-pidfd":
            os.close(self.handle)
            return
        assert self._kernel32 is not None
        self._kernel32.CloseHandle(self.handle)  # type: ignore[attr-defined]


def open_exact_process(
    identity: ProcessStartIdentity,
    *,
    terminate: bool = False,
) -> ExactProcessHandle:
    """Open and post-open verify a race-free handle for one recorded identity."""

    if os.name == "nt":
        if identity.platform != "windows":
            raise ProcessIdentityMismatch("recorded process platform is not Windows")
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        process_terminate = 0x0001
        access = process_query_limited_information | synchronize
        if terminate:
            access |= process_terminate
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(access, 0, identity.pid)
        if not handle:
            error_code = ctypes.get_last_error()
            if error_code in {87, 1168}:
                raise ProcessIdentityMismatch(
                    f"recorded process PID {identity.pid} is absent"
                )
            raise ProcessIdentityUnavailable(
                f"could not open exact process PID {identity.pid} (winerror={error_code})"
            )
        try:
            start_time = _windows_start_time_from_handle(kernel32, handle, identity.pid)
            if start_time != identity.start_time:
                raise ProcessIdentityMismatch(
                    f"PID {identity.pid} creation time does not match recorded identity"
                )
            return ExactProcessHandle(
                identity,
                kind="windows-handle",
                handle=handle,
                kernel32=kernel32,
            )
        except ProcessIdentityNotFound as error:
            kernel32.CloseHandle(handle)
            raise ProcessIdentityMismatch(
                f"recorded process PID {identity.pid} has terminated"
            ) from error
        except BaseException:
            kernel32.CloseHandle(handle)
            raise

    if not sys.platform.startswith("linux") or identity.platform != "linux":
        raise ProcessIdentityUnavailable("exact process handles require Linux/WSL or Windows")
    opener = getattr(os, "pidfd_open", None)
    if opener is None or getattr(signal, "pidfd_send_signal", None) is None:
        raise ProcessIdentityUnavailable("Linux pidfd signalling support is unavailable")
    try:
        descriptor = opener(identity.pid, 0)
    except ProcessLookupError as error:
        raise ProcessIdentityMismatch(
            f"recorded process PID {identity.pid} is absent"
        ) from error
    except OSError as error:
        raise ProcessIdentityUnavailable(
            f"could not open pidfd for PID {identity.pid}"
        ) from error
    try:
        observation = observe_process_identity(identity)
        if observation.state is ProcessIdentityState.MISMATCH:
            raise ProcessIdentityMismatch(
                f"PID {identity.pid} does not match recorded identity after pidfd open"
            )
        if observation.state is ProcessIdentityState.UNAVAILABLE:
            assert observation.error is not None
            raise observation.error
        return ExactProcessHandle(identity, kind="linux-pidfd", handle=descriptor)
    except BaseException:
        os.close(descriptor)
        raise


# Explicit names make platform-specific tests and callers self-documenting.
get_process_identity = process_identity
read_process_identity = process_identity
native_process_identity = process_identity
get_native_process_identity = process_identity
get_current_process_identity = current_process_identity
is_process_identity_current = process_identity_matches
SupervisorDiscovery = SupervisorDiscoveryRecord


__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "DEFAULT_SUPERVISOR_VERSION",
    "DISCOVERY_SCHEMA_VERSION",
    "DiscoveryRecord",
    "EndpointKind",
    "EndpointRef",
    "ExactProcessHandle",
    "IdentityPlatform",
    "NativeProcessIdentity",
    "ProcessIdentity",
    "ProcessIdentityMismatch",
    "ProcessIdentityNotFound",
    "ProcessIdentityObservation",
    "ProcessIdentityState",
    "ProcessIdentityUnavailable",
    "ProcessStartIdentity",
    "SupervisorDiscovery",
    "SupervisorDiscoveryRecord",
    "current_process_identity",
    "get_current_process_identity",
    "get_native_process_identity",
    "get_process_identity",
    "is_process_identity_current",
    "native_process_identity",
    "observe_process_identity",
    "open_exact_process",
    "process_identity",
    "process_identity_matches",
    "read_process_identity",
]
