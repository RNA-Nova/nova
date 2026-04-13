from .command import RemoteCommandTool
from .skill_tool import RemoteSkillTool
from .write import RemoteWriteTool
from .read import RemoteReadTool
from .send import SendTool

__all__ = [
    "RemoteCommandTool",
    "RemoteSkillTool",
    "RemoteWriteTool",
    "RemoteReadTool"
]

def create_all_tools(computex_manager):
    return {
        'execute_command':RemoteCommandTool(computex_manager),
        'skill_tool':RemoteSkillTool(computex_manager),
        'write':RemoteWriteTool(computex_manager),
        'read':RemoteReadTool(computex_manager),
        'send_to_frontend':SendTool(),
    }