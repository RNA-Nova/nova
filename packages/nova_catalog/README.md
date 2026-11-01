# nova-catalog

Nova 模型目录构建管线（对位 pi `packages/ai/catalog/`）。

自包含的构建期世界：知识（`providers/`）、族事实（`families/`）、薄拉取（`sources/`）、词汇（`define.py`）、引擎（`build.py`）、数据与校验（`model_data.py`）。**不依赖 `nova_ai` 运行时**——类型词汇经 `nova_protocol` 枢纽共用，行为全自建。

## 布局

```
src/nova_catalog/
├── sources/      # 全部拉取（一源一文件）：models_dev.py（拉取+翻译）、ark.py（火山在售）、http.py
├── families/     # 族事实（K3/Claude 级别表与 compat——常量）
├── providers/    # 知识定义：一家一个 ProviderBuildDef（声明式）
├── define.py     # ProviderBuildDef 类型 + define_provider
├── engine.py     # 修正语义引擎（apply_corrections——语义表驱动）
├── build.py      # 编排管线：取数 → 翻译 → 修正 → 盖戳
├── model_data.py # manifest/数据校验
scripts/          # 脚本级 CLI（只运行不导入）：generate / check_model_data / check_live
tests/            # 引擎/定义/族层金标测试
```

- `scripts/check_live_providers.py` 是唯一引 `nova_ai` 的例外（运维探针，对运行时包发真实请求）；
- 产物写入 `nova_ai` 包（`src/nova_ai/providers/data/*.json` 等）：默认按 monorepo 兄弟目录约定定位，可用环境变量 `NOVA_AI_PACKAGE_ROOT` 覆盖。

产物写入 `nova_ai` 包（`src/nova_ai/providers/data/*.json` 等）：默认按 monorepo 兄弟目录约定定位（`packages/nova_ai`），可用环境变量 `NOVA_AI_PACKAGE_ROOT` 覆盖。
