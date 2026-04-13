import asyncio
from typing import Optional, Dict, Any
from pi_agent import AgentToolResult, AbortSignal
from nova_ai import TextContent, ImageContent
from .remote_tool import RemoteTool


class RemoteReadTool(RemoteTool):
    """远程文件读取工具 - 通过XML-RPC在远程主机执行文件读取操作"""
    
    def __init__(self,computex_manager):
        """
        初始化远程文件读取工具
        """
        super().__init__(
            name="read",
            description="在远程服务器上读取文件内容。自动判断文件类型（文本或图片），支持文本文件分页读取。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径（相对或绝对路径）"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文本文件编码，默认为 utf-8",
                        "default": "utf-8"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（仅文本文件，1-indexed，不指定则从头开始）",
                        "minimum": 1
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大行数（仅文本文件，不指定则读取所有行）",
                        "minimum": 1
                    }
                },
                "required": ["path"]
            },
            computex_manager=computex_manager
        )
        self.label = "远程文件读取"
    
    async def execute(self, tool_call_id: str, params: Dict[str, Any],
                     signal: Optional[AbortSignal] = None,
                     on_update=None) -> AgentToolResult:
        """
        执行远程文件读取操作
        """
        # 获取连接参数（从基类自动添加的）
        path = params.get("path")
        encoding = params.get("encoding", "utf-8")
        offset = params.get("offset",None)
        limit = params.get("limit",None)

        # 检查是否已中止
        if signal and signal.aborted:
            return AgentToolResult(
                content=[TextContent(type="text", text="## ⚠️ 操作已中止")],
                details={"error": "Operation aborted"}
            )
        
        try:
            host = self.get_host()
            port = self.get_port()
            computex = self.get_computex()

            # 参数验证
            if not path:
                error_msg = "## ❌ 参数错误\n\n必须提供 path 参数"
                return AgentToolResult(
                    content=[TextContent(type="text", text=error_msg)],
                    details={"error": "Missing required parameter: path", "host": host, "port": port}
                )
            
            # 流式更新 - 开始执行
            if on_update:
                on_update(AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"正在从远程主机 {host}:{port} 读取文件: {path}"
                    )],
                    details={"status": "executing", "host": host, "port": port, "path": path}
                ))
            # 调用远程 read 方法
            result = computex.read(path, offset, limit,encoding)
            # 解析返回结果
            if result and isinstance(result, dict):
                success = result.get("success", False)
                error = result.get("error")
                file_type = result.get("file_type")
                content = result.get("content")
                mime_type = result.get("mime_type")
                bytes_read = result.get("bytes_read", 0)
                absolute_path = result.get("absolute_path", path)
                
                if success:
                    # 根据文件类型构建不同的响应
                    if file_type == "image":
                        # 图片文件响应
                        success_msg = f"""## ✅ 远程文件读取成功

**主机**: `{host}:{port}`
**路径**: `{path}`
**绝对路径**: `{absolute_path}`
**类型**: 图片文件
**MIME类型**: `{mime_type}`
**大小**: {bytes_read} 字节

图片已成功读取。"""
                        
                        # 返回包含图片内容的响应
                        return AgentToolResult(
                            content=[
                                TextContent(type="text", text=success_msg),
                                ImageContent(type="image", data=content, mime_type=mime_type)
                            ],
                            details=result
                        )
                    else:
                        # 文本文件响应
                        line_info = ""
                        if offset is not None or limit is not None:
                            if offset and limit:
                                line_info = f"\n**行范围**: {offset} - {offset + limit - 1}"
                            elif offset:
                                line_info = f"\n**起始行**: {offset}"
                            elif limit:
                                line_info = f"\n**最大行数**: {limit}"
                        
                        success_msg = f"""## ✅ 远程文件读取成功

**主机**: `{host}:{port}`
**路径**: `{path}`
**绝对路径**: `{absolute_path}`
**类型**: 文本文件
**编码**: `{encoding}`
**大小**: {bytes_read} 字节{line_info}

### 文件内容
```{path.split('.')[-1] if '.' in path else 'text'}
{content}
```"""
                        
                        return AgentToolResult(
                            content=[TextContent(type="text", text=success_msg)],
                            details=result
                        )
                else:
                    # 远程服务器返回错误
                    error_msg = f"""## ❌ 远程文件读取失败

**主机**: `{host}:{port}`
**路径**: `{path}`
**错误信息**: {error or '未知错误'}

可能的原因：
1. 文件不存在
2. 权限不足
3. 路径错误
"""
                    return AgentToolResult(
                        content=[TextContent(type="text", text=error_msg)],
                        details={"error": error, "host": host, "port": port, "path": path}
                    )
            else:
                # 返回格式错误
                error_msg = f"远程主机 {host}:{port} 返回了意外的结果格式"
                return AgentToolResult(
                    content=[TextContent(type="text", text=f"## ❌ {error_msg}")],
                    details={"error": error_msg, "raw_result": str(result), "host": host, "port": port}
                )
                
        except Exception as e:
            error_msg = f"""## ❌ 远程文件读取失败

**主机**: `{host}:{port}`
**路径**: `{path}`
**错误信息**: {str(e)}
"""
            return AgentToolResult(
                content=[TextContent(type="text", text=error_msg)],
                details={"error": str(e), "host": host, "port": port, "path": path}
            )