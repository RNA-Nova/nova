# nova-catalog

Nova 模型目录构建管线（对位 pi `packages/ai/catalog/`）。

自包含的构建期世界：知识（`providers/`）、族事实（`families/`）、薄拉取与解析（`sources/`）、词汇（`define.py`）、修正引擎（`engine.py`）、统一编排（`build.py`）、生成事务（`generate.py`）、数据与校验（`model_data.py`）。**不依赖 `nova_ai` 运行时**——类型词汇经 `nova_protocol` 枢纽共用。

## 布局

```
src/nova_catalog/
├── sources/       # models.dev 拉取/翻译、ark 响应解析、HTTP 薄封装
├── families/      # 跨 provider 的族级事实
├── providers/     # 一家一个 ProviderDefinition（声明式修正 + 逃生口）
├── define.py      # ProviderDefinition / ProviderContext + 修正键校验
├── engine.py      # 修正/盖戳语义单点
├── reasoning.py   # native effort 门控与级别表推导
├── input_limits.py# 图像输入的缓存安全默认缩放
├── build.py       # build_catalog：取数、stage-one、派生、共享规则、finalize
├── generate.py    # 产物序列化与暂存/提交/复验/回滚
└── model_data.py  # 产物路径、manifest 与四方对账
scripts/           # 薄 CLI 入口：generate / check_model_data
tests/             # 公开构建与生成入口、最小 fixture、关键语义金标
```

- 产物写入 `nova_ai` 包（`src/nova_ai/providers/data/*.json` 等），默认按 monorepo 兄弟目录定位；内部测试经显式 `package_root` 参数使用临时目录，不读取环境变量。
- 根 `generate-models` 默认 `--strict`，上游失败不提交。生成器先写暂存树，完整校验后才提交，提交后复验正式树；普通替换/删除失败回滚。全量生成清理旧 shard/JSON（含 orphan）；`--data-only` 以现有聚合器的 provider 集为边界。
- manifest 记录 schemaVersion、生成时间、结构哈希和文件哈希；`check-model-data` 负责聚合器、shard、数据分片与 manifest 的离线对账。
- Ark 账户/区域状态只在运行时 personalize 消费，不进入公共种子。
- 真实 API 全模型巡检归 `nova_ai/tests/integration/check_live.py`，根任务为 `pixi run -e dev check-live`。
