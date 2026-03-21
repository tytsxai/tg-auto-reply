# Changelog

All notable changes to this project will be documented in this file.

## [1.0.7] - 2026-03-22
- Strip query-string from `OPENAI_BASE_URL` in startup log to avoid leaking API keys.
- `check_alerts.sh`: silently exit 0 when `LOG_FILE` is unset or missing, preventing false cron alerts.
- `install_cron.sh`: forward `LOG_FILE` into cron env; use `python3` explicitly for cleanup job.
- `src/ai/chat.py`: document circuit-breaker thread-safety assumption.
- `src/client/manager.py`: add `CLIENT_RECONNECT_MAX_ATTEMPTS` cap; log attempt count on reconnect; fix memory leak in `stop_client` by using `pop`.
- `src/monitoring/health.py`: expose `bot_clients_online`, `bot_running_clients`, `ai_client_initialized` in `/readyz` payload.
- `src/utils/crypto.py`: add `Encryptor.reload()` for zero-downtime key rotation.
- `tests/test_coverage_boost.py`: add coverage tests for DB migration paths, circuit-breaker recovery, reconnect cap, and health-check error branches.

## [1.0.6] - 2026-02-12
- Added `scripts/ready_check.py` for production preflight checks (including strict DB/schema mode).
- Hardened backup/restore workflow: backup now fails by default when DB file is missing; restore now refuses to run when instance lock is held.
- Added startup-time Fernet key format validation for `ENCRYPTION_KEY` / `ENCRYPTION_KEY_FILE` in production.
- Improved shutdown robustness for async log worker and client stop path to reduce stuck shutdown risk.
- Refreshed operations documentation (`README`, `docs/API.md`, `docs/DEVELOPMENT.md`, `docs/READY_CHECKLIST.md`, `docs/USER_GUIDE.md`) to match current production behavior.

## [1.0.5] - 2025-12-31
- Removed markdown markers from bot replies to prevent formatting syntax leaks.
- Polished login prompt copy for clearer instructions.

## [1.0.4] - 2025-12-31
- Ensure SQLite database directory exists before engine initialization to avoid startup failures.
- Make migration script self-contained by adding project root to PYTHONPATH.

## [1.0.3] - 2025-12-30
- Added missing indexes for message logs and contact lists to improve long-term performance.
- Migration now supports upgrading existing databases to include the new indexes.
- Enforced production access control and safer healthcheck exposure defaults.
- Added a single-instance lock for SQLite in production to avoid multi-process conflicts.
- Improved reply task lifecycle (cancel on stop/logout/login, skip when hosting is inactive).
- Added login timeout cleanup to avoid stale sessions.

## [1.0.2] - 2025-12-30
- Added graceful shutdown wait for reply tasks with configurable timeout.
- Made log file setup resilient by creating parent directories when needed.
- Installation script now prefers requirements.lock for reproducible installs.
- Exposed active reply task metric for monitoring.

## [1.0.1] - 2025-12-30
- Added HTTP healthcheck/metrics endpoints with optional token auth.
- Added schema version enforcement and a migration helper script.
- Fixed 2FA login flow and improved error handling in handlers.
- Added comprehensive tests with coverage gate (>=80%).
- Expanded operations/docs and locked dependencies for production.
