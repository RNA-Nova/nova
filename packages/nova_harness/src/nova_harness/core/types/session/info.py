"""会话信息类型。"""

from datetime import datetime
from typing import Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import model_validator


class SessionInfo(NovaBaseModel):
    """会话信息"""

    path: str = ""
    id: str = ""
    cwd: str = ""
    name: Optional[str] = None
    parent_session_path: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    message_count: int = 0
    first_message: str = ""
    all_messages_text: str = ""

    @model_validator(mode="after")
    def set_default_datetimes(self):
        if self.created is None:
            self.created = datetime.now()
        if self.modified is None:
            self.modified = datetime.now()
        return self


__all__ = ["SessionInfo"]
