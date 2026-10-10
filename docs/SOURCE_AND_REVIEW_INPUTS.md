# 宿主来源、澄清与复查接口

适用于 `source_contract_version=1` 的新任务。由宿主维护内部文件，用户只用自然语言表达需求、授权和反馈。旧任务按原版本读取与导出，不能给旧文件补默认字段后重算摘要。

仅在当前阶段需要来源字段、澄清版本或特殊图片接口时读取。当前完整流程按 [CODEX_START.md](../CODEX_START.md) 与真实任务状态执行；下述原生评审和预算接口保留各自合同范围，不作为所有新任务的默认阶段。

## 1. 原始消息与需求分开

`prepare/refresh/run --sources inputs/task-sources.json` 读取以下格式。示例仅说明形状，不是当前用户需求或授权：

```json
{
  "format": "liu.input-sources.v1",
  "messages": [{
    "id": "message-001",
    "actor": "user",
    "text": "逐字保存本次真实消息",
    "origin": "host_transcription",
    "locator": "可核对的会话及消息位置",
    "in_reply_to": "",
    "confirmed_proposal_ids": [],
    "generation_grant": null
  }]
}
```

没有平台原始回执时用 `host_transcription`，不能声称平台认证。Agent提案用独立的 `actor=assistant` 消息。旧ID内容不可更改；后续来源文件可只提供新增消息，或重复原内容后追加。摘要只检测文件一致性，不证明宿主转录真实或语义理解正确。

Need的 `must_keep/avoid` 每项由 `constraint_evidence` 绑定来源。`user_explicit` 的文字须直接对应用户引文；不同措辞的解释放意图/提案，不能用不相关原句证明新限制。明确采纳提案时，确认消息记录 `in_reply_to` 与 `confirmed_proposal_ids`，约束同时引用提案和用户确认。单纯回复、“你决定”和没有否决都不等于确认。`scope_rule` 只用于代码定义的修改范围保护。显式IntentClaim同样须有真实用户 `source_refs`；自主设计可标inferred直接推进。

## 2. 澄清与需求版本

按任务导出的 `need.schema.json` 写 `understanding`：表达意图沿用Need，另记录task_mode、purpose、audience、change_scope、preserve、reference_roles。未知用途/受众留空，不补成用户事实。局部编辑对应真实scope与allowed_operations，关系迁移必须有参考。

`question_records` 保留ID、问题、affected_decision、decision_type、status、reason与source_refs。一次只展示当前next_question。回答一个问题仅将该ID改为answered，并引用回答；其余仍保留。只有Agent应自主承担的表达/技术选择可有理由撤回；涉及身份、指定文字、范围或参考作用的冲突不能删除放行。

用户回答后通过 `refresh --run ... --need ... --sources ...` 更新。原范围/领域或既有限制确需改变时，用brief_change引用新消息；must_keep/avoid为null表示沿用，空列表表示有依据地撤回。刷新使受影响的观察与旧方案失效，必须重新观察、设计和编译。

已预留图片请求（包括失败/未知）后，使用 `refresh --run parent --out child --need ... --sources ...` 建关联需求版本。父目录、冻结标准与已用预算保留。模型变化可由 `brief_change.targets` 引用新增用户来源，加载新目标档案后重新编译。

原生版本须用 `transfer-native-budget --from-run parent --run child` 关联实际父任务。默认入口保持用户已否决、证据完整、有余量以及子任务已冻结且未生成的规则。若用户明确追加续作许可，可在父子分别登记同一新增许可消息（累计 `max_images=2`），再加 `--source-id 新许可ID`：只支持严格关联的一父一子，父任务一次已知成功或失败，旧支出仍计数，不要求补造图片否决。未知请求、第二个子任务、来源变化与累计耗尽均阻断。API关联版本继续生图尚未接通，会阻断而不重置额度。

## 3. 授权与编译

产品最多两张不是用户默认许可。宿主只在真实用户明确给出生图许可的消息上填写 `generation_grant: {"max_images": 1}` 或2；这是累计上限。“你决定”只交付设计判断，不增加生图、分析或上传许可。原生生成前执行 `authorize-generation --run ... --source-id ...`，再dispatch。API沿用显式run许可及持久化原始上限，两个入口共享停止与修订条件；不能切换入口增加额度。

