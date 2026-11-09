from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import platform
import string
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CSIDL_PERSONAL = 0x0005
CSIDL_APPDATA = 0x001A
LAST_FILE_DIR_INI = "51a1fffea11325a1e4104c6b3de47af7.ini"
INSTALLER_FILE_DIR_INI = "98ae43cd6a1870deb10154bfed5f9eb6.ini"
LEGACY_WECHAT_INI = "3ebffe94.ini"
SYSTEM_USER_IGNORES = {"public", "default", "default user", "all users"}
DRIVE_SCAN_IGNORES = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "perflogs",
    "appdata",
    "node_modules",
    ".git",
}


@dataclass(frozen=True)
class WeChatDatabase:
    path: Path
    account_dir: Path
    modified_at: float
    size: int


def _dedup_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = os.path.normcase(os.path.normpath(str(path)))
        if key and key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _shell_folder_path(csidl: int) -> Path | None:
    if platform.system() != "Windows" or not hasattr(ctypes, "windll"):
        return None
    try:
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        hr = ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, 0, buf)
        if hr == 0 and buf.value:
            return Path(buf.value)
    except (AttributeError, OSError, ValueError):
        pass
    return None


def _registry_shell_folder(value_name: str) -> list[Path]:
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except ImportError:
        return []

    found: list[Path] = []
    subkeys = (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    )
    for subkey in subkeys:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, value_name)
                if isinstance(raw, str) and raw.strip():
                    expanded = os.path.expandvars(raw.strip())
                    found.append(Path(expanded))
        except OSError:
            continue
    return found


def _system_user_dirs() -> list[Path]:
    roots: list[Path] = []
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        users_parent = Path(user_profile).parent
        if users_parent.exists():
            roots.append(users_parent)
    sys_drive = os.environ.get("SystemDrive", "C:")
    users_dir = Path(f"{sys_drive}\\Users")
    if users_dir.exists():
        roots.append(users_dir)

    user_dirs: list[Path] = []
    for parent in _dedup_paths(roots):
        try:
            for entry in os.scandir(parent):
                if entry.is_dir(follow_symlinks=False) and entry.name.casefold() not in SYSTEM_USER_IGNORES:
                    user_dirs.append(Path(entry.path))
        except OSError:
            continue
    return _dedup_paths(user_dirs)


def _candidate_documents_dirs() -> list[Path]:
    docs: list[Path] = []
    shell_doc = _shell_folder_path(CSIDL_PERSONAL)
    if shell_doc:
        docs.append(shell_doc)
    docs.extend(_registry_shell_folder("Personal"))

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        profile_path = Path(user_profile)
        docs.append(profile_path / "Documents")
        docs.append(profile_path / "OneDrive" / "Documents")
        docs.append(profile_path / "OneDrive" / "文档")

    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        onedrive = os.environ.get(env_name)
        if onedrive:
            docs.append(Path(onedrive) / "Documents")
            docs.append(Path(onedrive) / "文档")

    try:
        docs.append(Path.home() / "Documents")
    except RuntimeError:
        pass

    public_dir = os.environ.get("PUBLIC")
    if public_dir:
        docs.append(Path(public_dir) / "Documents")

    for user_dir in _system_user_dirs():
        docs.append(user_dir / "Documents")
        docs.append(user_dir / "OneDrive" / "Documents")
        docs.append(user_dir / "OneDrive" / "文档")

    return _dedup_paths(docs)


def _candidate_appdata_dirs() -> list[Path]:
    appdatas: list[Path] = []
    appdata_env = os.environ.get("APPDATA")
    if appdata_env:
        appdatas.append(Path(appdata_env))
    shell_appdata = _shell_folder_path(CSIDL_APPDATA)
    if shell_appdata:
        appdatas.append(shell_appdata)
    appdatas.extend(_registry_shell_folder("AppData"))

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        appdatas.append(Path(user_profile) / "AppData" / "Roaming")

    try:
        appdatas.append(Path.home() / "AppData" / "Roaming")
    except RuntimeError:
        pass

    for user_dir in _system_user_dirs():
        appdatas.append(user_dir / "AppData" / "Roaming")

    return _dedup_paths(appdatas)


def parse_wechat_config_dir(
    raw_text: str,
    documents_dirs: Iterable[Path],
    appdata_dirs: Iterable[Path] = (),
) -> list[Path]:
    cleaned = raw_text.replace("\x00", "").lstrip("\ufeff").strip()
    if not cleaned:
        return []
    line = next((part.strip() for part in cleaned.splitlines() if part.strip()), "")
    if not line:
        return []

    lower = line.casefold()
    if lower.startswith("mydocument:"):
        suffix = line[len("mydocument:") :].lstrip("\\/")
        return [doc / suffix if suffix else doc for doc in documents_dirs]
    if lower.startswith("appdata:"):
        suffix = line[len("appdata:") :].lstrip("\\/")
        return [app / suffix if suffix else app for app in appdata_dirs]

    expanded = os.path.expandvars(line)
    return [Path(expanded)]


