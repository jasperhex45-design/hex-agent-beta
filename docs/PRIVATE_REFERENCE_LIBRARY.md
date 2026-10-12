# 私人艺术风格库

把用户选定的本地图片，或已具备适用处理权限的 Pinterest 画板，整理成 HEX 可用的艺术参考。原图、来源、视觉观察和用户偏好分别保存。发布后，新创作任务沿现有参考库入口选择可迁移的方法，再由当前宿主完成设计与目标模型编译。

这是 HEX 主 Skill 的图库维护能力。`python -m hex_agent references` 和 `python -m hex_agent.reference_catalog` 使用同一实现；无需再启动一套创作 Agent。Pinterest 来源适配使用官方 API v5；不依赖尚未在本机配置的官方 MCP 服务。

## 1. 宿主执行顺序

用户说“把这些图加入我的风格库”“整理这个画板”“更新我的收藏”时，复用已明确的集合和来源；只在无法确定实际素材位置、选择范围或权限时补充必要信息。库内维护不创建创作 run，不消耗生图预算。

1. 核对 HEX 主 Skill 绑定的解释器和真实工程，选择当前私人集合。已存在集合先 `status`；首次 `init` 保存用户选择此集合的原话。
2. 本地图片执行 `import-local`；Pinterest 先检查已配置的官方能力及适用处理权限，再 `sync-pinterest`。重复导入复用原图与已有观察，失败逐项报告。
3. `prepare` 默认返回最多 8 张待分析原图及当前卡片 schema。宿主实际打开 `input_files`，观察构图、光影、配色、材质和表达。每张提取一个主要视觉决定，保留事实、迁移条件、不可迁移项及可见失败信号。
4. 宿主填写 `hex.reference-observations.v1` 后 `observe`，完成正常批次后继续队列，直到没有未分析原图或存在具体工具/素材阻塞。不要让用户代填 JSON，也不要把标题或 Pin 描述当作看图结果。证据不足可保留 `candidate`；默认队列继续后续未分析图片，需复核卡片以 `needs_review` 单独报告，避免反复处理同一张。复核时通过 `prepare --reference-id` 明确选定原卡片。
5. `review` 生成离线看图页面；需要用户选偏好时展示实际页面。没有反馈也可发布已观察的资料，不能用收藏行为伪造“喜欢全部”。导入用户真实导出的反馈后重新发布。
6. `publish` 只发布 `curated` 卡片并切换新任务默认集合。完成时分别报告导入/去重/失败数、已分析/待分析数、发布版本、实际连接状态和未完成部分。

这是按请求执行的增量流程，没有安装常驻进程或定时任务。自动化宿主可重复调用同一入口；没有实际看图能力时停在候选队列，不自动写视觉结论。API 适配和单元测试通过也不证明已连接用户账号。

## 2. 本地素材与日常命令

下方 `PY`、`ENTRY`、`CATALOG`、`SOURCE` 等由宿主替换为真实解释器、入口及绝对路径，原话使用用户实际表达。默认私人目录为当前用户的 `~/.hex/reference-library`；多个集合使用不同 `--catalog` 路径。所有命令的 `--catalog` 放在子命令之前。

```text
PY ENTRY references --catalog CATALOG init --collection-id my-art --user-quote USER_QUOTE
PY ENTRY references --catalog CATALOG import-local --source-root SOURCE --max-files 500
PY ENTRY references --catalog CATALOG status
PY ENTRY references --catalog CATALOG prepare --limit 8 --out ANALYSIS_QUEUE
PY ENTRY references --catalog CATALOG observe --file OBSERVATIONS
PY ENTRY references --catalog CATALOG review --out REVIEW_HTML
PY ENTRY references --catalog CATALOG feedback --file EXPORTED_FEEDBACK
PY ENTRY references --catalog CATALOG publish
```

