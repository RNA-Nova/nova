from typing import Optional, Dict, Any, Callable
from abc import ABC
import json
import os
from json_repair import repair_json
from nova_ai import TextContent
from pi_agent import AbortSignal, AgentTool, AgentToolResult


class SendTool(AgentTool[Dict[str, Any], Any], ABC):
    """智能体发送消息给前端的工具"""
    
    def __init__(self) -> None:
        self.label = "发送消息到前端"
        super().__init__(
            name="send_to_frontend",
            description="主动向用户前端界面发送消息，支持文本、图片和文件等多种内容类型。用于在任务执行过程中实时展示进度、结果或需要用户确认的信息",
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": "纯文本消息或JSON字符串"
                            },
                            {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "type": {
                                                    "type": "string",
                                                    "enum": ["text", "image", "file"],
                                                    "description": "内容类型"
                                                },
                                                "text": {
                                                    "type": "string",
                                                    "description": "文本内容"
                                                },
                                                "data": {
                                                    "type": "string",
                                                    "description": "Base64编码的数据或图片数据"
                                                },
                                                "mime_type": {
                                                    "type": "string",
                                                    "description": "MIME类型，如 image/png, application/pdf"
                                                },
                                                "filename": {
                                                    "type": "string",
                                                    "description": "文件名"
                                                },
                                                "path": {
                                                    "type": "string",
                                                    "description": "文件本地路径，一定要使用绝对路径"
                                                }
                                            },
                                            "required": ["type"]
                                        },
                                        "description": "消息内容数组"
                                    },
                                    "display": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": "是否在前端显示此消息"
                                    }
                                },
                                "required": ["content"]
                            }
                        ],
                        "description": "要发送到前端的消息内容，可以是字符串或结构化消息对象"
                    }
                },
                "required": ["message"]
            }
        )
        
    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update: Optional[Callable[..., Any]] = None
    ) -> AgentToolResult:
        
        raw_message = params.get("message", {})
        # 检查是否已中止
        if signal and signal.aborted:
            return AgentToolResult(
                content=[TextContent(type="text", text="## ⚠️ 操作已中止")],
                details={"error": "Operation aborted"}
            )
        # 如果 message 是字符串，尝试使用 jsonrepair 修复并解析
        if isinstance(raw_message, str):
            try:
                repaired_json = repair_json(raw_message)
                agent_to_frontend_message = json.loads(repaired_json)
            except (json.JSONDecodeError, Exception):
                # 如果修复失败，保留原始字符串作为 text 类型处理
                agent_to_frontend_message = {
                    "content": [{"type": "text", "text": raw_message}],
                    "display": True
                }
        else:
            agent_to_frontend_message = raw_message
        
        # 补充文件的 size 字段（完全由服务端计算，不依赖入参）
        if isinstance(agent_to_frontend_message, dict) and "content" in agent_to_frontend_message:
            for item in agent_to_frontend_message["content"]:
                if isinstance(item, dict) and item.get("type") == "file":
                    file_path = item.get("path")
                    try:
                        item["size"] = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0
                    except (OSError, IOError):
                        item["size"] = 0
        
        content = [TextContent(
            text=json.dumps(agent_to_frontend_message, ensure_ascii=False)
        )]
        return AgentToolResult(
            content=content,
            details=agent_to_frontend_message,
        )