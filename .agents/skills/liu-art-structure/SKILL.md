---
name: liu-art-structure
description: 在Hex Agent中按需使用中西绘画的空间组织方法，将有来源的研究案例转成当前任务的可见动作、实际提示词与冻结检查。不替代原生图流程，不是新Agent或审美评分器。
---

# 艺术结构转译

先读原项目 `CODEX_START.md` 与本增量 `ART_STRUCTURE_START.md`。用户给需求和素材；由同一个Agent维护文件，不要求用户填摄影/艺术理论表格。

1. 保留本次原话、锁定项、编辑范围、参考用途和预算。明确允许的创作空间；局部修图和复刻优先保护原投影与身份。
2. 原流程完成需求解析与参考观察后，调用 `python -m liu_agent.art_structure prepare --run <task>`。可用结构问题作`--query`，不只查画家/流派名字。最多返回4个案例，允许零命中。
3. 读取本任务art-plan.schema.json。案例属于馆方文字资料与本项目转译假设；本版没有看过或打包其作品图，不冒充实际视觉分析，不自动加入references。需要原图时通过真实可用工具另找、实际看，并确认用途和权限。
4. 比较适用方法；最多采用2个。解释目标、实体、关系、虚空间用途与预计观看路径，并写成ArtPlan。每个decision完整复制案例的`avoid_when`到`avoid_when_considered`，再用`applicability_evidence`写本任务的可见适用依据；不能只写“适用”。`spatial_plan=null`时写不采用理由，同时不得保留原生`spatial_grammar`。非摄影空间保留原CameraChoice兼容字段，但使用非摄影说明与`focal_equivalent_mm=null`。
5. 每个方法绑定原Plan中现有choice_id、check_id，写双语可见动作与失败条件，不虚构mechanism_ids。区分观察、解释和假设，不将“画得像名作”作为目标。只借组织方法，不默认复制题材、人物、道具、字体或符号。
6. 生成原Compilation，执行`art_structure adopt`，由代码投影到原Plan、注入实际动作并调用原preflight。不绕过原参考/权限/硬约束检查。生成仍走dispatch和实际参数登记。
7. 使用真实候选与当前参考做Eval，检查已冻结关系；模型意见只能是建议。修订只调整实现动作，不能换方法、改表达目标或降低标准。保留被选中候选当时的空间快照。
8. 记录用户原话反馈。下一任务用`art_structure recall`读取少量同域记录，只采用与本次问题有关的经验。不得把单次拒绝直接提升为长期画风规则。

顺序：本次明确要求 > 本次采用方向 > 有范围的用户反馈 > 艺术方法建议。
无可用图像执行器时可以交未测方案，不能伪造生成、审美通过或原创性检测。
