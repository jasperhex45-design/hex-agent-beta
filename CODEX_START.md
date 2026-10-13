# HEX 操作入口

本文件保存 Harness 的操作顺序，文件名不限制对话模型或宿主。标准 Skill 包与新会话的无执行能力分支先读 [跨宿主入口](docs/HOST_PORTABILITY.md) 和 [共用方法](docs/HEX_CORE_METHOD.md)；指导方式不执行或伪造下方命令，已有任务仍沿原合同恢复。首次启动读第 1–2 节，此后按当前 `next_action` 和 `host_context` 读取所需章节；安装、特殊素材和历史接口不作为每题前置。共同边界见 [AGENTS.md](AGENTS.md)，已加载的内容无需重读。

下文 `PY`、`ENTRY`、`RUN` 等由宿主替换为主 Skill 已核验的解释器、随包 `scripts/hex.py` 或源码 `scripts/hex_entry.py` 和真实绝对路径；工程目录中也可用项目解释器执行 `-m hex_agent`。用户不填写内部占位符。本地 macOS/Linux 源码环境使用 `.venv/bin/python`，Windows 使用 `.venv/Scripts/python.exe`；标准包使用宿主现有解释器，不要求 `.venv` 或 `Hex.ps1`。客户端系统不等于远端沙箱系统，路径依据实际执行位置。

没有命令工具、Python 或沙箱临时不可用时，不先运行这些命令；新任务用当前可读资料指导，已有任务保留原合同与具体阻塞。执行服务在 Python 启动前拒绝请求时，停止无依据换 shell/重试，不自动归因 HEX 或 Mac，也不要求转用 Codex CLI。恢复须有服务恢复或新的定位证据；只有第 6 节的可选 Codex 集成需要相关宿主支持。

用户要求整理 Pinterest 收藏、本地素材或艺术风格库时，直接按 [私人艺术风格库](docs/PRIVATE_REFERENCE_LIBRARY.md) 使用 `PY ENTRY references`；此类维护不建创作 run。已发布集合由新任务沿既有参考入口选择，实际分析和创作继续使用当前宿主。

## 1. 接住需求并准备任务

先完成主 HEX Skill 的需求分析与问询：模糊时用 Copilot 只问当前关键问题；用户没有指定目标生成模型且没有明确委托选型时，必须先问用哪个模型，不能按工具可用性代选。保留已有回答与明确模型选择，不重复问；内容目标清楚、模型已确定后继续找素材、判断参考、设计和对应模型编译。原话、真实回答、已给素材与宿主提案分别保存，未定场景和光线不先锁入 brief。仅讨论产品或代码不建立视觉 run。

需要问时，第一条含问题的回复就使用宿主实际可用的交互选择组件；先读已提供组件的规则，一轮最多两个关键问题，每题 2–4 个贴题选项及自由填写，遵守更小的宿主上限。不先纯文字再等用户要求选择栏；确无适用组件才说明原因并用文字选项降级。普通按钮标签沿自然答复来源记录，不为呈现 UI 造 `DirectionDraft`；本地问答记录不证明按钮已显示。已有答案或明确委托继续使用。

日常任务是 WORK；仅明确研发实验使用 TEST。已启用私人学习时，建任务前按 [学习方法](.agents/skills/hex-work-learning/SKILL.md) 核对本次档案、项目、风格与选定模型，不继承上个客户的选择。匹配经验随 `user_feedback_context.personal_learning` 进入参考研究、方案及编译；当前明确要求始终优先。未启用时正常使用共用能力，不制造风格档案。

已有会话绑定或明确旧任务时直接按第 5 节恢复。新任务在工程 `inputs/` 保存最小 brief、sources 与必要素材，run 使用 `outputs/`；其它目录先核对 `allowed_run_roots`。按真实需求填写目标模型与交付类型，不无故加载未使用的目标：

