"""nova_ai.testkit —— 跨包共享的契约测试设施（codex ``storage_tests.rs``
的 ``#[path]`` 实例化对位）。

契约逻辑单源在此；各包在自家 ``tests/contracts/`` 用"继承契约类 + 本地
fixture 绑定实现工厂"的方式实例化：

- ``nova_ai/tests/contracts/``：内存实现；
- ``nova_harness/tests/contracts/``：文件型实现（tmp_path 真文件——
  真实性阶梯第一条）。

新增实现的及格线 = 在自己包的 tests/contracts/ 实例化并全绿
（testing-modeling §四.4）。
"""
