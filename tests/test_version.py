"""版本模块测试。"""

from src.version import get_version, __version__


def test_get_version_returns_string():
    """版本号应为字符串。"""
    version = get_version()
    assert isinstance(version, str)
    assert version != "unknown"


def test_version_module_exports():
    """模块应导出 __version__。"""
    assert __version__ is not None
    assert isinstance(__version__, str)


def test_version_format():
    """版本号应符合语义化版本格式。"""
    version = get_version()
    parts = version.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])
