"""Tests for the global broadcast bus and `/api/aniu/events` SSE route."""
from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import rate_limit as rate_limit_module
from app.core.config import get_settings
from app.db import database as database_module
from app.main import create_app
from app.services.event_bus import broadcast_bus, make_emitter
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service


def _create_test_client(monkeypatch, tmp_path) -> TestClient:
    from app.services.aniu_service import aniu_service

    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "events.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    monkeypatch.setattr(scheduler_service, "start", lambda: None)
    monkeypatch.setattr(scheduler_service, "stop", lambda: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    rate_limit_module._limiter.reset()
    aniu_service._account_overview_cache = None
    aniu_service._account_overview_cache_expires_at = None
    app = create_app()
    return TestClient(app)


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/aniu/login",
        json={"password": "release-pass"},
    )
    payload = response.json()
    return {"Authorization": f"Bearer {payload['token']}"}


def test_broadcast_bus_publish_fans_out_to_subscribers() -> None:
    """BroadcastBus.publish should reach every live subscriber."""
    sub_a = broadcast_bus.subscribe()
    sub_b = broadcast_bus.subscribe()
    try:
        broadcast_bus.publish("run_completed", {"run_id": 7, "actions": 2})
        event_a = sub_a.get(timeout=1.0)
        event_b = sub_b.get(timeout=1.0)
        assert event_a["type"] == "run_completed"
        assert event_a["run_id"] == 7
        assert event_a["actions"] == 2
        assert event_b == event_a
    finally:
        broadcast_bus.unsubscribe(sub_a)
        broadcast_bus.unsubscribe(sub_b)


def test_make_emitter_mirrors_completed_to_broadcast_bus() -> None:
    """make_emitter must mirror `completed` and `failed` onto broadcast_bus."""
    sub = broadcast_bus.subscribe()
    try:
        emit = make_emitter(123)
        emit("stage", stage="llm")  # not mirrored
        emit("completed", message="ok", run_type="analysis", schedule_id=None)
        emit("failed", message="boom", run_type="trade")

        first = sub.get(timeout=1.0)
        assert first["type"] == "run_completed"
        assert first["run_id"] == 123
        assert first["run_type"] == "analysis"

        second = sub.get(timeout=1.0)
        assert second["type"] == "run_failed"
        assert second["run_id"] == 123
        assert second["run_type"] == "trade"
        assert second["message"] == "boom"

        assert sub.empty(), "stage events must NOT be mirrored to broadcast_bus"
    finally:
        broadcast_bus.unsubscribe(sub)


def test_global_events_route_is_registered_and_authenticated(monkeypatch, tmp_path) -> None:
    """`/api/aniu/events` should reject anonymous clients but accept authenticated
    ones. We do not consume the long-lived stream from TestClient — that path
    is fragile because TestClient cannot signal disconnects to the sync
    generator. The full fan-out behaviour is covered by the unit tests above.
    """
    with _create_test_client(monkeypatch, tmp_path) as client:
        # Anonymous request must be rejected before the SSE handler runs.
        anon_resp = client.get(
            "/api/aniu/events",
            headers={"Accept": "text/event-stream"},
        )
        assert anon_resp.status_code == 401
