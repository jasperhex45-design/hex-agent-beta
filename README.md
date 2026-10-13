# HEX · 视觉创作助手

把你的想法、参考和修改要求，发展成画面方案与目标模型提示词；在工具可用时，继续生成、检查和修改图片。

HEX 运行在你的 AI 助手中，由创作方法、模型资料和可选的 Python 流程工具组成，**不是独立生图模型**。

**[开始使用](docs/INSTALL.md) · [标准 HEX-Skill 包](https://github.com/jasperhex45-design/hex-agent-beta/releases) · [提交反馈](https://github.com/jasperhex45-design/hex-agent-beta/issues/new/choose)**

## 能做什么

- **从想法到画面方案。**理解需求、分析参考，明确构图、光色、材质和表达方向。
- **为选定模型写提示词。**保留身份、数量、准确文字和修改范围，按实际生成或编辑入口组织表达。
- **根据真实结果继续修改。**保留正确部分，记录反馈与执行依据；有运行能力时支持任务恢复和受控执行。
- **只做你需要的阶段。**只要参考、方案或提示词，就在该阶段交付，不自动扩展为生图任务。

当前 Beta 优先验证汽车静图、有界改图和模型提示词。人物、平面设计与分镜静帧有方法入口，效果仍需各自的真实任务验证。

## 两条开始路径

**日常创作优先选标准 `HEX-Skill` 包；需要直接管理 Python 环境与源码时，再选源码版。**两者使用同一套创作方法与流程工具，实际执行能力取决于宿主。

| 你的使用方式 | 从哪里开始 |
| --- | --- |
| 日常创作（推荐） | 导入标准包中的完整 `hex/`，按[跨宿主指南](docs/HOST_PORTABILITY.md)核对资源和工具能力。 |
| 自己管理 Python 环境与源码 | 获取 [Beta 源码](https://github.com/jasperhex45-design/hex-agent-beta/archive/refs/heads/main.zip)，准备 Python 3.11+，按[安装指南](docs/INSTALL.md)运行 bootstrap。 |

安装后直接说明需求，例如：

> 用 HEX。以这张图为编辑输入，保留车身比例、机位和轮毂，只调整车漆质感。先看参考并确认本次模型，再执行，最后用结果图指出是否做到。

你不需要填写内部模板。已有模型选择和需求会沿用；缺少影响结果的信息时，HEX 再问必要问题。没有生图工具时，可以先完成方案或提示词。

## WORK 与 TEST

**WORK 是你的日常创作**，可按需开启[私人学习](docs/WORK_LEARNING.md)，让相关反馈用于下一次工作。

**TEST 是开发者验证功能和效果**，不要求你日常额外生图，测试记录也不自动写入你的私人偏好。

## 使用边界

- 模型、账户与生成额度由你自己的宿主或已连接服务提供。
- 私人学习默认关闭，经同意开启；你可以撤回同意、停用学习，它不训练权重，也没有离线后台。
- 私人档案只做本机逻辑隔离，不是账号鉴权或操作系统级隐私保护。

本源码已支持私人学习，下载包是否包含以其实际版本为准（[源码版本](pyproject.toml) · [更新记录](CHANGELOG.md)）。验证细节与反馈要求见 [BETA.md](BETA.md)。

## 文档入口

[安装与检查](docs/INSTALL.md) · [跨宿主能力](docs/HOST_PORTABILITY.md) · [私人学习](docs/WORK_LEARNING.md) · [共用创作方法](docs/HEX_CORE_METHOD.md) · [操作、续改与恢复](CODEX_START.md) · [模型提示词资料](knowledge/model-prompts/README.md) · [Beta 边界与反馈](BETA.md)

## 许可

有权许可的自有源码和文档采用 [CC BY-NC 4.0](LICENSE)，第三方来源与适用范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。公开下载不改变许可条款，也不为用户素材或模型服务提供额外授权。
