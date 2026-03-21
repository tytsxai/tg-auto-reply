"""加密工具 - 安全存储用户凭证"""

import logging
import os
from pathlib import Path
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)
DEFAULT_KEY_PATH = Path("data/encryption.key")


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() in {"prod", "production"}


def _read_key_from_file(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        return path.read_bytes().strip()
    except OSError:
        logger.warning("无法读取加密密钥文件: %s", path)
        return None


def _write_key_to_file(path: Path, key: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key + b"\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("无法设置加密密钥文件权限: %s", path)


def get_or_create_key() -> bytes:
    key = os.getenv("ENCRYPTION_KEY")
    if key:
        return key.encode()

    key_path = Path(os.getenv("ENCRYPTION_KEY_FILE", str(DEFAULT_KEY_PATH)))
    existing = _read_key_from_file(key_path)
    if existing:
        return existing

    if _is_production():
        raise RuntimeError(
            "生产环境必须设置 ENCRYPTION_KEY 或 ENCRYPTION_KEY_FILE，禁止自动生成密钥。"
        )

    new_key = Fernet.generate_key()
    _write_key_to_file(key_path, new_key)
    logger.warning(
        "未设置 ENCRYPTION_KEY，已生成本地密钥文件: %s。请妥善备份并设置 ENCRYPTION_KEY。",
        key_path,
    )
    return new_key


class Encryptor:
    """加密器"""

    def __init__(self):
        self._fernet = Fernet(get_or_create_key())

    def reload(self) -> None:
        """重新从环境变量/密钥文件加载密钥，用于密钥轮换后的热更新。

        调用前须先更新 ENCRYPTION_KEY 环境变量或密钥文件，
        热更新期间若有并发加解密操作可能短暂失败，请在低峰期执行。
        """
        self._fernet = Fernet(get_or_create_key())
        logger.info("Encryptor 密钥已热更新")

    def encrypt(self, data: str) -> str:
        """加密字符串"""
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        """解密字符串"""
        return self._fernet.decrypt(encrypted.encode()).decode()


# 全局实例
encryptor = Encryptor()
