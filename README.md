# 微信自定义表情导出工具

本工具用于从 Windows 微信 4.x 的本地配置文件中导出“我添加的表情”（自定义表情）。工具支持自动定位 `emoticon.db`、扫描运行中的微信进程以获取数据库解密密钥、解密表情数据库、下载并还原常见图片格式，并提供了一个基于 Tkinter 的 GUI 界面，方便在导出后进行筛选与整理。

## 功能特点

- **自动定位**：自动检索 `%USERPROFILE%\Documents\xwechat_files` 路径下的表情数据库。
- **密钥获取**：通过扫描运行中的 `Weixin.exe` 进程内存，自动获取 `emoticon.db` 的解密密钥。
- **数据库解密**：支持解密 Windows 微信 4.x 的 `db_storage/emoticon/emoticon.db` 数据库。
- **数据导出**：从 `kNonStoreEmoticonTable` 表中导出自定义/收藏的表情。
- **索引生成**：生成 `added_stickers_index.csv`，详细记录导出状态、本地文件路径及原始 CDN URL。
- **可视化筛选**：提供 GUI 界面，支持在导出后预览、筛选并整理表情。

## 暂不支持

- 不支持导出微信表情商店中的整套加密表情包。
- 对于本地缓存已清理且 CDN 链接已失效的早期旧表情，可能无法成功恢复。
- 不支持将导出的表情直接导入到 QQ、微信或其他客户端（仅提供导出与本地整理功能）。

## 环境要求

- **操作系统**：Windows
- **Python 版本**：Python 3.10 或更高版本
- **微信客户端**：Windows 微信 4.x（需保持运行并处于登录状态，以便扫描获取解密密钥）

## 安装指南

克隆仓库并初始化环境：

```powershell
git clone https://github.com/Ant39140/wechat_meme_export.git
cd wechat-meme-export
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

开发与测试环境配置：

```powershell
pip install -e ".[dev]"
pytest
```

## GUI 使用说明

启动图形界面：

```powershell
wechat-meme-export-gui
```

**基本操作流程**：

1. 启动并登录 Windows 微信 4.x。
2. 运行 `wechat-meme-export-gui` 打开工具界面。
3. 在数据库下拉菜单中，选择目标 `emoticon.db`。
4. 点击 **“扫描运行中微信获取密钥”**。
5. 选择导出的保存目录，点击 **“开始导出表情”**。
6. 导出完成后，可点击 **“筛选表情”** 预览并删除不需要的图片。

> **注意事项**：如果微信客户端是以管理员身份运行的，本工具也必须以管理员权限启动，否则将无法读取微信进程内存。

## CLI 命令行使用

**查看帮助信息**：

```powershell
wechat-meme-export --help
```

**列出检测到的数据库**：

```powershell
wechat-meme-export discover
```

**扫描解密密钥**：

```powershell
wechat-meme-export scan-key --db-index 1
```

如果扫描结果为 `total=0`，请确认微信已登录且正在使用对应账号，并确保命令行与微信以相同权限级别运行。若输出显示 `open_failed`，通常是权限不足；若 `bytes_read=0`，请检查 Python 与微信的位数是否一致（Windows 微信 4.x 通常需要 64 位 Python）。`candidates_tested=0` 表示当前微信版本的密钥内存格式尚未被扫描器识别；若候选数大于 0 但没有匹配密钥，请确认选中的数据库属于当前账号，或该版本使用了不同的密钥派生方式。

Weixin 4.1.13.12、4.1.13.65 和 4.1.15.13 使用按数据库保存的 `Config.Cipher` key/salt 记录。扫描器对这些版本按目标数据库 salt 精确匹配配置记录，并通过第一页 HMAC 校验 key；4.1.13.65 的适配参考了 [key-discovery.md](https://github.com/DLBorjigin/zanmenliaotian-ai-exporter/blob/main/skill/wechat-chat-export/references/key-discovery.md) 和 [key_probe.py](https://github.com/DLBorjigin/zanmenliaotian-ai-exporter/blob/main/src/wechat_ai_exporter/key_probe.py)。`config_pairs=0` 表示尚未定位到配置节点；`salt_matches>0` 但 `total=0` 表示记录中的 salt 与所选数据库一致，但第一页 HMAC 校验未通过。

**解密并一键导出**：

```powershell
wechat-meme-export run `
  --db-index 1 `
  --key "<scan-key 输出的 key>" `
  --output ".\exports"
```

**仅解密数据库（不导出文件）**：

```powershell
wechat-meme-export decrypt-db `
  --db-index 1 `
  --key "<scan-key 输出的 key>" `
  --output ".\exports\emoticon.decrypted.db"
```

**从已解密的数据库导出表情**：

```powershell
wechat-meme-export export `
  --db ".\exports\emoticon.decrypted.db" `
  --output ".\exports"
```

## 输出结构

导出文件将保存在指定的输出目录下：

- **表情图片目录**：`exports\images`
- **索引文件路径**：`exports\added_stickers_index.csv`

> **隐私提示**：项目已在 `.gitignore` 中配置忽略 `exports/` 目录、数据库文件、CSV 索引、日志及虚拟环境，以确保您的个人隐私数据不会被意外提交至公共代码仓库。

## 免责与合规说明

本项目仅供个人数据备份与整理研究使用。数据库解密、内存扫描、导出与筛选等所有操作均在本地执行；在下载表情时会请求微信记录中的官方 CDN 链接。请在遵守相关法律法规及微信服务协议的前提下使用本工具。
