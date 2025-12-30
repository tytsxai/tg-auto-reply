# Changelog

All notable changes to this project will be documented in this file.

## [1.0.1] - 2025-12-30
- Added HTTP healthcheck/metrics endpoints with optional token auth.
- Added schema version enforcement and a migration helper script.
- Fixed 2FA login flow and improved error handling in handlers.
- Added comprehensive tests with coverage gate (>=80%).
- Expanded operations/docs and locked dependencies for production.

## [1.0.2] - 2025-12-30
- Added graceful shutdown wait for reply tasks with configurable timeout.
- Made log file setup resilient by creating parent directories when needed.
- Installation script now prefers requirements.lock for reproducible installs.
- Exposed active reply task metric for monitoring.
