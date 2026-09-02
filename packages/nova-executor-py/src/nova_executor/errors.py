"""nova-executor SDK 异常定义"""


class ExecutorError(Exception):
    """SDK 基础异常"""


class ConnectionError(ExecutorError):
    """连接错误"""


class AuthError(ExecutorError):
    """认证错误"""


class ProcessError(ExecutorError):
    """进程错误"""


class FileSystemError(ExecutorError):
    """文件系统错误"""


class TimeoutError(ExecutorError):
    """超时错误"""


class ProtocolError(ExecutorError):
    """协议错误"""