原图支持单帧 PNG/JPEG/WEBP，每张不超过 64 MiB、3200 万像素。按原始字节 SHA-256 去重，保留实际尺寸与来源；相同文件来自多个路径或画板时保留多个来源。同一来源换了图片会形成新的图片绑定；重新编码后的相似图目前分别保存。程序不猜画师、镜头、制作工序或图像原作者。

观察文件的结构如下。此处 `CARD_FIELDS` 必须由宿主按 `prepare.card_schema` 填写，示意结构不能直接充当已观察记录。

```text
{
  "format": "hex.reference-observations.v1",
  "collection_id": "my-art",
  "analyses": [{
    "reference_id": "ref_...",
    "source_sha256": "实际原图摘要",
    "card": CARD_FIELDS
  }]
}
```

卡片 ID、原图路径/摘要和版本由程序绑定。只有宿主亲见图像才能标注 `seen_via=view_image`，完整可用的观察才设为 `curated`。`visible_facts` 描述画面事实；`dominant_decision`、`supporting_mechanisms` 说明方法；`transfer_conditions`、`do_not_transfer`、`anti_default`、`visible_test`、`failure_signals` 约束如何用于下一题。分类与关键词只为找到候选，不是审美评分。

## 3. 带图反馈与范围

离线页面展示经过原图摘要校验的缩略图、分析、来源及已保存偏好。页面没有远程资源。若原图缺失或改变，明确标记并禁用对应反馈；不使用旧预览掩盖失效来源。

用户可选择配色、光影、构图、材质、表达或整体，再明确记录“偏好”“避开”或整图“不再推荐”。每项保留用户输入；没有文字时保留实际点击的选择文字。初始状态无偏好，待导出选择不冒充已写入库。

`review --scope project:项目名` 默认记录仅此项目的偏好，也可显式改为长期偏好 `global`。导出 JSON 后，宿主用 `feedback --file` 入库，再按同一范围 `publish --scope`。不把项目喜好自动扩展到所有创作。导出批次有 `export_id`，同一文件重复导入幂等，用户重新选择形成新批次。没有 `export_id` 的历史文件只按整份内容去重；新的交互必须提供新 ID，否则程序无法区分“重新表达相同偏好”与“重试导入旧文件”。

“喜欢配色”不会自动成为“喜欢人物、服装、镜头和排版”。`avoid` 作为该属性的限制传给宿主，`do_not_recommend` 仅允许 `overall`，在相应发布范围排除整张图。若用户明确要求清除一项既有偏好，宿主用相同图片、属性及范围的 `signal=clear` 反馈事件，保存实际原话及新的 `export_id`。图片资料继续保留。

## 4. 集合发布与 HEX 创作

发布生成内容摘要命名的来源/偏好清单和卡片文件，再生成不可覆盖的集合快照。原图、卡片、来源与用户原话的绑定都会验证；尚未完成任何可用观察或内容损坏时，不切换已有配置。用户排除或撤回最后一张可推荐图片后，发布会禁用当前选定的这个集合，让新任务停止推荐；保留旧任务快照，也不禁用另一个集合的配置。

新任务读取发布时选定的集合；正在进行的任务继续引用创建时的版本。新增图片、补充观察或改变偏好后重新发布，不改写旧任务的参考上下文。只撤回一张图片时使用：

```text
PY ENTRY references --catalog CATALOG withdraw --reference-id REFERENCE_ID --user-quote USER_QUOTE
```

撤回将原图移至同一私人目录的 `withdrawn/`，使其原参考路径失效，并停止后续同步自动恢复；这是撤回来源，旧任务若仍引用它会报告来源失效，不能继续假装可用。撤回后重新发布其余资料。此操作保留私人归档，不是彻底删除图片。

集合领域由实际卡片和明确迁移领域推导，支持人物、车辆、插画、海报，以及明确适用于角色/道具的卡片。可用 `publish --domain poster` 等显式缩小范围。每题仍至多三张自动建议，允许零采用，局部编辑不自动换风格。

