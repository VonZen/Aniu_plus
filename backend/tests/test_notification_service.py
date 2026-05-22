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


def _capture_payload(monkeypatch) -> list[dict]:
    """Install a fake httpx.Client that records every sendMessage payload."""
    captured: list[dict] = []

    class FakeClient:
        def __init__(self, **_kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url, json):
            captured.append({"url": url, "json": json})
            return _FakeResponse()

    monkeypatch.setattr(notification_service.httpx, "Client", FakeClient)
    return captured


def test_trade_message_renders_chinese_trigger_label_and_run_type(monkeypatch) -> None:
    """`trigger_source` and `run_type` should be displayed in Chinese."""
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_trade_notification(
        bot_token="bot",
        chat_id="chat",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "600000",
                "name": "浦发银行",
                "quantity": 100,
                "price_type": "MARKET",
                "status": "submitted",
            }
        ],
        run_id=42,
        trigger_source="schedule",
        run_type="trade",
        schedule_name="盘前分析",
    )

    text = captured[0]["json"]["text"]
    assert "来源: 定时" in text
    assert "类型: 交易任务" in text
    assert "盘前分析" in text
    # Status label is appended after the order line
    assert "已挂出" in text


def test_trade_message_html_escapes_user_supplied_strings(monkeypatch) -> None:
    """HTML special chars in symbol/name/schedule_name must not break parsing."""
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_trade_notification(
        bot_token="bot",
        chat_id="chat",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "<bad>",
                "name": "A&B <evil>",
                "quantity": 100,
                "price_type": "MARKET",
                "status": "submitted",
            }
        ],
        run_id=1,
        trigger_source="manual",
        schedule_name="<script>alert(1)</script>",
    )

    text = captured[0]["json"]["text"]
    # Raw angle brackets from user input must be escaped; only the markup
    # we emit ourselves (<b>, <a>) is allowed through.
    assert "<bad>" not in text
    assert "&lt;bad&gt;" in text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "A&amp;B" in text


def test_trade_message_includes_summary_line_for_multiple_orders(monkeypatch) -> None:
    """2+ orders should produce a 「本轮 N 笔」 summary line."""
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_trade_notification(
        bot_token="bot",
        chat_id="chat",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "600000",
                "quantity": 100,
                "price_type": "LIMIT",
                "price": 10.5,
                "status": "submitted",
            },
            {
                "action": "BUY",
                "symbol": "600001",
                "quantity": 200,
                "price_type": "LIMIT",
                "price": 5.0,
                "status": "submitted",
            },
            {
                "action": "SELL",
                "symbol": "000001",
                "quantity": 100,
                "price_type": "LIMIT",
                "price": 20.0,
                "status": "submitted",
            },
        ],
        run_id=1,
        trigger_source="manual",
    )

    text = captured[0]["json"]["text"]
    assert "本轮 3 笔" in text
    # Buy: 100 * 10.5 + 200 * 5.0 = 2050; Sell: 100 * 20.0 = 2000
    assert "买 2(¥2,050)" in text
    assert "卖 1(¥2,000)" in text


def test_trade_message_omits_summary_line_for_single_order(monkeypatch) -> None:
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_trade_notification(
        bot_token="bot",
        chat_id="chat",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "600000",
                "quantity": 100,
                "price_type": "MARKET",
                "status": "submitted",
            }
        ],
        run_id=1,
        trigger_source="manual",
    )

    text = captured[0]["json"]["text"]
    assert "本轮" not in text


def test_trade_message_appends_account_summary_block(monkeypatch) -> None:
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_trade_notification(
        bot_token="bot",
        chat_id="chat",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "600000",
                "quantity": 100,
                "price_type": "MARKET",
                "status": "submitted",
            }
        ],
        run_id=1,
        trigger_source="manual",
        account_summary={
            "total_assets": 105234.56,
            "cash_balance": 5234.56,
            "daily_profit": -123.45,
            "daily_return_ratio": -1.23,
        },
    )

    text = captured[0]["json"]["text"]
    assert "总资产 ¥105,234.56" in text
    assert "现金 ¥5,234.56" in text
    assert "当日盈亏 -¥123.45" in text
    assert "-1.23%" in text


def test_trade_message_appends_detail_url(monkeypatch) -> None:
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_trade_notification(
        bot_token="bot",
        chat_id="chat",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "600000",
                "quantity": 100,
                "price_type": "MARKET",
                "status": "submitted",
            }
        ],
        run_id=42,
        trigger_source="manual",
        detail_url="https://example.com/tasks?run=42",
    )

    text = captured[0]["json"]["text"]
    assert '<a href="https://example.com/tasks?run=42">查看详情</a>' in text


def test_trade_message_rejects_non_http_detail_url(monkeypatch) -> None:
    """Non-http(s) URLs must be silently dropped to prevent javascript: links."""
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_trade_notification(
        bot_token="bot",
        chat_id="chat",
        trade_orders=[
            {
                "action": "BUY",
                "symbol": "600000",
                "quantity": 100,
                "price_type": "MARKET",
                "status": "submitted",
            }
        ],
        run_id=1,
        trigger_source="manual",
        detail_url="javascript:alert(1)",
    )

    text = captured[0]["json"]["text"]
    assert "javascript:" not in text
    assert "查看详情" not in text


def test_failure_notification_renders_error_block(monkeypatch) -> None:
    captured = _capture_payload(monkeypatch)

    notification_service.send_telegram_failure_notification(
        bot_token="bot",
        chat_id="chat",
        run_id=7,
        trigger_source="schedule",
        run_type="trade",
        schedule_name="下午运行",
        error_message="LLM API timeout: <connection reset>",
    )

    payload = captured[0]["json"]
    assert payload["parse_mode"] == "HTML"
    text = payload["text"]
    assert "任务执行失败" in text
    assert "类型: 交易任务" in text
    assert "下午运行" in text
    assert "来源: 定时" in text
    # Error string is wrapped in <pre> and HTML-escaped.
    assert "<pre>" in text
    assert "&lt;connection reset&gt;" in text


def test_failure_notification_truncates_long_error_message(monkeypatch) -> None:
    captured = _capture_payload(monkeypatch)
    long_error = "x" * 2000

    notification_service.send_telegram_failure_notification(
        bot_token="bot",
        chat_id="chat",
        run_id=1,
        trigger_source="manual",
        error_message=long_error,
    )

    text = captured[0]["json"]["text"]
    # 800 chars + ellipsis
    assert "x" * 800 + "…" in text
    assert "x" * 1000 not in text


def test_notifications_no_op_without_credentials(monkeypatch) -> None:
    """Empty bot_token / chat_id should short-circuit before touching httpx."""
    fail_count = 0

    class ExplodingClient:
        def __init__(self, **_kwargs):
            nonlocal fail_count
            fail_count += 1

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(notification_service.httpx, "Client", ExplodingClient)

    notification_service.send_telegram_trade_notification(
        bot_token="",
        chat_id="chat",
        trade_orders=[],
        run_id=1,
        trigger_source="manual",
    )
    notification_service.send_telegram_failure_notification(
        bot_token="bot",
        chat_id="",
        run_id=1,
        trigger_source="manual",
        error_message="boom",
    )

    assert fail_count == 0
