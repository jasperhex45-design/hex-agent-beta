# Hex Agent 启动与接续
完整开发仓库可带有 `.codex/hooks.json` 与仓库桥接；独立源码包使用本文件第 5 节的安装入口建立本机绑定。先核对实际目录、项目虚拟环境与宿主支持，再启用集成。云端提交或文件存在不代表本机已经信任或加载 hooks。

## 一个入口与当前规则

用户说“用 HEX”时，宿主按 [AGENTS.md](AGENTS.md) 和当前任务状态持续执行本题适用阶段。新任务使用完整流程要求；旧任务保留已保存合同、来源、预算和停止状态。宿主写内部记录，用户只需表达目标与反馈。

从工程目录可使用项目解释器执行 `python -m hex_agent`；从任意目录使用项目解释器与 `scripts/hex_entry.py` 的绝对路径。Windows 的 `Hex.ps1` 也进入同一公共入口。核心 Skill 旧名和内部 `liu_agent` 仅保留兼容，不从旧命令示例推断新默认。

安装副本的主 Skill 通过同目录 `installation.json` 解析真实解释器、入口脚本和工程路径。不要依赖当前工作目录、猜旧目录或把安装副本的相对路径当作工程位置。下文的 `RUN`、`BRIEF` 等代表宿主已准备的真实绝对路径，不让用户填写占位符。

## 1. 准备或恢复任务

先确认用户需要提示词、新图还是局部编辑，分别保存 `prompt_only`、`image`、`local_edit`。已给出目标就继续；仅有“用 HEX”且没有目标时，只问一个必要问题。不要因讨论产品或修改代码而创建视觉任务。

已有 run 先读取 `status` 与统一流程状态，再按 `harness plan` / `resume` 的真实结果继续。绑定了本会话时优先该任务；明确修改另一张图时核对真实父图。不要重新创建一份任务来回避旧预算或冻结输入。

新任务默认将输入放在工程的 `inputs/`、run 放在 `outputs/`，使用真实绝对路径；其它目录须已在安装配置的 `allowed_run_roots` 内。保存真实消息来源、必要素材及最小 brief 后准备，不把宿主想到的场景和光线先锁成用户要求。单车外观摄影走 `car prepare`，包括只给“汽车生活向”而未指定车型的任务：Brief 的 `vehicle_identity=open` 无须身份图，`preserve` 保留必要身份素材；其它题材走通用 `prepare`。新 Windows 任务保留原生语义知识策略，完整流程要求与知识策略分别保存。实际 Need 后才执行有限检索，允许零采用。

公共入口为新任务自动启用 `--workflow-profile full-flow-v1`；它不覆盖现有知识／领域执行策略。宿主显式填写交付类型。下方以成图为例，按本题选择通用或汽车准备命令的一条；仅提示词填 `prompt_only`，真实父图编辑填 `local_edit`。

如果本题显式使用 legacy 执行策略，原 native Eval／比较仍按原状态完成，再附加完整流程证据；默认生产策略不需要为形式再跑旧 native Eval。不要把 workflow profile 与 execution profile 混为一项。

```text
python -m hex_agent prepare --brief BRIEF --sources SOURCES --out RUN --deliverable-kind image
python -m hex_agent car prepare --brief BRIEF --sources SOURCES --out RUN --deliverable-kind image
python -m hex_agent workflow status --run RUN
python -m hex_agent workflow drive --run RUN
```

`workflow drive` 默认每次最多 8 个本地可验证步骤，可用 `--max-steps` 调整。它不调用生成模型，也不替宿主理解、设计、编译或看图；遇到这些步骤会返回当前状态和下一步。宿主完成实际动作后继续 `drive` / `status`，不把正常交回宿主的阶段当作需要用户填表或重新授权。

准备成功后读取当前 `HOST_TASK.md`、`creation-instructions.md`、`production-context.json`、`packet.json` 和原话。默认离线 Need 是草稿，由宿主按用户要求形成真实 Need 并 `refresh`；更新后再设计。不要提前写好完整画面或模型提示词再补办这些步骤。

