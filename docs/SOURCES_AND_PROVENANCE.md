# 来源与版本依据

## 来源范围

设计知识保留在 `knowledge/design` 的来源、限定阅读范围及可见检查方法中。它们属于有条件的设计假设，不是生成效果验证；内测包不包含开发过程中的私人对话、旧源码快照或测试输出。

镜头与叙事条目的来源包含已登记的 Yale 页面文字；网页课堂示例图像不随工程分发。模型与方法的来源核对日期保留为当时记录，不冒称今天重新核验。第三方权利和改编范围见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 本輪核对的官方接口/执行文档（2026-09-19）

- Codex Skills官方入口：https://developers.openai.com/codex/skills/ （当前跳转到官方技能说明；项目SKILL仍提供可直接阅读的降级路径）。
- OpenAI Structured Outputs：https://developers.openai.com/api/docs/guides/structured-outputs 。代码输出Schema要求对象所有字段required，未知字段禁止，可选使用null。
- OpenAI Image generation：https://developers.openai.com/api/docs/guides/image-generation 。实际适配generations和multipart edits，模型ID不写死。
- OpenAI Evaluation best practices：https://developers.openai.com/api/docs/guides/evaluation-best-practices 。评估使用任务特定标准与对照，不把自动评价当真值。
- Yale Cinematography：https://filmanalysis.yale.edu/cinematography/ 。景别、取景、视角和运动的表达语境。
- Yale Mise-en-scène：https://filmanalysis.yale.edu/mise-en-scene/ 。空间、表演、置景与光的共同组织。
- Yale Editing：https://filmanalysis.yale.edu/editing/ 。建立、反应、衔接和镜头之间的意义；单张图不证明实际剪辑效果。

## 继承研究库的来源（范围详见sources.json）

Getty设计原理/视觉元素、ARRI Lighting Handbook、Edmund Optics视场与焦距、Albers Foundation色彩关系、Henderson等注意研究。它们分别是教学、物理条件、艺术教学和特定实验，不混为普遍美学定律；没有声称本轮读完全部著作。

## GitHub方法来源，不是本轮安装清单

此前审美核查提出Google DeepMind proactive_t2i_agents（意图澄清）、TIFA（事实对齐）、VIEScore（语义和感知质量分开）等。当前实现借鉴需求澄清、参考对照、证据化检查的方法，**没有复制其完整项目，也没有把它们变成三套新的依赖或打分器**。依赖只安装requirements列出的Python库；测试输出不是外部论文结论。
