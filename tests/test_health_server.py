from __future__ import annotations

import importlib

import pytest
from aiohttp.test_utils import make_mocked_request


@pytest.mark.asyncio
async def test_health_ready_and_metrics(db_env, monkeypatch):
    db = db_env["db"]

    async with db.async_session() as session:
        session.add(db.User(telegram_id=7001, is_active=True))
        await session.commit()

    import src.monitoring.health as health

    health = importlib.reload(health)

    server = health.HealthServer("127.0.0.1", 8080, token=None)

    async def fake_db_check():
        return True, health.SCHEMA_VERSION

    monkeypatch.setattr(server, "_db_check", fake_db_check)

    ready_resp = await server._handle_ready(make_mocked_request("GET", "/readyz"))
    assert ready_resp.status == 200

    metrics_resp = await server._handle_metrics(make_mocked_request("GET", "/metrics"))
    assert "bot_pending_reply_tasks" in metrics_resp.text
    assert "bot_active_reply_tasks" in metrics_resp.text


@pytest.mark.asyncio
async def test_health_auth_required():
    import src.monitoring.health as health

    server = health.HealthServer("127.0.0.1", 8080, token="secret")

    with pytest.raises(health.web.HTTPUnauthorized):
        await server._handle_health(make_mocked_request("GET", "/healthz"))

    ok_resp = await server._handle_health(
        make_mocked_request("GET", "/healthz", headers={"X-Health-Token": "secret"})
    )
    assert ok_resp.status == 200


@pytest.mark.asyncio
async def test_health_server_start_stop(db_env):
    import src.monitoring.health as health

    server = health.HealthServer("127.0.0.1", 0, token=None)
    await server.start()
    await server.stop()
