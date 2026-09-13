"""语义 id（NewType）——跨边界流动 id 的类型化。

零运行时成本：运行时即 ``str``，线上 JSON 与 JSONL 落盘形态不变；
价值在静态检查——``EntryId`` 传给 ``SessionId`` 形参在 pyright 下直接报错，
不再等到运行期。

纯语义包装，**不做构造校验**（id 自产自销，校验只给不可信输入——
见 docs/data-modeling.md「表示选型」规则 3）。边界处（JSONL 读入、
RPC 参数校验后）一次性贴标：``SessionId(raw["sessionId"])``。
"""

from typing import NewType

SessionId = NewType("SessionId", str)
"""会话 id（sessions/<session-id>.jsonl 文件名主体）。"""

EntryId = NewType("EntryId", str)
"""会话树条目 id（JSONL 内每条 entry 的 id）。"""

RunId = NewType("RunId", str)
"""一次 agent 运行（run）的 id。"""

ToolCallId = NewType("ToolCallId", str)
"""一次工具调用的 id（LLM 工具调用与工具结果的相关键）。"""

__all__ = [
    "SessionId",
    "EntryId",
    "RunId",
    "ToolCallId",
]
