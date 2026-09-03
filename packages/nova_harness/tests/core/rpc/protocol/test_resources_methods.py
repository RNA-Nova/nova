"""resources 域 RPC 方法测试：listPromptTemplates / listSkills。"""

from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

from nova_harness.core.rpc.protocol import MethodRegistry
from nova_harness.core.rpc.protocol.methods import resources as resources_methods
from nova_harness.core.rpc.protocol.methods.state import ServerState


class FakeResourceLoader:
    def get_prompts(self):
        return {
            "prompts": [
                SimpleNamespace(
                    name="refactor",
                    description="重构当前文件",
                    argument_hint="<path>",
                    source="package",
                    source_info=SimpleNamespace(
                        source="package", path="/pkg/prompts/refactor.md"
                    ),
                ),
                SimpleNamespace(
                    name="debug",
                    description="调试问题",
                    argument_hint=None,
                    source="user",
                    source_info=None,
                ),
            ],
            "diagnostics": [],
        }

    def get_skills(self):
        return {
            "skills": {
                "commit": SimpleNamespace(
                    name="commit",
                    description="生成提交信息",
                    file_path="/pkg/skills/commit/SKILL.md",
                    source_label="package",
                ),
            },
            "diagnostics": [],
        }


class FakeRuntime:
    def __init__(self, session):
        self.session = session


@pytest.fixture
def registry():
    session = SimpleNamespace(resource_loader=FakeResourceLoader())
    state = ServerState(ui_context=SimpleNamespace())
    state.set_runtime(FakeRuntime(session))
    reg = MethodRegistry()
    resources_methods.register(reg, state)
    return reg


async def _call(registry, method: str, params: Optional[Dict[str, Any]] = None):
    msg = SimpleNamespace(method=method, params=params or {}, id=1)
    resp = await registry.dispatch(msg)
    assert resp is not None
    return resp


def _result(resp) -> Dict[str, Any]:
    assert resp.error is None, f"unexpected error: {resp.error}"
    return resp.result


@pytest.mark.asyncio
async def test_list_prompt_templates(registry):
    result = _result(await _call(registry, "listPromptTemplates"))
    prompts = {p["name"]: p for p in result["prompts"]}
    assert set(prompts) == {"refactor", "debug"}
    assert prompts["refactor"]["argumentHint"] == "<path>"
    assert prompts["refactor"]["source"] == "package"
    # handler 额外透出的 path 不在 PromptTemplateInfo 契约内，归一后被剥离
    # （前端只消费 name/description/argumentHint/source）
    assert "path" not in prompts["refactor"]
    assert prompts["debug"]["argumentHint"] is None


@pytest.mark.asyncio
async def test_list_skills(registry):
    result = _result(await _call(registry, "listSkills"))
    assert result["skills"] == [
        {
            "name": "commit",
            "description": "生成提交信息",
            "filePath": "/pkg/skills/commit/SKILL.md",
            "sourceLabel": "package",
        }
    ]
