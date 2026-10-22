# Recall AgentMemory

**跨 AI 编程会话的本地索引与经验检索工具。**

Recall AgentMemory（简称 Recall）为可读取或导入的 AI 编程会话建立统一索引，提供会话摘要、关键词搜索、原文查看和原会话跳转。适用于使用多种 AI 编程工具、需要长期检索历史排障过程与技术经验的开发者。

应用在本机运行，使用 Python 标准库与 SQLite FTS5。摘要和关键词通过本地文本提取生成，无需配置模型 API、云端数据库或额外付费服务。

## 核心功能

- **统一会话管理**：按 AI 来源与原始会话 ID 整理记录，合并同一会话的续写内容，保留来源身份。
- **全文检索**：搜索会话正文、摘要、关键词与个人笔记，支持中文和技术标识，并可按来源、项目、时间与收藏状态筛选。
- **经验卡片**：展示问题摘要、核心内容、关键词与原文命中片段，便于快速判断会话相关性。
- **原文溯源**：查看历史消息，并在客户端和原始记录可用时打开对应会话。
- **笔记与收藏**：为会话补充排障经验和检索线索，笔记内容同步参与搜索。
- **本地增量同步**：持续读取受支持的本地记录，并通过导入方式接入其他工具的会话。

## 快速开始

### 环境要求

