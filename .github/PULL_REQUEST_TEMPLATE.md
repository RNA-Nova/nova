# Pull Request 自检清单

> 单人 + AI agent 形态下，本模板替代"他人评审"——合并前逐项自检。

## 改了什么

<!-- 一段话说清改动内容 -->

## 为什么

<!-- 动机/关联 issue（Closes #123） -->

## 验证证据

<!-- 跑了什么测试/验证，结果如何（贴关键输出） -->

## 自检

- [ ] 四套测试全绿（harness `pytest tests -m "not integration"` / bundle `pytest backend/tests` / nova-tui `npm test` / bundle frontend `npm test`）
- [ ] wire 契约如有变更：`python -m nova_harness.core.rpc.protocol.schema_export` 已重新导出，gen 文件随 PR 一并提交
- [ ] 没有引入大二进制/构建产物/本地状态文件（dist/node_modules/会话数据等）
- [ ] 新增代码注释为中文、风格与周边一致（不复述外部项目出处）
- [ ] AGENTS.md / README 如涉及结构变化已同步
