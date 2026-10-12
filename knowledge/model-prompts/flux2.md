# FLUX.2 [pro/max/dev] 提示词指南

核验日期：2026-10-12。状态：experimental，官方资料核验；未执行目标模型生成。
这是提示词知识卡，不是生图适配器；没有可用且核验过的工具时交付prompt_only。
Klein使用独立的 `flux-klein` 卡，本卡不把多个型号合并成一种执行能力。

## 1. 适用版本与模式

| 型号 | 本卡来源范围 | 边界 |
| --- | --- | --- |
| FLUX.2 [pro] / [max] | 官方专用提示指南 | 方法可缓存，具体API字段仍以实际入口为准 |
| FLUX.2 [dev] | 官方仓库确认生成与单/多参考编辑 | 不据pro/max指南保证所有扩展语法或参数同效 |
| FLUX.2 [klein] | 使用独立Klein卡 | 不继承本卡的参考上限或推理合同 |
| FLUX.1 Kontext | 非本卡范围 | 不当作FLUX.2别名 |
| FLUX 3 | 当前通用文档已覆盖的新代际 | 新控制不回填为FLUX.2能力 |

来源：[F1]、[F2]、[F3]。型号不明确时问Pro、Max、dev或实际模型名；已明确时复用。
新建、单图编辑、多参考组合分别选用对应写法；能力支持不等于HEX当前已经接通。

## 2. 官方建议

- pro/max指南建议按主体、动作、风格、环境组织，最重要的内容放在前面。来源：[F1]。
- 可用自然语言；多元素映射复杂时可选择JSON，官方没有要求所有任务都JSON化。来源：[F1]。
- 文字用引号给出原文，说明位置、字形与层级；色码绑定具体对象。来源：[F1]。
- 多参考说明每张图供应的身份、物件、风格或场景职责。来源：[F1]。
- pro/max文档要求正向描述目标，不支持独立negative prompt。来源：[F1]。
- dev官方仓库提供可选提示词扩写流程；不是用户每次必须开启的步骤。来源：[F2]。

## 3. HEX 整理的中文骨架

下面是HEX按任务整理的顺序；不是官方强制字段或已验证的最佳模板。
编译为英语时保留同一方案和硬限制，不因换模型增加新情节。

### 新建

```text
[主要主体]正在[动作/姿态]，采用[媒介与视觉方式]，位于[场景]。[构图、光线、关键颜色和材质关系]。[必要文字原文、位置与层级]。
```

### 单图编辑

```text
将图1中的[明确对象/区域]由[原状态]改为[目标状态]，保留[主体结构、位置、构图和其他保护项]。让[修改部分]与原场景的[光影/材质]连贯。
```

### 多参考生成或编辑

```text
图1作为[底图/主体]；图2只提供[物件/材质/风格]；图3提供[另一职责]。将[允许借用的内容]放在[空间关系与位置]，保留[重要身份与结构]。
```

### 可选结构化写法——优先用于确实复杂的pro/max任务

```text
用scene记录目标场景，subjects记录具体对象与属性，lighting记录光线关系，text记录准确文案与位置；可保持JSON或转成自然语言，按目标入口处理。
```

这些示例字段是HEX的组织方法，不应当被当作API请求字段直接发送。

## 4. HEX 可复制示例

```text
A white ceramic cup on a pale stone table, still-life photography, a close side view. Soft light from a left window creates a continuous highlight on the glaze and a gentle shadow to the right.
```

编辑示例：

```text
Change the blue cup in image 1 to warm white glazed ceramic. Keep the cup shape, handle position, table, framing, and the light direction from the left window.
```

示例仅演示语法。实际主体、材质、构图与风格来自本题研究和选择。

## 5. 避免混用

- 不因当前官方导航转向FLUX 3，将bounding boxes等控制外推给FLUX.2。
- pro/max提示方法不等于dev、本地节点或第三方API支持同样参数。
- 参考数量、输出尺寸和扩写设置按实际型号与入口核验，不给全家族统一固定值。
- 内置扩写、工作流外部PE和HEX写作分别识别，避免重复扩写改变目标。
- 不把JSON当成质量保证；简单任务保持清楚的自然语言即可。
- 色码表达颜色意图，不保证画面所有像素数值等于色码。
- 不继承Stable Diffusion权重语法或Klein的固定采样合同。
- 强保留语句不等于像素保护；细节和结构是否保留要看真实结果。

## 6. 缓存与来源

目标型号与模式匹配时读取本卡，不每题重搜基础提示写法。
用户换型号、所需能力不在本卡、来源冲突或接口不支持时，针对该问题补查。
只有资料依据经过核验，没有真实生成结果就不声称成图质量或调用能力已验证。
社区方案如以后纳入，需另标来源、适用版本与证据，不能凭“高赞”代替验证。

- [F1] [BFL：FLUX.2 pro/max Prompting Guide](https://docs.bfl.ai/guides/prompting_guide_flux2)。checked：2026-10-12。
- [F2] [BFL官方FLUX.2仓库](https://github.com/black-forest-labs/flux2)。checked：2026-10-12。
- [F3] [BFL文档目录](https://docs.bfl.ai/llms.txt)。checked：2026-10-12。
