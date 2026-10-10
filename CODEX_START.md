# HEX 操作入口

本文件只保存一套操作顺序。首次启动读第 1–2 节，此后按当前 `next_action` 和 `host_context` 读取所需章节；安装、特殊素材和历史接口不作为每题前置。共同边界见 [AGENTS.md](AGENTS.md)，已加载的内容无需重读。

下文 `PY`、`ENTRY`、`RUN` 等由宿主替换为主 Skill 已核验的项目解释器、`scripts/hex_entry.py` 和真实绝对路径；工程目录中也可用项目解释器执行 `-m hex_agent`。用户不填写内部占位符。

## 1. 接住需求并准备任务

先完成主 HEX Skill 的需求判断：模糊时用 Copilot 只问当前关键问题；目标清楚或用户确实委托后继续必要研究。原话、真实回答、已给素材与宿主提案分别保存，未定场景和光线不先锁入 brief。仅讨论产品或代码不建立视觉 run。

已有会话绑定或明确旧任务时直接按第 5 节恢复。新任务在工程 `inputs/` 保存最小 brief、sources 与必要素材，run 使用 `outputs/`；其它目录先核对 `allowed_run_roots`。按真实需求填写目标模型与交付类型，不无故加载未使用的目标：

| 需求与当前能力 | `--deliverable-kind` |
| --- | --- |
| 只需目标模型提示词，或开始时已确认无可用生图工具 | `prompt_only`；说明图片需在对应模型中执行 |
| 需要新图且有可用生成路线 | `image` |
| 修改真实父图且有可用编辑路线 | `local_edit`；绑定父图与修改范围 |

用户已指定模型则保留；未指定时按实际可用能力选择。工具适配器存在不证明账户或工具可用。已建立的成图任务缺图时保留真实未完成状态，不把它静默改成提示词任务后宣称成图完成。

单车外观摄影先读 [汽车方法](.agents/skills/cl-hex-car-photography/SKILL.md)，将下方 `prepare` 换为 `car prepare`；其它题材使用通用入口。公共入口自动启用 `full-flow-v1`，知识/领域 execution profile 另行保留。

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

实际观察后 `observe`，通过 `choices.reference_uses` 关联发现、采用/舍弃依据及可见检查。`selection_state` 为 `exploring` 时继续研究，`uncertain/no_suitable_reference` 时解决缺口，`acceptable` 后形成有效 Plan；全部舍弃不能冒充已有依据。人物、平面、分镜静帧和通用改图只读 [通用方法](.agents/skills/liu-aesthetic-agent/SKILL.md)，汽车只读汽车方法。

```text
PY ENTRY refresh --run RUN --need NEED --sources SOURCES
PY ENTRY prepare-compilation --run RUN --plan PLAN --target TARGET
PY ENTRY preflight --run RUN --solution SOLUTION
```

对本题实际目标分别 `prepare-compilation`，真实执行时传已核验的 `--tool TOOL`。按返回的 `model_compile` 阶段上下文与当前 `compilation-input.json` 写完整正文/adaptation。不同模型改变表达与可用参数，保留同一需求、必要关系、允许变化、数量及精确文字；不以统一长模板或程序隐式补词代替适配。汽车在 preflight 后按专属方法预览并冻结输入。

专业作品分析、旧图反馈与视觉作品参考的证据要求不同；本题需要时读 [反馈与创作依据](docs/FEEDBACK_CREATION_CONTEXT.md)。只看到会话图片却拿不到字节、参考换批或来源字段不明时读 [来源接口](docs/SOURCE_AND_REVIEW_INPUTS.md)，不在普通任务加载全部字段。

## 4. 交付提示词或真实图片

`prompt_only` 完成需求、设计、编译与必要冻结检查后直接 `workflow complete`，交付各目标模型提示词，不执行像素、OCR 或图片评审，不声称生成效果已验证。

成图/编辑沿本题已有许可执行 `authorize-generation → dispatch → 实际工具或执行器 → record-result`，原样使用冻结正文、素材顺序、模型与实参，保存真实回执及图片。小云雀/ComfyUI 使用现有 execute/query 入口；宿主工具使用 dispatch 的真实参数。只查询 `pending/unknown` 原请求，不重复提交。

