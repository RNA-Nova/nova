"""把官方扩展目录加入 import path，方便测试扩展模块。"""

import sys
from pathlib import Path

_EXTENSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "extensions"
if str(_EXTENSIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_EXTENSIONS_DIR))