def _expand_wechat_root_candidates(base_paths: Iterable[Path]) -> list[Path]:
    roots: list[Path] = []
    for base in base_paths:
        if base.name.casefold() == "xwechat_files":
            roots.append(base)
        else:
            roots.append(base / "xwechat_files")
            roots.append(base)
    return _dedup_paths(roots)


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _safe_glob(path: Path, pattern: str) -> list[Path]:
    try:
        return list(path.glob(pattern))
    except OSError:
        return []


def _read_config_file(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "latin1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _configured_wechat_roots(
    documents_dirs: list[Path],
    appdata_dirs: list[Path],
) -> list[Path]:
    raw_values: list[str] = []
    for appdata in appdata_dirs:
        xwechat_cfg = appdata / "Tencent" / "xwechat" / "config"
        priority_inis = [
            xwechat_cfg / LAST_FILE_DIR_INI,
            xwechat_cfg / INSTALLER_FILE_DIR_INI,
        ]
        if _safe_is_dir(xwechat_cfg):
            for extra in sorted(_safe_glob(xwechat_cfg, "*.ini")):
                if extra.name.casefold() != "lock.ini" and extra not in priority_inis:
                    priority_inis.append(extra)

        all_users_cfg = appdata / "Tencent" / "xwechat" / "All Users" / "config"
        if _safe_is_dir(all_users_cfg):
            for extra in sorted(_safe_glob(all_users_cfg, "*.ini")):
                if extra.name.casefold() != "lock.ini":
                    priority_inis.append(extra)

        legacy_cfg = appdata / "Tencent" / "WeChat" / "All Users" / "config"
        priority_inis.append(legacy_cfg / LEGACY_WECHAT_INI)
        if _safe_is_dir(legacy_cfg):
            for extra in sorted(_safe_glob(legacy_cfg, "*.ini")):
                if extra not in priority_inis:
                    priority_inis.append(extra)

        for ini_path in priority_inis:
            if _safe_is_file(ini_path):
                content = _read_config_file(ini_path)
                if content:
                    raw_values.append(content)

    if platform.system() == "Windows":
        try:
            import winreg

            for subkey in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                        for value_name in ("FileSavePath", "OldFileSavePath"):
                            try:
                                val, _ = winreg.QueryValueEx(key, value_name)
                                if isinstance(val, str) and val.strip():
                                    raw_values.append(val)
                            except OSError:
                                continue
                except OSError:
                    continue
        except ImportError:
            pass

    bases: list[Path] = []
    for raw in raw_values:
        bases.extend(parse_wechat_config_dir(raw, documents_dirs, appdata_dirs))
    return _expand_wechat_root_candidates(bases)


def _fixed_drives() -> list[Path]:
    if platform.system() != "Windows" or not hasattr(ctypes, "windll"):
        return []
    drives: list[Path] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for idx, letter in enumerate(string.ascii_uppercase):
            if mask & (1 << idx):
                root = f"{letter}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                    drives.append(Path(root))
    except (AttributeError, OSError):
        pass
    return drives


def _fixed_drive_wechat_roots() -> list[Path]:
    roots: list[Path] = []
    common_relatives = (
        "xwechat_files",
        "Documents/xwechat_files",
        "My Documents/xwechat_files",
        "我的文档/xwechat_files",
        "WeChat Files/xwechat_files",
        "Weixin/xwechat_files",
        "WeChat/xwechat_files",
        "Tencent/xwechat_files",
        "Tencent Files/xwechat_files",
    )
    for drive in _fixed_drives():
        for rel in common_relatives:
            roots.append(drive / rel)
        try:
            for entry in os.scandir(drive):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name.casefold() in DRIVE_SCAN_IGNORES:
                    continue
                p1 = Path(entry.path) / "xwechat_files"
                if _safe_is_dir(p1):
                    roots.append(p1)
                try:
                    for sub in os.scandir(entry.path):
                        if not sub.is_dir(follow_symlinks=False):
                            continue
                        if sub.name.casefold() in DRIVE_SCAN_IGNORES:
                            continue
                        p2 = Path(sub.path) / "xwechat_files"
                        if _safe_is_dir(p2):
                            roots.append(p2)
                except OSError:
                    continue
        except OSError:
            continue
    return _dedup_paths(roots)


