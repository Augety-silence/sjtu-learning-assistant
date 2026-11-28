"""读取 Windows PE 版本资源，无需第三方工具或 PowerShell。"""
import ctypes
from pathlib import Path


def file_version(path: Path) -> str:
    dll = ctypes.WinDLL('version', use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    dll.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    dll.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    unused = ctypes.c_uint32()
    size = dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    if not size:
        raise ValueError('Missing PE version')
    buffer = ctypes.create_string_buffer(size)
    if not dll.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise ValueError('Invalid PE version')
    pointer, length = ctypes.c_void_p(), ctypes.c_uint32()
    if not dll.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        raise ValueError('Invalid PE version structure')
    values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    if values[0] != 0xFEEF04BD:
        raise ValueError('Invalid PE signature')
    return f'{values[2] >> 16}.{values[2] & 0xffff}.{values[3] >> 16}'
