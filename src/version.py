"""版本号管理 - 从 pyproject.toml 读取版本。"""

import tomllib
from pathlib import Path

_version: str | None = None


def get_version() -> str:
    """获取项目版本号。"""
    global _version
    if _version is not None:
        return _version
    
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    if pyproject.exists():
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
            _version = data.get("project", {}).get("version", "unknown")
    else:
        _version = "unknown"
    return _version


__version__ = get_version()
