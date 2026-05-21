from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WeChatDatabase:
    path: Path
    account_dir: Path
    modified_at: float
    size: int


def default_wechat_roots() -> list[Path]:
    roots: list[Path] = []
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        roots.append(Path(user_profile) / "Documents" / "xwechat_files")
    return roots


def discover_emoticon_databases(roots: list[Path] | None = None) -> list[WeChatDatabase]:
    candidates: list[WeChatDatabase] = []
    for root in roots or default_wechat_roots():
        if not root.exists():
            continue
        for db_path in root.glob("*/db_storage/emoticon/emoticon.db"):
            try:
                stat = db_path.stat()
            except OSError:
                continue
            candidates.append(
                WeChatDatabase(
                    path=db_path,
                    account_dir=db_path.parents[2],
                    modified_at=stat.st_mtime,
                    size=stat.st_size,
                )
            )
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
