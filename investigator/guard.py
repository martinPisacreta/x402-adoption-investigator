"""Network guard for sample mode (SPEC.md invariant I4 and section 7.4).

install_network_guard() patches the standard-library entry points that open outbound connections so that any
attempt raises NetworkDisabledError. The runner installs it before anything else in sample mode and logs the
patched names as the first run.jsonl event. The guard blocks connect-level calls (not the socket class itself)
so that an HTTP server started before the guard (run.py --serve) keeps accepting requests while its sample runs
stay provably offline. A NetworkDisabledError surfaces as E_INTERNAL: a caller bug, never a recovery.
"""
from __future__ import annotations

import socket
import urllib.request
from typing import Any, Callable

PATCHED_NAMES: tuple[str, ...] = (
    "socket.socket.connect",
    "socket.socket.connect_ex",
    "socket.create_connection",
    "socket.getaddrinfo",
    "urllib.request.urlopen",
)


class NetworkDisabledError(RuntimeError):
    """Raised by any patched network entry point while the guard is installed."""


_originals: dict[str, Callable[..., Any]] = {}


def _blocked(*_args: Any, **_kwargs: Any) -> Any:
    raise NetworkDisabledError(
        "sample mode: the network guard is installed; no socket may be opened (see SPEC.md section 7.4)")


def is_installed() -> bool:
    return bool(_originals)


def install_network_guard() -> list[str]:
    """Patch the entry points (idempotent) and return the patched names."""
    if _originals:
        return list(PATCHED_NAMES)
    _originals["socket.socket.connect"] = socket.socket.connect
    _originals["socket.socket.connect_ex"] = socket.socket.connect_ex
    _originals["socket.create_connection"] = socket.create_connection
    _originals["socket.getaddrinfo"] = socket.getaddrinfo
    _originals["urllib.request.urlopen"] = urllib.request.urlopen
    socket.socket.connect = _blocked  # type: ignore[assignment]
    socket.socket.connect_ex = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.getaddrinfo = _blocked  # type: ignore[assignment]
    urllib.request.urlopen = _blocked  # type: ignore[assignment]
    return list(PATCHED_NAMES)


def uninstall_network_guard() -> None:
    """Restore the originals (tests and the --serve loop use it; a sample run never does)."""
    if not _originals:
        return
    socket.socket.connect = _originals["socket.socket.connect"]  # type: ignore[assignment]
    socket.socket.connect_ex = _originals["socket.socket.connect_ex"]  # type: ignore[assignment]
    socket.create_connection = _originals["socket.create_connection"]  # type: ignore[assignment]
    socket.getaddrinfo = _originals["socket.getaddrinfo"]  # type: ignore[assignment]
    urllib.request.urlopen = _originals["urllib.request.urlopen"]  # type: ignore[assignment]
    _originals.clear()
