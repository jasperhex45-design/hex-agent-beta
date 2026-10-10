---
name: hex
description: HEX 视觉创作的统一入口。用户说“用HEX”“使用 Hex”“让HEX做”“按HEX全流程”“继续用HEX”，请求汽车、人像、平面设计、分镜静帧、改图或模型提示词时使用；先理解需求，模糊时用 Copilot 问清关键方向，再推进研究、设计、模型适配与交付。沿已绑定任务接续反馈。用户明确不用HEX，或只讨论HEX定位、架构、修改仓库代码时，不创建视觉任务。
---

# HEX：先理解需求，再推进创作

## 1. 先接住用户真正要做的事

先读用户原话、必要的已给素材和当前任务上下文，辨认想表达什么、用于哪里、必须保留什么、允许改变什么，以及需要图片还是某个模型的提示词。只整理影响本题的内容，不补成完整问卷；未指定的场景、光线和拍法保持待定。

**遵守本轮指定范围。** 用户只要求先找参考或形成方案、文案稍后给时，就在该阶段交付参考/方案与待续事项，保留原 run 的真实下一步和未完整交付状态；需要暂停时使用已有接续入口。不因“完整流程”擅自继续最终编译或生图，也不新增交付类型或伪造用户停止。

**目标仍模糊时，先用 Copilot 问最影响方向的一个问题，再开始外部找图和设计。** 例如用户只说“我要做一个图”，先问“这张图主要想表现什么内容？”不要先替用户决定题材或风格。必要时根据真实回答继续澄清，不重复已知信息，不让用户选择焦距、schema 或固定风格菜单。

**目标已经清楚，或用户确实委托自主决定，就继续本题必要的真实研究。** 委托允许宿主作取舍，不等于用户认可某个未提出的方案，也不免除研究。已有明确视觉方向、有界局部修改或明确禁止研究时，按唯一操作指南记录对应路线。

## 2. 定位真实工程

安装副本先读同目录 `installation.json`，使用其中 `python_executable`、`entry_script`、`runtime_root`、`repository_root`。仓库/源码包副本从本 Skill 目录向上三级找到包含 `.agents/` 的根：同时存在 `scripts/hex_entry.py` 和 `hex_agent/__main__.py` 时该根就是工程，否则核对 `hex-agent/` 子目录。解释器使用该工程 `.venv` 中的 `Scripts/python.exe` 或 `bin/python`。绑定路径缺失就报告缺口，不猜旧目录或偷偷换另一份 HEX。

命令使用真实解释器、入口脚本及绝对输入/run 路径；外部 run 目录须在安装配置 `allowed_run_roots` 内。本题尚未加载时读取 `runtime_root/AGENTS.md` 和 `runtime_root/CODEX_START.md` 的当前所需章节；已读内容不反复全文加载。

## 3. 沿唯一流程继续

按 `CODEX_START.md` 准备或恢复任务。澄清发生在建任务之前时，把原请求和真实回答一并保存；已有任务则绑定原 run。先取得本题指导并形成 Need，再研究、设计与模型编译，不能先自由写完提示词再补办流程。

新生产任务优先使用 `workflow drive` / `harness plan` 返回的当前阶段 `host_context`；编译使用 `prepare-compilation` 返回的上下文。执行其中 `instruction`，按 `input_files` 读取本阶段必要输入；`method_context` 说明方法来源，只加载当前需要的方法。返回缺少该对象时按操作指南兼容旧任务。

单车外观使用 `runtime_root/.agents/skills/cl-hex-car-photography/SKILL.md`；其它静态视觉题材使用 `runtime_root/.agents/skills/liu-aesthetic-agent/SKILL.md`。两者只补领域方法，共用同一任务和流程。需要开放研究时直接读 `runtime_root/.agents/skills/liu-aesthetic-agent/references/design-study.md`。

持续执行到本题真实交付或具体阻塞；不把正常交回宿主的判断步骤当作用户待办。交付目标模型提示词或实际图片，并说明真实检查与仍需用户判断的部分。详细执行、反馈、有限修改、恢复及 Codex 绑定均只使用 `CODEX_START.md` 的对应章节。
