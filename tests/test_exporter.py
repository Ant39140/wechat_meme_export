import sqlite3
from pathlib import Path

from wechat_meme_export import exporter
from wechat_meme_export.exporter import detect_format, export_custom_stickers, strip_pkcs7


def test_detect_format_png() -> None:
    assert detect_format(bytes.fromhex("89504e470d0a1a0a") + b"data") == "png"


def test_detect_format_webp() -> None:
    assert detect_format(b"RIFF1234WEBPdata") == "webp"


def test_strip_pkcs7() -> None:
    assert strip_pkcs7(b"hello" + bytes([3, 3, 3])) == b"hello"


def test_export_custom_stickers_reports_progress(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "emoticon.db"
    con = sqlite3.connect(db_path)
    con.execute(
        """
        create table kNonStoreEmoticonTable (
            md5 text,
            caption text,
            product_id text,
            aes_key text,
            cdn_url text,
            extern_url text,
            encrypt_url text,
            thumb_url text
        )
        """
    )
    con.execute(
        "insert into kNonStoreEmoticonTable values (?, ?, ?, ?, ?, ?, ?, ?)",
        ("abc", "caption", "", "", "https://example.test/a.png", "", "", ""),
    )
    con.commit()
    con.close()

    monkeypatch.setattr(exporter, "download", lambda url: bytes.fromhex("89504e470d0a1a0a") + b"data")
    progress = []

    rows = export_custom_stickers(
        db_path,
        tmp_path / "out",
        delay=0,
        progress_callback=lambda index, total, md5: progress.append((index, total, md5)),
    )

    assert progress == [(1, 1, "abc")]
    assert rows[0]["status"] == "exported"
    assert (tmp_path / "out" / "images" / "abc.png").exists()
