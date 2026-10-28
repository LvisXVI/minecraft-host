"""Stop the local application when its last browser view is closed."""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable


class BrowserSessions:
    """Track independent browser tabs and allow a short grace period for reloads."""

    def __init__(self, on_last_closed: Callable[[], None], grace_seconds: float = 5.0) -> None:
        self._on_last_closed = on_last_closed
        self._grace_seconds = grace_seconds
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._closed: dict[str, float] = {}
        self._timer: threading.Timer | None = None
        self._generation = 0
        self._stopping = False
        self._watch_versions: dict[str, int] = {}

    @staticmethod
    def _check(session_id: str) -> None:
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", session_id):
            raise ValueError("Ungültige Browser-Sitzung.")

    def _prune(self) -> None:
        cutoff = time.monotonic() - 600
        self._closed = {key: when for key, when in self._closed.items() if when >= cutoff}

    def open(self, session_id: str) -> bool:
        self._check(session_id)
        with self._lock:
            self._prune()
            # A delayed open request must not revive a tab that already closed.
            if self._stopping or session_id in self._closed:
                return False
            self._active.add(session_id)
            self._generation += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None
            return True

    def close(self, session_id: str) -> None:
        self._check(session_id)
        with self._lock:
            self._prune()
            if self._stopping or session_id in self._closed:
                return
            self._closed[session_id] = time.monotonic()
            self._active.discard(session_id)
            self._watch_versions.pop(session_id, None)
            self._schedule_if_empty()

    def watch_open(self, session_id: str) -> int | None:
        self._check(session_id)
        with self._lock:
            self._prune()
            if self._stopping or session_id in self._closed:
                return None
            self._active.add(session_id)
            self._generation += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None
            # An old disconnected stream must never unregister its replacement.
            version = self._generation
            self._watch_versions[session_id] = version
            return version

    def watch_closed(self, session_id: str, version: int) -> None:
        with self._lock:
            if self._watch_versions.get(session_id) != version:
                return
            self._watch_versions.pop(session_id, None)
            self._active.discard(session_id)
            # Unlike an explicit close, a disconnected stream may reconnect.
            self._schedule_if_empty()

    def _schedule_if_empty(self) -> None:
        # Caller holds the lock.
        if self._active or self._stopping:
            return
        self._generation += 1
        generation = self._generation
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self._grace_seconds, self._finish, args=(generation,))
        self._timer.daemon = True
        self._timer.start()

    def _finish(self, generation: int) -> None:
        with self._lock:
            if self._stopping or self._active or generation != self._generation:
                return
            self._stopping = True
            self._timer = None
        self._on_last_closed()

    def dispose(self) -> None:
        with self._lock:
            self._stopping = True
            if self._timer:
                self._timer.cancel()
                self._timer = None
