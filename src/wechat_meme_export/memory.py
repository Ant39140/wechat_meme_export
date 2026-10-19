from __future__ import annotations

import ctypes
import platform
import re
import struct
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .decrypt import PAGE_SIZE, RESERVE, detect_page_reserve, verify_first_page


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_WEIXIN_DLL_SIZE = 512 * 1024 * 1024
EXACT_SCAN_CHUNK = 1024 * 1024
MAX_EXACT_SCAN_BYTES = 1024 * 1024 * 1024
EXACT_SCAN_SECONDS = 20.0
EXACT_SALT_CONFIG_MARKER = b"com.Tencent.WCDB.Config.Cipher"
# 4.1.13.x stores the serialized key/salt value behind this repeating XOR mask.
CONFIG_CIPHER_XOR_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)
EXACT_SALT_ADAPTERS = {
    (4, 1, 13, 12): "weixin-4.1.13.12-exact-salt-config",
    (4, 1, 13, 65): "weixin-4.1.13.65-exact-salt-config",
    (4, 1, 15, 13): "weixin-4.1.15.13-exact-salt-config",
}
READABLE_PAGE_PROTECTIONS = {0x02, 0x04, 0x20, 0x40, 0x80}
KEY_PATTERNS = (
    (re.compile(rb"[xX]'([0-9a-fA-F]{64,128})'"), False),
    (re.compile(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{64})(?![0-9a-fA-F])"), False),
    (re.compile(rb"[xX]\x00'\x00((?:[0-9a-fA-F]\x00){64,128})'\x00"), True),
    (re.compile(rb"(?<![0-9a-fA-F]\x00)((?:[0-9a-fA-F]\x00){64})(?![0-9a-fA-F]\x00)"), True),
)


@dataclass
class ProcessScan:
    keys: set[str] = field(default_factory=set)
    open_error: int | None = None
    queried_regions: int = 0
    committed_regions: int = 0
    bytes_read: int = 0
    failed_reads: int = 0
    candidates_tested: int = 0
    reserve_sizes: set[int] = field(default_factory=set)
    weixin_version: str | None = None
    config_adapter: str | None = None
    config_marker_found: bool = False
    config_pairs: int = 0
    config_salt_matches: int = 0
    exact_scan_bytes: int = 0


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class ModuleEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_wchar * 256),
        ("szExePath", ctypes.c_wchar * 260),
    ]


