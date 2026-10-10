# 可选：艺术空间结构转译

这是复用 HEX 现有任务链的实验能力，用于把有来源的绘画空间方法转成当前设计中的遮挡、间隔、层次或视线关系。普通创作从 [CODEX_START.md](CODEX_START.md) 开始，无需开启本功能。

## 什么时候采用

当前任务确实需要空间组织，且检索案例能支持某个具体设计决定时，再使用这一入口。阅读 [.agents/skills/liu-art-structure/SKILL.md](.agents/skills/liu-art-structure/SKILL.md)，检索少量相关方法；不适用时保持原流程。

运行材料包括：

- `knowledge/design/cultural_grammars.json`：可执行的结构方法。
- `knowledge/art_structure/cases.json`：保留来源的文字研究案例。
- `knowledge/design/cultural_grammar_method_map.json`：案例与方法的显式映射。

这些文字案例不是已经观察过的图片。需要视觉参考时，宿主另行实际查看并记录；不自动上传作品，也不把艺术家名字当作可执行的空间动作。保留资料出处及 [第三方说明](THIRD_PARTY_NOTICES.md)。

## 在现有任务中使用

先沿原入口完成真实 Need、必要参考观察与当前任务准备，例如 `outputs/task`。检查目录结构与映射无需模型：

```text
python -m liu_agent.art_structure validate
python -m liu_agent.art_structure prepare --run outputs/task --query "空间层次 遮挡 间隔 视线连接"
```

宿主读取 `outputs/task/art-structure/host-context.json`、对应 `art-plan.schema.json` 和当前任务材料，形成 `art-plan.json` 与原 `Compilation` 格式的 `compilation.json`。采用的方法须把案例 `avoid_when` 原样写入 `avoid_when_considered`，并用 `applicability_evidence` 解释本题为何适用。一次任务只沿一条明确方法链进入设计；不采用时，`spatial_plan` 和原生 `spatial_grammar` 为空。

```text
python -m liu_agent.art_structure adopt --run outputs/task --art-plan outputs/task/art-plan.json --compilation outputs/task/compilation.json
```

`adopt` 保存结构快照、绑定编译动作并调用原 preflight。后续继续当前任务适用的 dispatch、执行、检查与交付流程，不绕过授权、冻结参数或累计预算。

任务已有 `result.json` 后，可检查选中结构；需要查阅自己已有任务的反馈时，再使用 `recall`：

```text
python -m liu_agent.art_structure inspect --run outputs/task
python -m liu_agent.art_structure recall --runs-root outputs --domain poster
```

宿主核对回查结果与本题的关系后再决定是否采用。单次反馈是对应案例的证据，不自动变为全局偏好。

## 能力与验收边界

`validate` 只验证案例结构与方法映射，`prepare` 只准备上下文；两者均不证明画面更好。记录实际提示词、成图、用户反馈与主要失败后，才能判断本题是否受益。

模块保留独立 API 研究入口，但不作为内测默认路线。它需要有效模型配置、明确的云端执行许可与调用预算；`--image-tests 0` 仍可能调用文本 API，不能理解为完全离线或免费。常规宿主流程不需要配置该研究入口。
