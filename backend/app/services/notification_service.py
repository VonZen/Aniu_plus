"""Lightweight Telegram Bot notification service."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TG_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10


def send_telegram_trade_notification(
    *,
    bot_token: str,
    chat_id: str,
    trade_orders: list[dict],
    run_id: int,
    trigger_source: str,
    schedule_name: str | None = None,
    http_proxy: str | None = None,
) -> None:
    """Send a Telegram notification for executed trade orders.

    Failure is logged but never propagated to the caller.
    """
    if not bot_token or not chat_id:
        return

    text = _build_trade_message(
        trade_orders=trade_orders,
        run_id=run_id,
        trigger_source=trigger_source,
        schedule_name=schedule_name,
    )

    url = f"{_TG_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    proxy_url = _normalize_http_proxy(http_proxy)

    try:
        if proxy_url:
            client_context = httpx.Client(timeout=_TIMEOUT_SECONDS, proxy=proxy_url)
        else:
            client_context = httpx.Client(timeout=_TIMEOUT_SECONDS)
        with client_context as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        logger.info("Telegram notification sent: run_id=%s", run_id)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Telegram notification HTTP error: run_id=%s, status=%s, body=%s",
            run_id,
            exc.response.status_code,
            exc.response.text[:200],
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Telegram notification network error: run_id=%s, error=%s",
            run_id,
            exc,
        )
    except Exception as exc:
        logger.warning(
            "Telegram notification failed: run_id=%s, error=%s",
            run_id,
            exc,
        )


def _normalize_http_proxy(http_proxy: str | None) -> str | None:
    proxy_url = str(http_proxy or "").strip()
    if not proxy_url:
        return None
    if "://" not in proxy_url:
        return f"http://{proxy_url}"
    return proxy_url


def _build_trade_message(
    *,
    trade_orders: list[dict],
    run_id: int,
    trigger_source: str,
    schedule_name: str | None,
) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    action_emoji = {"BUY": "\U0001f7e2", "SELL": "\U0001f534"}
    action_text = {"BUY": "买入", "SELL": "卖出"}

    lines = [
        "\U0001f4c8 <b>交易执行通知</b>",
        f"⏰ {now}",
    ]
    if schedule_name:
        lines.append(f"\U0001f4cb 任务: {schedule_name}")
    lines.append(f"\U0001f3af 来源: {trigger_source} | 运行 #{run_id}")
    lines.append("")

    for order in trade_orders:
        action = str(order.get("action") or "").upper()
        emoji = action_emoji.get(action, "")
        label = action_text.get(action, action)
        symbol = order.get("symbol") or "?"
        name = order.get("name") or ""
        quantity = order.get("quantity") or 0
        price_type = order.get("price_type") or "MARKET"
        price = order.get("price")

        name_part = f" ({name})" if name else ""
        if price_type == "MARKET":
            price_part = " 市价"
        elif price is not None:
            price_part = f" {price:.2f}元"
        else:
            price_part = ""

        lines.append(f"{emoji} <b>{label}</b> {symbol}{name_part} x{quantity}{price_part}")

    return "\n".join(lines)
