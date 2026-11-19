# nova-catalog

Nova 的通用模型目录知识与构建管线，对位 pi `packages/ai/catalog/`。

本包维护 Provider 模型知识、跨 Provider 模型族事实、构建期数据源与共享构建编排。它只依赖 `nova_protocol` 与自身，不知道任何消费方的目录结构、代码模板、生成入口或生成物校验。

## 边界

- `nova_catalog`：模型知识、数据源、修正与共享构建编排，产出 `ProviderCatalog`。
- `nova_ai`：通过 `scripts/generate.py` 生成自己的 Python/JSON 模型目录，并由 `scripts/model_data.py` 完成全部生成物对账。
- 目录站服务：复用同一构建结果，自行生成和发布公共 JSON；不属于本包。

## 结构

```text
src/nova_catalog/
├── providers/       # 各 Provider 的模型知识与构建钩子
├── families/        # 跨 Provider 的模型族事实
├── sources/         # 构建期数据源与翻译
├── define.py        # Provider 定义与字段盖戳
├── build.py         # 共享构建编排
├── reasoning.py     # effort/thinking 规则
└── input_limits.py  # 图像输入限制
```

在 monorepo 根目录执行：

```bash
pixi run -e dev generate-models   # AI 消费方脚本：生成 nova_ai 本地模型目录
pixi run -e dev check-model-data  # AI 消费方脚本：离线校验生成物一致性
pixi run -e dev test-catalog      # 运行本包密封测试
```