收到图可以先展示真实预览，再按当前阶段推进：

```text
PY ENTRY workflow requirements --run RUN
PY ENTRY workflow requirements --run RUN --file REQUIREMENTS
PY ENTRY workflow check --run RUN
PY ENTRY workflow prepare-review --run RUN
PY ENTRY workflow record-review --run RUN --report REVIEW
PY ENTRY workflow complete --run RUN
```

只有明确画内文字要求才登记 OCR 的 `required` 与原精确文案；否则按真实来源记 `not_applicable`，禁字要求不能反转为生成文字。多个候选时为每个应交付候选传真实 `--request-id`，不默认取最后一张。读取 `prepare-review` 的当前模板后实际打开候选及必要参考，填写观察再 `record-review`；模板本身不是已看图证据。

失败或不确定时也展示当前原图和返回的 `feedback_card`；`workflow status` 保留有效 `candidate_receipts/feedback_cards`。可见差距与原因未知分开，反馈页存在不证明用户已经看见。看图反馈与必要关系的方法见 [原图反馈](docs/ESSENTIAL_CREATION_FEEDBACK.md)。

明确问题沿原父图、许可与预算有限修复；没有新证据或控制、连续同类失败时停止该路线，说明缺口。只有统一完成状态通过才称完整流程已完成。交付实际产物、简短设计理由、真实检查和未验证项；预期收益、真实观察和用户接受分别说明。

## 5. 续改、反馈与旧任务恢复

先查原 `status` / `harness plan`，按真实 `resume` 下一步接续。已核验许可只是内部登记或父子预算关联缺失时，用已有恢复入口修复，不重复问用户。冻结后的续改用 `refresh --out` 建关联版本，保留真实父图、反馈、新 `change_scope/preserve` 与累计预算；必要时传 `--edit-reference` / `--parent-request-id`。变更参考、Need 或模型后重新观察与编译。

真实接受、拒绝、局部认可和相对喜欢用原反馈入口保存；CLI 自动登记适用来源，不把相对更好当最终接受，也不把跨题反馈自动套入本题。专业反馈召回、来源失效和诊断边界按需读上述反馈文档。

完整流程与 execution profile 独立；显式 legacy 执行策略仍完成原 native Eval/比较，默认生产策略不重复两套评审。没有 `workflow_policy` 的旧包沿 `status/resume/finalize` 恢复，不补新默认或宣称已通过新验收。明确停止用原 `stop-generation`，普通暂停用当前 `codex pause`；锁残留按来源接口的 `recover-lock` 核对，不删账本。

## 6. 只在安装或绑定需要时读取

先核对真实环境、解释器与宿主支持。仓库已有 `.codex/hooks.json` 和 `hex_repo_bridge.py` 时，只有同步到本机、依赖存在且宿主实际信任并执行 hooks 后才可能自动绑定；独立包或其它目录可使用安装入口：

```text
PY ENTRY codex install --dry-run
PY ENTRY codex install
PY ENTRY codex bind --config CONFIG --session-id SESSION --workspace WORKSPACE --run RUN
PY ENTRY codex status --config CONFIG --session-id SESSION --workspace WORKSPACE
```

安装先核对计划改动并保留现有配置；用户级主 Skill 才传真实 `--skills-dir`，自定义配置目录用 `--codex-dir`，额外 run 范围用 `--run-root`。已安装 Skill 从 `installation.json` 读取 `config_path`；否则用真实仓库根 `.codex/hex-integration.json`。绑定只用 hook 提供的真实会话 ID 与工作目录，不让用户填内部编号。安装成功不证明已信任或自动监督；宿主不支持时说明实际状态并继续可执行步骤。

仅对应场景再读： [Windows 检索与 OCR](docs/HEX_WINDOWS_WORKFLOW_20261007.md)、[Qwen 单父图](docs/HEX_QWEN_SINGLE_PARENT_EDIT_20261007.md)、[Qwen 蒙版编辑](docs/HEX_QWEN_MASKED_EDIT_20261007.md)。历史安装记录和另一台机器的配置不证明当前能力；指定宿主型号或独立会话的分工以真实交接为准。
