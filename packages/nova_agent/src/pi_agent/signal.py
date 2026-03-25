import asyncio
from typing import Optional

class AbortSignal:
    """极简中断信号类 - 只有状态，没有waiters"""
    
    def __init__(self, name: str = ""):
        self.name = name
        self._aborted = False
    
    @property
    def aborted(self):
        """是否被中断（只读属性）"""
        return self._aborted
    
    def set(self):
        """触发中断"""
        self._aborted = True
    
    def clear(self):
        """清除中断状态"""
        self._aborted = False
    
    def reset(self):
        """重置中断信号（同clear）"""
        self.clear()
    
    def __bool__(self):
        """可以直接用 if signal: 判断是否中断"""
        return self._aborted
    
    def __repr__(self):
        status = "ABORTED" if self._aborted else "NORMAL"
        return f"<AbortSignal {self.name}: {status}>"