- Python **3.9 或更高版本**，且其 SQLite 运行时支持 **FTS5**。
- 当前用户具有会话来源目录的读取权限，以及安装目录的写入权限。
- macOS 为主要验证平台；Linux 和 Windows 已提供适配入口，整机验证状态见[平台支持](#平台支持)。

已有兼容 Python 时，应用无需安装第三方 Python 包或前端构建依赖。缺少运行时时，安装脚本可使用已安装的 Homebrew 或 Windows winget 获取 Python；Linux 的系统包安装仅在已有 root 权限时执行。若无法自动准备运行时，脚本会返回具体错误。

### 获取项目

```bash
git clone https://github.com/WZYQAQ0v0/Recall-AgentMemory.git
cd Recall-AgentMemory
```

也可以[下载源码 ZIP](https://github.com/WZYQAQ0v0/Recall-AgentMemory/archive/refs/heads/main.zip)，解压后进入包含 `AGENTS.md`、`recallctl.py` 和安装脚本的目录。

### 方式一：通过 Agent 安装 （强烈推荐）

将项目目录交给能够读取本机文件并执行命令的 Agent，使用以下指令：

> 请阅读项目中的 AGENTS.md，在当前用户环境安装并启动 Recall，启用本机可用的会话来源和登录启动，执行 doctor 自检并打开看板。请保留已有配置和数据，并说明尚未验证的功能或需要用户处理的系统权限。

[AGENTS.md](AGENTS.md) 定义安装与验证流程，[CLAUDE.md](CLAUDE.md) 提供相同的 Claude Agent 入口。安装说明随源码提供，不依赖外部文档或其他会话上下文。

### 方式二：通过脚本安装

macOS / Linux：

```bash
sh ./install.sh
```

Windows：

```powershell
.\install.cmd
```

macOS 也可双击 `install.command`。默认安装流程包括来源发现、用户级登录启动配置、后台服务启动和自检，并在完成后打开浏览器。

看板默认从 `http://127.0.0.1:8767` 选择本机空闲端口。端口已被占用时，安装器会选择邻近端口，以安装结果返回的地址为准。

## 会话来源与接入方式

| 来源 | 接入方式 | 支持范围 |
| --- | --- | --- |
| Codex 桌面应用 / CLI | 自动读取本机记录 | 主会话、归档记录与同一会话的续写内容 |
| Claude Code | 自动读取本机记录 | 终端主会话；过滤子代理、思考内容和工具输出等非对话记录 |
| Trae / Trae CN | 新会话事件采集 + 历史导入 | 检测到客户端后登记事件；历史通过导出文件接入 |
| ChatGPT / Claude 导出 | 原生导出 JSON 导入 | 用户消息、AI 回复及可识别的原会话信息 |
| Cursor、Gemini、Copilot、OpenCode、豆包及其他 Agent | 通用格式导入 | JSON、JSONL 或文本记录；支持自定义来源名称 |

Trae 事件采集依赖客户端实际发送 `SessionStart`、`UserPromptSubmit` 和 `Stop` 等事件。`doctor` 中的 `awaiting_client_callback` 表示已配置但尚未观察到真实回调；模拟链路检查不代表真实客户端采集已验证。Trae 的加密历史数据库不做自动解密。

其他工具目前通过通用导入接入，不提供原生历史记录的自动读取适配。安装器仅启用当前电脑具备条件的来源与功能。

## 检索与原会话跳转

在搜索框输入问题、报错或技术关键词，即可检索已收录的正文、摘要和笔记。搜索结果展示命中片段，并保留对应的 AI 来源、项目和原始会话标识。

卡片右下角及详情页提供原会话入口：

- **Codex**：原生记录可用时打开对应桌面对话；已安装 CLI 和终端时可使用终端恢复入口。
- **Claude Code**：在原项目目录打开终端并恢复指定会话，不自动发送新的问题。
- **Claude 历史镜像**：原文件不可用但存在 Codex 镜像时，入口明确标记为“打开 Codex 镜像”。
- **网页会话**：使用可识别的原会话 ID 或已有链接打开页面，访问仍取决于原账号与权限。

客户端未安装、源记录已移动或缺少原链接时，可继续查看索引中保存的原文。Trae 本地会话目前没有已验证的直达协议，支持原文查看及已有合法分享链接的打开。

## 运行管理与升级

在项目根目录执行以下命令；Windows 将 `python3` 替换为本机兼容的 Python 命令：

```bash
# 查看运行与集成状态
python3 -B recallctl.py doctor --json

# 启动服务并打开看板
python3 -B recallctl.py start --open

# 停止当前服务
python3 -B recallctl.py stop

# 卸载代码与集成，保留个人数据
python3 -B recallctl.py uninstall
```

安装完成后，也可使用安装结果中的固定 `control` 路径管理服务。自定义安装目录时，管理命令需使用对应的 `--prefix`。

- **升级**：获取新版本后再次运行安装脚本，保留索引、笔记、收藏和来源配置。
- **停止**：停止当前运行的服务，登录启动配置继续保留。
- **卸载**：移除当前安装的代码、启动项和事件集成，保留会话索引、导入记录及个人配置。

`doctor` 分别报告运行时、包完整性、服务身份、来源状态与事件证据。`ready=true` 表示这些检查未发现错误；源文件读取异常及尚未观察到的客户端回调会在报告中单独列出。

## 平台支持

| 系统 | 默认安装目录 | 登录启动方式 | 验证状态 |
| --- | --- | --- | --- |
| macOS | `~/Library/Application Support/Recall` | 用户级 LaunchAgent | 安装与服务生命周期已验证 |
| Linux | `$XDG_DATA_HOME/recall`，默认 `~/.local/share/recall` | systemd 用户服务；不可用时使用桌面 autostart | 适配已提供，整机行为尚未验证 |
| Windows | `%LOCALAPPDATA%\Recall` | 当前用户 Startup 目录 | 适配已提供，整机行为尚未验证 |

不同平台的客户端协议、目录权限与企业管理策略可能影响来源读取和启动配置。验证范围与功能边界见[验收说明](docs/VALIDATION.md)。

## 数据存储与隐私

运行数据与源码分开保存：

| 目录 | 内容 |
| --- | --- |
| `app/` | 已安装的应用代码 |
| `data/` | 会话索引、导入记录与事件捕获数据 |
| `config/` | 来源配置与安装登记 |
| `logs/` | 服务运行日志 |

应用仅监听 `127.0.0.1`，会话处理不依赖远程模型服务。源码和发布包不包含开发者的个人会话、运行索引、凭证或截图；安装时读取的是使用者本机的来源。

常见密钥模式会在索引或导入写入前脱敏。此机制不等同于覆盖所有敏感信息的审计，共享运行数据前仍需检查其内容。安装完成后可移动或删除原源码目录，固定安装中的代码与数据继续保留。

## 配置与扩展

修改安装目录中的 `config/sources.json`，可调整 `codex`、`claude` 和 `trae` 的来源目录；将对应值设为空字符串可停用该来源的本地读取。重新安装会保留已有配置值。

通用导入示例见 [examples/import.json](examples/import.json)。会话对象包含 `id` 或 `native_id`、`provider`、`title` 和 `messages`；每条消息包含 `role`、`content`，可选 `timestamp`。自定义来源名称使用 1–32 位 ASCII 字母、数字、下划线或连字符，并以字母开头。

## 开发与贡献

项目使用 Python 标准库、SQLite FTS5 与静态 HTML / CSS / JavaScript。维护、隔离测试及来源扩展说明见 [docs/MAINTAINING.md](docs/MAINTAINING.md)。

修改发布文件后，重新生成清单并运行现有测试：

```bash
python3 -B scripts/build_release.py
python3 -B -m unittest discover -s tests -v
```

`package-manifest.json` 包含逐文件 SHA-256 校验值，README 修改也需要同步清单。安装器会拒绝文件缺失或内容与清单不一致的分发包。

欢迎通过 [Issues](https://github.com/WZYQAQ0v0/Recall-AgentMemory/issues) 提交问题与功能建议，或通过 Pull Request 改进文档、来源适配和测试。反馈时请附版本、操作系统与复现步骤，并移除私人会话和凭证。

## 许可证

本项目采用 [MIT License](LICENSE)。
