from __future__ import annotations

import importlib
from logging.handlers import RotatingFileHandler
import pytest


def _reload_main_module():
    import main as main_module

    return importlib.reload(main_module)


_VALID_FERNET_KEY = "6rJY4PaAt9wwz2ZX4ioNmeQflxFbJ84xP40pTVF6RzQ="


@pytest.mark.parametrize("env_value", ["production", "prod"])
def test_require_encryption_key_accepts_valid_env_key(monkeypatch, env_value):
    monkeypatch.setenv("ENVIRONMENT", env_value)
    monkeypatch.setenv("ENCRYPTION_KEY", _VALID_FERNET_KEY)
    monkeypatch.delenv("ENCRYPTION_KEY_FILE", raising=False)

    main_module = _reload_main_module()
    main_module._require_encryption_key()


def test_require_encryption_key_rejects_invalid_env_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENCRYPTION_KEY", "invalid-key")
    monkeypatch.delenv("ENCRYPTION_KEY_FILE", raising=False)

    main_module = _reload_main_module()
    with pytest.raises(ValueError, match="ENCRYPTION_KEY 格式非法"):
        main_module._require_encryption_key()


def test_require_encryption_key_accepts_valid_key_file(monkeypatch, tmp_path):
    key_file = tmp_path / "encryption.key"
    key_file.write_text(_VALID_FERNET_KEY + "\n", encoding="utf-8")

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(key_file))

    main_module = _reload_main_module()
    main_module._require_encryption_key()


def test_require_encryption_key_rejects_empty_key_file(monkeypatch, tmp_path):
    key_file = tmp_path / "encryption.key"
    key_file.write_text("\n", encoding="utf-8")

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(key_file))

    main_module = _reload_main_module()
    with pytest.raises(ValueError, match="空文件"):
        main_module._require_encryption_key()


def test_require_encryption_key_ignored_in_non_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY_FILE", raising=False)

    main_module = _reload_main_module()
    main_module._require_encryption_key()


def test_log_file_uses_rotating_handler(monkeypatch, tmp_path):
    log_file = tmp_path / "logs" / "bot.log"
    monkeypatch.setenv("LOG_FILE", str(log_file))

    main_module = _reload_main_module()
    rotating_handlers = [
        handler for handler in main_module._handlers if isinstance(handler, RotatingFileHandler)
    ]
    assert rotating_handlers
    assert log_file.parent.exists()

    for handler in rotating_handlers:
        handler.close()
