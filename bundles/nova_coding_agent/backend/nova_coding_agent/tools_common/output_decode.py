"""流式输出的编码容错解码（Windows OEM 代码页回退）。

背景（Windows 实机实证）：bash 引擎的子进程是 MSYS2（UTF-8），但它调起的
cmd/powershell/query 等 Windows 原生控制台程序按系统 OEM 代码页（中文
Windows = GBK/936）写字节。一律按 UTF-8 解码（pi 同款现状）必出乱码
（`版本`→`汾`）。

策略：**UTF-8 优先 + 整块回退 + 回退后粘滞**——逐块先走 UTF-8 增量解码
（strict 探测；跨块的半个多字节字符由增量状态承接）；整块非法时冲刷待定
残余、回退系统 OEM 代码页，**此后本流保持 OEM**（一条流的产出者同源同码
页——粘滞消除"GBK 字节碰巧是合法 UTF-8"（`E6B1BE`→汾）的反复横跳，
那是实机观测到的乱码形态）。真实输出按程序成块（bash 自身 UTF-8 / 原生
程序 OEM），块粒度判定 + 流级粘滞足够；ASCII 段两种解码一致。

POSIX 保持旧行为（UTF-8 + replace）——GBK 文件在 Linux 上的乱码是既有
状态，不在本次范围。
"""

from __future__ import annotations

import codecs
import sys
from typing import Any, Optional


def system_oem_codec() -> Optional[str]:
    """系统 OEM 代码页的 codec 名（``cp936`` 等）；非 Windows 返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        cp = ctypes.windll.kernel32.GetOEMCP()  # type: ignore[attr-defined]
        if cp == 65001:  # 系统已是 UTF-8——无需回退
            return None
        return f"cp{cp}"
    except Exception:
        return "mbcs"  # 兜底：ANSI 代码页


class StreamDecoder:
    """逐流增量解码器：UTF-8 优先，整块失败回退 *fallback* 编码。

    ``fallback=None`` 时等同旧行为（UTF-8 + errors=replace）。
    """

    def __init__(self, fallback: Optional[str] = None) -> None:
        self._oem: Optional[Any] = (
            codecs.getincrementaldecoder(fallback)(errors="replace")
            if fallback
            else None
        )
        # 有回退：strict 探测（非法整块抛错转 OEM）；无回退：replace（旧行为）
        self._utf8 = codecs.getincrementaldecoder("utf-8")(
            errors="strict" if fallback else "replace"
        )
        # 回退后本流粘滞 OEM（产出者同源同码页——杜绝反复横跳）
        self._oem_active = False

    def decode(self, data: bytes) -> str:
        if self._oem is None:
            return self._utf8.decode(data)
        if self._oem_active:
            return self._oem.decode(data)
        try:
            return self._utf8.decode(data)
        except UnicodeDecodeError:
            # 冲刷 UTF-8 待定残余（半个序列按替换符落出），整块走 OEM
            pending = self._flush_utf8()
            self._oem_active = True
            return pending + self._oem.decode(data)

    def flush(self) -> str:
        """流结束：冲刷残余（等价 decode(b"", final=True) 语义）。"""
        if self._oem is None:
            return self._utf8.decode(b"", True)
        pending = self._flush_utf8()
        return pending + self._oem.decode(b"", True)

    def _flush_utf8(self) -> str:
        """冲刷 UTF-8 待定残余并复位（strict 解码器的收尾）。"""
        try:
            return self._utf8.decode(b"", True)
        except UnicodeDecodeError:
            self._utf8.reset()
            return ""  # 半个多字节序列——丢弃，由 OEM 侧承接当前块


__all__ = ["StreamDecoder", "system_oem_codec"]
