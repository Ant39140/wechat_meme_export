from __future__ import annotations

import csv
import hashlib
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


INDEX_FIELDS = ["md5", "caption", "product_id", "status", "mode", "output", "source_url", "error"]
ExportProgressCallback = Callable[[int, int, str], None]


def detect_format(data: bytes) -> str | None:
    if data.startswith(bytes.fromhex("89504e470d0a1a0a")):
        return "png"
    if data.startswith(bytes.fromhex("ffd8ff")):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def strip_pkcs7(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def aes_decrypt_variants(data: bytes, aes_key: str) -> tuple[bytes, str] | None:
    keys: list[bytes] = []
    try:
        key_hex = bytes.fromhex(aes_key)
        if len(key_hex) in (16, 24, 32):
            keys.append(key_hex)
    except ValueError:
        pass

    key_ascii = aes_key.encode("utf-8")
    if len(key_ascii) in (16, 24, 32):
        keys.append(key_ascii)

    usable = data[: len(data) - (len(data) % 16)]
    if not usable:
        return None

    for key in keys:
        for mode_name, mode in (("cbc0", modes.CBC(b"\x00" * 16)), ("ecb", modes.ECB())):
            try:
                decrypted = Cipher(algorithms.AES(key), mode).decryptor().update(usable)
            except ValueError:
                continue
            for candidate in (decrypted, strip_pkcs7(decrypted)):
                if detect_format(candidate):
                    return candidate, f"aes:{mode_name}"
    return None


def download(url: str, timeout: int = 25) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def export_custom_stickers(
    db_path: Path,
    output_root: Path,
    delay: float = 0.02,
    progress_callback: ExportProgressCallback | None = None,
) -> list[dict[str, str]]:
    images_dir = output_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    query = """
        select md5, caption, product_id, aes_key, cdn_url, extern_url, encrypt_url, thumb_url
        from kNonStoreEmoticonTable
        order by md5
    """

    rows: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    try:
        count_row = con.execute("select count(*) from kNonStoreEmoticonTable").fetchone()
        total = int(count_row[0]) if count_row else 0

        for index, row in enumerate(con.execute(query), start=1):
            md5 = row["md5"]
            if progress_callback:
                progress_callback(index, total, md5)
            urls = [row["cdn_url"], row["extern_url"], row["encrypt_url"], row["thumb_url"]]
            record = {
                "md5": md5,
                "caption": row["caption"] or "",
                "product_id": row["product_id"] or "",
                "status": "failed",
                "mode": "",
                "output": "",
                "source_url": "",
                "error": "",
            }

            for url in [value for value in urls if value]:
                try:
                    data = download(url)
                    fmt = detect_format(data)
                    mode = "download"
                    if not fmt and row["aes_key"]:
                        decrypted = aes_decrypt_variants(data, row["aes_key"])
                        if decrypted:
                            data, mode = decrypted
                            fmt = detect_format(data)
                    if not fmt:
                        record["error"] = f"not_image:{data[:8].hex()}"
                        continue

                    digest = hashlib.sha256(data).hexdigest()
                    if digest in seen_hashes:
                        record["status"] = "duplicate"
                        record["source_url"] = url
                        break
                    seen_hashes.add(digest)

                    target = images_dir / f"{md5}.{fmt}"
                    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        target = images_dir / f"{md5}_{digest[:8]}.{fmt}"
                    target.write_bytes(data)

                    record.update({"status": "exported", "mode": mode, "output": str(target), "source_url": url})
                    break
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                    record["error"] = type(exc).__name__ + ":" + str(exc)[:160]

            if delay:
                time.sleep(delay)
            rows.append(record)
    finally:
        con.close()

    return rows


def write_index(output_root: Path, rows: list[dict[str, str]]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / "added_stickers_index.csv"
    with index_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return index_path
