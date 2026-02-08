# 生产就绪核对清单（上线前）

本清单只覆盖“现在不做，上线后高概率出事故”的必要项。

## 1) 配置与密钥

- [ ] `ENVIRONMENT=production`
- [ ] 已设置 `BOT_TOKEN`
- [ ] 已设置 `OPENAI_API_KEY`（或 `API_KEY`）
- [ ] 已设置 `ENCRYPTION_KEY` 或 `ENCRYPTION_KEY_FILE`
- [ ] 已设置 `ALLOWED_TELEGRAM_IDS`（若确需开放，显式 `ALLOW_UNRESTRICTED_ACCESS=1`）
- [ ] `LOG_LEVEL`、`LOG_FILE` 已配置（建议落地文件并轮转）
- [ ] 复核所有可选数值型环境变量：留空表示“走默认值”，不要写非法字符串

## 2) 数据与迁移

- [ ] 首次部署或升级后执行：`python scripts/migrate.py`
- [ ] 校验 schema：`GET /readyz` 返回 200
- [ ] 已执行一次备份演练：`./scripts/backup.sh /your/backup/path`
- [ ] 可恢复验证：至少验证可从备份文件读取表结构/关键数据

## 3) 单实例与并发

- [ ] 已设置 `INSTANCE_LOCK_FILE`（建议落在持久卷）
- [ ] 生产确认仅运行一个 polling 实例（避免 getUpdates 冲突）
- [ ] 并发参数已按预期设置：`MAX_CONCURRENT_REPLIES*`、`MAX_PENDING_REPLY_TASKS*`

## 4) 观测与告警

- [ ] 已启用健康检查端口（`HEALTHCHECK_PORT` 或 `ENABLE_HTTP_HEALTHCHECK=1`）
- [ ] 若健康检查对外暴露，已设置 `HEALTHCHECK_TOKEN`
- [ ] 已接入 `/metrics`（Prometheus 或等价采集）
- [ ] 已配置关键日志告警（可用 `scripts/check_alerts.sh`）

建议至少关注：
- `AI 回复失败`
- `回复队列已满`
- `database is locked`
- `terminated by other getUpdates`

## 5) 运行与回滚

- [ ] 已配置进程守护（systemd/docker restart policy）
- [ ] 已验证优雅停机（SIGTERM 后可正常退出）
- [ ] 明确回滚步骤：停止服务 → 恢复 `bot.db` 与密钥 → 回滚代码 → 重启

## 6) 验收冒烟

- [ ] `/start`、`/login`、`/status` 正常
- [ ] `/start_hosting` 后可自动回复
- [ ] `/stop_hosting` 后不再自动回复
- [ ] `/logs`、`/stats` 有可读结果
- [ ] `/readyz` 为 200，`/metrics` 可访问

