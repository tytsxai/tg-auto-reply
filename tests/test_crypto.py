from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet


def test_encrypt_decrypt_roundtrip(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setenv("ENVIRONMENT", "development")

    import src.utils.crypto as crypto

    crypto = importlib.reload(crypto)

    secret = "hello"
    encrypted = crypto.encryptor.encrypt(secret)
    assert crypto.encryptor.decrypt(encrypted) == secret


def test_production_requires_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(tmp_path / "missing.key"))

    import src.utils.crypto as crypto

    with pytest.raises(RuntimeError):
        importlib.reload(crypto)


def test_key_file_creation(monkeypatch, tmp_path):
    key_path = tmp_path / "keyfile.key"
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(key_path))

    import src.utils.crypto as crypto

    crypto = importlib.reload(crypto)
    assert key_path.exists()
    assert crypto.encryptor.decrypt(crypto.encryptor.encrypt("ok")) == "ok"


def test_key_file_read(monkeypatch, tmp_path):
    key_path = tmp_path / "keyfile.key"
    key = Fernet.generate_key()
    key_path.write_bytes(key + b"\n")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(key_path))

    import src.utils.crypto as crypto

    crypto = importlib.reload(crypto)
    assert crypto.get_or_create_key() == key


def test_read_key_file_oserror(monkeypatch, tmp_path):
    """测试读取密钥文件时发生 OSError 的情况"""
    from unittest.mock import patch
    key_path = tmp_path / "keyfile.key"
    key_path.write_bytes(b"dummy")

    import src.utils.crypto as crypto

    with patch.object(type(key_path), "read_bytes", side_effect=OSError("mock")):
        result = crypto._read_key_from_file(key_path)
    assert result is None


def test_write_key_file_chmod_oserror(monkeypatch, tmp_path):
    """测试写入密钥文件时 chmod 失败的情况"""
    import src.utils.crypto as crypto

    key_path = tmp_path / "newkey.key"
    key = Fernet.generate_key()

    def raise_oserror(path, mode):
        raise OSError("mock chmod error")

    monkeypatch.setattr("os.chmod", raise_oserror)
    crypto._write_key_to_file(key_path, key)
    assert key_path.exists()