| 需求与当前能力 | `--deliverable-kind` |
| --- | --- |
| 只需目标模型提示词，或开始时已确认无可用生图工具 | `prompt_only`；说明图片需在对应模型中执行 |
| 需要新图且有可用生成路线 | `image` |
| 修改真实父图且有可用编辑路线 | `local_edit`；绑定父图与修改范围 |

运行 HEX 的宿主/对话模型、目标生图服务与实际工具分别确认。只有已确认的 OpenAI 图像入口使用 `target=image`；用户选择未知后端的宿主内置服务不等于选了 GPT Image。新任务遇到未适配入口按跨宿主文档采用指导方式，不为了运行 Harness 借用模型 target；未知外部工具的通用参数并不证明 schema 已匹配。

目标模型须来自本任务原话、已有上下文或明确的模型选择委托；只委托构图、风格或找参考不能扩大成选型委托。缺少这些依据时先问用户，解决后显式填写 `targets` 再 `prepare`；新任务省略目标将被拦截。已有任务中的未决选型问题用 `question_records` 的 `technical`、`affected_decision=brief.targets` 保存，待答期间不设计、编译或派发，不能自行撤回。工具适配器存在不证明账户或工具可用，也不证明用户选择它。已建立的成图任务缺图时保留真实未完成状态，不把它静默改成提示词任务后宣称成图完成。

单车外观摄影先读 [汽车方法](.agents/skills/cl-hex-car-photography/SKILL.md)，将下方 `prepare` 换为 `car prepare`；其它题材使用通用入口。公共入口自动启用 `full-flow-v1`，知识/领域 execution profile 另行保留。

新任务保存流程合同版本 2。宿主根据用户交付目标在 brief 中写 `delivery_level`：`exploratory`（探索首稿）、`formal`（正式成品）、`high_risk`（身份保持、严格修改等）；未指定默认 formal。它不改变图片/提示词交付类型，不要求用户填写内部字段。级别进入冻结合同，不能在失败后降级；有身份参考或非 create 修改范围的任务不接受 exploratory。已有版本 1 合同照原样恢复，不在读取时迁移。

```text
PY ENTRY prepare --brief BRIEF --sources SOURCES --out RUN --deliverable-kind KIND
PY ENTRY workflow drive --run RUN
```

## 2. 只执行当前阶段

新生产任务的 `workflow drive` / `harness plan --run RUN` 返回 `host_context`。执行其中 `instruction`，按 `input_files` 读取本阶段所需输入；`method_context` 说明适用方法来源，不代表要全文加载整个方法库。`stage`、`packet_digest` 与 `instruction_sha256` 绑定当前上下文，任务更新后重新取得，不能复用旧阶段内容。

先依据指导和真实来源形成 Need，保存 `direction_review` 与必要 `question_records`，再用 `refresh` 更新。默认离线 Need 是草稿，不是已理解证明；不得先完成设计或提示词，再补办 HEX 指导。回答绑定原问题与真实来源，用户明确委托的技术取舍由宿主承担。

`drive` 默认最多推进 8 个本地可验证步骤，可用 `--max-steps` 调整；它不调用生成模型，不替宿主理解、设计、编译或看图。宿主完成返回的动作后继续 `drive`，正常交回宿主不需要用户再次授权。

旧返回没有 `host_context` 时，按真实下一步读取原 `HOST_TASK.md`、`creation-instructions.md`、`production-context.json`、`packet.json` 中的必要部分，沿原合同继续；不为了取得新字段改写旧包。

## 3. 参考、方案与目标模型编译

Need 中按真实来源选择 `visual_research`：

| 路线 | 适用条件 |
| --- | --- |
| `study` | 开放目标、明确研究请求或已拒绝的方向；主动找并实际看作品 |
| `specified_direction` / `bounded_edit` | 用户已给定视觉方向或有界局部修改，并有真实来源 |
| `user_waived` | 用户明确禁止研究，绑定原话与 `user_prohibited_study`，记为 `skipped_by_user` |

