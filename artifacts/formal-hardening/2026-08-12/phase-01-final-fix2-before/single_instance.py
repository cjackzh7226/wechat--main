"""Process-lifetime single-instance guard.

The guard is deliberately acquired by :func:`main.main`, not at import time, so
tests and command-line diagnostics can inject a harmless adapter.
"""

from __future__ import annotations

import os
import threading
from typing import Protocol


MUTEX_NAME = r"Local\WeChatStyleReplyAssistant.ManagedRuntime.v1"


class MutexApi(Protocol):
    def create(self, name: str) -> tuple[object, bool]: ...

    def close(self, handle: object) -> None: ...


class _WindowsMutexApi:
    ERROR_ALREADY_EXISTS = 183

    def create(self, name: str) -> tuple[object, bool]:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        ctypes.set_last_error(0)
        handle = create_mutex(None, False, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        return handle, ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS

    def close(self, handle: object) -> None:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_bool
        if not close_handle(handle):
            raise OSError(ctypes.get_last_error(), "CloseHandle failed")


class _ProcessMutexApi:
    """Non-Windows process-local adapter used only where named mutexes do not exist."""

    _lock = threading.Lock()
    _active: set[str] = set()

    def create(self, name: str) -> tuple[object, bool]:
        with self._lock:
            already_exists = name in self._active
            if not already_exists:
                self._active.add(name)
        handle = (name, object(), not already_exists)
        return handle, already_exists

    def close(self, handle: object) -> None:
        name, _token, owns_name = handle  # type: ignore[misc]
        if not owns_name:
            return
        with self._lock:
            self._active.discard(name)


def _default_api() -> MutexApi:
    return _WindowsMutexApi() if os.name == "nt" else _ProcessMutexApi()


class SingleInstance:
    def __init__(self, name: str = MUTEX_NAME, *, api: MutexApi | None = None) -> None:
        self.name = name
        self._api = api or _default_api()
        self._handle: object | None = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle, already_exists = self._api.create(self.name)
        if already_exists:
            self._api.close(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._api.close(handle)

    def __enter__(self) -> "SingleInstance":
        if not self.acquire():
            raise RuntimeError("another instance is already running")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
