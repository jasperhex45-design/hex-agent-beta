# HEX 安装与检查

**日常创作优先安装标准 `HEX-Skill` 包。**需要直接管理 Python 环境和源码时，再选源码版；两者使用同一套创作方法与流程工具，不是功能完整度的区别。

| 你要怎样使用 | 获取什么 | 下一步 |
| --- | --- | --- |
| 日常创作（推荐） | [版本页](https://github.com/jasperhex45-design/hex-agent-beta/releases)中实际提供的标准 `HEX-Skill` 包 | 按下方“标准 Skill”安装，并核对当前宿主能力。 |
| 自己管理 Python 环境与源码 | [Beta 源码 ZIP](https://github.com/jasperhex45-design/hex-agent-beta/archive/refs/heads/main.zip)、发行源码包，或已有完整开发库 | 找到运行工程根，准备 Python 3.11+，在具备执行能力且允许安装依赖时运行对应系统的 bootstrap。 |
| 只得到 wheel 或成功 `import hex_agent` | Python 模块层面的产物/检查 | 当前 wheel 不含完整知识、schema 与 Skill 资源；模块导入成功不等于完整产品或宿主 Skill 已安装，使用上面对应的完整发行物。 |

标准 Skill 包与源码 ZIP 不是同一种包，不要只复制一份 `SKILL.md` 或把源码 ZIP 直接当作 Skill 导入。本指南不要求下载生图模型、安装开发测试集或配置付费服务。

## 标准 Skill：交给当前宿主导入

标准包包含完整的 `hex/`，包括根 `SKILL.md`、runner、运行资源、可读方法和模型资料。按宿主真实的本地技能导入或技能目录入口安装，保留相对目录；具体豆包工作、WorkBuddy、Codex、Claude 入口见[跨宿主指南](HOST_PORTABILITY.md)。这四个宿主使用同一核心，不要求改用 Codex CLI。导入方式有文档依据不代表每个客户端或 Mac 版本已经完成实测。

Claude 自定义 Skills 还要求其 Code execution and file creation 功能开启，企业策略须允许 Skills；这与 HEX 文本本身无需 Python 是两回事。普通指导性读取不能当作正式 Skill 安装成功。ChatGPT 网页的 Skills 当前采用插件分发路径，不能把 Codex/桌面本地 Skill 目录或本 ZIP 直接等同网页已安装。

网页版直接按[网页导入边界](HOST_PORTABILITY.md#网页端导入后使用不启动本地安装流程)核对当前入口：Claude 的 ZIP 上传与自身执行开关有官方依据；豆包工作网页专属菜单和 ZIP 细则尚未核验，不能猜菜单或宣称已安装。无需为了网页使用先 clone、bootstrap 或启动本机 CLI。

可以对助手说：

> 请把附件中的完整 HEX 标准 Skill 包安装到当前宿主，保留相对目录。先核对你真实提供的导入与启用入口，安装后检查能否读取方法和模型资料，以及文件、看图、搜索、Python 和生成工具分别是否可用。不支持的部分请直接说明，不要运行 Codex 专属安装命令，也不要把未知的内置生图后端猜成某个已知模型。

<details>
<summary>需要运行随包流程时：检查 Python 与本地依赖</summary>

只阅读方法资料不需要 Python。需要运行随包流程时，Python 要求 3.11+；在实际的 **Skill 根目录 `hex/`** 使用：

```text
python scripts/hex.py --skill-doctor
```

这里的 `python` 代表当前宿主已核验的 Python 3.11+；本地 macOS 可能使用 `python3` 或已有 `.venv/bin/python`，不要猜 Windows 路径。它会校验并展开本包运行资源，再核对本地运行依赖。展开需要实际 Skill 目录可写；写入被拒就报告限制，保留原安装。缺依赖时先说明缺口，在获得授权后按[跨宿主指南](HOST_PORTABILITY.md)使用该包声明的运行环境；不要在 Skill 根猜测执行源码 bootstrap。检查通过不证明外部账户、生成工具或宿主交互组件已可用。

无 Python、无命令沙箱或沙箱暂不可用时，不把 doctor 作为读取方法的前置步骤。通过真实可用的文件/资源工具打开 `references/runtime/docs/HEX_CORE_METHOD.md` 与本题模型卡；新任务可指导，已有 run 保留原合同与阻塞，不能伪造持久化、回执或跨会话私人经验。`sandbox is unavailable` 等命令启动失败不证明缺依赖或 Mac 不兼容；先保留原报错和发生阶段，停止无依据换 shell/路径/宿主重试，待服务恢复或有新证据再核查。

</details>

**导入并启用后，附上参考图就可以说：**

> 用 HEX，基于这张参考给方案与提示词，先不生图。

如果本次还没选目标模型，助手会先问你。日常创作读到这里即可，不需要再装一份源码；**下方源码安装与检查仅供自管环境的用户阅读，Codex 专节仅在选择其集成时使用。**

## 源码安装：先找对运行目录

需要预先安装 **Python 3.11 或更新版本**以及要使用的 AI 宿主。下载或克隆到独立目录，不覆盖已有工程。

| 你的目录来源 | 在哪里运行安装与检查命令 |
| --- | --- |
| 独立 Beta 仓库、其源码 ZIP 或发行源码包 | 解压后的产品根：同层有 `README.md`、`CODEX_START.md`、`pyproject.toml`、`scripts/`。 |
| 完整开发仓库 | 进入仓库内的 **`hex-agent/`** 子目录；外层也有 README，但不是运行工程根。 |

以下源码命令都从这个运行工程根执行。不要仅凭“有 README”判断目录，也不要在 `docs/` 中运行。源码的 `.agents/skills/hex` 引用同一完整工程，不能仅复制这一个目录当作标准包；跨宿主搬运请选择标准包。源码 bootstrap 会安装依赖，助手执行时须遵守当前下载管理和授权规则，不能在沙箱不可用时盲目调用。

也可以用 Git 获取同一份 Beta 源码：

```sh
git clone https://github.com/jasperhex45-design/hex-agent-beta.git
cd hex-agent-beta
```

如果已有完整开发库，从其外层进入：

```sh
cd hex-agent
```

这些是获取源码的替代路径，不需要全部执行；让 AI 助手代办时，应遵守当前宿主的下载管理入口与授权要求。

### Windows PowerShell

```powershell
.\scripts\bootstrap.ps1
```

脚本创建或复用项目 `.venv`，安装基础依赖，并运行 `doctor` 与 `validate`。任一步失败先处理报错，不继续宣称安装完成。

<details>
<summary>脚本被执行策略阻止时</summary>

在同一个运行工程根逐条执行；无需激活虚拟环境或修改全局执行策略。已有 `.venv` 时跳过第一条。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -c "import sys; assert sys.version_info >= (3,11), 'Python >=3.11 required'"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m hex_agent doctor
.\.venv\Scripts\python.exe -m hex_agent validate
```

如果没有 `py` 启动器，第一条改为使用已安装的 Python 3.11+：`python -m venv .venv`。已有虚拟环境不满足版本要求时先报告环境问题，不盲目覆盖；每条命令失败都应先停止并处理原因。

</details>

### macOS / Linux

```sh
sh scripts/bootstrap.sh
```

脚本使用 `python3` 创建或复用 `.venv`，检查 Python 版本、安装基础依赖，并运行 `doctor` 与 `validate`。macOS/Linux 使用 `.venv/bin/python`，`Hex.ps1` 与 `.venv/Scripts/python.exe` 是 Windows 入口，不用于 Mac。不要跨系统复制虚拟环境；发现 Windows 环境或失效的解释器时保留现状并说明需要本机环境，不直接覆盖。按具体报错修复环境，不把部分步骤通过当作全部成功。

客户端在 Mac 上不证明工具执行也在 Mac：云端或网页宿主应先核对沙箱实际系统与路径。Mac 真机安装和完整生成流程本次未测；Darwin 分支模拟只验证参数路由。可选 Paddle OCR/离线 research 仍有 Windows 专用启动路径，不将它们或 `windows_runtime` 这个历史模块名当成整个 HEX 能否使用的判断。需要这些扩展但不可用时保留检查阻塞，不自动取消既定验收。

基础安装不下载生图模型，不运行开发测试集，也不会自动注册全局 `hex-agent` 命令。当前按源码目录使用，未发布 PyPI 安装包；单独 wheel 不是包含知识与 schema 的完整发行物。

## 让 AI 助手代办源码安装

下面这段适用于**能访问文件、运行终端的助手**，例如在本机工程中工作的 Codex。只有聊天能力的网页会话不能据此直接安装到你的电脑。

```text
请帮我安装 HEX Beta：
https://github.com/jasperhex45-design/hex-agent-beta

按当前宿主的下载管理和权限要求，把源码下载或克隆到独立目录，不覆盖已有工程。先读 README.md 和 docs/INSTALL.md。独立 Beta 源码使用产品根；完整开发库先进入 hex-agent/。核对同层存在 CODEX_START.md、pyproject.toml 和 scripts/，不要只看 README 判断目录。

检查 Python >= 3.11；Windows 运行 .\scripts\bootstrap.ps1，macOS/Linux 运行 sh scripts/bootstrap.sh。使用项目 .venv 核对 doctor、validate 的真实结果；失败就报告具体原因，不宣称安装成功。基础安装不要下载生图模型、运行开发测试集或配置付费服务。

只有我选择 Codex 集成时，才先用项目解释器运行 -m hex_agent codex install --dry-run，检查实际配置位置和计划变更后再执行 install。不要把配置写入当作全局 Skill 或 hooks 已启用。

最后告诉我运行工程目录、检查通过与未通过的部分，以及如何在当前宿主开始用 HEX。模型账户和生图额度使用我自己的宿主或已连接服务。
```

## 源码安装检查与静态演示

源码安装使用项目解释器，无需激活 `.venv`。

**Windows：**

```powershell
.\.venv\Scripts\python.exe -m hex_agent doctor
.\.venv\Scripts\python.exe -m hex_agent validate
.\.venv\Scripts\python.exe -m hex_agent demo --out outputs/demo
```

**macOS / Linux：**

```sh
.venv/bin/python -m hex_agent doctor
.venv/bin/python -m hex_agent validate
.venv/bin/python -m hex_agent demo --out outputs/demo
```

`doctor` 与 `validate` 用于核对本地运行环境和配置。demo 为可选项；打开 `outputs/demo/result.html` 查看内置静态示例，**它不是模型生成效果**。命令成功不代表宿主搜索、OCR、模型账户或生成路线已接通。

## 仅需要 Codex 集成时执行

其它宿主不要运行本节命令。先在运行工程根检查 dry-run 的计划改动：

**Windows：**

```powershell
.\.venv\Scripts\python.exe -m hex_agent codex install --dry-run
```

**macOS / Linux：**

```sh
.venv/bin/python -m hex_agent codex install --dry-run
```

核对实际定位的项目 `.codex/hooks.json`、`hex-integration.json` 等文件与已有配置，确认范围后再执行对应命令：

```powershell
# Windows
.\.venv\Scripts\python.exe -m hex_agent codex install
```

```sh
# macOS / Linux
.venv/bin/python -m hex_agent codex install
```

完整开发库的根 Skill 位于外层 `.agents/skills/hex`，独立产品源码包在自己的根目录携带同一入口；项目使用不需要额外指定全局 Skill 目录。安装回执与配置写入只证明对应动作完成，hooks 是否获信任、实际执行及自动会话绑定仍须在当前宿主核对。专属安装、绑定与状态检查统一见 [CODEX_START.md](../CODEX_START.md)。

## 开始使用与遇到缺口时

在当前宿主打开安装好的 Skill 或完整源码工程，对它说“用 HEX”，说明目标、参考、必须保留的内容和本次模型。助手依据[共用创作方法](HEX_CORE_METHOD.md)工作；能够执行完整流程时再沿[操作入口](../CODEX_START.md)推进。

- 没有生图工具时可以交付方案或提示词，不声称已生成图片。
- 标准包、源码或工具接口未适配时，按[跨宿主能力分支](HOST_PORTABILITY.md)说明能完成与不能验证的部分；已有任务不能靠换方式绕过原状态。
- 私人学习需另外取得真实同意，不因安装成功自动开启，见[私人学习说明](WORK_LEARNING.md)。
- 可选的本地检索和 Windows OCR 按需配置，见[运行环境说明](HEX_WINDOWS_WORKFLOW_20261007.md)。

反馈问题与公开素材边界见 [BETA.md](../BETA.md)。
