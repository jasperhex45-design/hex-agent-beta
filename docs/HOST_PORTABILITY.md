# HEX 跨宿主安装与能力边界

核验日期：2026-10-12。HEX 的核心方法与模型提示词资料随标准 Skill 包提供；运行 HEX 的对话模型、实际生图服务、宿主工具分别确认。宿主能导入 Skill 不等于已经接通 HEX Harness 或任何生图模型。本页的平台入口依据下方官方资料；尚未在 WorkBuddy 或豆包工作客户端做安装/生成实测。

## 标准包与共用入口

`HEX-Skill-v<version>.zip` 内只有一个 `hex/` Skill 根，包含根 `SKILL.md`、`scripts/hex.py`、`bundle.json`、`assets/` 运行资源和 `references/runtime/` 可读资料。八类模型卡、领域方法与运行文档从同一公开源码清单自动导出；无需 Python 也能读取它们。为防止宿主将内嵌方法再次安装为重名技能，可读镜像中的领域入口使用 `METHOD.md`；构建时只转换这些已知入口的引用，原运行包中的方法不变。`bundle.json` 的 `readable_resources` 保存逻辑来源、真实路径与转换前后摘要，读取时按 source→path 映射。`agents/openai.yaml` 是可选宿主展示信息，其存在不要求使用 OpenAI 对话模型或图像服务。

导入完整 `hex/`，保留其相对目录；不要只复制根 Skill 而漏掉方法/模型卡，也不要把 `assets/` 中的运行 ZIP 当成可直接阅读的参考文档。支持工具注入资源的宿主可通过其资源接口提供相同文件，不要求固定机器路径。

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
| 其它支持 Agent Skills 的宿主 | 使用当前宿主的本地 Skill 导入/技能目录，将完整 `hex/` 保留为一个 Skill，确认读取根 `SKILL.md` 及相对资源 | 文件读取与资源工具注入均可；先查当前宿主实际支持，不把某个平台的目录路径当通用安装命令 |
| Codex / 当前个人 Skill | 同样使用完整 Skill 与随包 runner；已有 Codex 工程绑定继续按其真实配置 | Codex hooks、自动会话绑定是可选集成，仅在该宿主实际支持并完成配置时生效 |

WorkBuddy 的 `.codebuddy/skills/` 是代码开发项目场景的项目技能路径，不是所有桌面/网页视觉任务的统一个人安装目录。普通海报、素材和提示词任务优先使用 UI 导入。WorkBuddy 开放平台的 `description_zh`、`description_en`、`version`、`author` 是市场发布规范中的额外元数据；本次不进行市场上架，不将其写成每个本地 Skill 的共同前提。

### 可直接交给宿主的安装指令

> 请把附件中的 HEX 标准 Skill 包安装为当前宿主可调用的技能。先读取包内 `hex/SKILL.md`，保留完整相对目录、公开方法以及八类模型提示词资料，不只摘抄简介。按你当前真实提供的自建/导入技能入口创建并启用它；如果你支持从仓库安装，使用仓库实际的 HEX Skill 入口及公开包构建方式，不猜另一份目录。安装后核对你能否读取引用资源、显示原生问答选择栏、执行 Python，以及有哪些实际可用的搜索/看图/生图工具。缺少执行能力时按 HEX 指导方式工作并如实说明；不要执行 Codex 专属安装命令、假定内置后端是 OpenAI，或声称未经验证的工具已经接通 Harness。若当前界面要求用户完成上传/启用，请指出准确的这一步。

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
| 新任务；没有执行能力，或已选原生生图入口未适配 Harness | 读取 `HEX_CORE_METHOD.md` 与本题资料，采用 HEX 指导方式；必要时用已选、可用且已理解参数的宿主原生工具 | 可交付研究、判断、对应模型/通用服务提示词和真实原生图片；明确未经过 Harness 验证 |
| 已有 run、锁、待定请求、预算或拒绝状态 | 恢复原合同；缺状态或能力就保留具体阻塞 | 不以指导方式重开任务绕过约束，不补造旧回执，不把失败改称已完成 |

标准包的执行检查为 `python scripts/hex.py --skill-doctor`，从实际 Skill 根运行。它校验并展开自身随包运行资源；Python 要求 3.11+，依赖以 `runtime/requirements.txt` 的锁定版本为准，使用当前宿主实际可用环境。依赖缺失时说明缺口；有授权和安装能力再按普通 Python 环境安装，无此能力仍可读取方法资料。检查通过只证明此包和本地运行依赖一致，不证明外部账号、原生选择栏、搜索、OCR 或生图路线可用。

生图入口需另核对当前工具实际名称、输入字段、参考图能力与所选目标。现有 `generation_spec` 对未注册外部工具的通用提示词/参考路径构造不证明平台 schema 匹配；不把 `host_not_introspected` 说成已完成接入。不要为 WorkBuddy 或豆包名称新增假适配器。

## 模型资料的使用

八类缓存位于 `knowledge/model-prompts/`：Image、Nano、Midjourney、FLUX.2 Klein、FLUX.2 pro/max、Qwen Image、Seedream、Krea。读索引和已选卡，不每题重复搜索一般写法；未覆盖的版本、用户要最新或接口冲突才补查原始官方资料。

只有确认 OpenAI 图像服务的入口才用 `target=image`；当前宿主的名字不是这个确认。用户选择“就在这里出图”，且其内置后端没有公开时，记录已选服务与未知后端，使用 `unknown-service.md` 的通用表达指导，不逼用户猜 API ID、不把它注册为另一个模型 target。实际工具能力与最终效果都按本次证据报告。

## 官方依据

- [Agent Skills 格式](https://agentskills.io/specification)；[文件读取及工具注入实现](https://agentskills.io/client-implementation/adding-skills-support)。
- [WorkBuddy Skill 开放文档](https://open.workbuddy.cn/docs/skill)：SKILL.md、资源目录与 ZIP，以及独立的市场发布元数据要求。
- [WorkBuddy 技能市场](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)：本地上传入口。
- [WorkBuddy 更新日志](https://www.workbuddy.cn/docs/workbuddy/Changelog)：4.24.0 问答选择器自定义输入。
- [WorkBuddy 项目](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)：代码开发场景的项目技能范围。
- [豆包工作官方介绍](https://www.feishu.cn/content/article/7677519271848610746)：自建技能、对话安装、本地技能文件，以及脚本/参考文档/模板。
