---
name: hex
description: HEX 视觉创作的统一入口。用户说“用HEX”“使用 Hex”“让HEX做”“按HEX全流程”“继续用HEX”，请求汽车、人像、平面设计、分镜静帧、改图或模型提示词时使用；持续推进本题的需求、设计、编译、生成及交付检查。沿已绑定任务接续自然语言反馈。用户明确不用HEX，或只讨论HEX定位、架构、修改仓库代码时，不创建视觉任务。
---

# HEX：从需求推进到可核查交付

## 1. 定位当前工程

使用安装副本时，先读取本 Skill 同目录的 `installation.json`，使用其中的 `python_executable`、`entry_script`、`runtime_root` 与 `repository_root`。这些路径由安装器绑定，不从用户名称或旧目录猜测。

使用仓库或源码包副本时，从本文件所在的 `hex/` 目录向上三级，得到包含 `.agents/` 的根。若该根同时包含 `scripts/hex_entry.py` 和 `hex_agent/__main__.py`，它就是工程；否则核对其 `hex-agent/` 子目录中的这两个文件。解释器优先使用确定工程的 `.venv`，按实际操作系统选择 `Scripts/python.exe` 或 `bin/python`。已绑定解释器或脚本不存在时报告具体缺口，不偷偷使用另一份 HEX。

用选定解释器执行 `entry_script`，所有内部输入与 run 路径使用绝对路径，因此不依赖当前工作目录。读取 `runtime_root` 下的 `AGENTS.md`、`CODEX_START.md`；不要把本 Skill 的相对路径当作工程路径。

## 2. 接住用户任务

用户表达目标，宿主整理文件。已有会话绑定或明确旧任务时先检查实际状态并恢复，沿用当前原话、参考、图像、反馈、许可和预算；不重新做一份相似任务。只有“用 HEX”而上下文没有目标时，先确认入口状态，再问一句要完成什么。

按实际交付选择内部类型：仅提示词用 `prompt_only`；需要新图用 `image`；修改真实父图用 `local_edit`。不要求用户填写这些字段，不从口头激活推断无限额度或上传许可。

新任务默认在 `runtime_root/inputs/` 保存最小 brief 与真实消息来源，在 `runtime_root/outputs/` 创建新的任务目录；使用外部任务目录前核对安装配置的 `allowed_run_roots`，不因绑定失败换路径规避约束。先执行统一准备入口，读取该 run 生成的任务指令后才形成 Need、设计与模型正文。不能先自由写完整提示词，再补跑 HEX 并宣称它指导了创作。

简短目标足以开始理解，不能替代对用户意图的判断。新任务先做 `direction_review`：想表达什么仍模糊时，在找图和设计前用 Copilot 问清最影响方向的感受、用途或偏好；不先给一套固定风格菜单，也不让用户选焦距。已有清楚目标或明确委托则继续。用户未指定的场景、车型、人物关系与光线保留为提案，不在最小 brief 中提前锁成要求。

公共入口为新任务自动启用 `full-flow-v1`，宿主显式传本题 `--deliverable-kind`；知识／领域执行策略另行保留。单车外观使用 `car prepare`，其它任务使用 `prepare`。以下 `PY`、`ENTRY`、`RUN` 等由宿主替换为上文核对的实际路径与类型，不是用户表单：

```text
PY ENTRY prepare --brief BRIEF --sources SOURCES --out RUN --deliverable-kind KIND
PY ENTRY workflow status --run RUN
PY ENTRY workflow drive --run RUN
```

`drive` 每次最多推进 8 个本地可验证步骤，可用 `--max-steps` 显式设置。它在需要宿主理解、设计、编译或实际看图时返回下一步，不能替宿主执行这些判断，也不调用生成模型。完成所需动作后继续 `workflow drive` / `status`，不要把交回宿主的正常阶段当成要问用户的阻塞。

## 3. 持续推进适用阶段

