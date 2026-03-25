import asyncio
from typing import Optional, Dict, Any, TypeVar, Generic
import xmlrpc.client
from abc import ABC, abstractmethod
from pi_agent import AgentTool, AgentToolResult
from nova_ai import ImageContent, TextContent


class RemoteTool(AgentTool[Dict[str, Any],Any], ABC):
    """远程工具基类 - 自动处理host和port参数，管理XML-RPC连接"""
    
    def __init__(self, name: str, description: str, parameters: dict):
        """
        初始化远程工具
        
        Args:
            name: 工具名称
            description: 工具描述
            parameters: 工具参数定义（不包含host和port）
        """
        # 确保参数定义中包含必要的字段
        if "properties" not in parameters:
            parameters["properties"] = {}
        
        # 强制添加host和port参数
        parameters["properties"]["host"] = {
            "type": "string",
            "description": "远程主机地址，可以是IP或域名",
        }
        parameters["properties"]["port"] = {
            "type": "integer",
            "description": "XML-RPC服务端口号",
            "minimum": 1,
            "maximum": 65535
        }
        
        # 更新required列表，如果存在的话
        if "required" in parameters and isinstance(parameters["required"], list):
            # 如果host不在required列表中，添加它
            if "host" not in parameters["required"]:
                parameters["required"].append("host")
            # 如果port不在required列表中，添加它
            if "port" not in parameters["required"]:
                parameters["required"].append("port")
        else:
            # 如果required不存在或不是列表，创建新的required列表
            parameters["required"] = ["host", "port"]
        
        super().__init__(
            name=name,
            description=description,
            parameters=parameters
        )
        self._proxies: Dict[str, xmlrpc.client.ServerProxy] = {}
    
    def _get_proxy_key(self, host: str, port: int) -> str:
        """生成代理连接的缓存键"""
        return f"{host}:{port}"
    
    def _get_proxy(self, host: str, port: int):
        """获取或创建XML-RPC代理"""
        key = self._get_proxy_key(host, port)
        
        if key not in self._proxies:
            url = f'http://{host}:{port}'
            proxy = xmlrpc.client.ServerProxy(url, allow_none=True)
            # 初始化连接，加载bash配置
            try:
                proxy.run("bash", "source ~/.bashrc")
            except Exception as e:
                # 初始化失败时，记录错误但继续
                print(f"警告：初始化远程主机 {host}:{port} 失败: {e}")
            self._proxies[key] = proxy
        
        return self._proxies[key]
    
    def clear_proxy_cache(self, host: Optional[str] = None, port: Optional[int] = None):
        """
        清除代理连接缓存
        
        Args:
            host: 指定主机，为None时清除所有缓存
            port: 指定端口，当host指定时有效
        """
        if host is None:
            self._proxies.clear()
        else:
            key = self._get_proxy_key(host, port or 50001)
            self._proxies.pop(key, None)