`study` 读取 [设计研究](.agents/skills/liu-aesthetic-agent/references/design-study.md)。不确定就扩展有意义的来源；仍无适用案例或用户偏好未解时回 Copilot。没有参考、检索关闭、零采用知识或宿主自行拟定场景都不等于免研究。研究按决定是否有依据收束，不固定风格菜单、方向数或参考总数，也不要求为每个方向生图。

开放题已做必要尝试，但来源持续不可访问或继续研究无决策增量时，新合同允许在 `Need.visual_research.limited_exit` 中保留退出原因、真实尝试与未解决问题，再沿原授权自主首稿。状态应为 `limited_with_disclosure`，对用户说明研究不足，不填造 acceptable 或宣称研究完成；明确必做研究及关键身份、父图、文字、范围未决不适用。

实际观察后 `observe`，通过 `choices.reference_uses` 关联发现、采用/舍弃依据及可见检查。`selection_state` 为 `exploring` 时继续研究，`uncertain/no_suitable_reference` 时解决缺口，`acceptable` 后形成有效 Plan；全部舍弃不能冒充已有依据。人物、平面、分镜静帧和通用改图只读 [通用方法](.agents/skills/liu-aesthetic-agent/SKILL.md)，汽车只读汽车方法。

```text
PY ENTRY refresh --run RUN --need NEED --sources SOURCES
PY ENTRY prepare-compilation --run RUN --plan PLAN --target TARGET
PY ENTRY preflight --run RUN --solution SOLUTION
```

对本题已选目标分别 `prepare-compilation`，真实执行时传已核验的 `--tool TOOL`。省略 `--target` 只会沿用任务中唯一已选模型，多目标必须显式指定；不能调用只有档案、未被本任务选择的模型。按返回的 `model_compile` 阶段上下文与当前 `compilation-input.json` 写完整正文/adaptation。每个目标若有 `prompt_guide`，先读其中已冻结的本地指南正文、来源与适用版本；指南来自 `knowledge/model-prompts/`，按已选目标加载并绑定编译摘要，正常任务无需重复搜索同一写法。未收录、版本更新或能力冲突时再核对官方资料；已有任务的指南不会因本地文件改写而静默变化。不同模型改变表达与可用参数，保留同一需求、必要关系、允许变化、数量及精确文字；不以统一长模板或程序隐式补词代替适配。汽车在 preflight 后按专属方法预览并冻结输入。

专业作品分析、旧图反馈与视觉作品参考的证据要求不同；本题需要时读 [反馈与创作依据](docs/FEEDBACK_CREATION_CONTEXT.md)。只看到会话图片却拿不到字节、参考换批或来源字段不明时读 [来源接口](docs/SOURCE_AND_REVIEW_INPUTS.md)，不在普通任务加载全部字段。

## 4. 交付提示词或真实图片

`prompt_only` 完成需求、设计、编译与必要冻结检查后直接 `workflow complete`，交付各目标模型提示词，不执行像素、OCR 或图片评审，不声称生成效果已验证。

成图/编辑沿本题已有许可执行 `authorize-generation → dispatch → 实际工具或执行器 → record-result`，原样使用冻结正文、素材顺序、模型与实参，保存真实回执及图片。`dispatch` 同样只沿用唯一已选目标，多模型须显式指定 `--target`，未选目标在预留调用前被拦截。小云雀/ComfyUI 使用现有 execute/query 入口；宿主工具使用 dispatch 的真实参数。只查询 `pending/unknown` 原请求，不重复提交。

收到图可以先展示真实预览，再按当前阶段推进：

```text
PY ENTRY workflow requirements --run RUN
PY ENTRY workflow requirements --run RUN --file REQUIREMENTS
PY ENTRY workflow check --run RUN
PY ENTRY workflow prepare-review --run RUN
PY ENTRY workflow record-review --run RUN --report REVIEW
PY ENTRY workflow complete --run RUN
```

