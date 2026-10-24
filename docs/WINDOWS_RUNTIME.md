# Windows 使用与可选本地组件

先按 [README.md](../README.md) 完成基础安装，再按 [操作入口](RUNTIME.md) 使用 HEX；工程目录可使用 `Hex.ps1`。下列本地检索、OCR 和研究环境均为可选组件；测试包不含模型权重、虚拟环境或作者机器的配置。

## 日常入口

```powershell
.\Hex.ps1 runtime
.\Hex.ps1 prepare --brief inputs/task.json --sources inputs/task-sources.json --out outputs/task
.\Hex.ps1 car prepare --brief inputs/car.json --sources inputs/car-sources.json --out outputs/car
```

`inputs/` 中的任务文件由宿主根据真实用户请求整理。`Hex.ps1` 与项目解释器执行的 `python -m hex_agent` 调用同一运行入口；基础安装不注册 console script。`runtime` 只检查文件是否存在，不证明组件已完成推理或图片质量合格。

Windows 新任务默认采用 `knowledge-assisted-v1`；汽车入口采用 `car-photography-knowledge-v1`，保留车辆身份与输入冻结规则。通用 `prepare` / `run` 需要使用新任务基线时，可显式传入 `--execution-profile production-baseline`；该选项不用于 `car prepare`。已有任务的恢复保留原策略、来源、停止记录和累计预算。

| 环节 | 当前职责 |
|---|---|
| 理解与设计 | 宿主形成真实 Need 和 Plan，区分用户要求与自主提案 |
| 方法检索 | Need 形成后按本题检索、筛选知识；允许零采用 |
| 提示词与执行 | 按模型档案编译，经过 preflight、dispatch 和真实回执 |
| 文件与文字检查 | 检查实际文件；明确要求画内文字时才调用 OCR |
| 视觉与反馈 | 宿主实际看图，分别记录观察、问题和用户反馈 |

## 可选：本地语义检索

安装器在 `.runtime/knowledge` 建立独立 CPU 环境，并下载清单中固定版本的模型文件；需要可用网络。只想检查已安装文件时使用 `--verify-only`。

```powershell
.\.venv\Scripts\python.exe scripts/runtime/native_install.py
.\.venv\Scripts\python.exe scripts/runtime/native_install.py --verify-only
```

所用基础模型是 `BAAI/bge-small-zh-v1.5`，ONNX 转换由 Xenova 发布。版本、文件摘要、来源及 MIT 许可说明保存在 [模型清单](../scripts/runtime/native_model_manifest.json)。安装器保留上游模型卡；HEX 的项目许可不替代上游许可。知识环境使用 CPU，不需要安装 Torch。

配置见 [knowledge-windows.json](../configs/knowledge-windows.json)。真实 Need 形成后，最多 3 次查询、6 条候选；宿主核对领域、修改范围、参考职责、版本和冲突，再决定是否采用。首次 `prepare` 的离线 Need 是草稿，不能代替实际理解。

语义模型或环境缺失、运行失败时，记录降级并回退至词法检索（BM25）。日常查询不会自动下载组件，也不能将降级结果称为语义检索成功。原生知识环境按宿主平台选择解释器，Windows 虚拟环境不能直接复制到其它系统使用。

## 可选：Windows 本地 OCR

当前 OCR 启动器使用 Windows 虚拟环境路径。基础安装不包含 PaddleOCR 或其权重；有画内文字核对需求时，再独立安装：

```powershell
.\.venv\Scripts\python.exe -m venv .runtime/ocr/venv
.\.runtime\ocr\venv\Scripts\python.exe -m pip install -r scripts/runtime/ocr-requirements.txt
.\.runtime\ocr\venv\Scripts\python.exe scripts/runtime/ocr_models.py
```

最后一条命令下载固定摘要的 `PP-OCRv5_mobile_det` 和 `PP-OCRv5_mobile_rec`。下载来源与 Apache-2.0 许可说明写入本机 `.runtime/ocr/model-manifest.json`。推理读取已安装权重并核验摘要，不自动下载；缺失时报告未执行文字核对。

```powershell
.\Hex.ps1 check --run outputs/task --image result.png --out reports/check.json
.\Hex.ps1 check --run outputs/task --image result.png --ocr-requirements inputs/text-checks.json --out reports/text-check.json
.\Hex.ps1 review --run outputs/task --image result.png --out reports/review-input.json
```

`text-checks.json` 由宿主从当前冻结检查派生，例如：

```json
[{"criterion_id":"当前检查ID","text_required":true,"exact_text":"用户明确要求的画内文字"}]
```

检查 ID 和准确文字必须对应当前任务，不能将示例占位符直接用于验收。没有文字要求时不启动 OCR；缺组件时保留未核验状态。报告须放在任务目录外，使用新文件名。`review` 仅准备隔离评审输入，宿主仍需实际查看当前图片并记录观察。

上述命令是组件级检查与输入准备。正式交付继续执行当前任务的 `workflow check / prepare-review / record-review / complete`；组件报告不替代统一完成状态。

OCR 通过只表示相关文字检查通过，不能代替版式、语义、视觉质量或用户验收。

## 可选研究的边界

GEPA、promptfoo 和外部 RAGFlow 接口属于按需研究能力。内测基础包不安装这些环境，也不要求常驻服务。生产流程不会自动训练模型或发布新方法；缺少实际配置时不宣称具备这些能力。普通创作直接沿当前任务状态继续，无需运行研究脚本或完整研发测试。
