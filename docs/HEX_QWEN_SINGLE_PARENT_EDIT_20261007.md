# Qwen 单父图编辑

`comfyui.qwen-image-edit` 是当前接入的本地 ComfyUI 编辑路线，目标模型键为 `qwen-image`。它接收一张已登记的 PNG 父图，通过参考条件生成完整画面，适用于明确的图像修改任务。是否保住身份、材质和非目标细节，须比较实际父图与结果。

首次使用从 [CODEX_START.md](../CODEX_START.md) 进入正常任务流程。此页补充当前连接器的输入与限制，不另建一套生成入口。

## 运行前提

需要本机 ComfyUI 服务以及连接器指定的模型文件：

- `qwen_image_2.1_int8_convrot.safetensors`
- `qwen3vl_8b_int8_convrot.safetensors`
- `qwen_image_2.1_vae_bf16.safetensors`

内测包不包含 ComfyUI、这些权重或其安装环境。所需原生节点包含 `TextEncodeQwenImage21` 及其 `images.image_1`、VAE 输入和输出接口；执行前核对实际服务。连接器使用固定图结构，不支持直接传入任意工作流或替换模型文件。

## 冻结输入

| 项目 | 当前约定 |
|---|---|
| 父图 | 仅一张任务内 PNG；登记真实 ID、职责、文件和 SHA256 |
| 父图职责 | `identity`、`edit_target` 或 `edit_canvas` |
| 本地服务 | 显式端口的本地回环 HTTP 地址，例如 `http://127.0.0.1:8188` |
| 必需选项 | `endpoint`、`width`、`height`、`seed`、`reference_resolution` |
| 输出尺寸 | 宽高均为 32 的倍数，范围为 32–4096，并匹配父图推导尺寸 |
| 参考分辨率 | 显式选择 `0`、`512`、`768` 或 `1024` |
| 当前采样选项 | `steps` 为 25、40 或 50，默认 25；`cfg` 固定为 1 |

这些是当前 HEX 固定连接器的范围，不代表模型或 ComfyUI 的全部能力。未支持的参数直接报错，不静默丢弃。

`reference_resolution` 按近似平方像素面积推导宽高，不是长边。非零值保持原比例进行面积推导，再取最近 32 倍数；值为 0 时仍将原宽高取最近 32 倍数。以 768×1024 父图为例，当前推导分别为：

| `reference_resolution` | 输出宽×高 |
|---|---|
| `0` | 768×1024 |
| `768` | 672×896 |
| `1024` | 896×1184 |

取整可能改变比例。上传使用原始父图字节并回读核验，输出校验使用冻结的处理尺寸；尺寸相同不等于原像素无损，也不保证模型内部没有重编码。

## 执行与恢复

宿主观察父图，按本题形成 Need、Plan 和完整编译，再沿原 preflight、授权、dispatch 与执行器提交。连接器核对冻结参数、父图摘要、上传回读和返回尺寸。查看可用入口与已有任务：

```text
python -m hex_agent executors
python -m hex_agent status --run outputs/task --format human
python -m hex_agent harness plan --run outputs/task
```

需要让目录显示某个既有任务的实际执行证据时，可添加 `executors --evidence-run outputs/task`。目录查询本身不生成图片。

结果未知时只查询原请求编号，不重发。没有新证据或可用控制，且同一路线重复出现相同失败时，停止该路线并解释缺口。切换路线须建立关联修改版本、重新编译和预检，沿实际许可与累计预算继续，不能改写已派发请求。

## 适用边界

该入口不接收多父图、蒙版、姿态关键点或透明控制。父图作为视觉与参考 latent 条件，采样后解码整张画面；未修改区域也可能重建。

需要区域像素保护时，查看独立的 [Qwen 蒙版编辑](HEX_QWEN_MASKED_EDIT_20261007.md)。身份、动作、接触关系和视觉质量仍需宿主实际看图，不能将一次成功或文件检查通过解释为通用保真保证。模型资料与来源见 [Qwen 档案](../knowledge/models/qwen-image.json)。
