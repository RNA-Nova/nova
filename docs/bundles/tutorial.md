# 完整教程：从零写一个 bundle

目标：写一个 `nova-weather` 包——一个 `weather` 工具（查天气，演示 UI 交互与降级）、一个扩展（注册 `/weather` 命令 + footer 状态）、一个 `weather_assistant` agent 组合、一个 TUI 渲染器。全程可运行验证。

## 0. 准备

```bash
mkdir nova-weather && cd nova-weather
nova-pkg init        # 可选：生成 [tool.nova] 脚手架（我们先手工建结构）
```

## 1. 骨架与清单

```
nova-weather/
├── pyproject.toml
├── agents/weather_assistant.yaml
├── backend/
│   ├── personas/weather.md
│   ├── tools/weather.py
│   └── extensions/weather_ext.py
└── frontend/tui/tools/weather.ts
```

```toml
# pyproject.toml
[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

[tool.poetry]
name = "nova-weather"
version = "0.1.0"
description = "天气查询 bundle（教程示例）"
authors = ["you <you@example.com>"]

[tool.poetry.dependencies]
python = ">=3.12,<3.14"

[tool.nova]
requires = ["nova-base"]          # 用 UI 原语糖库
agents = ["./agents/"]
tools = ["./backend/tools/weather.py"]
extensions = ["./backend/extensions/weather_ext.py"]
personas = ["./backend/personas/"]
```

## 2. 工具（`backend/tools/weather.py`）

```python
"""weather 工具——演示：UI 交互（confirm）+ headless 降级 + 错误人话。"""

from typing import Any, Dict, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_base.ui_primitives import confirm

from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

_FAKE_DATA = {  # 教程用假数据；真实包换成你的 API 调用
    "北京": ("晴", 31), "上海": ("多云", 29), "深圳": ("雷阵雨", 27),
}


class Tool:
    name = "weather"
    description = "查询城市天气。仅支持中国主要城市；城市不在支持列表时返回错误并列出可用城市。"
    prompt_snippet = "查询城市天气"
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名（如 北京）"},
            "advice": {"type": "boolean", "default": False,
                       "description": "是否附带出行建议"},
        },
        "required": ["city"],
    }

    def __init__(self, context: ToolContext):
        self._context = context

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ):
        if signal is not None and signal.aborted:
            return AgentToolResult(
                content=[TextContent(type="text", text="Operation aborted")],
                details={"error": "aborted"}, is_error=True,
            )

        city = str(params.get("city", "")).strip()
        if city not in _FAKE_DATA:
            return AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=f"暂不支持城市「{city}」。可用：{', '.join(_FAKE_DATA)}",
                )],
                details={"error": "unsupported_city", "city": city,
                         "available": list(_FAKE_DATA)},
                is_error=True,
            )

        sky, temp = _FAKE_DATA[city]
        advice = ""
        if params.get("advice"):
            # 附建议前要征得用户同意（演示 UI 交互 + headless 降级）
            if ctx.has_ui and await confirm(ctx.ui, "出行建议", f"为{city}生成出行建议？"):
                advice = "；出门记得带伞" if "雨" in sky else "；天气不错，适合出行"
            elif not ctx.has_ui:
                advice = "（headless 模式跳过建议确认）"

        text = f"{city}：{sky}，{temp}°C{advice}"
        return AgentToolResult(
            content=[TextContent(type="text", text=text)],
            details={"city": city, "sky": sky, "temp": temp, "advice": bool(advice)},
        )
```

## 3. 扩展（`backend/extensions/weather_ext.py`）

```python
"""weather 扩展：/weather 命令 + footer 状态段。"""


def extension(nova):
    async def _weather(args, ctx):
        city = args.strip() or "北京"
        # 命令上下文经 send_message 投递用户消息，让模型调 weather 工具
        await ctx.send_user_message(f"查一下{city}的天气，给出行建议")

    nova.registerCommand("weather", {
        "description": "查天气: /weather <城市>",
        "handler": _weather,
    })

    async def on_session_start(event, ctx):
        if ctx.has_ui:
            await ctx.ui.notify("set_status", {"key": "weather", "text": "☀ weather"})

    nova.on("session_start", on_session_start)
```

