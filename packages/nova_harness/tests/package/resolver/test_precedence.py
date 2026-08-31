"""测试 resolver 元数据与优先级。"""

import pytest

from nova_harness.core.types.package import (
    PathMetadata,
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)
from nova_harness.package.resolve.resolver import (
    resource_precedence_rank,
    sort_resolved_resources,
)


@pytest.mark.parametrize(
    "metadata,expected_rank",
    [
        # project settings highest
        (PathMetadata("local", SourceScope.PROJECT, SourceOrigin.TOP_LEVEL), 0),
        # project auto
        (PathMetadata("auto", SourceScope.PROJECT, SourceOrigin.TOP_LEVEL), 1),
        # user settings
        (PathMetadata("local", SourceScope.USER, SourceOrigin.TOP_LEVEL), 2),
        # user auto
        (PathMetadata("auto", SourceScope.USER, SourceOrigin.TOP_LEVEL), 3),
        # package lowest
        (PathMetadata("git:x", SourceScope.USER, SourceOrigin.PACKAGE), 4),
    ],
)
def test_resource_precedence_rank(metadata: PathMetadata, expected_rank: int) -> None:
    assert resource_precedence_rank(metadata) == expected_rank


def test_sort_resolved_resources() -> None:
    resources = [
        ResolvedResource(
            "/a", True, PathMetadata("auto", SourceScope.USER, SourceOrigin.TOP_LEVEL)
        ),
        ResolvedResource(
            "/b",
            True,
            PathMetadata("local", SourceScope.PROJECT, SourceOrigin.TOP_LEVEL),
        ),
        ResolvedResource(
            "/c", True, PathMetadata("git:x", SourceScope.USER, SourceOrigin.PACKAGE)
        ),
    ]
    sorted_paths = [r.path for r in sort_resolved_resources(resources)]
    assert sorted_paths == ["/b", "/a", "/c"]
