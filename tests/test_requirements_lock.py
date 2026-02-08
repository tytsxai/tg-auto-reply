from __future__ import annotations

from pathlib import Path


def test_requirements_lock_uses_exact_pins():
    lock_file = Path(__file__).resolve().parent.parent / "requirements.lock"
    lines = lock_file.read_text(encoding="utf-8").splitlines()

    deps = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    assert deps, "requirements.lock 不应为空"

    for dep in deps:
        assert "==" in dep, f"锁文件依赖必须使用精确版本: {dep}"
        assert "-e " not in dep, f"锁文件不应包含可编辑依赖: {dep}"
        assert " @ file://" not in dep, f"锁文件不应包含本地路径依赖: {dep}"