用户在首图后明确追加一次修订许可时，把新消息交给 `authorize-generation --run ... --source-id 新消息ID --sources 新许可文件`，累计max_images为2。许可来源和历史同文件原子追加，不改冻结packet/合同，也不退回已用次数。关联预算需在实际owner登记累计许可，子任务自己的许可不能扩大owner额度。尚未生成但refresh改变packet时，旧许可不会自动沿用；只有新增明确许可可由宿主加 `--rebind` 重新绑定，已预留图片后禁止重新绑定。

新版本编译会把所有choice的action/cooperation保留到中文prompt；英文模型逐项填写 `choice_translations: [{"choice_id": "...", "text": "..."}]`。普通参考与审美卡均适用。覆盖检查不能证明翻译等义，宿主仍须检查关键关系、允许损失及失败条件是否一致。

## 4. 从真实调用复查

原生record-result之后运行prepare-eval，读取生成时快照、actual_prompt、有序输入图片及摘要构成的generation_evidence。API复查和恢复从原始预留与完成记录取同类证据。不得手填verified、用当前solution替代生成时方案、或以工具已返回证明表达成立。

调用记录缺失或错配时宿主入口先停下补证，不能登记新Eval。证据对应后先看图写事实，再定位需求、参考、设计、编译或执行问题；仍缺少区分原因的依据时用unknown。只有明确not_met可支撑revise：每个revision_target绑定失败criterion_id和具体change，同时列出preserve。uncertain、已接受/否决、已ready或额度用尽均不自动生成。两图比较同时写收益与损失；机器结论在用户反馈前保存，用户可以都不保留。

锁残留时使用 `recover-lock --run ...`，只有原进程已退出或PID已被其他进程复用的证据才会释放；状态不明保持阻断。恢复锁不删请求、不改授权、不退回已用预算。

## 5. 已看见会话图片但没有附件字节

仅当宿主实际看到真实用户会话中的图片、工具未暴露其字节时，使用 `add-host-reference --run RUN --file REFERENCE_JSON` 保存独立 `host_visible_references`。搜索标题、网页图或未看到的链接不适用；取得网页像素参考的方法见 [设计研究](../.agents/skills/liu-aesthetic-agent/references/design-study.md)。

按当前导出的 `HostVisibleReference.schema.json` 填写：

| 字段 | 要求 |
| --- | --- |
| `id`、`source_id` | 独立参考 ID 与已存在的真实用户消息 ID |
| `locator` | 图片所在真实会话位置 |
| `evidence_type`、`observation_method` | `host_attested`、`conversation_image` |
| `observation_limits` | 实际可见范围与观察限制 |
| `role`、`note`、`evaluation_only` | 研究职责与说明，`evaluation_only=true` |

随后通过 `observe` 保存真实 findings，并用 `choices.reference_uses` 关联当前决定和检查。宿主观察不能转录成用户原话；没有看见的部分保持未知。

`metadata_digest` 只绑定来源与宿主声明，不是像素 SHA 或平台认证。这类记录只能支持观察研究，不能作为身份图、编辑画布、生成输入或基准参考；需要这些职责必须取得真实图片字节。API 或隔离评审只收到元数据时，不得声称重新看过原图，对依赖原图的判断记不确定。

## 6. 更新或替换参考

先用 `python -m hex_agent.reference_roles list --run RUN` 核对当前像素与宿主参考。当前清单容量是同时绑定上限，允许保存真实淘汰原因后换入有价值的新参考，不为绕过容量新建任务。

```text
python -m hex_agent.reference_roles remove --run RUN --reference-id HOST_ID --expected-metadata-digest DIGEST --expected-packet-digest PACKET_DIGEST
```

移除像素参考使用其 `--expected-sha256`，宿主参考使用独立元数据摘要。输入均由宿主从当前记录读取，不让用户填写 ID 或猜摘要。新增、移除或替换会使受影响观察和方案失效，随后重新 `observe`、设计与编译。冻结或已试图任务通过关联版本继续，不原地改旧派发，也不把旧观察套给新图片。
