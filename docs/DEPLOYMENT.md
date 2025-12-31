# 生产部署指南

本指南补充 `OPERATIONS.md`，面向生产部署与升级流程。

## 依赖安装（锁定版本）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e .
```

如需开发/测试依赖：

```bash
pip install -e '.[dev]'
```

## 环境变量准备

1. 复制 `.env.example` 到 `.env`
2. 设置必填项：`BOT_TOKEN`、`OPENAI_API_KEY`（或 `API_KEY`）
3. 生产环境必须设置 `ENCRYPTION_KEY`（或 `ENCRYPTION_KEY_FILE`）
4. 生产环境必须设置 `ALLOWED_TELEGRAM_IDS`（如需开放访问，设置 `ALLOW_UNRESTRICTED_ACCESS=1`）
5. 需要健康检查时设置 `HEALTHCHECK_PORT`/`HEALTHCHECK_TOKEN`
6. 变更 `BOT_TOKEN` 或访问控制配置后需重启服务生效

## 数据库迁移

首次部署或更新版本后执行：

```bash
python scripts/migrate.py
```

## systemd（示例）

```
[Service]
WorkingDirectory=/opt/telegram-bot
EnvironmentFile=/opt/telegram-bot/.env
ExecStart=/opt/telegram-bot/.venv/bin/python /opt/telegram-bot/main.py
Restart=on-failure
```

## 回滚建议

- 停止服务
- 恢复 `data/bot.db` 与 `data/encryption.key`
- 回滚代码版本并重启
