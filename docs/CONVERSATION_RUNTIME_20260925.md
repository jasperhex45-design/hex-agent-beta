# 对话状态、恢复与真实反馈

HEX 的状态来自当前任务、调用账本、候选文件、检查记录和用户消息。对外说明应与这些记录一致；文件存在、模型返回或局部认可都不能单独代表全任务完成。

## 查看与恢复

```text
python -m hex_agent status --run outputs/task --format human
python -m hex_agent status --run outputs/task --format json
python -m hex_agent workflow status --run outputs/task
python -m hex_agent harness plan --run outputs/task
```

`human` 适合阅读；JSON 中的 `presentation` 与 `active_question` 供宿主查看结构化状态。`presentation.json` 是导出时的摘要，恢复时读取实时状态和真实下一步。旧需求、冻结参数与反馈不因更新说明而改写。

| 状态或记录 | 能说明什么 |
|---|---|
| `pending` | 本地已准备；需核对实际提交状态 |
| `outcome_unknown` | 结果未知；查询原请求，不重新提交 |
| `failed` | 保留该次实际失败和已有证据 |
| `completed` | 调用已返回；仍需核对候选文件和适用检查 |
| 视觉检查 | 记录当前图片的实际观察及证据等级 |
| 用户接受 | 只在真实用户明确接受后记录对应候选 |

宿主检查、API 返回的检查意见和用户意见分开保存。检查输入或候选不匹配时，不沿用旧结论。`validate_message_claims` 核对结构化证据关系，不能判断任意自然语言陈述是否真实，也不能约束绕过 HEX 的外部调用。

## 区分四种反馈

| `record-feedback --decision` | 适用情形 |
|---|---|
| `positive_feedback` | 用户认可某些表现，仍可继续修改 |
| `relative_preference` | 用户认为当前两张图中的一张更接近目标 |
| `accepted_by_user` | 用户明确接受当前选中候选 |
| `rejected_by_user` | 用户明确否决当前选中候选 |

“更喜欢 A”不自动成为最终接受；单次反馈也不自动成为永久偏好。宿主定位当前候选和请求编号，用户无需填写内部 ID。局部认可与相对偏好的来源格式不同，按 `record-feedback --help` 和当前任务指导填写。

## 登记明确接受的示例

先保存真实用户消息与当前候选绑定。下例仅展示结构，所有占位符、摘要和引文须来自实际任务；没有该用户消息时不能执行登记：

```json
{
  "format": "liu.feedback-sources.v1",
  "sources": {
    "format": "liu.input-sources.v1",
    "messages": [{
      "id": "feedback-1",
      "actor": "user",
      "text": "我接受当前选中的这一张。",
      "origin": "host_transcription",
      "locator": "真实用户消息的位置"
    }]
  },
  "decisions": [{
    "source_id": "feedback-1",
    "quote": "我接受当前选中的这一张。",
    "decision": "accepted_by_user",
    "scope": "selected_candidate",
    "candidate_sha256": "当前选中图的真实SHA256",
    "contract_digest": "当前检查合同摘要",
    "packet_digest": "当前需求摘要"
  }]
}
```

```text
python -m hex_agent record-feedback --run outputs/task --decision accepted_by_user --user-quote "我接受当前选中的这一张。" --source "真实用户消息的位置" --sources inputs/feedback-source.json --source-id feedback-1
```

反馈来源只允许追加。旧原话不能改写或在新否决后重放为当前接受。新版消息快照的证据等级 `host_recorded_not_platform_authenticated` 表示已核对本地绑定，仍不证明平台身份或宿主的语义判断正确。无消息快照的兼容入口等级较低，不能覆盖已有可核来源。

`machine_status`、候选接受和全任务完成分别判断。`task_acceptance=not_recorded` 不可解释为用户已接受全任务；当前完整流程的下一步以 `workflow status` 为准。新增请求与恢复继续遵守实际授权、预算和停止记录，用户接受本身不授予更多生成额度。
