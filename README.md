# HEX · 邀请制内测

让宿主模型按你的目标完成视觉创作：理解需求、看参考、形成画面、写适配目标模型的提示词，再根据实际结果检查和修改。

当前版本以 [pyproject.toml](pyproject.toml) 为准。这个版本面向受邀测试者；具体测试范围、反馈方式与已知边界见 [BETA.md](BETA.md)。

## 安装

需要 Python 3.11 或更新版本。打开包含本文件的工程目录；在完整开发仓库中它是 `hex-agent/`，在独立内测包中就是解压后的根目录。

**Windows PowerShell：**

```powershell
.\scripts\bootstrap.ps1
```

**macOS / Linux：**

```sh
sh scripts/bootstrap.sh
```

安装只使用 3 个基础依赖，并执行 `doctor` 与 `validate`。不会运行开发测试集，也不会下载生成模型或可选知识模型。已有项目虚拟环境会被复用。

## 开始创作

在支持文件、看图和相应生成工具的宿主中打开整个工程目录，然后说明需求，例如：

> 用 HEX。以这张图为编辑输入，保留车身比例、机位和轮毂，只把漆面做得更平滑通透。先看参考，再执行，最后用结果图指出是否做到。

宿主读取 [CODEX_START.md](CODEX_START.md)，使用统一入口处理文件与流程。你只需说明目标、必须保留的内容和反馈，不需要填写内部模板。

HEX 使用你当前宿主或已连接服务的能力；Python 安装本身不提供模型账户或生图额度。没有生图工具时可以完成提示词任务。可选语义检索和 Windows OCR 按需配置，见 [运行环境说明](docs/HEX_WINDOWS_WORKFLOW_20261007.md)。

## 检查安装

Windows 使用 `.\.venv\Scripts\python.exe`，macOS / Linux 使用 `.venv/bin/python`：

```text
PYTHON -m hex_agent doctor
PYTHON -m hex_agent validate
PYTHON -m hex_agent demo --out outputs/demo
```

上面的 `PYTHON` 由你的宿主替换为项目解释器。打开 `outputs/demo/result.html` 查看安装演示；它是内置静态示例，不是模型生成效果。

如需 Codex 集成，先由宿主核对目标配置，再使用 `scripts/hex_entry.py codex install --dry-run` 查看变更，确认环境后安装。主 Skill 支持仓库与源码包两种布局；是否启用 hooks 以当前宿主的实际状态为准。

## 反馈与维护

实际问题通过内测仓库的 **Issues → HEX 内测反馈** 提交。原始需求、结果图和最关键的一处差距，比整包日志更有用。详见 [内测说明](BETA.md)。

维护者在完整开发库中通过 `python -m pip install -e '.[test]'` 安装测试工具，按改动执行必要回归；内测包不包含测试集。源码或 editable 安装用于保留运行所需知识与 schema，单独 wheel 不是完整发行物。

`hex_agent`、`cl_hex` 是入口，`liu_agent` 保留实际实现；旧命令只作兼容。完整命名关系见 [名称说明](docs/PROJECT_IDENTITY.md)。

## 许可

沿用 [CC BY-NC 4.0](LICENSE)，第三方来源与适用范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。邀请控制仓库访问，不改变这些条款。程序检查通过不等于画面已满足创作要求，效果由实际任务与用户反馈验证。
