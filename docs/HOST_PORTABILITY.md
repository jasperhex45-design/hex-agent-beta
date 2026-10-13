# HEX 跨宿主安装与能力边界

资料核验日期：2026-10-13。HEX 的核心方法与模型提示词资料随同一个标准 Skill 包提供，适用于豆包工作、WorkBuddy、Codex、Claude 的可用 Skill 入口；具体导入、资源读取与执行仍按宿主能力核对。运行 HEX 的对话模型、实际生图服务、宿主工具分别确认。宿主能导入 Skill 不等于已经接通 HEX Harness 或任何生图模型。本地检查覆盖 Windows 包与程序、Darwin 分支模拟；macOS 云端安装检查结果单独以公开 Beta Actions 记录为准。四宿主的真实客户端导入、生图及用户 Mac 设备仍未实测。不承诺全部模型、版本或服务接口已适配。

## 先按四层定位，不要求换宿主

| 层次 | 要核对的证据 | 不应据此推断 |
| --- | --- | --- |
| 导入布局 | 文件名、ZIP 根 `hex/SKILL.md`、完整相对资源、宿主实际导入回执 | 源码 ZIP、wheel、单个 Skill 文件等同完整标准包 |
| 指引与选型 | 实际加载的 HEX 入口、用户所选宿主/模型与可选集成 | 文件名 `CODEX_START.md`、`agents/openai.yaml` 要求转 Codex CLI 或 OpenAI 生图 |
| Python/执行能力 | 命令工具是否存在、真实解释器及版本、依赖与可写目录检查结果 | `import hex_agent` 成功就是完整安装，或本地缺 Python 就无法读方法 |
| 宿主沙箱服务 | 原始错误、发生阶段、是否已启动 Python、宿主服务恢复证据 | `sandbox is unavailable` 自动等于 HEX 依赖缺失或 macOS 路径错误 |

服务在 Python 启动前不可用时先停下无依据的换 shell、路径猜测与重复 doctor。新任务可用原生资源读取工具读核心/模型卡继续指导；连资源也读不到就说明缺口，请求提供所需文本或等待宿主恢复。不卸载/重装宿主、不改全局配置，也不以切换宿主绕过原任务预算、锁或拒绝。只在取得新证据或服务恢复后重新做对应检查。未经核验的错误码不得写成 HEX 已知 root cause。

## 标准包与共用入口

`HEX-Skill-v<version>.zip` 内只有一个 `hex/` Skill 根，包含根 `SKILL.md`、`scripts/hex.py`、`bundle.json`、`assets/` 运行资源和 `references/runtime/` 可读资料。八类模型卡、领域方法与运行文档从同一公开源码清单自动导出；无需 Python 也能读取它们。为防止宿主将内嵌方法再次安装为重名技能，可读镜像中的领域入口使用 `METHOD.md`；构建时只转换这些已知入口的引用，原运行包中的方法不变。`bundle.json` 的 `readable_resources` 保存逻辑来源、真实路径与转换前后摘要，读取时按 source→path 映射。`agents/openai.yaml` 是可选宿主展示信息，其存在不要求使用 OpenAI 对话模型或图像服务。

导入完整 `hex/`，保留其相对目录；不要在外面再套源码仓库目录，不要只复制根 Skill 而漏掉方法/模型卡，也不要把 `assets/` 中的运行 ZIP 当成可直接阅读的参考文档。支持工具注入资源的宿主可通过其资源接口提供相同文件，不要求固定机器路径。

标准包、源码、wheel 和 Python 导入须分开验收：标准包提供宿主可发现的单个 Skill 与可读资料；源码入口依赖同一完整工程，独立复制 `.agents/skills/hex` 不会带走引用的工程资源；当前 wheel 配置只收 Python 包，未包含相邻 knowledge/schema/Skill 资源，不是完整发行物。`import hex_agent` 不验证资源、宿主启用或图像接口。

在仓库重建标准包：

```text
python scripts/build_skill_package.py --output-dir OUTPUT
```

命令在 `hex-agent/` 工程根执行；正式发布额外传已核验的 `--source-commit`。不传时来源提交保持未声明，不能伪装已发布。构建产物不包含旧任务、图片、私人图库、测试集、密钥或依赖环境。

## 分别在这些入口安装

