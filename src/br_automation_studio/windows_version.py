
"""Read a Windows binary's product version without loading the binary."""

import ctypes
from ctypes import wintypes
import sys


def product_version(path):
    if sys.platform != "win32":
        return None

    api = ctypes.WinDLL("version")
    size_of = api.GetFileVersionInfoSizeW
    size_of.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    size_of.restype = wintypes.DWORD
    read = api.GetFileVersionInfoW
    read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    read.restype = wintypes.BOOL
    query = api.VerQueryValueW
    query.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR,
                      ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.UINT)]
    query.restype = wintypes.BOOL

    size = size_of(str(path), None)
    if not size:
        return None
    resource = ctypes.create_string_buffer(size)
    if not read(str(path), 0, size, resource):
        return None

    addressed = wintypes.LPVOID()
    length = wintypes.UINT()
    if not query(resource, r"\VarFileInfo\Translation", ctypes.byref(addressed), ctypes.byref(length)):
        return None
    words = ctypes.cast(addressed, ctypes.POINTER(wintypes.WORD))
    for i in range(0, length.value // 4 * 2, 2):
        key = rf"\StringFileInfo\{words[i]:04x}{words[i+1]:04x}\ProductVersion"
        if query(resource, key, ctypes.byref(addressed), ctypes.byref(length)) and length.value:
            return ctypes.wstring_at(addressed, length.value).rstrip("\0")
    return None