## 4. 人格与 agent 组合

```markdown
<!-- backend/personas/weather.md -->
你是天气助手。用户问天气时用 weather 工具查询并如实转述；
城市不支持时如实告知可用城市，不编造数据。
```

```yaml
# agents/weather_assistant.yaml
name: weather_assistant
version: "0.1.0"
description: 天气助手（教程示例）
author: you

persona:
  - ../backend/personas/weather.md

tools:
  - weather
extensions:
  - weather_ext
commands:
  - weather
  - help
```

## 5. TUI 渲染器（`frontend/tui/tools/weather.ts`）

```ts
import { Container, Text, type Component } from '@earendil-works/pi-tui';
import { detailsOf, type RendererInput } from 'nova-tui';

export default function renderWeather(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const bad = (s: string) => colors?.error?.(s) ?? s;
  const accent = (s: string) => colors?.accent?.(s) ?? s;

  const c = new Container();
  if (typeof d.error === 'string' && d.error) {
    c.addChild(new Text(bad(`城市不支持: ${String(d.city ?? '')}（可用: ${(d.available as string[])?.join(', ') ?? ''}）`), 1, 0));
    return c;
  }
  c.addChild(new Text(accent(`${String(d.city ?? '')} ${String(d.sky ?? '')} ${String(d.temp ?? '')}°C`), 1, 0));
  return c;
}
```

## 6. 本地验证

```bash
# 校验结构
nova-pkg validate ./nova-weather

# 装进 user 级（path 源 + editable 开发态原地引用）
nova-pkg install --editable path:./nova-weather

# 确认登记与资源解析
nova-pkg list
```

启动 `nova` → `/agent weather_assistant` → 输入"深圳天气怎么样，给我建议"：

- 模型调 `weather` 工具 → 弹确认（confirm 原语）→ 卡片显示渲染器输出（`深圳 雷阵雨 27°C`，主题色）；
- `/weather 上海` 走扩展命令；
- footer 出现 `☀ weather` 状态段；
- headless 验证降级：`nova-harness run weather_assistant --task "北京天气"`（print 模式——confirm 被跳过，结果带"headless 模式跳过建议确认"）。

## 7. 测试

```bash
mkdir -p backend/tests/tools
```

```python
# backend/tests/tools/test_weather.py
import pytest
from nova_harness.core.types.resources.tools import NULL_TOOL_EXEC_CONTEXT, ToolContext

from tools.weather import Tool  # 按你的加载方式调整 import 路径


@pytest.mark.asyncio
async def test_known_city(tmp_path):
    tool = Tool(ToolContext(cwd=str(tmp_path), settings=None))
    result = await tool.execute("t1", {"city": "北京"}, None, None, NULL_TOOL_EXEC_CONTEXT)
    assert not result.is_error
    assert "北京" in result.content[0].text


@pytest.mark.asyncio
async def test_unknown_city_lists_available(tmp_path):
    tool = Tool(ToolContext(cwd=str(tmp_path), settings=None))
    result = await tool.execute("t2", {"city": "火星"}, None, None, NULL_TOOL_EXEC_CONTEXT)
    assert result.is_error
    assert "北京" in result.content[0].text  # 错误文本带可用清单（模型能自我纠正）
```

## 8. 发布

- 开源：`git tag v0.1.0` 推到 GitHub → 用户 `nova-pkg install git:github.com/you/nova-weather`；
- 公开：发 npm → `nova-pkg install npm:nova-weather`；
- 细节见[分发与发布](distribution.md)。

## 常见坑（教程之外的真实经验）

1. 装了没生效 → `/reload`；还没生效 → `nova-pkg list` 看资源是否被解析（过滤链三关）；
2. 前端渲染器没上线 → `/debug` 看诊断（多半是 `frontend/package.json` 缺运行时依赖声明或 jiti 加载报错）；
3. headless 下工具卡住 → 你大概率没判 `ctx.has_ui` 就等了 UI 响应（NoOp 会安全降级返回空，但你的逻辑要处理空）；
4. 工具不被模型调用 → `description` 没写清使用边界，或 agent yaml 的 `tools:` 名单没放它。
