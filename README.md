# NovelForge

[![CI](https://github.com/liqiheng987/NovelForge/actions/workflows/ci.yml/badge.svg)](https://github.com/liqiheng987/NovelForge/actions/workflows/ci.yml)
[![Windows Release](https://github.com/liqiheng987/NovelForge/actions/workflows/release.yml/badge.svg)](https://github.com/liqiheng987/NovelForge/actions/workflows/release.yml)

NovelForge 是一款面向长篇小说创作的本地桌面工作台。它把素材分析、作品规划、结构化记忆、连续生成、章节恢复、设定校验和多格式导出整合在一个应用中。

当前版本：`0.1.0`

## 下载与安装

项目和 Release 已公开。任何用户都可以从 [NovelForge v0.1.0 Release](https://github.com/liqiheng987/NovelForge/releases/tag/v0.1.0) 下载 Windows x64 安装包：

- `NovelForge_0.1.0_x64-setup.exe`：常规安装程序。
- `NovelForge_0.1.0_x64_en-US.msi`：Windows Installer 安装包。
- `SHA256SUMS.txt`：安装包 SHA-256 校验值。

安装包已经在全新 Windows 环境完成以下自动验收：

- MSI 和 EXE 全新安装、启动及卸载。
- `0.0.9` 到 `0.1.0` 的升级。
- 升级后用户数据保留。
- 安装版内置 Agent 启动和数据库初始化。

当前安装包尚未进行 Authenticode 代码签名，Windows SmartScreen 可能显示未知发布者警告。代码签名完成前，建议只从本仓库的 Release 页面下载安装包，并使用 `SHA256SUMS.txt` 核对文件。

## 主要能力

### 素材与知识库

- 导入 TXT、PDF、DOCX、EPUB，单文件最大 200 MB。
- 生成结构化素材树、章节卡片和关键内容索引。
- 支持素材类型提示、置顶、删除和按需加载。

### 长篇创作

- 按作品隔离会话、章节、素材、设定和故事结构。
- 支持讨论、正式创作、续写、修改和连续多章生成。
- 支持协作、静默、溯源、教学等创作模式。
- 支持会话分支、比较与合并。
- 支持灵感生成、文风试写、跨类型桥接和内容缺口检查。

### 连贯性与恢复

- 使用宇宙规则、事实表、章节记忆和影响分析维护连贯性。
- 批量生成任务可记录进度，并在中断后继续。
- 章节编辑支持草稿自动保存和过期版本保护。
- 章节历史、回收站、恢复和永久清理。
- 项目删除前自动创建安全备份。

### 数据保护与交付

- 每天首次启动自动创建 SQLite 一致性备份，最多保留 7 份。
- 支持手动备份、完整性检查和安全恢复。
- 支持交付前完整性检查。
- 导出 TXT、PDF 和 EPUB。

### 模型服务

- 支持 OpenAI API。
- 支持兼容 OpenAI Chat Completions 的第三方或本地模型服务。
- 每位用户自行配置 API 地址、模型和 API Key。
- 项目和安装包不包含预置的第三方 API Key。

## 首次使用

1. 安装并启动 NovelForge。
2. 打开右上角“设置”。
3. 选择 OpenAI 或第三方兼容 API。
4. 填写 API 地址、API Key 和模型 ID。
5. 使用“测试连接”确认模型可用。
6. 创建作品，然后导入素材或直接开始对话创作。

第三方兼容服务通常填写以 `/v1` 结尾的基础地址，也可以填写完整的 `/chat/completions` 地址。使用本地模型时，可以在作品设置中启用“纯本地模型”隐私模式。

## 隐私与本地数据

- 标准模式会把当前操作需要的上下文发送给用户配置的模型服务。
- 纯本地模式只允许连接 `localhost`、`127.0.0.1` 或 IPv6 loopback 地址。
- Windows 下使用 DPAPI 加密保存 API 配置，只有当前 Windows 用户能够解密。
- 桌面端与本机 Python Agent 使用随机端口、随机启动令牌和实例校验。
- 脱敏诊断报告不包含 API Key、提示词、小说正文、用户文件名或日志正文。

主要数据位置：

```text
数据库：%APPDATA%\NovelForge\storage\novel_forge.db
备份：  %APPDATA%\NovelForge\storage\backups\
日志：  %APPDATA%\NovelForge\logs\
配置：  %APPDATA%\com.novelforge.desktop\settings.json
```

卸载应用不会主动删除用户的创作数据库。删除或迁移数据前，请先创建备份。

## 技术架构

```text
React + TypeScript + Zustand
           |
      Tauri invoke / 本机 HTTP
           |
Tauri + Rust -------- FastAPI + Python
  系统能力              AI 与业务逻辑
           \             /
                SQLite
```

- React：界面、交互和客户端状态。
- Tauri/Rust：窗口、文件权限、配置加密、Agent 启动和健康监控。
- FastAPI/Python：模型调用、素材分析、创作流程和数据库业务。
- SQLite：作品、会话、章节、记忆、任务、历史和备份状态。

## 开发环境

需要：

- Windows 10/11
- Node.js 22+
- pnpm 10+
- Python 3.12+
- Rust stable
- Tauri 的 Windows 构建依赖和 WebView2

安装依赖：

```powershell
pnpm install --frozen-lockfile
python -m venv python-agent\.venv
.\python-agent\.venv\Scripts\Activate.ps1
python -m pip install -r python-agent\requirements.txt
```

启动桌面开发环境：

```powershell
.\python-agent\.venv\Scripts\Activate.ps1
pnpm dev
```

## 测试与构建

运行项目快速检查：

```powershell
pnpm check
```

该命令运行 Python 回归测试、前端生产构建和 Rust 测试。当前仓库包含 87 项 Python 测试和 10 项 Rust 测试。

完整 CI 还会运行：

```powershell
python -m compileall -q python-agent
cargo fmt --manifest-path src-tauri\Cargo.toml --check
cargo clippy --manifest-path src-tauri\Cargo.toml --all-targets -- -D warnings
```

构建 Windows 安装包：

```powershell
pnpm build
```

该命令先使用 PyInstaller 打包独立 Python Agent，再由 Tauri 生成 MSI 和 NSIS EXE。

## 发布流程

- `main` 分支和 Pull Request 自动运行 CI。
- 版本标签必须采用 `v主版本.次版本.修订版本`，例如 `v0.1.0`。
- Windows Release 工作流检查四处版本号一致性。
- 工作流构建 Agent、MSI 和 EXE，并在全新 Windows Runner 执行安装生命周期验收。
- 验收通过后生成 SHA-256 并创建 GitHub Release。
- 配置代码签名 Secret 后，工作流还会签名、添加时间戳并验证安装包签名。

详细说明见 [Windows 发布流程](docs/RELEASING.md)。

## 项目文档

- [全栈桌面应用开发全流程](docs/FULLSTACK_DESKTOP_DEVELOPMENT_GUIDE.md)
- [Windows 发布与代码签名](docs/RELEASING.md)

## 当前限制

- `v0.1.0` 安装包尚未代码签名。
- 当前没有应用内自动更新器，需要手动下载安装新版本。
- 前端尚未建立组件测试和完整桌面 UI E2E；安装器 E2E 已投入使用。
- 当前主要面向 Windows x64，其他桌面平台尚未完成发布验收。

## 安全说明

不要把 API Key、小说数据库、用户素材、日志、备份或代码签名证书提交到 Git。发现安全问题时，请不要在公开 Issue 中粘贴密钥、小说正文或完整诊断日志。
