"""Thread-owned SQLite transactions with an explicit close fence."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")


class RepositoryClosed(RuntimeError):
    """The repository owner has fenced all new transactions."""


class SQLiteConnectionFactory:
    """Open one SQLite connection per transaction in the calling thread."""

    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        self._database_path = database_path.resolve()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._condition = threading.Condition(threading.Lock())
        self._state = "open"
        self._active_transactions = 0
        self._initialize_journal()

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def active_transactions(self) -> int:
        with self._condition:
            return self._active_transactions

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._state == "closed"

    @contextmanager
    def transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        with self._condition:
            if self._state != "open":
                raise RepositoryClosed("repository transaction owner is closed")
            self._active_transactions += 1

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                isolation_level=None,
                timeout=self._busy_timeout_ms / 1_000,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            if connection is not None:
                connection.close()
            with self._condition:
                self._active_transactions -= 1
                if self._active_transactions == 0:
                    self._condition.notify_all()

    def run(
        self,
        *,
        write: bool,
        operation: Callable[[sqlite3.Connection], _T],
    ) -> _T:
        with self.transaction(write=write) as connection:
            return operation(connection)

    def close(self) -> None:
        with self._condition:
            if self._state == "closed":
                return
            self._state = "closing"
            while self._active_transactions:
                self._condition.wait()
            self._state = "closed"
            self._condition.notify_all()

    def _initialize_journal(self) -> None:
        connection = sqlite3.connect(
            self._database_path,
            isolation_level=None,
            timeout=self._busy_timeout_ms / 1_000,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        finally:
            connection.close()
