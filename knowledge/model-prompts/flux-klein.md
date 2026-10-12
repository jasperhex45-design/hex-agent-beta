# FLUX.2 [klein] 提示词指南

核验日期：2026-10-12。状态：experimental；本次核验官方资料，未重新验证本地加载或生成效果。
本卡保留HEX已登记的本地9B FP8蒸馏执行边界；家族官方能力不自动扩展该执行器。
按新建、参考编辑的实际入口读取，不能把模型名字当作可调用工具。

## 1. 型号与执行边界

官方仓库区分4B、9B、9B KV，以及4B/9B Base；Base与蒸馏版不是相同推理合同。[K2]
用户说“Klein”但具体型号未定时，问实际模型名称；已有本题明确选择则复用。
官方家族支持文生图、单参考和多参考编辑；官方API帮助页列最多4张参考。[K1]、[K2]
参考数量与采样行为不跨本地实现、API和第三方节点无条件复用。

| 层级 | 可以据此说明什么 | 不能据此说明什么 |
| --- | --- | --- |
| 官方Klein家族 | 模型具有生成与参考编辑能力 | 当前HEX已接通所有能力 |
| 已登记 `comfyui.flux-klein` | 现有纯文生成合同 | 可以传图片参考 |
| 已登记 `comfyui.flux-klein-edit` | 现有单父图指令编辑合同 | 已支持多参考、蒙版或像素保护 |
| 旧机器节点/模型证据 | 2026-09-29曾记录本地配置 | 本次机器已加载、可运行或效果合格 |

## 2. 官方建议

- 使用连贯的场景叙述，写清构图、光线、风格和环境，避免关键词堆栈。来源：[K1]。
- Klein产品没有内置prompt upsampling；需要的具体细节直接写在正文。来源：[K1]。
- 官方仓库确认生成、单图与多图编辑及不同型号分支。来源：[K2]。
- 当前BFL通用提示目录已转向FLUX 3；其新增控制不能视为Klein能力。来源：[K3]。

## 3. HEX 整理的中文骨架

下面的顺序、保留项组织及示例由HEX整理，非官方固定语法；按已有方案编译成英语正文。

### 新建

```text
[本题主要主体与动作]出现在[环境]中。[重要空间关系与构图]。[光源]使[表面]呈现[可见材质与颜色关系]。[确实必要的次要内容]。
```

### 单父图编辑

```text
把图1中的[明确对象/区域]由[原状态]改为[目标状态]，保留[主体结构、位置、构图及本题保护项]。让[新材质或光影]与原图场景连贯。
```

### 多参考写法——仅已核验支持该模式的目标入口

```text
图1提供[底图/主体]，图2只提供[物件/材质/风格]。将[允许借用内容]放入[指定位置与关系]，保留[需要保护的内容]。
```

现有HEX单父图编辑工具不支持上一模板的多图派发；本卡可交付对应模型的提示词，不虚报工具扩展。

## 4. HEX 可复制示例

```text
A white ceramic cup stands on a pale stone table. The cup is framed from the side with open space above it. Soft window light from the left forms a continuous highlight across the glaze and a gentle shadow to the right.
```

编辑时替换为本轮变化，不把上面的完整新建场景当成编辑指令：

```text
Change the blue cup to warm white glazed ceramic. Keep the cup shape, handle position, table, and framing. Keep the illumination from the left window and the existing shadow direction.
```

## 5. 执行与意图保留

- 沿用当前已核验执行器合同；其中步数、调度器和尺寸是特定工作流配置，不是通用PE建议。
- 纯文工具不接参考；单父图编辑保留已有来源、哈希、尺寸与上传核验要求。
- 无新证据时不删除已有父图限制，不以低降噪img2img替代登记的指令编辑路线。
- 修改光线时描述受光面、反射与投影关系；未要求灯具入画时，不新增灯具或重排布景。
- 参考提供的属性与底图身份分别使用；“保持”仍需要生成后看图核验。

## 6. 避免混用与缓存

- Klein、FLUX.2 dev与FLUX.1 Kontext不是别名，型号不明确时先问。
- 蒸馏/Base及具体工作流分别记录；不把负面分支、CFG和步数写成全家族结论。
- 当前登记的蒸馏路线无负向分支，用目标状态表达避项；Base另按真实实现核验。
- 无内置扩写不代表ComfyUI没有外接PE节点，避免重复扩写改变方案。
- 不复制pro/flex的参考上限，也不把FLUX 3的框选布局能力塞入Klein。
- 不套固定题材、风格或相机型号；保持本题已选设计和用户硬限制。
- 型号与模式匹配直接读缓存；新增版本、具体失败、冲突或未覆盖能力才补查。

## 7. 来源与访问限制

- [K1] [BFL Klein官方帮助页](https://help.bfl.ai/articles/7592221790-how-do-i-generate-quickly-with-flux-2-klein)。checked：2026-10-12。
- [K2] [BFL官方FLUX.2仓库](https://github.com/black-forest-labs/flux2)。checked：2026-10-12。
- [K3] [BFL文档目录](https://docs.bfl.ai/llms.txt)。checked：2026-10-12。
- [K4] [Klein旧专用提示页](https://docs.bfl.ai/guides/prompting_guide_flux2_klein)。checked：2026-10-12；读取返回Internal Error，未据此页正文作结论。

旧机器与工作流来源原日期保留在ModelProfile；不伪装成2026-10-12重新实测。
未采用二手镜像冒充官方正文；本卡不包含未证实的“社区公认最佳”结论。
