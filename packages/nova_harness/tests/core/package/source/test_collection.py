"""PackageSourceCollection 单元测试。"""

from pathlib import Path

from nova_harness.core.package.source import (
    PackageSourceCollection,
    ResolvedScopedSources,
)


def test_collection_keeps_disjoint_sources():
    user_dir = "/user"
    project_dir = "/project"
    collection = PackageSourceCollection(user_dir, project_dir)

    user_sources = ["path:/user/a"]
    project_sources = ["path:/project/b"]

    result = collection.resolve(user_sources, project_sources)

    assert result.user == user_sources
    assert result.project == project_sources


def test_collection_project_wins_over_user():
    user_dir = "/user"
    project_dir = "/project"
    collection = PackageSourceCollection(user_dir, project_dir)

    user_sources = ["path:/project/shared"]
    project_sources = [{"source": "path:/project/shared", "editable": True}]

    result = collection.resolve(user_sources, project_sources)

    assert result.user == []
    assert result.project == project_sources


def test_collection_dedupes_within_user_scope():
    user_dir = "/user"
    project_dir = "/project"
    collection = PackageSourceCollection(user_dir, project_dir)

    user_sources = ["path:/user/pkg", "path:./pkg"]
    project_sources: list = []

    result = collection.resolve(user_sources, project_sources)

    # path:/user/pkg 与 path:./pkg 在 base_dir=/user 下解析到同一 identity，
    # 因此只保留第一个。
    assert len(result.user) == 1
    assert result.user[0] == "path:/user/pkg"
    assert result.project == []


def test_collection_resolves_git_identity_without_ref():
    user_dir = "/user"
    project_dir = "/project"
    collection = PackageSourceCollection(user_dir, project_dir)

    user_sources = ["git:github.com/org/repo@main"]
    project_sources = ["git:github.com/org/repo@v1.0"]

    result = collection.resolve(user_sources, project_sources)

    # git identity 忽略 ref，project 优先。
    assert result.user == []
    assert result.project == project_sources


def test_collection_returns_dataclass():
    collection = PackageSourceCollection("/a", "/b")
    result = collection.resolve([], [])
    assert isinstance(result, ResolvedScopedSources)