| 宿主 | 本次支持的安装方式 | 需要在本机核对的部分 |
| --- | --- | --- |
| WorkBuddy | 打开技能市场，通过“添加技能 → 上传技能”上传本地技能包文件并启用 HEX；以当前 UI 的文件格式要求为准 | 官方支持本地技能包和标准资源目录；仍须核对当前客户端实际导入结果、资源读取、工具名称和参数 |
| 豆包工作 | 使用自建技能、对话安装或导入本地技能文件的入口；提供完整附件/解压目录并让其按当前导入格式创建 HEX | 官方已说明这些入口，但尚未核验精确 ZIP 层级、Python 版本和原生问答组件；不要把普通豆包聊天或 Coze 规则当作这里的安装规范 |
| Codex | 完整 Skill 放在当前官方支持的项目/个人 Skill 入口；项目发现位置是 `.agents/skills/hex`，桌面端亦有 Skills 入口 | 不要求改用 Codex CLI；现有工程绑定继续核验，hooks 只在明确选择集成后配置。官方当前将 ChatGPT 网页 Chat/Work 的 Skills 放在插件分发路径，不能声称此 ZIP 已直接装入网页；此处不创建新插件或网站 |
| Claude | Claude 的 Customize → Skills → Create skill → Upload a skill 上传完整 ZIP 并启用；Claude Code 如已选用，则使用其 `.claude/skills/hex` 本地入口 | Claude 网页/桌面 Skills 要求启用 Code execution and file creation，并受组织策略控制。Claude Code 的本地目录与网页上传入口不能混用；不要求从 Claude 转到 Code 或 Codex |
| 其它支持 Agent Skills 的宿主 | 使用当前宿主的本地 Skill 导入/技能目录，将完整 `hex/` 保留为一个 Skill，确认读取根 `SKILL.md` 及相对资源 | 文件读取与资源工具注入均可；先查当前宿主实际支持，不把某个平台的目录路径当通用安装命令 |

Claude 的创建帮助页将 description 上限写为 200 字符，而开放 Agent Skills 规范为 1024；共用根入口采用较小的 200 字符边界，不删减正文方法。这是文档约束对齐，不代表已复现客户端拒绝。Claude 关闭执行能力时可能无法启用 Skills；HEX 的“资料不需要 Python”仅说明静态内容本身可读，不绕过宿主功能开关。可读文本由其它合法入口提供时才继续指导。

WorkBuddy 的 `.codebuddy/skills/` 是代码开发项目场景的项目技能路径，不是所有桌面/网页视觉任务的统一个人安装目录。普通海报、素材和提示词任务优先使用 UI 导入。WorkBuddy 开放平台的 `description_zh`、`description_en`、`version`、`author` 是市场发布规范中的额外元数据；本次不进行市场上架，不将其写成每个本地 Skill 的共同前提。

### 可直接交给宿主的安装指令

> 请把附件中的 HEX 标准 Skill 包安装为当前宿主可调用的技能。先读取包内 `hex/SKILL.md`，保留完整相对目录、公开方法以及八类模型提示词资料，不只摘抄简介。按你当前真实提供的自建/导入技能入口创建并启用它；如果你支持从仓库安装，使用仓库实际的 HEX Skill 入口及公开包构建方式，不猜另一份目录。安装后核对你能否读取引用资源、显示原生问答选择栏、执行 Python，以及有哪些实际可用的搜索/看图/生图工具。缺少执行能力时按 HEX 指导方式工作并如实说明；不要执行 Codex 专属安装命令、假定内置后端是 OpenAI，或声称未经验证的工具已经接通 Harness。若当前界面要求用户完成上传/启用，请指出准确的这一步。

## 网页端：导入后使用，不启动本地安装流程

网页使用继续采用本包的核心与相对资源，不新建一套网页版 HEX。不要因用户在浏览器里要求“安装 HEX”就 clone 源码、执行 bootstrap、Windows 脚本或 Codex CLI；先确认当前网页提供的正式导入/启用入口。

| 网页使用场景 | 官方已确认的入口或限制 | 本次仍未验证 |
| --- | --- | --- |
| Claude 网页 | 开启自身 Code execution and file creation，组织策略允许时，在 Customize → Skills → + Create skill → Upload a skill 上传 ZIP（内含 `hex/`），再启用 HEX | 实际账户上传、扫描、资源保留、工具调用与跨会话持久化；未操作用户开关 |
| 豆包工作网页 | 官方介绍确认通用自建技能、对话安装与本地文件导入，任务可在云端运行 | 未取得网页专属导入菜单、精确 ZIP 校验与沙箱 OS 证据；不把 PC 的通用入口、普通豆包聊天或飞书其它入口视为已验证的同一界面 |
| WorkBuddy 网页 | 官方通用技能页提供“添加技能 → 上传技能 → 本地技能包” | 网页具体账户是否提供相同入口、是否保留全部引用资源及其执行能力尚未实测；发现 UI 差异先报告 |
| ChatGPT 网页（与 Codex 本地 Skill 分开） | 当前 OpenAI 文档将网页 Chat/Work 的 Skills 放在插件分发路径；standalone Skills 对应桌面/Codex CLI/IDE | 本标准 ZIP 未验证为网页插件，不声称已直接导入；没有用户授权不另建插件或替换为 CLI |

