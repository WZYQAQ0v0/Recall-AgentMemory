# Recall：把 AI 编程会话变成可检索的经验卡片

Recall 在每位使用者自己的电脑上运行，为会话生成标题、关键词、问题摘要和核心结论。一次搜索可以跨 AI 找到相关卡片，查看原文、保存笔记、收藏经验，并从卡片直接打开原对话。摘要采用本机文本提取，不需要模型 API、云端数据库或额外付费服务。

## 把文件夹交给 Agent

解压整个 recall-agent-package 文件夹，交给可以读取文件和执行本机命令的 Agent，并发送：

> 请读取这个文件夹的 AGENTS.md，在当前用户的电脑安装并启用 Recall，包括检测到的会话来源、Trae 事件采集和登录启动。完成 doctor 自检后打开看板，保留现有配置及数据，并报告需要用户操作或尚未实际验证的功能。

AGENTS.md 和 CLAUDE.md 包含完整安装合同，不依赖其他对话或在线文档。不同 Agent 的权限设置可能不同；遇到系统权限、企业策略或缺失运行时，需要如实报告具体阻塞。

## 直接安装

macOS 或 Linux，在解压的文件夹中执行：

    sh ./install.sh

macOS 也可以双击 install.command。Windows 执行：

    .\install.cmd

安装器默认启用登录启动、本机发现的 Trae 事件采集，启动服务并打开浏览器。命令结束前会检查运行时、包文件完整性、服务身份、已登记的集成，以及模拟事件到索引、搜索和摘要的完整链路。安装结果会给出实际地址；默认从 http://127.0.0.1:8767 开始查找空闲端口。

首次需要 Python 3.9 或更高版本，并包含 SQLite FTS5。已有兼容 Python 时没有第三方运行依赖。缺少运行时时，启动脚本优先使用已安装的 Homebrew 或 Windows winget；Linux 的系统包安装只在已有 root 权限时进行。没有合适包管理器或安装权限时，Agent 应协助安装官方 Python，不能把失败当成安装完成。只读预览模式不会自动安装 Python。

## 会话来源及边界

| 来源 | 自动接入 | 找回会话 |
| --- | --- | --- |
| Codex 桌面和 CLI | 读取本机 sessions、archived_sessions 与只读元数据 | 点击打开 Codex 原对话；保留复制继续命令入口 |
| Claude Code 终端 | 读取本机 projects 下的主会话；忽略子代理工具噪声 | 点击打开终端并恢复原会话；保留复制命令入口 |
| Trae / Trae CN | 检测到客户端时，登记 SessionStart、UserPromptSubmit、Stop 事件 | 查看已捕获原文；历史通过导出接入 |
| ChatGPT / Claude 导出 | 导入原生导出 JSON | 查看导入原文及导出文件中的原链接 |
| Cursor、Gemini、Copilot、OpenCode、豆包、其他 Agent | 通用 JSON、JSONL、文本导入；可填写自定义来源名称 | 查看导入原文，按来源筛选 |

Trae 的加密历史数据库不做自动解密。配置事件并不证明客户端已发送事件，doctor 会单独报告 awaiting_client_callback；客户端重启后，正常产生新会话事件才会出现实际记录。模拟链路测试不会给真实事件状态造证据。

“全部功能”表示安装和启用当前包支持、且当前电脑具备条件的功能。未安装的客户端、禁止读写的目录、企业阻止的启动项，以及没有导出或采集接口的 AI，不能凭空取得会话。

## 点击打开原对话

会话卡片右下角和详情页显示“打开原对话”。Codex 原生会话使用 codex://threads/原始ID 打开桌面应用；Claude Code 本地记录会打开终端，在原项目目录执行 claude --resume 原始ID。打开时不会自动发送问题。

原 Claude 文件已移走但保留 Codex 镜像时，入口明确显示“打开 Codex 镜像”。ChatGPT 原生导出可按会话 ID 打开 chatgpt.com/c/ID；其他支持的网页来源使用导出中保存的会话链接，仍需原账号有访问权限。

没有原链接、客户端不可用或源记录已移走时，卡片显示“查看原文”，详情说明原因。Trae 当前本地会话没有已验证的直接跳转协议，使用原文查看或已有的合法分享链接；不调用会话传送接口迁移用户会话。

打开接口只接受索引中的会话 ID，原生记录再次核对身份，不执行导入文件携带的命令。

## 安装后的文件与管理命令

| 系统 | 默认安装目录 | 登录启动 |
| --- | --- | --- |
| macOS | ~/Library/Application Support/Recall | 当前用户 LaunchAgent |
| Linux | $XDG_DATA_HOME/recall，默认 ~/.local/share/recall | systemd 用户服务；不可用时使用桌面 autostart |
| Windows | %LOCALAPPDATA%\Recall | 当前用户 Startup 目录 |

目录中的 app 保存可替换代码，data 保存索引及导入记录，config 保存来源配置和安装登记，logs 保存运行日志。安装器只复制清单列出的代码文件，不携带作者会话、个人配置、凭证或索引。安装完成后，可以移动或删除原解压文件夹。

以下命令在解压文件夹中执行；也可以使用安装结果中的 python 和 control 绝对路径执行：

    python3 -B recallctl.py doctor --json
    python3 -B recallctl.py start --open
    python3 -B recallctl.py stop
    python3 -B recallctl.py uninstall

Windows 将 python3 替换为兼容的 Python 命令。stop 停止当前运行，登录启动登记仍保留。uninstall 移除本安装的代码、启动登记和 Trae 事件，保留会话索引、导入文件、笔记、收藏及来源配置。重新安装会继续使用这些数据。

升级时，用新文件夹再次执行安装命令；安装身份和数据不变。升级采用代码暂存和回滚，不覆盖其他集成的事件条目。安装目录不属于 Recall、启动项被其他人修改或包文件与清单不符时，会停止相关写入并报告原因。

## 配置、导入与开发

config/sources.json 可以修改 codex、claude、trae 的目录；设置为空字符串可以停用对应本地读取。安装器保留现有值，并补充缺少的标准来源。支持 CODEX_HOME、CLAUDE_CONFIG_DIR、XDG_DATA_HOME、XDG_CONFIG_HOME、LOCALAPPDATA 和 APPDATA 的常见路径覆盖。

examples/import.json 只含虚构示例。通用导入对象包含 id、provider、title、messages；messages 每项含 role 和 content，可选 timestamp。自定义 provider 使用 1–32 位 ASCII 字母、数字、下划线或连字符，并以字母开头。JSON 中已有 provider 的会话保留该来源。

看板只绑定 127.0.0.1；常见密钥模式在写入索引或导入时会脱敏。请把运行目录视为个人数据，不要把它装进发布包。项目采用 MIT 许可证。

测试、打包和隔离开发方式见 docs/MAINTAINING.md。平台验收标准见 docs/VALIDATION.md。本版本以 macOS 为主要验收平台；Linux 和 Windows 已提供安装及启动适配，整机行为需在对应系统实际验收后再承诺支持。
