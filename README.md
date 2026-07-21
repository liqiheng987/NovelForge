# NovelForge

NovelForge 是一款面向长篇小说创作的桌面工作台。它将素材分析、作品规划、结构化记忆、连续生成、版本分支、设定校验和多格式导出整合在一个本地应用中。

## 当前能力

- 导入 TXT、PDF、DOCX、EPUB 素材并生成结构化素材树。
- 按作品隔离会话、章节、宇宙规则、事实表和故事结构。
- 支持讨论、正式创作、续写、修改和连续多章生成。
- 支持章节确认、编辑、排序和 TXT/PDF/EPUB 导出。
- 支持 OpenAI API 和兼容 OpenAI Chat Completions 的模型服务。
- Windows 下使用 DPAPI 加密保存 API 配置。
- 桌面端与本机 Python Agent 使用随机端口、启动令牌和实例校验。
- 每天首次启动自动创建数据库一致性备份，并支持在设置中立即备份。

## 开发环境

- Node.js 22+
- pnpm 10+
- Rust stable
- Python 3.12+

安装依赖：

```powershell
pnpm install --frozen-lockfile
python -m pip install -r python-agent/requirements.txt
```

启动桌面开发环境：

```powershell
pnpm dev
```

也可以单独启动 Python Agent 和前端。手动开发模式默认使用 `127.0.0.1:8000`；通过 Tauri 启动时会自动改用随机端口和访问令牌。

## 验证

```powershell
pnpm check
```

该命令会运行 Python 回归测试、前端生产构建和 Rust 测试。GitHub Actions 会在 `main` 分支和 Pull Request 上执行相同的质量检查。

## 隐私说明

- 标准模式会把本轮所需上下文发送到用户配置的模型服务。
- 纯本地模式只允许连接 `localhost`、`127.0.0.1` 或 IPv6 loopback 模型地址。
- API Key、Agent 启动令牌、数据库、小说素材、测试输出和本地构建产物不会提交到 Git。

## 数据保护

- 自动备份保存在数据库同级的 `backups` 目录，最多滚动保留 7 份。
- 备份通过 SQLite 在线备份接口生成，并在完成后执行完整性检查。
- 设置页的“创作数据保护”区域可以随时手动创建一份新备份。

## 发布状态

`pnpm build` 会先将 Python Agent 打包为独立 sidecar，再生成 MSI 和 NSIS 两种 Windows 安装包。GitHub 的 `Windows Release` 工作流也可以通过版本标签或手动触发生成安装文件。

正式公开发布前仍建议完成代码签名和干净 Windows 虚拟机验收；未签名安装包可能触发 Windows SmartScreen 提示。