对明确应出现的画内文字登记 OCR 的 `required` 与冻结文案。用户指定文字按默认用户来源；宿主自拟片名等用 `text.source_kind=design_proposal` 绑定真实 assistant 提案和非 hard 设计检查，不冒充用户要求。无文字要求才按用户来源记 `not_applicable`，禁字要求不能反转为生成文字。默认保留 Paddle；本机实际使用 Tesseract 时显式传 `workflow check --ocr-backend tesseract --ocr-language eng`，不可用或低置信度均保留失败/未知。多个候选时为每个应交付候选传真实 `--request-id`，不默认取最后一张。读取 `prepare-review` 的当前模板后实际打开候选及必要参考，填写观察再 `record-review`；模板本身不是已看图证据。新合同的探索首稿可记录 `review_context=author_context`，明确是自检；正式/高风险交付和用户准确文字要求须 `isolated`。隔离能力不可用时保留具体阻塞，不填假字段、不降低交付等级。

失败或不确定时也展示当前原图和返回的 `feedback_card`；`workflow status` 保留有效 `candidate_receipts/feedback_cards`。可见差距与原因未知分开，反馈页存在不证明用户已经看见。看图反馈与必要关系的方法见 [原图反馈](docs/ESSENTIAL_CREATION_FEEDBACK.md)。

明确问题沿原父图、许可与预算有限修复；没有新证据或控制、连续同类失败时停止该路线，说明缺口。

**单次可控返修当前支持范围。** v2 原生生产任务只接通“将过大的 PNG 首稿同比例缩小至原请求冻结的精确尺寸”，不裁切、不放大，不自动改文字、身份、主体或审美。用户原始消息须另有 `repair_grant={scope:downscale_to_requested_size,max_repairs:1}`，它与 `generation_grant` 分开；剩余生图额度不能代替返修许可。原图检查仅尺寸失败、已有视觉评审无分歧时，`workflow drive` 可沿 `needs_bounded_repair` 执行 `workflow repair`，先在原 run 占用一次剩余槽位再变换。原图与原 not_met 保留，`bounded-repair/repaired.png` 是独立本地派生产物，不追加模型 trial；随后重新检查和评审最终图，再 complete。反馈与导出绑定最终图，生成次数仍为 1、本地返修为 1，二者分开呈现。失败或中断不重试、不退回占用。关联版本、API和多模型比较暂不进入此本地返修器，沿原合同处理。只有统一完成状态通过才称已完成对应级别的交付。交付实际产物、简短设计理由、真实检查和未验证项；预期收益、真实观察和用户接受分别说明。带偏差接受的完成回执必须保留 `quality_standards_met=false`，探索自检保留 `independent_review=false`，两者都不能称全部质量验证通过。

## 5. 续改、反馈与旧任务恢复

先查原 `status` / `harness plan`，按真实 `resume` 下一步接续。已核验许可只是内部登记或父子预算关联缺失时，用已有恢复入口修复，不重复问用户。冻结后的续改用 `refresh --out` 建关联版本，保留真实父图、反馈、新 `change_scope/preserve` 与累计预算；必要时传 `--edit-reference` / `--parent-request-id`。变更参考、Need 或模型后重新观察与编译。

真实接受、拒绝、局部认可和相对喜欢用原反馈入口保存；CLI 自动登记适用来源，不把相对更好当最终接受，也不把跨题反馈自动套入本题。已绑定档案的任务返回 `registration_kind=private_learning_event`，按学习方法处理新增事件并回读结果，不等 TEST 或额外生图。`learning_deferred` 表示原反馈保留、提炼尚未成功；检查原 run 的 `learning-pending.json`，在原身份/模式可用后用 `learning ... capture-run --run RUN` 重试来源。状态为 pending 时不得宣称已经记住或离线还在学习。

