# 故障排查指南

## 登录与凭证

- **无法解密凭证**
  - 可能密钥丢失或更换。确认 `ENCRYPTION_KEY` 或 `ENCRYPTION_KEY_FILE` 是否一致。
  - 必要时重新 `/login`。

- **登录已失效**
  - Telegram 会话过期或被踢下线。
  - 重新 `/login` 并检查账号安全设置。

## AI 回复

- **AI 回复失败**
  - 检查 `OPENAI_API_KEY` 是否有效、网络是否通畅。
  - 观察日志 `AI 回复失败` 关键词。
  - 可临时 `ALLOW_START_WITHOUT_AI=1` 启动，避免服务中断。

## 数据库

- **database is locked**
  - 同一数据库被多实例写入。
  - 确保仅运行一个实例，必要时迁移到更强的数据库。

- **命令无响应 / 没有任何回复**
  - 确认正在与 `BOT_TOKEN` 对应的 Bot 私聊。
  - 确认发送的是命令（如 `/start`、`/login`），普通文本不会触发回复。
  - 确保服务只运行一个实例，避免 `Conflict: terminated by other getUpdates`。
  - 检查 `.env` 中 `BOT_TOKEN` 是否有效，并重启服务使配置生效。
  - 查看 `bot.log` 是否有报错信息。

- **出现 `terminated by other getUpdates`**
  - 表示有多个实例在拉取更新（Polling 冲突）。
  - 停止重复实例，并设置 `INSTANCE_LOCK_FILE` 防止多实例运行。

- **检测到已有实例在运行**
  - SQLite 启用了单实例锁。
  - 停止重复实例后重启服务。

- **schema 版本不一致**
  - 执行 `python scripts/migrate.py`。

## 监控/健康检查

- **/readyz 返回 503**
  - 检查数据库连接与 schema 版本。
  - 确认 `DATABASE_URL` 与 `HEALTHCHECK_*` 配置。

## 性能与队列

- **回复队列已满**
  - 调大 `MAX_PENDING_REPLY_TASKS` 或 `MAX_CONCURRENT_REPLIES`。
  - 减少 `AUTO_REPLY_COOLDOWN_SECONDS` 以降低重复触发。
