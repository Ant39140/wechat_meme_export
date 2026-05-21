from __future__ import annotations

import ctypes
import platform
import re
import subprocess
from ctypes import wintypes
from pathlib import Path


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
KEY_RE = re.compile(rb"x'([0-9a-fA-F]{80,128})'")


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def require_windows() -> None:
    if platform.system() != "Windows":
        raise RuntimeError("process memory scanning is only supported on Windows")


def get_weixin_pids() -> list[int]:
    require_windows()
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Process Weixin -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]


class ProcessMemoryReader:
    def __init__(self) -> None:
        require_windows()
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.open_process = self.kernel32.OpenProcess
        self.open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.open_process.restype = wintypes.HANDLE
        self.close_handle = self.kernel32.CloseHandle
        self.close_handle.argtypes = [wintypes.HANDLE]
        self.close_handle.restype = wintypes.BOOL
        self.virtual_query_ex = self.kernel32.VirtualQueryEx
        self.virtual_query_ex.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(MemoryBasicInformation),
            ctypes.c_size_t,
        ]
        self.virtual_query_ex.restype = ctypes.c_size_t
        self.read_process_memory = self.kernel32.ReadProcessMemory
        self.read_process_memory.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.read_process_memory.restype = wintypes.BOOL

    def read(self, handle: int, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = self.read_process_memory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read))
        if not ok or read.value == 0:
            return b""
        return buffer.raw[: read.value]

    def scan_pid_for_db_key(self, pid: int, salt_hex: str, max_region_mb: int = 8) -> set[str]:
        handle = self.open_process(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            return set()

        found: set[str] = set()
        address = 0
        mbi = MemoryBasicInformation()
        max_region = max_region_mb * 1024 * 1024
        try:
            while self.virtual_query_ex(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)
                address = base + size

                if mbi.State != MEM_COMMIT:
                    continue
                if mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD):
                    continue
                if size <= 0:
                    continue

                offset = 0
                overlap = b""
                while offset < size:
                    chunk_size = min(max_region, size - offset)
                    chunk = self.read(handle, base + offset, chunk_size)
                    if chunk:
                        haystack = overlap + chunk
                        for match in KEY_RE.finditer(haystack):
                            key_hex = match.group(1).decode("ascii").lower()
                            if salt_hex in key_hex:
                                found.add(key_hex)
                        overlap = haystack[-256:]
                    offset += chunk_size
        finally:
            self.close_handle(handle)
        return found


def scan_database_keys(db_path: Path, max_region_mb: int = 8) -> dict[int, set[str]]:
    salt_hex = db_path.read_bytes()[:16].hex()
    reader = ProcessMemoryReader()
    results: dict[int, set[str]] = {}
    for pid in get_weixin_pids():
        keys = reader.scan_pid_for_db_key(pid, salt_hex, max_region_mb)
        results[pid] = keys
    return results
