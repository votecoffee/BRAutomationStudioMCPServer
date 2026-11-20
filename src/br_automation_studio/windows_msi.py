"""Read one registered Windows Installer product identity without opening or repairing it."""

import ctypes
from ctypes import wintypes
import sys


INSTALLSTATE_DEFAULT = 5


def installed_product(product_code):
    if sys.platform != "win32":
        return {"stateCode": None, "version": None}

    msi = ctypes.WinDLL("msi")
    state = msi.MsiQueryProductStateW
    state.argtypes = [wintypes.LPCWSTR]
    state.restype = ctypes.c_int
    result = {"stateCode": state(product_code), "version": None}
    if result["stateCode"] != INSTALLSTATE_DEFAULT:
        return result

    metadata = msi.MsiGetProductInfoW
    metadata.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    metadata.restype = wintypes.UINT
    value = ctypes.create_unicode_buffer(64)
    length = wintypes.DWORD(len(value))
    status = metadata(product_code, "VersionString", value, ctypes.byref(length))
    if status == 0:
        result["version"] = value.value
    else:
        result["metadataStatus"] = int(status)
    return result
