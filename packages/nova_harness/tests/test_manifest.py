"""Tests for package manifest parsing."""

import os
import tempfile

from nova_harness.core.package.manifest import read_manifest


def test_binary_dependencies_parsed():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
            f.write("""
{
  "name": "demo",
  "nova": {
    "agents": ["./agents/x"],
    "binary_dependencies": {"rg": "ripgrep", "fd": "fd-find"}
  }
}
""")
        manifest = read_manifest(d)
        assert manifest.nova is not None
        assert manifest.nova.binary_dependencies == {"rg": "ripgrep", "fd": "fd-find"}
