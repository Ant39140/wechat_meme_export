from pathlib import Path

from wechat_meme_export import discovery
from wechat_meme_export.discovery import (
    INSTALLER_FILE_DIR_INI,
    LAST_FILE_DIR_INI,
    _configured_wechat_roots,
    discover_emoticon_databases,
    parse_wechat_config_dir,
)
from wechat_meme_export.memory import version_from_module_path


def test_parse_wechat_config_dir_mydocument_and_custom(tmp_path: Path) -> None:
    doc1 = tmp_path / "User1" / "Documents"
    doc2 = tmp_path / "RedirectedDocs"
    appdata = tmp_path / "AppData" / "Roaming"

    assert parse_wechat_config_dir("MyDocument:", [doc1, doc2], [appdata]) == [doc1, doc2]
    assert parse_wechat_config_dir("MyDocument:\\SubDir", [doc1], [appdata]) == [doc1 / "SubDir"]
    assert parse_wechat_config_dir("Appdata:\\TencentData", [doc1], [appdata]) == [appdata / "TencentData"]
    assert parse_wechat_config_dir(str(tmp_path / "CustomWeChat"), [doc1], [appdata]) == [
        tmp_path / "CustomWeChat"
    ]


def test_configured_wechat_roots_reads_xwechat_ini(tmp_path: Path) -> None:
    docs = tmp_path / "Documents"
    appdata = tmp_path / "AppData" / "Roaming"
    custom_dir = tmp_path / "D_Drive" / "WeChatData"
    cfg_dir = appdata / "Tencent" / "xwechat" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / LAST_FILE_DIR_INI).write_text(str(custom_dir), encoding="utf-8")
    (cfg_dir / INSTALLER_FILE_DIR_INI).write_text("MyDocument:", encoding="utf-8")

    roots = _configured_wechat_roots([docs], [appdata])

    assert custom_dir / "xwechat_files" in roots
    assert custom_dir in roots
    assert docs / "xwechat_files" in roots


def test_discover_emoticon_databases_supports_custom_root_and_deduplicates(
    tmp_path: Path, monkeypatch
) -> None:
    custom_base = tmp_path / "CustomStorage"
    db_file = custom_base / "xwechat_files" / "wxid_test_123" / "db_storage" / "emoticon" / "emoticon.db"
    db_file.parent.mkdir(parents=True)
    db_file.write_bytes(b"sqlite-data")

    monkeypatch.setattr(discovery, "default_wechat_roots", lambda: [custom_base, custom_base / "xwechat_files"])

    found = discover_emoticon_databases()
    assert len(found) == 1
    assert found[0].path == db_file
    assert found[0].account_dir.name == "wxid_test_123"

    # Also supports passing account dir or direct emoticon.db file path
    assert discover_emoticon_databases([db_file.parents[2]])[0].path == db_file
    assert discover_emoticon_databases([db_file])[0].path == db_file


def test_discover_emoticon_databases_ignores_permission_error(tmp_path: Path, monkeypatch) -> None:
    forbidden_root = tmp_path / "Forbidden"
    valid_root = tmp_path / "Valid" / "xwechat_files"
    db_file = valid_root / "wxid_ok" / "db_storage" / "emoticon" / "emoticon.db"
    db_file.parent.mkdir(parents=True)
    db_file.write_bytes(b"sqlite-data")

    orig_exists = Path.exists

    def fake_exists(self: Path) -> bool:
        if self == forbidden_root:
            raise PermissionError("Access is denied")
        return orig_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    found = discover_emoticon_databases([forbidden_root, valid_root])
    assert [item.path for item in found] == [db_file]


def test_version_from_module_path_parses_weixin_4_versions() -> None:
    assert version_from_module_path(Path(r"C:\Program Files\Tencent\Weixin\4.1.13.48\Weixin.dll")) == (4, 1, 13, 48)
    assert version_from_module_path(Path(r"C:\Program Files\Tencent\Weixin\4.2.0.50\Weixin.dll")) == (4, 2, 0, 50)
