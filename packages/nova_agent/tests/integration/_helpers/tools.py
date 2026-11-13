"""真实模型集成测试使用的确定性本地工具。"""

from nova_protocol import AbortedError, AgentToolResult, TextContent

from nova_agent import AgentTool


class EchoTool(AgentTool):
    name: str = "echo"
    description: str = "Echo the input message"
    label: str = "Echo"
    parameters: dict = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"echo: {params['message']}")], details={}
        )


class ErrorTool(AgentTool):
    name: str = "error_tool"
    description: str = "Always raises an error"
    label: str = "Error"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        raise RuntimeError("intentional tool error")


class SlowTool(AgentTool):
    name: str = "slow_tool"
    description: str = "Waits until aborted"
    label: str = "Slow"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        assert signal is not None
        await signal.wait()
        raise AbortedError("Operation aborted")


class GetDateTool(AgentTool):
    name: str = "get_date"
    description: str = "Return today's date"
    label: str = "Date"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="2026-06-10")], details={})


class GetTimeTool(AgentTool):
    name: str = "get_time"
    description: str = "Return current time"
    label: str = "Time"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="12:00")], details={})


def extract_text(message) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(block.text for block in message.content if block.type == "text")
