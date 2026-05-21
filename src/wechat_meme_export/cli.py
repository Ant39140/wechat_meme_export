from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from .decrypt import decrypt_database
from .discovery import discover_emoticon_databases, resolve_database
from .exporter import export_custom_stickers, write_index
from .memory import scan_database_keys


def print_counts(rows: list[dict[str, str]]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("counts=" + ",".join(f"{key}:{counts[key]}" for key in sorted(counts)))


def validate_sqlite(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    result = con.execute("pragma integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"decrypted database failed integrity check: {result}")


def cmd_scan_key(args: argparse.Namespace) -> int:
    db_path = resolve_database(args.db, args.wechat_root, args.db_index)
    salt = db_path.read_bytes()[:16].hex()
    print(f"db={db_path}")
    print(f"salt={salt}")

    results = scan_database_keys(db_path, max_region_mb=args.max_region_mb)
    all_keys: set[str] = set()
    for pid, keys in results.items():
        print(f"pid={pid} matching_keys={len(keys)}")
        all_keys.update(keys)
    for key in sorted(all_keys):
        print(key)
    print(f"total={len(all_keys)}")
    return 0 if all_keys else 2


def cmd_decrypt_db(args: argparse.Namespace) -> int:
    output = Path(args.output)
    decrypt_database(resolve_database(args.db, args.wechat_root, args.db_index), output, args.key)
    if args.check:
        validate_sqlite(output)
    print(f"output={output}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    output = Path(args.output)
    rows = export_custom_stickers(Path(args.db), output, delay=args.delay)
    index = write_index(output, rows)
    print(f"index={index}")
    print_counts(rows)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    decrypted = output / "emoticon.decrypted.db"
    decrypt_database(resolve_database(args.db, args.wechat_root, args.db_index), decrypted, args.key)
    if args.check:
        validate_sqlite(decrypted)
    rows = export_custom_stickers(decrypted, output, delay=args.delay)
    index = write_index(output, rows)
    print(f"decrypted_db={decrypted}")
    print(f"index={index}")
    print_counts(rows)
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    roots = [Path(args.wechat_root)] if args.wechat_root else None
    databases = discover_emoticon_databases(roots)
    for index, item in enumerate(databases, start=1):
        print(f"{index}. {item.path} size={item.size} mtime={item.modified_at:.0f}")
    print(f"total={len(databases)}")
    return 0 if databases else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat-meme-export")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="list discovered WeChat emoticon.db files")
    discover.add_argument("--wechat-root", help="xwechat_files directory; defaults to %%USERPROFILE%%\\Documents\\xwechat_files")
    discover.set_defaults(func=cmd_discover)

    scan = subparsers.add_parser("scan-key", help="scan running Weixin.exe processes for the target emoticon.db key")
    scan.add_argument("--db", help="encrypted db_storage/emoticon/emoticon.db path; auto-discovers latest when omitted")
    scan.add_argument("--wechat-root", help="xwechat_files directory used for auto-discovery")
    scan.add_argument("--db-index", type=int, default=1, help="1-based database number from discover")
    scan.add_argument("--max-region-mb", type=int, default=8)
    scan.set_defaults(func=cmd_scan_key)

    decrypt = subparsers.add_parser("decrypt-db", help="decrypt a WeChat 4.x emoticon.db")
    decrypt.add_argument("--db", help="encrypted emoticon.db path; auto-discovers latest when omitted")
    decrypt.add_argument("--wechat-root", help="xwechat_files directory used for auto-discovery")
    decrypt.add_argument("--db-index", type=int, default=1, help="1-based database number from discover")
    decrypt.add_argument("--key", required=True, help="raw key printed by scan-key")
    decrypt.add_argument("--output", required=True, help="decrypted SQLite output path")
    decrypt.add_argument("--no-check", dest="check", action="store_false", help="skip SQLite integrity check")
    decrypt.set_defaults(func=cmd_decrypt_db, check=True)

    export = subparsers.add_parser("export", help="export custom stickers from a decrypted emoticon.db")
    export.add_argument("--db", required=True, help="decrypted emoticon.db path")
    export.add_argument("--output", required=True, help="output directory")
    export.add_argument("--delay", type=float, default=0.02, help="delay between CDN requests")
    export.set_defaults(func=cmd_export)

    run = subparsers.add_parser("run", help="decrypt and export in one step")
    run.add_argument("--db", help="encrypted emoticon.db path; auto-discovers latest when omitted")
    run.add_argument("--wechat-root", help="xwechat_files directory used for auto-discovery")
    run.add_argument("--db-index", type=int, default=1, help="1-based database number from discover")
    run.add_argument("--key", required=True, help="raw key printed by scan-key")
    run.add_argument("--output", required=True, help="output directory")
    run.add_argument("--delay", type=float, default=0.02, help="delay between CDN requests")
    run.add_argument("--no-check", dest="check", action="store_false", help="skip SQLite integrity check")
    run.set_defaults(func=cmd_run, check=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
