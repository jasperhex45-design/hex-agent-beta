# Midjourney 提示词指南

核验日期：2026-10-12。状态：experimental，仅核验官方资料，未验证本卡成图收益。
按具体版本与入口使用：普通生成、Image Prompt、Style Reference、Edit Model。
目标版本和操作模式影响写法；本卡不证明帐号权限或生图工具已经接通。

## 1. 版本与入口

| 范围 | 已核验行为 | 不外推的边界 |
| --- | --- | --- |
| V8.2 | 核验日官方默认版本 | 不代替用户本题的选择 |
| V8.1 / V8.2 Edit Model | 接受编辑指令、最多4张参考，可用Editor局部编辑 | 不使用V7的Omni入口代替 |
| V7 | Omni Reference用于人物或物体引用 | 不默认给V8拼接旧oref |
| V6 | 旧Character Reference体系 | 不套用到V8 |
| Image Prompt | 引导内容与整体视觉，正文描述最终结果 | 不按精确改图接口解释 |
| Style Reference | 借用色彩、媒介、纹理、光线等风格 | 不承担身份或产品比例锁定 |

来源：[M2]、[M3]、[M4]、[M5]。本题已明确版本就复用；未明确且影响能力时问实际界面型号。
用户只要干净正文或明确要求不带双横线参数时，输出正文，必要设置另列。
这是输出格式选择，不是“Midjourney不支持参数”的模型结论。

## 2. 官方建议

- 普通生成用简洁、具体的最终画面描述，避免冗长命令和无关列表。来源：[M1]。
- 主体、媒介、环境、光线、颜色、情绪、构图按需选择；关键数量写明确。来源：[M1]。
- Image Prompt正文包含目标画面，补足参考图没有显示的必要细节。来源：[M3]。
- Style Reference配简洁正文；必要时加入与参考相容的风格词，避免互相冲突。来源：[M4]。
- V8 Edit Model可直接写修改指令，也可描述最终结果；具体写法由当前操作决定。来源：[M5]。

## 3. HEX 整理的中文骨架

以下是信息顺序，不是官方固定语法；编译为英语正文时，硬限制与准确文案不得漂移。

### 普通生成

```text
[主体、数量及动作]，[媒介/画面类型]，[环境]，[构图与景别]，[关键光线与色彩]，[少量决定性风格特征]。
```

### Image Prompt 或 Style Reference

```text
在对应入口添加已核验图片。正文描述[最终主体、动作、环境与构图]。Image Prompt补足内容；Style Reference只在必要时补[与参考一致的风格特征]。
```

### V8.1 / V8.2 Edit Model

```text
将[图中的明确对象/区域]改为[目标状态]，保留[本题关键身份、结构、位置与构图]。[多图时说明各参考提供什么]。
```

## 4. HEX 可复制示例

只展示语法差别，不固化创作风格；用户原有主体与风格优先。

普通生成：

```text
A white ceramic cup on a pale stone table, still-life photograph, close side view, soft window light from the left, a thin highlight along the rim, open space in the upper right.
```

Edit Model（需在正确入口附上原图）：

```text
Change the blue cup to warm white glazed ceramic. Keep its shape, handle position, the table, and the framing. Preserve the light direction from the left window.
```

## 5. 避免混用

- 普通Image Prompt描述目标画面，Edit Model才能按其能力使用修改指令。
- V8不默认继承V7的Omni/oref或V6的Character/cref。
- sref负责风格，不承诺复制人物、车辆结构或logo。
- 参数和正文分开管理；是否给参数取决于用户格式要求与实际平台。
- Style Weight（sw）和Stylize（s）是不同控制，不混写名称或套固定偏好数值。
- 版本参数兼容性分开核验；不把旧Quality或多提示词语法默认加给V8。
- 不因为追求短提示删掉身份、数量、文案及修改范围等硬限制。
- “保持”表达目标，不能作为像素保真或成图质量的保证。

## 6. 缓存与来源

同版本同入口直接读取本卡；新版本、界面变化、冲突或未覆盖功能才定向补查。
来源是官方建议，骨架和示例由HEX整理；未据社区声量宣称其为最佳结构。

- [M1] [Prompt Basics](https://docs.midjourney.com/hc/en-us/articles/32023408776205-Prompt-Basics)。checked：2026-10-12。
- [M2] [Version](https://docs.midjourney.com/hc/en-us/articles/32199405667853-Version)。checked：2026-10-12。
- [M3] [Image Prompts](https://docs.midjourney.com/hc/en-us/articles/32040250122381-Image-Prompts)。checked：2026-10-12。
- [M4] [Style Reference](https://docs.midjourney.com/hc/en-us/articles/32180011136653-Style-Reference)。checked：2026-10-12。
- [M5] [Edit Model](https://docs.midjourney.com/hc/en-us/articles/48495453462797-Edit-Model)。checked：2026-10-12。
