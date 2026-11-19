"""通用模型目录知识与构建管线（对位 pi ``packages/ai/catalog/``）。

包含 Provider 构建钩子（``providers/``）、族事实（``families/``）、
薄拉取器（``sources/``）、构建词汇（``define.py``）与共享编排
（``build.py``）。只依赖 ``nova_protocol`` 与自身，产出通用目录数据；
消费方专用代码模板、输出路径、生成入口和生成物校验不归本包。
"""
