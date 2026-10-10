# 第三方来源与许可

## 2026-10-08 可选多模态检索试验

隔离试验适配器参考 [EmbeddingGemma 2 官方模型卡](https://huggingface.co/google/embeddinggemma-2) 和 [Transformers 5.19.0 的官方接口说明](https://huggingface.co/docs/transformers/v5.19.0/en/model_doc/embedding_gemma2)。Google DeepMind 的模型卡标明 Apache 2.0；模型权重、其来源和许可材料仍按上游条款使用，不随 HEX 源码包分发，也不改用 HEX 的主许可。可选 PyTorch、Transformers、Sentence Transformers 依赖保留各自安装包许可；它们不进入基础运行依赖。实际本地 checkpoint 的版本、文件摘要及许可声明随试验报告保留。本段记录接口与来源，不代表已运行模型或完成效果验证。

`tests/fixtures/visual_retrieval_trial/` 是本轮由编码 Agent 编写的几何合成测试材料，按本项目自有材料许可分发；标签未经过用户或人工审核，不来自私人图库，不代表用户审美判断。

## 2026-10-07 本机可选知识模型

Windows 原生检索使用 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（上游模型卡标明 MIT），固定上游版本 `7999e1d3359715c523056ef9478215996d62a620`。ONNX 文件来自 [Xenova 的转换仓库](https://huggingface.co/Xenova/bge-small-zh-v1.5)，固定版本 `75c43b069aac4d136ba6bc1122f995fedcfd2781`，不是 BAAI 官方发布的 ONNX。下载来源、字节数、SHA 与分词/池化约定见 `scripts/runtime/native_model_manifest.json`；原模型卡随本机权重保留。权重及运行环境位于忽略目录，不打包进源码，也不改用 Hex 的主许可。其它本机 OCR/研究依赖保留其安装包自带许可与锁文件，本段不声称完成全仓库发行许可审计。

核对日期：2026-09-28。本文件用于 CL-HEX（仓库名 `cl-hex`）当前源码交付中的第三方来源、改编范围和许可说明。CL-HEX 有权许可的自有材料采用 [LICENSE](LICENSE) 中的 CC BY-NC 4.0；第三方材料仍按下述声明及各自条款使用。

## 造梦师 / DREAM DIRECTOR

- 原项目：**造梦师：AI时代电影视觉指南 / DREAM DIRECTOR / zy-cinematic-realism**。
- 原作者：**ZY / popopo-99**。
- 原版权声明：**Copyright (c) 2026 ZY / popopo-99**。
- 来源：[popopo-99/zy-cinematic-realism](https://github.com/popopo-99/zy-cinematic-realism)。
- 使用版本：v2.3.0，固定提交 [`e78c9669d84373e60c2c9d60cf578184ae4b8c3a`](https://github.com/popopo-99/zy-cinematic-realism/commit/e78c9669d84373e60c2c9d60cf578184ae4b8c3a)。
- 上游许可：**Creative Commons Attribution-NonCommercial 4.0 International，CC BY-NC 4.0**；SPDX 标识 `CC-BY-NC-4.0`。
- 许可链接：[概要](https://creativecommons.org/licenses/by-nc/4.0/)、[完整法律条款](https://creativecommons.org/licenses/by-nc/4.0/legalcode)。完整条款包含第 5 节的无担保及责任限制声明。
- 原始声明：[LICENSE](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/LICENSE)、[NOTICE.md](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/NOTICE.md)。

### 本地适用范围

以下是 CL-HEX 于 2026-09-28 对上游方法文本所作的节选、归纳及改写，按改编材料保留上游署名与 CC BY-NC 4.0 条件；CL-HEX 对这些改编段落中拥有的新增表达也采用同一许可。此范围不等于整份 Python 文件、所有历史规则或整个 Agent 都来自上游。

| 本地位置 | 适用内容 | 主要上游来源 |
| --- | --- | --- |
| [liu_agent/instructions.py](liu_agent/instructions.py)，`PRODUCTION_DESIGN` | 拼接正文开头三段，始于“先判断整体画面靠什么成立”，止于“不声称已接通长期个人化”；后面的“阶段：输出既有 Plan”及原有规则不在本条声明范围内 | [Dream Decode](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/zy-cinematic-realism/references/dream-decode.md)、[Prompt Compiler](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/zy-cinematic-realism/references/prompt-compiler.md)、[Medium Router](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/zy-cinematic-realism/references/medium-router.md) |
| 同文件，`RECORD_ONLY_REVIEW` | 第一段“先区分用户本次改变要求所产生的合理差异与真正走样”至“不从自评自动授权再生成”；原有 record-only 规则不在本条声明范围内 | [Result Repair](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/zy-cinematic-realism/references/result-repair.md) |
| [liu_agent/exporter.py](liu_agent/exporter.py)，`REASSESSMENT_DESCRIPTION` | 仅连续片段“整体表达、空间或媒介不成立才回设计；整体已成立则只处理有依据的局部差距，”；同常量内需求/偏好优先、证据诊断、编译/执行分流和授权预算规则，以及模块其它代码均不在本条上游改编范围内 | [Result Repair 的结构/局部修复分流](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/zy-cinematic-realism/references/result-repair.md#L18-L37) |
| [design-study.md](.agents/skills/liu-aesthetic-agent/references/design-study.md) | “用参考改变本题的主次”一节的三段规则 | 上述四项，以及 [Reference Role Router](https://github.com/popopo-99/zy-cinematic-realism/blob/e78c9669d84373e60c2c9d60cf578184ae4b8c3a/zy-cinematic-realism/references/reference-role-router.md) |
| 开发库中的上游审计与功能对照报告（不随内测包分发） | 对上游内容的节选与转述部分；本文所列许可同样适用于这些派生表达，各报告保留具体来源链接 | 报告内固定提交的逐项引用 |

### 改动说明

CL-HEX 将参考事实、视觉方法、需随新任务调整的构图等分类，以及参考职责、媒介控制、有效改编与真正漂移的区分，压缩接入现有 `choices.action/cooperation/tradeoff` 和反馈流程。未引入上游整套 Skill、卡库、脚本或独立 Scene Master 数据结构。

改写保留 CL-HEX 的需求优先、无参考也可自主设计、来源与实际图片绑定、机器意见不触发生成等规则；不照搬固定规则数量。轻微风格强度和商业题材的光泽选择是对上游默认倾向的调整，不代表上游作者主张。这里的“商业题材”是画面用途与美术语言的描述，不是对商业使用本改编材料的额外授权。

本项目与上游作者不存在由本声明产生的赞助、认可或官方关联。上述署名不表示上游作者认可 CL-HEX 的改编或效果。

### 分享与使用

涉及上述许可材料的分享和改编须符合 CC BY-NC 4.0：保持适当署名、原版权和许可信息、来源链接、改动说明及适用的无担保声明，并仅在该许可允许的非商业范围内使用。非商业依标准条款判断，不仅以是否收费判断。不得对上游材料施加妨碍接收者行使原许可权利的额外限制；本文不排除法律允许的例外或独立于许可的使用。

复制相关段落、分发包含它们的源码包、Skill 或导出的指令文档时，应一并保留本声明或同等完整的可访问声明。完整源码发行的入口见项目 [README](README.md)。本声明不自动为生成图片设定归属，也不替代模型服务、输入图片或其他第三方材料的许可。

## CL-HEX 主许可与分发边界

用户于 2026-09-28 明确选择与上述非商业条款相同的 CC BY-NC 4.0。仓库根目录和当前工程均已提供相同的完整 LICENSE；当前工程单独作为源码包分发时，随包保留本目录的 [LICENSE](LICENSE) 和本声明。版权主体标为 `Copyright (c) 2026 CL-HEX contributors`，只覆盖贡献者有权许可的自有源代码、文档及其它项目材料，另有独立许可声明的部分除外。这是用户选择的公开源码、非商业许可，不另换软件专用许可，也不称为 OSI 开源许可。

第三方代码、依赖、历史导入内容和上游改编材料的原版权与原条款继续有效；CL-HEX 主许可不覆盖或撤销它们的条件。参考图、生成图等图像及私人材料默认不纳入本次仓库级授权，除非另有明确的独立许可；文件在仓库中出现不自动授予额外权利。模型服务条款和输入素材权限也不因本次许可标注改变。

两份 LICENSE 均在范围说明后保留来自 [Creative Commons 官方完整纯文本](https://creativecommons.org/licenses/by-nc/4.0/legalcode.txt) 的未经改动全文，含定义、许可范围、署名、无担保、终止及解释条款；范围说明不改写标准条款。官方正文与本地文件的核验记录保留在开发库的许可来源核对报告中；内测包保留完整 LICENSE 和本声明。

本次核查范围是当前造梦师研究与合并新增的规则，并非整个历史仓库、全部依赖与素材的发行许可审计。详细核验记录保留在开发库；上游固定版本与适用段落已列于本声明。
