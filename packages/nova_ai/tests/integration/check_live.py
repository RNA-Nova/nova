#!/usr/bin/env python3
"""内置 provider 全模型可用性巡检（live 运维面，不占测试套件名额）。

对每个**有凭据**的 provider 的全部模型发真实请求（基础 + 各思考级别），
报告制输出；任何模型不可用即退出码 1（供定时任务/CI 告警用）。

测试套件里与其对应的是 tests/integration/ 的特性契约 e2e（每 provider
只打一个探针模型）；全模型可用性是运维关注，故为脚本而非测试。

用法::

    pixi run -e dev check-live  # 全量巡检
"""

import argparse
import asyncio
import sys

from nova_protocol import Context, UserMessage

from nova_ai.stream_options import SimpleStreamOptions

from ._helpers.auth import build_manager, has_credential
from ._helpers.probes import E2E_PROVIDERS

_TIMEOUT_S = 120


async def _check_model(manager, provider_id: str, model) -> list:
    """单模型巡检：基础 + 每个声明支持的思考级别。"""
    cases = [("basic", SimpleStreamOptions())]
    level_map = model.thinking_level_map or {}
    from nova_protocol import ThinkingLevel

    key_to_level = {level.name.lower(): level for level in ThinkingLevel}
    for key, target in level_map.items():
        if key == "off" or target is None or key not in key_to_level:
            continue
        cases.append(
            (f"thinking:{key}", SimpleStreamOptions(reasoning=key_to_level[key]))
        )

    results = []
    ctx = Context(messages=[UserMessage(content="你好，1+1等于多少？一句话回答。")])
    for name, options in cases:
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                result = await manager.complete_simple(model, ctx, options)
            error = result.error_message
            text = "".join(c.text for c in result.content if c.type == "text")
            results.append((name, error is None and len(text) > 0, error or ""))
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
    return results


async def _run(provider_filter) -> int:
    if provider_filter and provider_filter not in E2E_PROVIDERS:
        print(f"未知 provider: {provider_filter}", file=sys.stderr)
        return 2
    manager = await build_manager()
    failures = 0
    checked = 0
    for provider_id in E2E_PROVIDERS:
        if provider_filter and provider_id != provider_filter:
            continue
        if not has_credential(provider_id):
            print(f"== {provider_id}: 无凭据，跳过")
            continue
        models = manager.get_models(provider_id)
        print(f"== {provider_id}（{len(models)} 模型）")
        for model in models:
            results = await _check_model(manager, provider_id, model)
            checked += len(results)
            bad = [r for r in results if not r[1]]
            status = "OK" if not bad else f"FAIL（{len(bad)}/{len(results)}）"
            print(f"  {model.id}: {status}")
            for name, ok, error in bad:
                print(f"    - {name}: {error[:160]}")
            failures += len(bad)
    print(f"\n巡检完成，检查 {checked} 例，失败 {failures} 例")
    if checked == 0:
        print("没有实际执行任何巡检用例", file=sys.stderr)
        return 2
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="内置 provider 全模型可用性巡检")
    parser.add_argument("--provider", help="只巡检指定 provider")
    args = parser.parse_args()
    return asyncio.run(_run(args.provider))


if __name__ == "__main__":
    raise SystemExit(main())
