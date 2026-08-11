"""Fail-closed native process identity and supervisor discovery contracts.

The process identity helpers intentionally do not terminate processes.  They only read the
native start identity needed to distinguish a live owner from a recycled PID.
"""

from __future__ import annotations

import ctypes
import os
import sys
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
        stat_text = stat_path.read_text(encoding="ascii")
        boot_id = boot_id_path.read_text(encoding="ascii").strip()
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


def process_identity_matches(identity: ProcessStartIdentity) -> bool:
    """Return false when identity cannot be re-read; never infer from PID alone."""

    try:
        observed = process_identity(identity.pid)
    except ProcessIdentityUnavailable:
        return False
    return observed == identity


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
    "IdentityPlatform",
    "NativeProcessIdentity",
    "ProcessIdentity",
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
    "process_identity",
    "process_identity_matches",
    "read_process_identity",
]
