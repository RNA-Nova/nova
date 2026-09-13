# nova_protocol —— Nova 词汇枢纽

本包是 Nova 全部**跨组件边界词汇**的唯一住所。当前已入住：LLM 消息/内容/模型/用量/
流式事件、枚举与 compat、auth 词汇、模型目录存储条目（`ModelsStoreEntry`）、语义 id、
取消原语（`signal.py`，登记制唯一成员）。按迁移路线待迁入：agent 循环事件、
session 落盘 entries、线上 item 正典、共享 config 形状。

## 收录标准（唯一一条）

> 这个类型是否**跨组件边界被序列化**（或跨包共享的纯形状）？是 → 进本包；否 → 回其语义所有者。

## 三纪律

1. **依赖最小**：运行时依赖只允许 `pydantic`（见 `tests/test_purity.py` 机械执法）；
2. **零行为、零 I/O**：本包只有形状 + 平凡不变量（validator / computed / `to_*`·`from_*`
   纯转换）。类型需要行为时以消费方扩展函数安置，行为函数写在消费方模块，不进本包。
   唯一缓冲带是**基础原语**（登记制）：全仓签名通用、零业务语义、只依赖 stdlib 的极小
   机制——当前唯一登记成员是 `signal.py`（取消原语，纯洁性审计白名单即登记册）。
   原语攒到 ≥2 个时升格为独立叶子包，枢纽恢复绝对纯形状；
3. **单一正典源**：每个类型在本包有且只有一个定义点。消费方一律
   `from nova_protocol import X`（`__init__.py` 全量门面再导出，内部文件布局自由）。

## 双序列化出口（出厂配置）

`NovaBaseModel` 在此定义，全部枢纽类型继承：

- `model_dump()`——持久化/内部形态（snake_case 字段名，磁盘存量兼容）；
- `dump_wire()`——线上形态（`by_alias=True` camelCase）。

边界（JSONL 读入 / RPC 收参 / 包加载）一律 `model_validate()` 全量校验；
内部自产自销一律 `model_construct()` 跳过校验。
