# Pull Request 自检清单

> 单人 + AI agent 形态下，本模板替代"他人评审"——合并前逐项自检。

## 改了什么

<!-- 一段话说清改动内容 -->

## 为什么

<!-- 动机/关联 issue（Closes #123） -->

## 验证证据

<!-- 跑了什么测试/验证，结果如何（贴关键输出） -->

## 自检

- [ ] 全量测试绿（`pixi run -e dev test-all` / `cargo test --workspace`）
- [ ] 线上协议如有变更：PROTOCOL.md 同步 + 版本号按规则 bump（major 破变更 / minor 新增）
- [ ] 没有引入大二进制/构建产物/本地状态文件（vendor/target/dist/会话数据等）
- [ ] 新增代码注释为中文、风格与周边一致
- [ ] AGENTS.md / README 如涉及结构变化已同步