def _running_process_wechat_roots() -> list[Path]:
    if platform.system() != "Windows":
        return []
    try:
        from .memory import (
            PROCESS_QUERY_INFORMATION,
            PROCESS_VM_READ,
            ProcessMemoryReader,
            get_weixin_pids,
        )
    except Exception:
        return []

    found: list[Path] = []
    suffix_u16 = "xwechat_files".encode("utf-16le")
    try:
        reader = ProcessMemoryReader()
        for pid in get_weixin_pids():
            mod = reader.get_weixin_module(pid)
            if not mod:
                continue
            base, _size, _dll_path = mod
            handle = reader.open_process(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not handle:
                continue
            try:
                hdr = reader.read(handle, base, 4096)
                if len(hdr) < 0x100:
                    continue
                pe_off = struct.unpack_from("<I", hdr, 0x3C)[0]
                if pe_off + 24 > len(hdr):
                    continue
                num_sec = struct.unpack_from("<H", hdr, pe_off + 6)[0]
                opt_size = struct.unpack_from("<H", hdr, pe_off + 20)[0]
                sec_off = pe_off + 24 + opt_size
                for i in range(num_sec):
                    if sec_off + (i + 1) * 40 > len(hdr):
                        break
                    s = hdr[sec_off + i * 40 : sec_off + (i + 1) * 40]
                    name = s[:8].rstrip(b"\x00").decode("ascii", "ignore")
                    vsize, vaddr = struct.unpack_from("<II", s, 8)
                    if name != ".data" or not (0 < vsize <= 32 * 1024 * 1024):
                        continue
                    sec_data = reader.read(handle, base + vaddr, vsize)
                    for off in range(0, len(sec_data) - 32, 8):
                        ptr, _, length, cap = struct.unpack_from("<QQQQ", sec_data, off)
                        if 13 <= length <= 260 and length <= cap <= 512 and 0x10000 <= ptr < 0x800000000000:
                            raw_str = reader.read(handle, ptr, length * 2)
                            if len(raw_str) == length * 2 and raw_str.lower().endswith(suffix_u16):
                                text = raw_str.decode("utf-16le", "ignore")
                                if text:
                                    found.append(Path(text))
            finally:
                reader.close_handle(handle)
    except Exception:
        return []
    return _dedup_paths(found)


def default_wechat_roots() -> list[Path]:
    documents_dirs = _candidate_documents_dirs()
    appdata_dirs = _candidate_appdata_dirs()
    roots: list[Path] = []
    roots.extend(_configured_wechat_roots(documents_dirs, appdata_dirs))
    for doc_dir in documents_dirs:
        roots.append(doc_dir / "xwechat_files")
    roots.extend(_fixed_drive_wechat_roots())
    return _dedup_paths(roots)


def _iter_emoticon_dbs_under_root(root: Path) -> Iterable[tuple[Path, Path]]:
    if not _safe_exists(root):
        return
    if _safe_is_file(root):
        account_dir = root.parents[2] if len(root.parents) >= 3 else root.parent
        yield root, account_dir
        return

    direct_db = root / "emoticon.db"
    if _safe_is_file(direct_db):
        account_dir = direct_db.parents[2] if len(direct_db.parents) >= 3 else root
        yield direct_db, account_dir

    storage_db = root / "emoticon" / "emoticon.db"
    if _safe_is_file(storage_db):
        account_dir = storage_db.parents[2] if len(storage_db.parents) >= 3 else root.parent
        yield storage_db, account_dir

    account_db = root / "db_storage" / "emoticon" / "emoticon.db"
    if _safe_is_file(account_db):
        yield account_db, root

    for db_path in _safe_glob(root, "*/db_storage/emoticon/emoticon.db"):
        if _safe_is_file(db_path):
            yield db_path, db_path.parents[2]

    nested_root = root / "xwechat_files"
    if _safe_is_dir(nested_root):
        for db_path in _safe_glob(nested_root, "*/db_storage/emoticon/emoticon.db"):
            if _safe_is_file(db_path):
                yield db_path, db_path.parents[2]

def discover_emoticon_databases(roots: list[Path] | None = None) -> list[WeChatDatabase]:
    candidates: list[WeChatDatabase] = []
    seen_db_paths: set[str] = set()

    def collect(search_roots: Iterable[Path]) -> None:
        for root in search_roots:
            for db_path, account_dir in _iter_emoticon_dbs_under_root(root):
                try:
                    resolved_key = os.path.normcase(str(db_path.resolve()))
                    if resolved_key in seen_db_paths:
                        continue
                    stat = db_path.stat()
                except OSError:
                    continue
                seen_db_paths.add(resolved_key)
                candidates.append(
                    WeChatDatabase(
                        path=db_path,
                        account_dir=account_dir,
                        modified_at=stat.st_mtime,
                        size=stat.st_size,
                    )
                )

    collect(roots if roots is not None else default_wechat_roots())
    if not candidates and roots is None:
        collect(_running_process_wechat_roots())

    return sorted(candidates, key=lambda item: item.modified_at, reverse=True)


def resolve_database(db: str | None, wechat_root: str | None = None, index: int = 1) -> Path:
    if db:
        return Path(db)

    roots = [Path(wechat_root)] if wechat_root else None
    candidates = discover_emoticon_databases(roots)
    if not candidates:
        raise FileNotFoundError("no emoticon.db found; pass --db explicitly or use --wechat-root")
    if index < 1 or index > len(candidates):
        raise IndexError(f"--db-index must be between 1 and {len(candidates)}")
    return candidates[index - 1].path