按当前 `CODEX_START.md` 与实际任务的下一步推进：需求与参考观察 → 必要检索 → 设计 → 模型编译 → 派发执行（仅成图/编辑任务）→ 对应交付检查 → 有界修复或完成。用户没有要求每题运行全部离线研究工具；明确不适用的阶段按真实类型处理，不能借此跳过必需步骤。

单车外观摄影只加载 `runtime_root/.agents/skills/cl-hex-car-photography/SKILL.md` 的身份和设计方法；其它静态视觉任务加载 `runtime_root/.agents/skills/liu-aesthetic-agent/SKILL.md`。旧名 Skill 是兼容方法入口，共用当前流程，不创建第二套合同或另一个 run。

新任务在 Need 中明确 `visual_research` 路线。开放目标、明确要求研究或已否定的方向走 `study`：宿主按目标主动搜索、实际看作品并作审美取舍；不确定就扩展不同来源，找不到合适案例或偏好仍未解决时用 Copilot 与用户共创。风格范围不由固定菜单或候选数量限定，最终选择一个有依据的方案。`open_creation`、检索关闭和零采用知识都不证明研究完成。`specified_direction` / `bounded_edit` 须有真实用户来源；明确禁止研究可据原话选 `user_waived`，如实记为跳过。操作按 `CODEX_START.md`；核心返回下一步，宿主实际调用可用搜索和看图工具，不因状态推进而声称已浏览作品。

宿主确实看过会话图片但拿不到附件字节时，按 `CODEX_START.md` 使用 `add-host-reference` 登记 `host_attested` 的独立研究证据，保存真实用户来源、会话图片位置和观察限制，再 `observe`。这不是像素校验，不能代替身份、编辑或生成输入；未看到的链接和文字检索不适用，宿主描述不能冒充用户原话。

宿主实际读取当前任务指导、观察必要图片、作设计取舍并填写真实检查；保留不确定项。HEX 的结构检查不能替代这些工作。不要用已列出 Skill、已安装工具、命令返回 0、提示词文件存在或收到图片代替完整执行。

如果宿主 hook 已提供本会话 ID 与工作目录，准备成功后确认它绑定了真实 run；未绑定时通过当前 `codex bind` 接续，`--config` 使用安装配置中的 `config_path` 或仓库根的 `.codex/hex-integration.json`。只使用 hook 提供的真实 ID，不让用户填内部编号，不伪造会话绑定。未安装 hooks 时如实说明监督状态，仍执行任务与完成检查。

## 4. 完成、阻塞与恢复

每个阶段后读取实际状态与下一步；还有适用且可执行的阶段就继续。授权范围内的内部记录、检查和恢复不重复询问。只有真实缺素材、指定工具不可用、必要权限不足、预算耗尽或需要用户决定的关键取舍才暂停，并说明已经完成什么、卡在哪里、需要补充什么。

旧 run 的 `packet.json` 缺少 `workflow_policy` 时，按原 `status` / `resume` / `finalize` 恢复既有合同，不调用 `workflow complete`，不宣称它已经通过新完整流程验收；不要为套用新默认而重建任务或改写原政策。

`pending/unknown` 查询原请求，不重发。用户停止则保存真实来源并登记停止；普通暂停不伪造成用户停止。显式续改绑定真实父图、反馈和原累计预算，保留已正确部分；没有新证据或新控制的重复失败停止该路线。

根据 `CODEX_START.md` 完成 requirements、check 和真实 review 等本题所需记录后，执行 `PY ENTRY workflow complete --run RUN`。仅当返回的统一完成状态通过，才说“HEX 完整流程已完成”。可以先展示真实预览，但继续核查尚未完成的步骤。最终给实际产物、执行状态、检查结论和仍有的限制；未看图不声称视觉合格，机器记录不代替用户接受。文件、数量和结构合格与画面表达是否成立分开评价；基础检查满分不称审美满分，比较结论不得只挑有利分项。

本 Skill 约束宿主如何使用 HEX；宿主集成是否安装、是否支持监督，以真实检查为准。不能声称仅凭 Skill 文案就已拦截 HEX 之外的工具调用或自然语言回复。
