from typing import Optional, Dict, Any
from abc import ABC
from pi_agent import AgentTool
from ..computex import ComputexManager

class RemoteTool(AgentTool[Dict[str, Any],Any], ABC):
    """远程工具基类 - 自动处理host和port参数，管理XML-RPC连接"""
    
    def __init__(self, name: str, description: str, parameters: dict, computex_manager: ComputexManager):
        """
        初始化远程工具
        
        Args:
            name: 工具名称
            description: 工具描述
            parameters: 工具参数定义（不包含host和port）
            computex_manager: 工作机集群管理器
        """
        self.computex_manager = computex_manager
        
        super().__init__(
            name=name,
            description=description,
            parameters=parameters
        )
    
    def get_computex(self):
        """
        获取远程连接的主机
        """
        return self.computex_manager.get_proxy()
    
    def get_host(self):
        return self.computex_manager.get_current_host()
    
    def get_port(self):
        return self.computex_manager.get_current_port()