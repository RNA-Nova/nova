import asyncio
from typing import Optional, Dict, Any, Set
from pi_agent import AgentToolResult, AbortSignal
from nova_ai import ImageContent, TextContent
from .remote_tool import RemoteTool

class RemoteCommandTool(RemoteTool):
    """远程命令执行工具 - 通过XML-RPC在远程主机执行命令"""
    
    def __init__(self,computex_manager):
        """
        初始化远程命令工具
        """
        super().__init__(
            name="execute_command",
            description="作为一个远程持续交互式接口，能够在远程服务器上运行多种计算机语言的命令，返回其标准输出和标准错误。远程端已预先完成 Conda 的初始化与激活，因此不需要再执行任何 Conda 构建相关的操作",
            parameters={
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["bash", "python"],
                        "description": "使用的脚本语言类型"
                    },
                    "command": {
                        "type": "string",
                        "description": "要执行的命令内容"
                    }
                },
                "required": ["language", "command"]
            },
            computex_manager=computex_manager
        )
        self.label = "远程命令执行"
    
    async def execute(self, tool_call_id: str, params: Dict[str, Any], 
                     signal: Optional[AbortSignal] = None,
                     on_update=None) -> AgentToolResult:
        """执行远程命令"""
        language = params["language"]
        command = params["command"]
        
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

            # 流式更新 - 开始执行
            if on_update:
                # 注意：on_update 可能是异步的
                on_update(AgentToolResult(
                    content=[TextContent(
                        type="text", 
                        text=f"正在远程主机 {host}:{port} 上使用 {language} 执行命令: {command}"
                    )],
                    details={"status": "executing", "host": host, "port": port}
                ))
            # 执行run方法，参数顺序是 (language, command)
            results = computex.run(language, command)
            
            # 解析返回结果 - 支持两种格式
            content_items = []
            
            if results and isinstance(results, list):
                for item in results:
                    if isinstance(item, dict):
                        item_type = item.get('type')
                        item_format = item.get('format')
                        item_content = item.get('content', '')
                        
                        if item_type == 'console' and item_format == 'output':
                            # 控制台输出 - 作为文本内容
                            content_items.append(
                                TextContent(
                                    type="text",
                                    text=item_content
                                )
                            )
                        
                        elif item_type == 'image' and item_format == 'base64':
                            # base64图片 - 作为图片内容
                            # 根据base64字符串判断mime类型，默认png
                            mime_type = "image/png"
                            if item_content.startswith("/9j/"):
                                mime_type = "image/jpeg"
                            elif item_content.startswith("iVBOR"):
                                mime_type = "image/png"
                            elif item_content.startswith("R0lGOD"):
                                mime_type = "image/gif"
                            
                            content_items.append(
                                ImageContent(
                                    type="image",
                                    data=item_content,
                                    mime_type=mime_type
                                )
                            )
            
            # 流式更新 - 命令完成
            if on_update:
                on_update(AgentToolResult(
                    content=[TextContent(
                        type="text", 
                        text=f"在主机 {host}:{port} 上的命令执行完成"
                    )],
                    details={"status": "completed", "host": host, "port": port}
                ))
            
            return AgentToolResult(
                content=content_items,
                details={
                    "raw_result": results,
                    "language": language,
                    "command": command,
                    "host": host,
                    "port": port
                }
            )
                
        except Exception as e:
            error_msg = f"## ❌ 远程命令执行失败\n\n**主机**: `{host}:{port}`\n**语言**: `{language}`\n**命令**: `{command}`\n**错误信息**: {str(e)}"
            
            if on_update:
                on_update(AgentToolResult(
                    content=[TextContent(type="text", text=error_msg)],
                    details={"status": "error", "error": str(e), "host": host, "port": port}
                ))
            
            return AgentToolResult(
                content=[TextContent(type="text", text=error_msg)],
                details={"error": str(e), "host": host, "port": port}
            )