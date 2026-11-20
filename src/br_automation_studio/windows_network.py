
"""Read owner-tagged Windows TCP listeners and UDP endpoints."""

import ctypes
from ctypes import wintypes
from ipaddress import ip_address
import socket
import sys

from .windows_job import win_function

DWORD = wintypes.DWORD
ADDRESS = ctypes.c_ubyte * 16


class Tcp4(ctypes.Structure):
    _fields_ = [
        ("state", DWORD), ("local", DWORD), ("port", DWORD),
        ("remote", DWORD), ("remote_port", DWORD), ("pid", DWORD),
    ]


class Tcp6(ctypes.Structure):
    _fields_ = [
        ("local", ADDRESS), ("scope", DWORD), ("port", DWORD),
        ("remote", ADDRESS), ("remote_scope", DWORD), ("remote_port", DWORD),
        ("state", DWORD), ("pid", DWORD),
    ]


class Udp4(ctypes.Structure):
    _fields_ = [("local", DWORD), ("port", DWORD), ("pid", DWORD)]


class Udp6(ctypes.Structure):
    _fields_ = [("local", ADDRESS), ("scope", DWORD), ("port", DWORD), ("pid", DWORD)]


def read_table(query, family, table_type, row_type, protocol):
    size = DWORD()
    result = query(None, ctypes.byref(size), False, family, table_type, 0)
    if result not in (0, 122):
        raise ctypes.WinError(result)
    for _ in range(4):
        data = ctypes.create_string_buffer(size.value)
        result = query(data, ctypes.byref(size), False, family, table_type, 0)
        if result == 122:
            continue
        if result:
            raise ctypes.WinError(result)
        total = DWORD.from_buffer(data).value
        row_bytes = ctypes.sizeof(row_type)
        if ctypes.sizeof(DWORD) + total * row_bytes > len(data):
            raise OSError("Windows network table has an inconsistent size")
        for index in range(total):
            row = row_type.from_buffer(data, ctypes.sizeof(DWORD) + index * row_bytes)
            address = row.local.to_bytes(4, sys.byteorder) if family == socket.AF_INET else bytes(row.local)
            yield {"protocol": protocol, "address": str(ip_address(address)),
                   "port": socket.ntohs(row.port & 0xFFFF), "pid": row.pid}
        return
    raise OSError("Windows network table changed during each read")


def endpoints():
    if sys.platform != "win32":
        raise OSError("ARsim endpoint inspection requires Windows")
    api = ctypes.WinDLL("iphlpapi")
    types = (wintypes.LPVOID, ctypes.POINTER(DWORD), wintypes.BOOL, DWORD, ctypes.c_int, DWORD)
    tcp = win_function(api, "GetExtendedTcpTable", DWORD, *types)
    udp = win_function(api, "GetExtendedUdpTable", DWORD, *types)
    for family, tcp_row, udp_row in ((socket.AF_INET, Tcp4, Udp4), (socket.AF_INET6, Tcp6, Udp6)):
        yield from read_table(tcp, family, 3, tcp_row, "tcp")
        yield from read_table(udp, family, 1, udp_row, "udp")