新任务先填写 `direction_review` 判断意图是否清楚。模糊时进入 Copilot，先用一个开放问题问清最影响方向的感受、用途或用户偏好，再开始找图和设计；无需预制风格选项，焦距、曝光等技术取舍由宿主承担。把真实回答绑定原任务，已有清楚目标或明确委托就继续，不反复问已回答的要求。

## 2. 参考、设计与模型编译

汽车读取 [.agents/skills/cl-hex-car-photography/SKILL.md](.agents/skills/cl-hex-car-photography/SKILL.md)，其它静态视觉任务读取 [.agents/skills/liu-aesthetic-agent/SKILL.md](.agents/skills/liu-aesthetic-agent/SKILL.md)。只加载必要素材和实际目标模型资料，不读取整个工程历史或图库。

新任务先在 Need 的 `visual_research` 中记录路线：开放目标、明确要求研究或已拒绝方向选 `study`；用户已指定的视觉方向或有界局部修改可选 `specified_direction` / `bounded_edit` 并绑定真实来源。用户明确禁止研究时，用 `user_waived` 与 `user_prohibited_study` 绑定原话，状态为 `skipped_by_user`，不称已研究。宿主自己的初稿不是免研究依据。

`study` 按 [设计研究](.agents/skills/liu-aesthetic-agent/references/design-study.md) 主动找并实际看案例，以用户目标判断视觉关系和适用性。不确定就换来源、场景或表现方法继续研究；找不到合适案例或剩余取舍需要用户偏好时，再用 Copilot 说明实见差异与问题，请用户参与选择，不强行选一个交差。明确了情绪目标仍需把它落实为视觉依据，不能自动改成免研究。

新任务使用自适应研究规则，不再要求固定两个方向或固定参考总数；按实际需要用 `creative_directions` 保留提案和取舍，最终形成一个有效 Plan。一次检索或当前参考清单的容量是批次限制，不是风格范围的上限；可继续搜索，并让当前清单保留对决定有用的材料和真实观察。扩展研究不增加生成预算，也不要求分别生图。核心不自动调用浏览器，宿主按下一步实际调用可用搜索与看图工具；结构检查不证明审美判断正确。旧冻结任务沿原研究版本接续，不静默改写。

遇到图片访问失败，先检验当前宿主可用的浏览器和本地文件路径，打开作品详情并亲自看图；取得文件后再打开本地副本核对，以仅研究的职责接入。网页图不能登记成用户会话图片。具体获取、换批与判断方法见设计研究；参考的差异应改变设计决定，而非只增加相似图片的数量。

在 `ReferenceAnalysis` 的 `selection_state` / `selection_reason` 保存当前判断与依据：`exploring` 继续扩展真实参考，`uncertain` / `no_suitable_reference` 回到 Copilot 解决偏好或方向，`acceptable` 才继续最终设计。新研究版本要求可采用的实见参考与 choices / checks 关联；全部舍弃说明尚未找到合适依据，不能填成已选定。旧冻结的研究版本 1 保留原合同，新任务使用版本 2。

必要参考先亲自看图，使用当前参考 schema 写真实观察并 `observe`；身份、风格、照明、评价参考各自承担既定职责。研究参考可保持 `evaluation_only`，不强迫上传给生成模型。用 Plan 的 `choices.reference_uses` 关联实际观察、采用或舍弃依据与当前检查；仅搜到链接、读过文字或填了美感标签不构成看图证据。为最终方案的关键选择写可见作用和失败条件，不伪造引用或覆盖用户硬限制。`open_creation`、检索关闭和零采用知识都不能单独将研究标为不适用。

宿主实际看到了用户会话中的图片、但工具未暴露附件字节时，可用 `add-host-reference --run RUN --file REFERENCE_JSON` 保存独立 `host_visible_references`：填写 `id`、已存在的真实用户 `source_id`、图片所在会话 `locator`、`evidence_type=host_attested`、`observation_method=conversation_image`、观察限制 `observation_limits`、研究职责 `role/note` 与 `evaluation_only=true`。不能把宿主图像描述转录为用户原话；搜索标题、链接或未看到的图片不适用。随后用同一 `observe` 把实际观察写入 findings，再关联 choices/checks。示例与字段 schema 可用 `schemas` 导出为 `HostVisibleReference.schema.json`。

