"""Restore inherited pipes only for explicit frozen CLI helper modes.

Windows windowed PyInstaller sets sys.std* to None even when a parent passes
pipes. Never allocate a console or change the normal desktop process streams.
"""
from __future__ import annotations

import os
import sys


def _windows_stream(handle_id: int, mode: str):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                      wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.GetStdHandle(handle_id & 0xffffffff)
    if kernel.GetFileType(handle) not in (1, 3):  # inherited disk or pipe
        raise OSError('No inherited helper pipe')
    duplicate = wintypes.HANDLE()
    process = kernel.GetCurrentProcess()
    if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
        raise OSError('Cannot duplicate helper pipe')
    try:
        fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | (os.O_RDONLY if mode == 'r' else os.O_WRONLY))
    except BaseException:
        kernel.CloseHandle(duplicate)
        raise
    return os.fdopen(fd, mode, encoding='utf-8', newline='\n', buffering=1)


def restore_helper_stdio() -> bool:
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        created = {}
        try:
            for name, handle, mode in (('stdin', -10, 'r'), ('stdout', -11, 'w')):
                if getattr(sys, name) is None:
                    created[name] = _windows_stream(handle, mode)
            if sys.stderr is None:
                try:
                    created['stderr'] = _windows_stream(-12, 'w')
                except OSError:
                    created['stderr'] = open(os.devnull, 'w', encoding='utf-8')
        except OSError:
            for stream in created.values():
                stream.close()
            return False
        for name, stream in created.items():
            setattr(sys, name, stream)
    return sys.stdin is not None and sys.stdout is not None
