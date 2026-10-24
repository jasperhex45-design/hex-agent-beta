# 参与 HEX

- 开发在私有库进行，本仓库是公开发行版；欢迎通过 [Issues](https://github.com/jasperhex45-design/hex-agent-beta/issues/new/choose) 反馈实际问题。
- 修改前保护现有差异；共享业务代码指定一个写入者。
- 改动后验证受影响的入口与必要的失败分支：`python -m hex_agent doctor`、`validate`、`demo`；共享合同变化时再验 schema。
- 不伪造日志、图片或用户反馈。
- 发布保留许可与来源声明（见 [LICENSE](LICENSE)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)），不提交凭据、缓存、私人材料或未授权图片。
- 发行包由 `scripts/release_local.py` 构建，`MANIFEST.json` 在构建时生成，不提交到仓库。
- 宿主侧的共同执行规则见 [docs/RUNTIME.md](docs/RUNTIME.md#共同执行规则)。