这种记录的 `metadata_digest` 只绑定来源与宿主声明，绝不是像素 SHA 或平台认证。它只能支持观察研究，不能作为身份图、编辑画布、生成输入或基准参考；需要这些职责仍须取得真实图片字节。新增记录使旧观察与方案失效，冻结后须建立关联版本。隔离评审只收到独立元数据；没有看到截图的评审者不得声称比较过参考原图，依赖原图的判断记不确定。API 没收到会话图片字节，不能替宿主重新观察。

宿主参考清单满时可先淘汰再换入，不需要新建任务来绕过容量。`list` 分别列出像素参考和宿主参考；宿主参考移除使用当前元数据摘要，像素参考仍使用 `--expected-sha256`。移除会使旧观察与方案失效，随后沿原入口补入参考并重新观察、编译，冻结与已试图任务仍拒绝原地改动。

```text
python -m hex_agent.reference_roles list --run RUN
python -m hex_agent.reference_roles remove --run RUN --reference-id HOST_ID --expected-metadata-digest DIGEST --expected-packet-digest PACKET_DIGEST
```

```text
python -m hex_agent refresh --run RUN --need NEED_JSON --sources SOURCES_JSON
python -m hex_agent prepare-compilation --run RUN --plan PLAN_JSON --target TARGET --tool TOOL
python -m hex_agent preflight --run RUN --solution SOLUTION_JSON
```

在 `prepare-compilation` 后读取实际生成的模型输入，再编写完整正文与 adaptation。中文、英文都保留原需求的关系、数量、主次和精确文案；用该工具真实支持的参数。汽车在预览核对后继续 `car freeze-input`，按本题实际模型传必要比例和分辨率。预览与 preflight 成功均不等于已经生成或完成交付。

## 3. 真实生成或编辑

仅提示词任务进入对应完成检查。成图和编辑任务继续原有权限与执行链：有效来源登记 → `dispatch` → 实际执行器或宿主工具 → `record-result`。已有本题授权沿用；内部登记、任务恢复、检查或跨会话交接不要求用户重复授权。新增范围、预算耗尽或必要许可确实缺失时才说明具体阻塞。

派发后使用冻结的完整正文、模型、素材顺序与参数，不改实参后宣称同一调用。小云雀与 ComfyUI 使用原 `execute-*` / `query-*`；宿主图片工具使用 dispatch 中的真实参数并立即记录回执与图片。`pending/unknown` 只查询原编号，失败如实计入原账本，不能换 run 重发。

局部编辑必须绑定真实父图、反馈、新 change_scope 与 preserve。用已有 `refresh --out` 建立关联版本；必要时使用原 `--edit-reference` 与 `--parent-request-id`。参考、Need 或模型变化后重新观察和编译；不修改已冻结派发。原图、保留项、父子关系及累计预算继续有效。

## 4. 检查与完成

收到图片后可以先给用户看真实预览，同时继续本题适用的检查。图片返回、导出提示词或普通 `finalize` 的产物不能单独代替统一完成状态。

成图与编辑任务核验真实文件和当前候选绑定；用户明确要求画内文字时核对原 checks 并调用 OCR，没有文字要求时不加载 OCR。宿主按隔离的评审输入实际看当前图片，再记录每项观察、失败或未知。只能填写亲自核对的证据，不把报告模板、命令成功或自评声明当成视觉事实。

成图／编辑任务先取得当前检查要求模板，宿主依据真实用户来源填写 `text`：明确画内文字才选 `required` 并逐字绑定原标准；没有该要求则填写 `not_applicable`、依据与真实 source_ids，items 保持空。禁字要求不能反转为要求生成文字。保存返回的 packet/contract 摘要与实际内容，再登记；这是内部记录，不是用户问卷。

