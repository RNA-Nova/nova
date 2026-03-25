import asyncio
from typing import Optional, Dict, Any
from pi_agent import AgentToolResult
from nova_ai import TextContent
from ..core import RemoteTool



class RemoteWriteTool(RemoteTool):
    """远程文件写入工具 - 通过XML-RPC在远程主机执行文件写入操作"""
    
    def __init__(self):
        """
        初始化远程文件写入工具
        """
        super().__init__(
            name="write",
            description="在远程服务器上写入文件内容。如果文件不存在则创建，存在则覆盖。自动创建父目录。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径（相对或绝对路径）"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的内容"
                    }
                },
                "required": ["path", "content"]
            }
        )
        self.label = "远程文件写入"  
    
    async def execute(self, tool_call_id: str, params: Dict[str, Any],
                     signal: Optional[asyncio.Event] = None,
                     on_update=None) -> AgentToolResult:
        """
        执行远程文件写入操作
        """
        # 获取连接参数（从基类自动添加的）
        host = params.get("host", "localhost")
        port = params.get("port", 50001)
        path = params.get("path")
        content = params.get("content")
        
        # 参数验证
        if not path or content is None:
            error_msg = "## ❌ 参数错误\n\n必须提供 path 和 content 参数"
            return AgentToolResult(
                content=[TextContent(type="text", text=error_msg)],
                details={"error": "Missing required parameters", "host": host, "port": port}
            )
        
        # 检查是否已中止
        if signal and hasattr(signal, 'is_set') and signal.is_set():
            return AgentToolResult(
                content=[TextContent(type="text", text="## ⚠️ 操作已中止")],
                details={"error": "Operation aborted", "host": host, "port": port}
            )
        
        # 流式更新 - 开始执行
        if on_update:
            on_update(AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=f"正在远程主机 {host}:{port} 上写入文件: {path}"
                )],
                details={"status": "executing", "host": host, "port": port, "path": path}
            ))
        
        try:
            # 使用基类提供的代理方法
            proxy = self._get_proxy(host, port)
            
            # 调用远程write方法
            result = proxy.write(path, content)
            
            # 解析返回结果
            if result and isinstance(result, dict):
                success = result.get("success", False)
                error = result.get("error")
                absolute_path = result.get("absolute_path", path)
                bytes_written = result.get("bytes_written", 0)
                operation = result.get("operation", "unknown")
                
                if success:
                    # 成功响应
                    success_msg = f"""## ✅ 远程文件写入成功

**主机**: `{host}:{port}`
**路径**: `{path}`
**绝对路径**: `{absolute_path}`
**大小**: {bytes_written} 字节
**操作**: {operation}

文件已成功写入远程主机。"""
                    
                    return AgentToolResult(
                        content=[TextContent(type="text", text=success_msg)],
                        details=result
                    )
                else:
                    # 远程服务器返回错误
                    error_msg = f"""## ❌ 远程文件写入失败

**主机**: `{host}:{port}`
**路径**: `{path}`
**错误信息**: {error or '未知错误'}
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
            error_msg = f"""## ❌ 远程文件写入失败

**主机**: `{host}:{port}`
**路径**: `{path}`
**错误信息**: {str(e)}

请检查：
1. 远程主机是否可访问（{host}:{port}）
2. XML-RPC服务是否正常运行
3. 网络连接是否正常
"""
            return AgentToolResult(
                content=[TextContent(type="text", text=error_msg)],
                details={"error": str(e), "host": host, "port": port, "path": path}
            )