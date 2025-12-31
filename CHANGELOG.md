# Changelog

All notable changes to this project will be documented in this file.

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
