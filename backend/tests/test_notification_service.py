from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import notification_service


class _FakeResponse:
    text = "ok"
    status_code = 200

    def raise_for_status(self) -> None:
        return None


def test_telegram_notification_uses_configured_http_proxy(monkeypatch) -> None:
    captured_client_kwargs: list[dict] = []
    captured_payloads: list[dict] = []

    class FakeClient:
        def __init__(self, **kwargs):
            captured_client_kwargs.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url, json):
            captured_payloads.append({"url": url, "json": json})
            return _FakeResponse()

    monkeypatch.setattr(notification_service.httpx, "Client", FakeClient)

    notification_service.send_telegram_trade_notification(
        bot_token="bot-token",
        chat_id="chat-id",
        http_proxy="127.0.0.1:7890",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "600000",
                "quantity": 100,
                "price_type": "MARKET",
            }
        ],
        run_id=3,
        trigger_source="manual",
    )

    assert captured_client_kwargs[0]["timeout"] == notification_service._TIMEOUT_SECONDS
    assert captured_client_kwargs[0]["proxy"] == "http://127.0.0.1:7890"
    assert captured_payloads[0]["url"].endswith("/botbot-token/sendMessage")
    assert captured_payloads[0]["json"]["chat_id"] == "chat-id"


def test_telegram_notification_direct_connects_without_http_proxy(monkeypatch) -> None:
    captured_client_kwargs: list[dict] = []

    class FakeClient:
        def __init__(self, **kwargs):
            captured_client_kwargs.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url, json):
            del url, json
            return _FakeResponse()

    monkeypatch.setattr(notification_service.httpx, "Client", FakeClient)

    notification_service.send_telegram_trade_notification(
        bot_token="bot-token",
        chat_id="chat-id",
        http_proxy=" ",
        trade_orders=[
            {
                "action": "SELL",
                "symbol": "600000",
                "quantity": 100,
                "price_type": "MARKET",
            }
        ],
        run_id=4,
        trigger_source="manual",
    )

    assert captured_client_kwargs[0] == {
        "timeout": notification_service._TIMEOUT_SECONDS,
    }