```text
python -m hex_agent workflow requirements --run RUN
python -m hex_agent workflow requirements --run RUN --file REQUIREMENTS_JSON
python -m hex_agent workflow check --run RUN --request-id REQUEST_ID
python -m hex_agent workflow prepare-review --run RUN --request-id REQUEST_ID
python -m hex_agent workflow record-review --run RUN --request-id REQUEST_ID --report REVIEW_JSON
python -m hex_agent workflow complete --run RUN
```

候选唯一时可以省略 `--request-id`；双模型或多个候选时必须由实际结果定位，不自动选最后一张。`prepare-review` 只准备绑定当前候选的材料与 `review_record_template`；宿主必须实际打开候选和必要参考，按原标准填写该模板后才执行 `record-review`，不能让模板原样充当已看图记录。每个应交付候选分别完成适用检查。

`prompt_only` 完成需求、设计与模型编译检查后直接进入 `workflow complete`，不执行像素、OCR 或看图步骤。无需手填 completion 凭证：命令核对当前状态后调用原导出路径，并验证导出文件与完成证据一致。

检查存在问题时保存具体证据，按现有许可和可用控制处理；需要新图就沿关联编辑版本与原预算执行。不能为了得到通过报告改写检查标准、虚构用户接受，或无依据地连续抽卡。暂时没有可行控制、必要素材或额度时报告阻塞与下一步，保留未完成状态。

仅在统一完成门禁通过后说明“完整流程已完成”，交付实际产物、简短设计理由、检查结果和仍需用户判断的部分。使用导出的 `design-explanation.md` 解释收益时区分预期与真实观察；工程通过、工具成功、视觉结果和用户接受分别记录。对象、结构和文件检查属于基础可用性，不称审美满分；本题的情绪、主次和表达是否成立另据实际图像判断，比较报告保留整体结论与不利分项。

## 5. Codex 绑定与中断接续

宿主集成提供上下文提醒与任务绑定；Skill 文案本身不拦截宿主外的动作。先核对目标 Codex 环境和配置，再安装，保留现有配置；在云端验证源码不代表用户本机已经安装或信任 hooks。

```text
python scripts/hex_entry.py codex install --dry-run
python scripts/hex_entry.py codex install
```

需要安装用户级主 Skill 时，向安装命令传真实 `--skills-dir`；目标 Codex 配置不在默认工程位置时传 `--codex-dir`。run 位于默认工程范围外时，由安装时的 `--run-root` 明确绑定允许范围。路径由实际环境决定，不使用示例路径冒充成功安装。

准备成功后，若 hooks 尚未自动绑定，使用 hook 提供的真实会话 ID 与工作目录绑定真实 run。安装副本从 `installation.json` 读取 `config_path`；仓库默认配置为仓库根 `.codex/hex-integration.json`。

```text
python scripts/hex_entry.py codex bind --config CONFIG --session-id SESSION --workspace WORKSPACE --run RUN
python scripts/hex_entry.py codex status --config CONFIG --session-id SESSION --workspace WORKSPACE
```

宿主不支持或尚未信任 hooks 时如实说明监督状态，继续可以执行的 HEX 任务步骤，不假报自动监管。真实需暂停时使用当前 `codex pause`，不要伪造用户停止；恢复仍先读取原 run。用户已指定宿主型号或独立生图对话时保留分工，只有实际完成交接后才报告已切换。

## 按需参考

- [Windows 工作流](docs/HEX_WINDOWS_WORKFLOW_20261007.md)：原生检索、OCR 和离线研究的真实边界。
- [来源输入](docs/SOURCE_AND_REVIEW_INPUTS.md)：真实原话和来源 schema。
- [模型适配](docs/MODEL_ADAPTATION_AND_VALUE_20260926.md)：目标模型输入与正文绑定。
- [内测说明](BETA.md)：安装边界、实际结果反馈和发行范围。

研发按需读取具体源码；工程状态与历史报告留在开发库。普通创作不先跑 pytest，不因旧报告里的单次“停止生成”误停新任务。
