# 汽车摄影入口

汽车静态摄影使用统一 HEX 流程中的 `car` 方法。它保存车辆身份、参考职责、模型输入与调用记录；实际设计、看图和判断由当前宿主完成。完整准备、检查和结束条件见 [操作入口](RUNTIME.md)。

## 保留什么，允许改变什么

先按当前需求区分两种任务：指定车辆身份时以真实素材保留车型和结构；未指定车型的开放创作可用 `vehicle_identity=open`。开放身份不能用来规避用户的保车要求。

多张身份图共同说明车辆结构，不自动锁死摄影机位；用户明确要求保留原图角度的编辑必须保留其投影。风格、光线和背景参考各自承担指定职责，不替换身份事实。

## 准备与核对

以下文件由宿主根据实际需求和素材维护，路径只作格式示例。公共入口会为新任务启用完整流程要求：

```text
python -m hex_agent car prepare --brief inputs/car.json --sources inputs/sources.json --out outputs/car-new --deliverable-kind image
```

准备后读取该任务指导，整理真实 Need，实际查看必要参考并 `observe`，完成 Plan，再 `prepare-compilation`、编写适配正文并 `preflight`。

```text
python -m hex_agent car preview --run outputs/car-new --solution outputs/car-new/solution.json
python -m hex_agent car freeze-input --run outputs/car-new --solution outputs/car-new/solution.json
```

按实际目标工具填写它支持的比例和分辨率参数；不把某通道的 CLI 参数传给另一个工具。`--expected-prompt FILE` 可核对指定基线正文，差异必须如实报告。预览和冻结不调用生成模型，也不代表已有生图许可。

## 执行与结果

已有本题许可沿用，真实缺失的许可按当前流程登记。随后执行 `dispatch`，原样调用实际工具并保存回执和图片；未知请求只查询原编号。修改真实父图时建立关联版本，保留身份、已有正确内容与累计预算。

生成后检查实际图像、编辑范围和本题表达，再完成对应 workflow 检查与真实用户反馈。文件检查通过、图像生成成功和用户接受是不同结果；不能用测试数量证明车漆更好或角度更准。

内测请提供原需求、身份/编辑输入、实际结果和最明显的一处差距。参考 [README 的局限与边界](../README.md#局限与边界)提交带图反馈。
