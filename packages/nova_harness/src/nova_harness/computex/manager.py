from typing import Optional, Dict
import xmlrpc.client


class ComputexManager:
    """XML-RPC代理连接管理器 - 管理单个当前连接"""
    
    def __init__(self):
        """初始化代理管理器"""
        self._proxies: Dict[str, xmlrpc.client.ServerProxy] = {}
        self._current_proxy: Optional[xmlrpc.client.ServerProxy] = None
        self._current_host: Optional[str] = None
        self._current_port: Optional[int] = None
    
    def _get_proxy_key(self, host: str, port: int) -> str:
        """生成代理连接的缓存键"""
        return f"{host}:{port}"
    
    def regist(self, host: str, port: int) -> None:
        """
        注册一个XML-RPC代理实例到缓存中
        
        Args:
            host: 远程主机地址
            port: 远程主机端口
        """
        key = self._get_proxy_key(host, port)
        if key not in self._proxies:
            url = f'http://{host}:{port}'
            Computex = xmlrpc.client.ServerProxy(url, allow_none=True)
            # 初始化连接，加载bash配置
            try:
                Computex.run("bash", "source ~/.bashrc")
            except Exception as e:
                # 初始化失败时，记录错误但继续
                print(f"警告：初始化远程主机 {host}:{port} 失败: {e}")
            self._proxies[key] = Computex
    
    def set_proxy(self, host: str, port: int) -> None:
        """
        设置当前使用的代理
        
        Args:
            host: 远程主机地址
            port: 远程主机端口
        """
        key = self._get_proxy_key(host, port)
        if key not in self._proxies:
            # 如果未注册，先注册
            self.regist(host, port)
        
        self._current_proxy = self._proxies[key]
        self._current_host = host
        self._current_port = port
    
    def get_proxy(self) -> xmlrpc.client.ServerProxy:
        """
        获取当前代理实例
        
        Returns:
            当前的XML-RPC代理对象
            
        Raises:
            RuntimeError: 如果没有设置当前代理
        """
        if self._current_proxy is None:
            raise RuntimeError("未设置当前代理，请先调用 set() 方法")
        return self._current_proxy
    
    def get_current_host(self) -> str:
        """
        获取当前主机地址
        
        Returns:
            当前主机地址
            
        Raises:
            RuntimeError: 如果没有设置当前代理
        """
        if self._current_host is None:
            raise RuntimeError("未设置当前代理，请先调用 set() 方法")
        return self._current_host
    
    def get_current_port(self) -> int:
        """
        获取当前端口
        
        Returns:
            当前端口
            
        Raises:
            RuntimeError: 如果没有设置当前代理
        """
        if self._current_port is None:
            raise RuntimeError("未设置当前代理，请先调用 set() 方法")
        return self._current_port
    
    def clear_cache(self, host: Optional[str] = None, port: Optional[int] = None):
        """
        清除代理缓存
        
        Args:
            host: 指定主机，为None时清除所有缓存
            port: 指定端口，当host指定时有效
        """
        if host is None:
            self._proxies.clear()
        else:
            key = self._get_proxy_key(host, port or 50001)
            self._proxies.pop(key, None)
    
    def has_registered(self, host: str, port: int) -> bool:
        """
        检查指定主机是否已注册
        
        Args:
            host: 远程主机地址
            port: 远程主机端口
            
        Returns:
            是否已注册
        """
        key = self._get_proxy_key(host, port)
        return key in self._proxies