**带偏差收下（新合同）。** 仅一个真实已完成候选、无在途请求，文件/必要 OCR、硬要求、整体目标和基准比较均成立，仅作者非硬性局部拍法存在明确 not_met 时，先用简短人话逐项说明实际 candidate_evidence；不要求向用户照读内部 question 或编号。保存真实 assistant 披露消息和用户明确接受的回复（`in_reply_to` 指向披露消息），沿原 feedback-sources 格式绑定图片、packet、contract 后调用 `record-feedback --decision accepted_by_user --sources FILE --source-id ID --user-quote 原话 --source 来源 --run RUN`。来源绑定不认证宿主实际展示，必须真实问答，不把“好看”猜成知情接受。随后 `workflow complete` 以 `accepted_with_deviations` 结束；原检查/图片/预算不改写，不自动返修，不形成永久偏好。硬要求失败、整体目标未达或看不清不能由此豁免。唯一已完成候选在正式导出前也可用 `record-feedback --decision rejected_by_user --sources FILE --source-id ID` 保存明确否决；仍核验原图、请求与来源，未完成的检查保留，不再要求用户重复评价。多候选不能猜选，拒绝不授予重做预算。专业反馈召回、来源失效和诊断边界按需读上述反馈文档。

完整流程与 execution profile 独立；显式 legacy 执行策略仍完成原 native Eval/比较，默认生产策略不重复两套评审。没有 `workflow_policy` 的旧包沿 `status/resume/finalize` 恢复，不补新默认或宣称已通过新验收。明确停止用原 `stop-generation`；普通暂停保留当前阶段与真实接续资料，只有已显式选择并实际绑定 Codex 集成时使用 `codex pause`。其它宿主不运行该命令；无持久化能力就说明交接尚未保存，不宣称自动恢复。锁残留按来源接口的 `recover-lock` 核对，不删账本。API 长调用期间可登记停止，后续付费文本/图像调用在入口与实际交给 provider 前重新检查；已交给 provider 的请求不宣称被撤销，保留真实返回或 unknown 并查询原请求。最后本地检查与 provider 调用之间仍有极短并发窗口，不能把本地停止称为服务端原子取消。Comfy 节点预检发现新增必填参数时保持原 pending 预留、不上传或提交，不填入默认参数篡改冻结输入。

## 6. 只在 Codex 安装或绑定需要时读取

本节只适用于明确使用 Codex 集成的宿主，其它宿主按跨宿主入口导入完整 Skill，不运行这些专属命令。先核对真实环境、解释器与宿主支持。仓库已有 `.codex/hooks.json` 和 `hex_repo_bridge.py` 时，只有同步到本机、依赖存在且宿主实际信任并执行 hooks 后才可能自动绑定；独立包或其它目录可使用安装入口：

```text
PY ENTRY codex install --dry-run
PY ENTRY codex install
PY ENTRY codex bind --config CONFIG --session-id SESSION --workspace WORKSPACE --run RUN
PY ENTRY codex status --config CONFIG --session-id SESSION --workspace WORKSPACE
```

安装先核对计划改动并保留现有配置；用户级主 Skill 才传真实 `--skills-dir`，自定义配置目录用 `--codex-dir`，额外 run 范围用 `--run-root`。已安装 Skill 从 `installation.json` 读取 `config_path`；否则用真实仓库根 `.codex/hex-integration.json`。绑定只用 hook 提供的真实会话 ID 与工作目录，不让用户填内部编号。安装成功不证明已信任或自动监督；宿主不支持时说明实际状态并继续可执行步骤。

仅对应场景再读： [Windows 检索与 OCR](docs/HEX_WINDOWS_WORKFLOW_20261007.md)、[Qwen 单父图](docs/HEX_QWEN_SINGLE_PARENT_EDIT_20261007.md)、[Qwen 蒙版编辑](docs/HEX_QWEN_MASKED_EDIT_20261007.md)。历史安装记录和另一台机器的配置不证明当前能力；指定宿主型号或独立会话的分工以真实交接为准。
