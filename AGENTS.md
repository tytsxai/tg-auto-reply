# AGENTS.md

> 本文件是项目骨架与职责边界的快速索引，面向维护者与值班同学。

## 目录结构（关键路径）

```text
.
├── main.py                      # 进程入口：配置校验、启动/停机编排、信号处理
├── src/
│   ├── ai/chat.py               # AI 调用、超时重试、熔断器
│   ├── bot/handlers.py          # Bot 命令、消息调度、并发与日志批处理
│   ├── client/manager.py        # Telethon 客户端生命周期与重连
│   ├── db/database.py           # DB 引擎、schema 版本检查与迁移
│   ├── db/models.py             # ORM 数据模型（用户/凭证/设置/日志/名单）
│   ├── monitoring/health.py     # /healthz /readyz /metrics 监控端点
│   └── utils/crypto.py          # 凭证加解密与密钥管理
├── scripts/
│   ├── backup.sh                # SQLite 热备份 + 密钥备份
│   ├── restore.sh               # SQLite 恢复 + 恢复前快照 + 完整性校验
│   ├── migrate.py               # schema 迁移入口
│   ├── ready_check.py           # 生产预检（配置/依赖/schema 快速体检）
│   ├── cleanup_logs.py          # 历史日志清理
│   ├── check_alerts.sh          # 基于关键词的日志告警检查
│   └── install_cron.sh          # 定时备份/清理/告警任务安装
├── docs/
│   ├── READY_CHECKLIST.md       # 上线前硬性核对清单
│   ├── DEPLOYMENT.md            # 部署与回滚步骤
│   ├── API.md                   # 命令与监控接口说明
│   └── TROUBLESHOOTING.md       # 常见故障处理
└── tests/                       # 单元测试 + 脚本运行时测试
```

## 模块职责与边界

- `main.py`
  - 只做“编排”，不承载业务逻辑：读取环境、启动 bot、健康检查、优雅停机。
- `src/bot/handlers.py`
  - 只负责交互与调度：命令处理、回复队列、冷却窗口、日志入库。
  - 不直接管理 Telethon 生命周期，委托给 `client/manager.py`。
- `src/client/manager.py`
  - 只负责客户端生命周期：连接、监听、重连、停止。
  - 业务过滤与回复策略不放在此模块。
- `src/monitoring/health.py`
  - 只暴露监控视图：就绪性、指标聚合；不修改业务状态。
- `src/db/*`
  - `database.py` 管理引擎和 schema 版本；`models.py` 管理数据结构定义。

## 关键依赖关系

```text
main.py
  ├─> src.bot.handlers
  │     ├─> src.client.manager
  │     ├─> src.ai.chat
  │     └─> src.db (session/models)
  └─> src.monitoring.health
        ├─> src.bot.handlers (只读指标)
        ├─> src.client.manager (只读状态)
        └─> src.db (只读探针)
```

## 运行约束（必须遵守）

- 生产环境必须：
  - 设置访问控制（`ALLOWED_TELEGRAM_IDS` 或显式 `ALLOW_UNRESTRICTED_ACCESS=1`）
  - 固定加密密钥（`ENCRYPTION_KEY` 或 `ENCRYPTION_KEY_FILE`）
  - 单实例运行（建议 `INSTANCE_LOCK_FILE`）
- 任何 schema 升级前后都要执行并验证：
  - `python scripts/migrate.py`
  - `GET /readyz == 200`
- 任何上线前必须完成一次“备份 + 恢复演练”：
  - `./scripts/backup.sh`
  - `./scripts/restore.sh <db-backup> <key-backup>`

## 本次变更（2026-02-12）

- 新增 `scripts/restore.sh`：可执行恢复脚本（含完整性校验与恢复前快照）。
- 增强 `handlers.py` 日志可靠性观测：增加日志丢弃/失败/回退计数指标。
- 增强 `health.py` 就绪探针：当启用异步日志但 worker 异常时，`/readyz` 返回 503。
- 调整 `client/manager.py`：`UserClient.connect()` 连接异常改为向上抛出，由调用方区分“失效登录”与“瞬时故障”。
- 修复运行依赖：补齐 `greenlet`（SQLAlchemy asyncio 必需），避免生产环境运行时崩溃。
- 强化备份恢复脚本：`backup.sh` 默认在数据库文件缺失时失败并告警；`restore.sh` 增加实例锁占用检查，防止“服务运行中误恢复”。
- 新增 `scripts/ready_check.py`：上线前可执行预检，覆盖生产必填项、数字配置合法性、依赖与数据库可用性。
- 强化 `main.py` 生产密钥校验：启动阶段验证 `ENCRYPTION_KEY/ENCRYPTION_KEY_FILE` 的 Fernet 格式，避免运行中解密才失败。
- 强化 `handlers.py` 停机可靠性：日志 worker 异常/阻塞时可超时回收并统计丢弃量，避免停机卡死。
- 强化 `client/manager.py` 停止路径：`stop_client()` 对客户端 stop 异常进行兜底并确保监听任务被回收。
