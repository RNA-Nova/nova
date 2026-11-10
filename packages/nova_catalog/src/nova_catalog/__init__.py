"""模型目录构建管线（对位 pi ``packages/ai/catalog/``）。

自包含的构建期世界：知识（``providers/``）、族事实（``families/``）、
薄拉取器（``sources/``）、词汇（``define.py``）、引擎（``build.py``）、
入口（``generate.py``）。**不依赖 ``nova_ai`` 运行时**——类型经
``nova_protocol`` 共用；运行期共享件（引擎/站翻译/输入限制）也住 src。
manifest 记录结构、文件哈希与生成时间；校验器离线对账四方产物。

产物：``src/nova_ai/providers/data/*.json``（数据分片）+
``.manifest.json``（结构/哈希）+ 各家 ``models.py``（生成 shard）+
``models_generated.py``（聚合器）。
"""
