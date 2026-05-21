from pathlib import Path

from wechat_meme_export.stickers import load_stickers


def test_load_stickers_from_directory(tmp_path: Path) -> None:
    (tmp_path / "2.jpg").write_bytes(b"fake")
    (tmp_path / "1.png").write_bytes(b"fake")
    (tmp_path / "ignore.txt").write_text("x")

    items = load_stickers(tmp_path)

    assert [item.path.name for item in items] == ["1.png", "2.jpg"]
