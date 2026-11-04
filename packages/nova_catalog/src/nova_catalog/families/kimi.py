"""Kimi K3 模型族的族级共享事实（对位 pi ``catalog/families/kimi.ts``）。

K3 及其点发布（k3 / k3-256k / kimi-for-coding / kimi-k3 ……）跨宿主
（kimi-coding / moonshotai / moonshotai-cn / 将来网关）共享同一份
事实——一处定义，全链同引：当前消费方为构建期
（``scripts/model_build`` 的 BUILD_EXTRAS）；运行期各家
corrections.py 如需族事实亦从此引，不另立副本。

注意：两侧现行协议同为 openai-completions（kimi-coding 已随 pi 切换，
与官方 Kimi Code 客户端同协议）；协议戳归各家出厂声明，不进族层。
"""

from typing import Dict, Optional

# K3 族 openai-completions 协议侧 compat（pi.dev 实测：K3 走 openai 格式
# + effort——不是 deepseek，k2.x 才是）；k2.x 的 deepseek 基线归各家出厂
K3_OPENAI_COMPAT: Dict[str, object] = {
    "thinking_format": "openai",
    "supports_reasoning_effort": True,
    "requires_reasoning_content_on_assistant_messages": True,
}

__all__ = ["K3_OPENAI_COMPAT"]
