"""Project Trust 模块。

负责项目级资源（``.nova/`` 配置、扩展、skills 等）的信任门控决策与持久化。
"""

from nova_harness.core.harness.project_trust.callback import (
    make_resolve_project_trust_callback,
)
from nova_harness.core.harness.project_trust.project_trust import (
    ResolveProjectTrustedOptions,
    get_project_trust_options,
    has_trust_requiring_project_resources,
    resolve_project_trusted,
)
from nova_harness.core.harness.project_trust.trust_store import ProjectTrustStore

__all__ = [
    "ProjectTrustStore",
    "ResolveProjectTrustedOptions",
    "get_project_trust_options",
    "has_trust_requiring_project_resources",
    "make_resolve_project_trust_callback",
    "resolve_project_trusted",
]
