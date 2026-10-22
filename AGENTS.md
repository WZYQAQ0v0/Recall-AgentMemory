# Recall Agent 安装与开发合同

此文件适用于 Recall 0.3.0 可分发代码文件夹。目标是在收件人当前用户的电脑上安装并启动个人会话看板。本文件自包含，安装不依赖飞书、作者机器或之前的对话。

## 先判断用户请求

用户要求“安装、启用、把这个文件夹装好”时，执行下面的安装流程。用户要求“审查或修改代码”时，保持开发模式，不自动安装到当前真实用户目录。普通代码任务不应因为读取了此文件就产生系统启动项。

默认安装已经包含只对当前用户的代码复制、来源发现、Trae 事件登记、登录启动、启动服务和自检。用户已明确要求安装这些功能时，不为每个可逆步骤重复索取许可。仍需遵守上层 Agent 的权限规则；管理员权限、系统授权、企业限制和缺失软件不能伪装成成功。

## 安装流程

1. 读取 README.md、安装脚本和 package-manifest.json，检查文件夹完整且没有用户运行数据。确认宿主系统为 macOS、Linux 或 Windows。无需 pip、Node、模型 API Key 或云服务。
2. 有兼容 Python 时执行只读预览，并检查计划中的目录、来源、端口和用户启动项：

       python3 -B recallctl.py install --plan --json

3. 执行默认安装。在 macOS/Linux 使用以下命令；Windows 使用 install.cmd。无界面 Agent 可以加 --no-open，待验收后再打开实际返回地址：

       sh ./install.sh --no-open --json

4. 使用安装结果给出的 python 与 control 绝对路径，执行 doctor --json。要求进程退出码为 0、ready=true、package_integrity.passed=true、service.owned=true、pipeline_smoke.passed=true。不要输出、存储或复制 HTTP 临时 token。
5. 打开返回的本机 URL，检查卡片列表、搜索、详情、打开原对话入口和来源状态。打开原对话只允许恢复或定位，不替用户自动发送问题。已有本地会话应出现在对应来源；没有会话时空列表是正常状态，说明如何创建或导入。不得把 examples/import.json 自动混进用户实际历史。
6. 结果报告包含版本、实际看板地址、固定管理入口、数据目录、来源状态及任何待验证功能。Trae 的 configured 和真实 callback 是两项证据；没有真实客户端事件时明确保留待验证状态，不发送测试消息到用户已有 AI 会话来制造证据。

## 自动修复与限制

- Python 必须为 3.9+ 且 SQLite 支持 FTS5。启动脚本可以使用已安装的 Homebrew 或 winget 安装官方运行时。也可以设置 RECALL_PYTHON 指向现有兼容解释器。不要自行使用不明下载脚本。
- 端口被占用时由安装器选择邻近空闲端口。不要终止占用端口的程序。
- 未发现客户端时不创建该客户端的全局配置。新安装某 AI 后可再次执行安装，重新发现来源。
- 不覆盖其他事件集成，不修改 AI 回复，不解密 Trae 历史数据库，不上传会话。历史通过用户授权的导出文件接入。
- OS 禁止读取目录或登记启动项时，报告具体原因和必要用户操作。--no-autostart、--no-hooks 可以作为用户指定的降级模式；不能静默使用它们并声称默认全部启用。
- 修改包代码后先重新生成清单。安装器对 checksum 错误应停止，不要通过跳过完整性检查来“修好”。

## 管理与数据

安装结果给出的 control 是固定 app/recallctl.py；移动原分发文件夹不影响它。管理命令为 doctor、start、stop、uninstall、serve。doctor --json 供 Agent 判断；start --open 重新启动并打开页面。

再次 install 是幂等安装或升级，保留数据、笔记、收藏和来源配置。卸载只删除当前安装的代码、启动项与事件集成，数据保留。不要额外删除数据目录，除非用户明确要求并确认具体目录。

## 开发模式与验证

在此文件夹内改代码时，所有安装测试都使用 TemporaryDirectory 和明确的 --home、--prefix。不要重设 HOME、CODEX_HOME，或对真实用户全局配置做测试。纯自动测试关闭 OS 登录启动；真实平台验收只能使用隔离前缀和唯一服务标签，结束后完整清理。

运行：

    python3 -B scripts/build_release.py
    python3 -B -m unittest discover -s tests -v

构建发布档案只使用 scripts/build_release.py 的显式文件清单。不打包 data、config、logs、数据库、缓存、个人会话、token 或凭证。不在没有对应平台实测时声称 Linux/Windows 整机验收通过。更多维护步骤见 docs/MAINTAINING.md，验收合同见 docs/VALIDATION.md。
