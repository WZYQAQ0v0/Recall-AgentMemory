# Recall 维护与分发

Recall 使用 Python 标准库、SQLite FTS5 和静态 HTML/CSS/JavaScript。recall/core.py 负责读取与索引，server.py 提供仅本机 HTTP，capture.py 接收 Trae JSON 事件，install.py 管理代码和登记，services.py 管理平台启动项，doctor.py 执行分层自检，navigation.py 负责原始会话跳转。

## 修改代码和清单

在分发文件夹中修改源码。每次修改任何发布文件后，运行：

    python3 -B scripts/build_release.py
    python3 -B -m unittest discover -s tests -v

manifest 使用每文件 SHA-256，用于发现丢失或意外修改；它不是数字签名，也不替代获取来源的信任判断。安装只复制 manifest 列出的文件；临时文件或运行数据不参与发布。

pyproject.toml 提供 Python 项目元数据和可编辑开发入口。默认面向用户的交付是完整源码文件夹与 ZIP，不要求 pip 安装。若从 pip 可编辑入口调用安装，必须传 --source 指向包含完整清单的源码文件夹。

## 隔离安装调试

不要把开发测试安装到真实默认目录。使用独立测试路径，例如：

    python3 -B recallctl.py install --home "/tmp/recall-test/user" --prefix "/tmp/recall-test/install" --no-autostart --no-open --json
    python3 -B recallctl.py doctor --home "/tmp/recall-test/user" --prefix "/tmp/recall-test/install" --json
    python3 -B recallctl.py uninstall --home "/tmp/recall-test/user" --prefix "/tmp/recall-test/install" --json

Windows 使用该系统的临时目录。测试结束后，只删除明确创建的测试目录。不要用改写 HOME 或 CODEX_HOME 的方式模拟使用者，这可能影响其他客户端。

安装后的 app 代码目录可以整体替换，用户数据始终位于独立 data。SQLite API 连接在使用后关闭；索引增量处理追加或替换的源文件。修改数据 schema 时，应增加显式迁移并验证旧索引、笔记和收藏，而不是删除旧索引重建。

## 发布

修改版本时同步 recall/__init__.py 与 pyproject.toml，更新独立的验收说明。然后构建：

    python3 -B scripts/build_release.py --archive "../recall-agent-package.zip"

ZIP 根目录包含 README.md、AGENTS.md、CLAUDE.md、各平台安装入口、源码、示例、测试及 manifest。检查 archive 内没有 sqlite、jsonl 历史、日志、缓存、凭证或作者绝对路径。分发前校验 manifest；安装后再次 doctor。

CLAUDE.md 与 AGENTS.md 保持相同内容，适配不同 Agent 的入口约定。文档必须自包含；在线文档只是镜像，收件人不需要在线文档权限即可安装。

## 扩展来源

已有客户端采用读取适配或事件接入；新的客户端应先确认可获得的数据格式和真实身份。保留 provider + native_id，不把导入镜像误认成新提供方的原生会话。没有可读取记录的客户端可以先提供通用导入，而不能宣布自动同步已完成。

改动 Trae 捕获时保留安静、可重入和有界输入的行为；错误不能影响客户端回答。真实事件状态只由用户实际数据目录中的事件证据更新，doctor 的合成测试使用临时目录。

## 原会话打开

navigation.py 按索引 provider、native_id、源文件和本机客户端生成入口。HTTP 只接收会话 ID，不接收可执行命令或 URL。原生文件必须位于对应根目录中，打开前再次核对身份。macOS 的 Claude 恢复使用只对当前用户可执行的 .command 文件，不通过 AppleScript 控制终端。

Codex URI 由本机已安装客户端确认支持，仍需真实客户端定位验收。网页链接仅允许已支持 AI 的 HTTPS 会话路径；导入文件中的 command、file、javascript 等协议不参与启动。缺少本地文件或原项目目录时，不能把导入文本误标为可恢复的终端会话。
