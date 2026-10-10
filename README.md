# HEX Beta · 公开测试

让宿主模型按你的目标完成视觉创作：理解需求、看参考、形成画面、写适配目标模型的提示词，再根据实际结果检查和修改。

**[下载 ZIP](https://github.com/jasperhex45-design/hex-agent-beta/archive/refs/heads/main.zip) · [GitHub 仓库](https://github.com/jasperhex45-design/hex-agent-beta) · [提交反馈](https://github.com/jasperhex45-design/hex-agent-beta/issues/new/choose)**

当前版本以 [pyproject.toml](pyproject.toml) 为准。本版更新见 [CHANGELOG.md](CHANGELOG.md)。这是公开测试版，任何人都可下载试用；功能范围与真实质量边界见 [BETA.md](BETA.md)。

## 让 AI 助手帮你安装

把下面这段交给**能访问文件、运行终端的 AI 助手**，例如在本机工程中工作的 Codex。仅能聊天的网页会话不能据此直接安装到你的电脑。

```text
请帮我安装 HEX Beta：
https://github.com/jasperhex45-design/hex-agent-beta

下载 main 分支 ZIP 到独立目录并解压，或用 Git 克隆到新目录，不覆盖已有工程。进入包含 README.md 的根目录，先读 README.md 和 CODEX_START.md。检查 Python >= 3.11；Windows 运行 .\scripts\bootstrap.ps1，macOS/Linux 运行 sh scripts/bootstrap.sh。

使用项目 .venv 核对 bootstrap 的 doctor、validate 真实结果；失败就报告具体原因，不宣称安装成功。基础安装不需要下载生图模型、运行开发测试集或配置付费服务。

如果我要在 Codex 中使用集成，先用项目解释器运行 -m hex_agent codex install --dry-run，核对当前项目 .codex 配置和计划改动，再运行 -m hex_agent codex install 并检查实际状态。仓库已有 .agents/skills/hex；不要把依赖或集成配置安装成功当作全局 Skill 或 hooks 已启用。

最后告诉我安装目录、已通过与未通过的检查，以及如何在当前宿主中开始用 HEX。模型与生图额度使用我自己的宿主或已连接服务。
```

## 手动安装

需要 **Python 3.11 或更新版本**。点击上面的“下载 ZIP”，解压后，在**包含 README.md 的根目录**打开终端。

**Windows PowerShell：**

```powershell
.\scripts\bootstrap.ps1
```

<details>
<summary>Windows 提示脚本被执行策略阻止时</summary>

在同一工程目录逐条运行以下命令。无需激活虚拟环境或修改全局执行策略；已有 `.venv` 时跳过第一条。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m hex_agent doctor
.\.venv\Scripts\python.exe -m hex_agent validate
```

如果没有 `py` 启动器，第一条改用已安装的 Python 3.11+ 的 `python` 命令；任一步失败先处理报错。

</details>

**macOS / Linux：**

```sh
sh scripts/bootstrap.sh
```

也可以用 Git 获取同一份源码，再按系统执行上述安装命令：

```sh
git clone https://github.com/jasperhex45-design/hex-agent-beta.git
cd hex-agent-beta
```

安装将基础依赖放入项目 `.venv`，并执行 `doctor` 与 `validate`；已有项目虚拟环境会被复用。Python 与宿主软件需预先安装。安装不会运行开发测试集、下载模型或自动注册全局 `hex-agent` 命令。当前通过源码目录使用，未发布 PyPI 安装包。

## 开始创作

在支持文件、看图和相应生成工具的宿主中打开整个工程目录，然后说明需求，例如：

> 用 HEX。以这张图为编辑输入，保留车身比例、机位和轮毂，只把漆面做得更平滑通透。先看参考，再执行，最后用结果图指出是否做到。

宿主读取 [CODEX_START.md](CODEX_START.md)，使用统一入口处理文件与流程。你只需说明目标、必须保留的内容和反馈，不需要填写内部模板。

HEX 使用你当前宿主或已连接服务的能力；安装本身不提供模型账户、API Key 或生图额度。没有生图工具时可以完成提示词任务。可选语义检索和 Windows OCR 按需配置，见 [运行环境说明](docs/HEX_WINDOWS_WORKFLOW_20261007.md)。

## 检查安装与 Codex 集成

Windows 使用 `.\.venv\Scripts\python.exe`，macOS / Linux 使用 `.venv/bin/python`：

```text
PYTHON -m hex_agent doctor
PYTHON -m hex_agent validate
PYTHON -m hex_agent demo --out outputs/demo
```

将 `PYTHON` 替换为上述项目解释器。打开 `outputs/demo/result.html` 查看安装演示；它是内置静态示例，不是模型生成效果。

如需 Codex 集成，在当前工程目录使用上述项目解释器：

```text
PYTHON -m hex_agent codex install --dry-run
PYTHON -m hex_agent codex install
```

先检查 dry-run 对当前项目 `.codex/hooks.json` 与 `hex-integration.json` 的计划改动，再执行第二条。仓库已包含 `.agents/skills/hex`，无需为项目使用额外指定全局 Skill 目录。完整步骤见 [CODEX_START.md](CODEX_START.md)。配置写入成功不证明 hooks 已获信任或生效，以当前宿主实际状态为准。

## 反馈与维护

通过 **[Issues → HEX Beta 反馈](https://github.com/jasperhex45-design/hex-agent-beta/issues/new/choose)** 提交实际问题。**Issues 内容公开，请只上传可公开的素材。**原始需求、结果图和最关键的一处差距，比整包日志更有用；提交方式见 [BETA.md](BETA.md)。

测试版不包含开发测试集。维护者在完整开发库中安装测试工具并按改动执行必要回归。源码目录保留运行所需知识与 schema，单独 wheel 不是完整发行物。

`hex_agent`、`cl_hex` 是入口，`liu_agent` 保留实际实现；旧命令只作兼容。完整命名关系见 [名称说明](docs/PROJECT_IDENTITY.md)。

## 许可

沿用 [CC BY-NC 4.0](LICENSE)，第三方来源与适用范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。公开下载不改变这些条款。程序检查通过不等于画面已满足创作要求，效果由实际任务与用户反馈验证。