导入回执、启用状态、核心/已选模型资料实际可读、执行工具可用、生成工具适配、真实持久化分别核对。宿主必须启用执行服务才允许 Skills 时，服务不可用就是导入/启用阻塞，不能把 HEX 指导模式称为已安装；仅在仍可合法读取资料时指导新任务。工具或持久化缺失时清楚披露，不把临时容器文件当作已跨会话保存，也不绕过已有 run、父图、预算、锁或在途请求。

## 第一轮决策界面

必要问询遵守根 Skill：第一条含问题的回复就使用当前宿主实际可用的选择组件，一轮最多两个关键问题，给贴题选项和自由输入。已有模型/委托直接沿用，不为展示 UI 再问一次。

- WorkBuddy 官方已记录问答选择器支持自定义输入；运行时仍要读实际组件名称与 schema，不能抄写 ChatGPT 工具调用。
- ChatGPT 提供 `ask_user_input` Skill 或相应工具时先读其规则；可用 `request_user_input` 时遵守其实际适用范围与选项上限。
- 豆包工作及其它宿主先检查真实能力。无适用组件才简短说明并提供编号选项和自由填写；文字、JSON 或伪按钮不算已经显示原生组件。

这是问询的呈现适配，不新建代理层，不把普通选项硬塞进视觉 `DirectionDraft`。

## 执行能力分支

| 当前任务及能力 | 应采取的方式 | 可如实声称的结果 |
| --- | --- | --- |
| 新任务；文件/Python 可用，运行检查通过，目标及执行入口受当前适配支持 | 用同一随包 runner 执行现有 Harness，按当前 `host_context` 与任务合同推进 | 只报告真实保存/冻结/派发/回执/检查状态；有包不等于工具已可用 |
| 新任务；无 Python、无命令工具、沙箱临时不可用，或已选原生生图入口未适配 Harness | 通过可用资源工具读取 `HEX_CORE_METHOD.md` 与本题资料，采用 HEX 指导方式；必要时用已选、可用且已理解参数的宿主原生工具 | 可交付研究、判断、对应模型/通用服务提示词和真实原生图片；明确未经过 Harness 验证，不伪造 run、回执、持久化或跨会话学习 |
| 已有 run、锁、待定请求、预算或拒绝状态 | 恢复原合同；缺状态或能力就保留具体阻塞 | 不以指导方式重开任务绕过约束，不补造旧回执，不把失败改称已完成 |

标准包的执行检查为 `python scripts/hex.py --skill-doctor`，从实际 Skill 根运行。它校验并展开自身随包运行资源；Python 要求 3.11+，依赖以 `runtime/requirements.txt` 的锁定版本为准，使用当前宿主实际可用环境。依赖缺失时说明缺口；有授权和安装能力再按普通 Python 环境安装，无此能力仍可读取方法资料。检查通过只证明此包和本地运行依赖一致，不证明外部账号、原生选择栏、搜索、OCR 或生图路线可用。

生图入口需另核对当前工具实际名称、输入字段、参考图能力与所选目标。现有 `generation_spec` 对未注册外部工具的通用提示词/参考路径构造不证明平台 schema 匹配；不把 `host_not_introspected` 说成已完成接入。不要为 WorkBuddy 或豆包名称新增假适配器。

## macOS 的已实现与未验证边界

先确定执行工具在哪里运行，再选路径。豆包工作官方说明任务可在云端持续运行；Mac 客户端不证明其执行容器是 Darwin。浏览器会话、远端沙箱和本机目录也不能视为同一文件系统。