def version_from_module_path(path: Path) -> tuple[int, int, int, int] | None:
    for part in reversed(path.parts):
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", part):
            values = tuple(int(value) for value in part.split("."))
            return values[0], values[1], values[2], values[3]
    return None


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
        self.read_process_memory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read))
        if read.value == 0:
            return b""
        return buffer.raw[: read.value]

    def get_weixin_module(self, pid: int) -> tuple[int, int, Path] | None:
        create_snapshot = self.kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        create_snapshot.restype = wintypes.HANDLE
        module_first = self.kernel32.Module32FirstW
        module_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ModuleEntry32W)]
        module_first.restype = wintypes.BOOL
        module_next = self.kernel32.Module32NextW
        module_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ModuleEntry32W)]
        module_next.restype = wintypes.BOOL

        snapshot = create_snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or int(snapshot) in (-1, invalid_handle):
            return None
        try:
            entry = ModuleEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = module_first(snapshot, ctypes.byref(entry))
            while ok:
                if entry.szModule.casefold() == "weixin.dll":
                    return (
                        int(entry.modBaseAddr or 0),
                        int(entry.modBaseSize),
                        Path(entry.szExePath),
                    )
                entry.dwSize = ctypes.sizeof(entry)
                ok = module_next(snapshot, ctypes.byref(entry))
        finally:
            self.close_handle(snapshot)
        return None

    def iter_memory_hits(
        self,
        handle: int,
        needle: bytes,
        deadline: float,
        result: ProcessScan,
    ) -> Iterator[int]:
        if not needle:
            return

        scanned = 0
        address = 0x10000
        maximum = 0x7FFFFFFFFFFF
        overlap = len(needle) - 1
        while address < maximum and scanned < MAX_EXACT_SCAN_BYTES:
            if time.monotonic() > deadline:
                return
            mbi = MemoryBasicInformation()
            if not self.virtual_query_ex(
                handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
            ):
                return
            result.queried_regions += 1
            region_start = int(mbi.BaseAddress or 0)
            region_size = int(mbi.RegionSize or 0)
            next_address = region_start + region_size
            if next_address <= address:
                return
            address = next_address

            protection = int(mbi.Protect) & 0xFF
            if (
                mbi.State != MEM_COMMIT
                or mbi.Type not in (MEM_PRIVATE, MEM_MAPPED)
                or mbi.Protect & PAGE_GUARD
                or protection not in READABLE_PAGE_PROTECTIONS
                or region_size <= 0
            ):
                continue
            result.committed_regions += 1
            cursor = region_start
            end = region_start + region_size
            carry = b""
            while cursor < end and scanned < MAX_EXACT_SCAN_BYTES:
                if time.monotonic() > deadline:
                    return
                length = min(EXACT_SCAN_CHUNK, end - cursor, MAX_EXACT_SCAN_BYTES - scanned)
                chunk_start = cursor
                chunk = self.read(handle, cursor, length)
                scanned += length
                cursor += length
                result.exact_scan_bytes += len(chunk)
                result.bytes_read += len(chunk)
                if len(chunk) < length:
                    result.failed_reads += 1
                if not chunk:
                    carry = b""
                    continue

                combined = carry + chunk
                combined_start = chunk_start - len(carry)
                hit_start = 0
                while True:
                    hit = combined.find(needle, hit_start)
                    if hit < 0:
                        break
                    yield combined_start + hit
                    hit_start = hit + 1
                carry = combined[-overlap:] if overlap else b""

    def scan_wcdb_config_key(
        self,
        pid: int,
        handle: int,
        first_page: bytes,
        deadline: float,
        tested: set[str],
        result: ProcessScan,
    ) -> bool:
        module = self.get_weixin_module(pid)
        if module is None:
            return False
        base, module_size, dll_path = module
        version = version_from_module_path(dll_path)
        if version is None:
            return False
        result.weixin_version = ".".join(map(str, version))
        adapter = EXACT_SALT_ADAPTERS.get(version)
        if adapter is None:
            return False
        result.config_adapter = adapter
        if time.monotonic() > deadline or not 0 < module_size <= MAX_WEIXIN_DLL_SIZE:
            return False

        image = self.read(handle, base, module_size)
        result.bytes_read += len(image)
        if len(image) != module_size:
            result.failed_reads += 1
            return False
        marker_offset = image.find(EXACT_SALT_CONFIG_MARKER)
        if marker_offset < 0:
            return False
        result.config_marker_found = True
        marker_address = base + marker_offset
        needle = struct.pack("<QQ", marker_address, len(EXACT_SALT_CONFIG_MARKER))

        def counted_read(address: int, size: int) -> bytes:
            data = self.read(handle, address, size)
            result.bytes_read += len(data)
            if len(data) != size:
                result.failed_reads += 1
            return data

        salt = first_page[:16]
        for pair_address in self.iter_memory_hits(handle, needle, deadline, result):
            result.config_pairs += 1
            if pair_address < 0x10:
                continue
            # These offsets follow the versioned WCDB Config.Cipher node layout.
            node = counted_read(pair_address - 0x10, 0x50)
            if len(node) != 0x50:
                continue
            config_ptr = struct.unpack_from("<Q", node, 0x28)[0]
            if not 0x10000 <= config_ptr < 0x800000000000:
                continue
            obj = counted_read(config_ptr + 0x88, 0x28)
            if len(obj) != 0x28:
                continue
            data_ptr = struct.unpack_from("<Q", obj, 0x08)[0]
            data_len = struct.unpack_from("<Q", obj, 0x10)[0]
            if not (0 < data_len <= 1024 and 0x10000 <= data_ptr < 0x800000000000):
                continue
            blob = counted_read(data_ptr, int(data_len))
            if len(blob) != data_len:
                continue
            decoded = bytes(
                value ^ CONFIG_CIPHER_XOR_MASK[index % len(CONFIG_CIPHER_XOR_MASK)]
                for index, value in enumerate(blob)
            )
            for match in re.finditer(
                rb"[xX]'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'", decoded
            ):
                candidate_salt = bytes.fromhex(match.group(2).decode("ascii"))
                if candidate_salt != salt:
                    continue
                result.config_salt_matches += 1
                candidate = bytes.fromhex(match.group(1).decode("ascii"))
                key_hex = candidate.hex()
                if key_hex in tested:
                    continue
                tested.add(key_hex)
                result.candidates_tested += 1
                if verify_first_page(first_page, candidate):
                    result.keys.add(key_hex)
                    result.reserve_sizes.add(RESERVE)
                    return True
        return False

    def scan_pid_for_db_key(
        self,
        pid: int,
        first_page: bytes,
        max_region_mb: int = 8,
        exact_deadline: float | None = None,
    ) -> ProcessScan:
        handle = self.open_process(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            return ProcessScan(open_error=ctypes.get_last_error())

        result = ProcessScan()
        tested: set[str] = set()
        if exact_deadline is None:
            exact_deadline = time.monotonic() + EXACT_SCAN_SECONDS
        address = 0
        mbi = MemoryBasicInformation()
        max_region = max_region_mb * 1024 * 1024
        try:
            if self.scan_wcdb_config_key(
                pid, handle, first_page, exact_deadline, tested, result
            ):
                return result
            while self.virtual_query_ex(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                result.queried_regions += 1
                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)
                next_address = base + size
                if size <= 0 or next_address <= address:
                    break
                address = next_address

                if mbi.State != MEM_COMMIT:
                    continue
                if mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD):
                    continue
                result.committed_regions += 1

                offset = 0
                overlap = b""
                while offset < size:
                    chunk_size = min(max_region, size - offset)
                    chunk = self.read(handle, base + offset, chunk_size)
                    if chunk:
                        result.bytes_read += len(chunk)
                        if len(chunk) < chunk_size:
                            result.failed_reads += 1
                        haystack = overlap + chunk
                        for pattern, wide in KEY_PATTERNS:
                            for match in pattern.finditer(haystack):
                                encoded = match.group(1)
                                if wide:
                                    encoded = encoded.replace(b"\x00", b"")
                                if len(encoded) % 2:
                                    continue
                                candidate = bytes.fromhex(encoded.decode("ascii"))
                                for start in range(len(candidate) - 31):
                                    key = candidate[start : start + 32]
                                    key_hex = key.hex()
                                    if key_hex in tested:
                                        continue
                                    tested.add(key_hex)
                                    result.candidates_tested += 1
                                    reserve = detect_page_reserve(first_page, key)
                                    if reserve is not None:
                                        result.keys.add(key_hex)
                                        result.reserve_sizes.add(reserve)
                        overlap = haystack[-256:]
                    else:
                        result.failed_reads += 1
                        overlap = b""
                    offset += chunk_size
        finally:
            self.close_handle(handle)
        return result


def scan_database_keys(db_path: Path, max_region_mb: int = 8) -> dict[int, ProcessScan]:
    with db_path.open("rb") as db_file:
        first_page = db_file.read(PAGE_SIZE)
    if len(first_page) < PAGE_SIZE:
        raise ValueError(f"database is shorter than one {PAGE_SIZE}-byte page: {db_path}")

    reader = ProcessMemoryReader()
    results: dict[int, ProcessScan] = {}
    exact_deadline = time.monotonic() + EXACT_SCAN_SECONDS
    for pid in get_weixin_pids():
        result = reader.scan_pid_for_db_key(
            pid, first_page, max_region_mb, exact_deadline=exact_deadline
        )
        results[pid] = result
    return results