新任务命中的卡片如有偏好，会收到 `local_reference_context.preference_context`，只含命中图片的逐属性原话。词法分数不因偏好被人为抬高；宿主根据当前需求和每项 scope 决定怎样使用。项目集合的偏好只有本题明确属于该项目时才采用，无法确认可零采用。原有创作结果反馈库和私人经验召回策略不因此启用。

图片通过既有 `reference_library attach` 加入本题的 `evaluation_only` 参考，再亲见原图并完成任务观察。发布风格库不会赋予上传给生成服务或复刻主体的许可；引用可迁移关系仍通过本题 `choices.reference_uses` 和原模型编译流程。

## 5. Pinterest 官方接入

先使用当前环境中已有的 OAuth 配置，不在聊天、文件示例或日志里放 token。适配器从 `PINTEREST_ACCESS_TOKEN` 环境变量读取，也可用 `--token-env` 指定已有环境变量名。它只对固定官方 API 发送只读请求，图片 CDN 请求不携带此 token。

```text
PY ENTRY pinterest probe --board-id BOARD_ID
PY ENTRY pinterest boards --max-pages 1 --max-items 100
PY ENTRY references --catalog CATALOG sync-pinterest --board-id BOARD_ID --permission-record PERMISSION_RECORD
```

`probe` 只汇报实际访问能力。没有 token 返回 `AUTH_NOT_CONFIGURED`；被限制访问的个人收藏可能返回 `PINNER_DATA_ACCESS_DENIED`，不循环重试。实际官方授权流程见 [认证与授权](https://developers.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/) 和 [API 更新日志](https://developers.pinterest.com/docs/changelog/changelog/)。这不是登录 Cookie 抓取器，也没有配置或冒充官方 MCP 服务。

API 读取能力与允许长期存储、分析图像的范围分开。按 [Pinterest 开发者规则](https://policy.pinterest.com/en/developer-guidelines)，接入方需要保留适用于此用途的真实授权依据。`PERMISSION_RECORD` 是接入方提供的 JSON 记录：

| 字段 | 内容 |
| --- | --- |
| `evidence` | 实际平台批准/适用协议的链接，或现有凭据文件的可核对说明；不能编造或填占位语句 |
| `board_ids` | 该依据明确覆盖的画板 ID 数组 |
| `operations` | 实际许可包含 `download_images`、`persistent_storage`、`visual_analysis` 时，才列出这些操作 |
| `account_id` | 可选的真实账户标识；不能把画板拥有者当作作品作者 |

程序只核对接入方提供记录的结构和范围，不会声称独立核验了 Pinterest 实际授权。没有适用依据时，宿主完成本地资料整理入口并准确报告接入缺口，不自行签发处理权限，不把 API 读取成功视为全部权利。

同步以实际 API 返回的 Pin ID、图片地址和分页 bookmark 为依据，不推测原图 URL。默认每画板最多 2 页、100 项，可通过 `--max-pages`、`--max-items` 调整。分页未完时下一次从已保存 bookmark 继续；完整扫描结束后下一轮从头检查变更，已经入库且地址未变的图片不重复下载或分析。使用 `--restart` 可明确从第一页重试。

没有可用静态图片的 Pin 会报告跳过；下载失败保留可重放的分页位置，成功项按摘要幂等。仅允许 HTTPS `i.pinimg.com` 图片、无重定向，并检查体积及实际图像内容。远端移除/移动的 Pin 目前不会自动删除本地资料，需要用户明确撤回。适配器没有后台监控、相似图聚类或自动模型微调。

## 6. 数据位置与验证

SQLite、原图、导出反馈、离线页面、观察文件和集合快照属于用户私人数据，放在所选私人目录或宿主确认的位置。默认目录不在工程内，不同步到 Git；产品发布只包括实现和说明。若自选工程内目录，须先确认已被忽略，不能把图片或处理权限记录随源码推送。

工程回归使用合成图片与 mock API，检查增量、绑定、分页、失败恢复、导出、scope 和版本隔离。它们不证明真实 Pinterest 账户可用，也不证明艺术质量提高。实际效果要在用户真实素材上观察，并通过创作结果和用户反馈判断。