| 共用部件 | 已实现/静态证据 | 验证边界 |
| --- | --- | --- |
| 标准 Skill 与模型资料 | 同一个 ZIP 使用相对路径、UTF-8 文本；无需执行脚本即可读取核心和八类模型资料 | Windows 已做 ZIP/摘要/链接检查；Mac 客户端导入、隐藏目录保留及资源工具读取未测 |
| 随包 runner | `scripts/hex.py` 使用启动它的解释器和本包资源，无 Codex CLI 或 PowerShell 前置 | Windows 现有 Python 检查；Darwin 真机、只读宿主安装位置及真实沙箱待测。只读目录不能展开 runtime 时保留失败与可读资料，不换包或绕写 |
| macOS/Linux 源码入口 | `sh scripts/bootstrap.sh` 使用 `.venv/bin/python`；脚本为 LF、无 BOM。`Hex.ps1` 仅 Windows 便捷入口 | bootstrap 含依赖安装，只在授权及宿主下载规范允许时执行；本次未在 Mac 运行或安装依赖，不跨系统复制 `.venv` |
| 主 Python 路由 | `windows_runtime` 是历史模块名；只有 `sys.platform == 'win32'` 才添加 Windows 本地检索默认，新任务仍使用共用 `full-flow-v1`，已有任务参数保持 | Windows 内模拟 Darwin 路由不能证明 macOS 客户端、进程锁、文件系统或生成工具正常 |
| 可选本地扩展 | 原生检索解释器已有 Darwin `bin/python` 分支；Paddle OCR 与离线 research 启动仍有 Windows `Scripts/python.exe` 路径 | 不宣称这些可选扩展已在 Mac 可用；需要对应能力时明确阻塞，保留原验收合同，不自动换 OCR 后端或跳过文字检查 |

四宿主使用相同核心与能力分支，均不要求为了 Mac 转用 Codex CLI。WorkBuddy 官方有 macOS 12+ 的 Intel/ARM64 客户端说明；OpenAI 有 macOS 桌面入口；Claude 有 Mac/Cowork 说明。豆包工作已有 PC/云端及自建技能资料，但本次未核验其 Mac 导入格式和沙箱系统。平台有 Mac 客户端不证明 HEX 已完成安装验收。开发库常规 CI 覆盖 Windows/Linux；macOS 的安装包检查在公开 Beta Actions 中单独记录。云端运行检查不等于四宿主 Mac 客户端已经完成导入验收。

## 模型资料的使用

八类缓存位于 `knowledge/model-prompts/`：Image、Nano、Midjourney、FLUX.2 Klein、FLUX.2 pro/max、Qwen Image、Seedream、Krea。读索引和已选卡，不每题重复搜索一般写法；未覆盖的版本、用户要最新或接口冲突才补查原始官方资料。

只有确认 OpenAI 图像服务的入口才用 `target=image`；当前宿主的名字不是这个确认。用户选择“就在这里出图”，且其内置后端没有公开时，记录已选服务与未知后端，使用 `unknown-service.md` 的通用表达指导，不逼用户猜 API ID、不把它注册为另一个模型 target。实际工具能力与最终效果都按本次证据报告。

## 官方依据

以下为 2026-10-13 实际获取的官方正文；页面、入口与组织策略可能更新。这些安装指引不是本次客户端安装回执。

- [Agent Skills 格式](https://agentskills.io/specification)；[文件读取及工具注入实现](https://agentskills.io/client-implementation/adding-skills-support)。
- [WorkBuddy Skill 开放文档](https://open.workbuddy.cn/docs/skill)：SKILL.md、资源目录与 ZIP，以及独立的市场发布元数据要求。
- [WorkBuddy 技能市场](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)：本地上传入口。
- [WorkBuddy 更新日志](https://www.workbuddy.cn/docs/workbuddy/Changelog)：4.24.0 问答选择器自定义输入。
- [WorkBuddy 项目](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)：代码开发场景的项目技能范围。
- [豆包工作官方介绍](https://www.feishu.cn/content/article/7677519271848610746)：自建技能、对话安装、本地技能文件、脚本/参考文档/模板，以及云端任务。
- [WorkBuddy Mac 安装说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Installation-Mac-Guide)：系统与芯片支持，不等于 HEX 实测。
- [OpenAI Skills 文档](https://developers.openai.com/codex/skills)（本次跳转至 [Build skills](https://learn.chatgpt.com/docs/build-skills)）：Skill 目录、可选 `openai.yaml`、桌面/CLI/IDE 等入口；[桌面端](https://developers.openai.com/codex/app)。
- [Claude 自建 Skill](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills)：ZIP 单文件夹布局、200 字符 description；[启用与上传](https://support.claude.com/en/articles/12512180-use-skills-in-claude)：执行能力前提及组织策略；[Claude Code Skills](https://code.claude.com/docs/en/skills)：独立的 `.claude/skills` 入口。
