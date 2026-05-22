"""Lightweight Telegram Bot notification service."""
from __future__ import annotations

import html
import logging
from typing import Any, Iterable

import httpx

logger = logging.getLogger(__name__)

_TG_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10

# Map internal trigger keys to user-visible Chinese labels. Anything we don't
# recognize is passed through unchanged so future trigger sources stay visible.
_TRIGGER_SOURCE_LABEL = {
    "manual": "手动",
    "schedule": "定时",
    "auto": "自动",
    "retry": "重试",
}

_RUN_TYPE_LABEL = {
    "analysis": "分析任务",
    "trade": "交易任务",
    "chat": "对话任务",
}

# Map order entry status (set in `aniu_service._extract_executed_actions`) to a
# Chinese label shown next to the order line. Unknown values are rendered as-is
# (escaped) so unexpected statuses are still visible.
_ORDER_STATUS_LABEL = {
    "submitted": "已挂出",
    "completed": "已完成",
    "cancel_requested": "撤单",
    "filled": "已成交",
    "partial": "部分成交",
    "rejected": "已驳回",
    "failed": "失败",
}


def send_telegram_trade_notification(
    *,
    bot_token: str,
    chat_id: str,
    trade_orders: list[dict],
    run_id: int,
    trigger_source: str,
    schedule_name: str | None = None,
    run_type: str | None = None,
    account_summary: dict | None = None,
    detail_url: str | None = None,
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
        run_type=run_type,
        account_summary=account_summary,
        detail_url=detail_url,
    )

    _send(
        bot_token=bot_token,
        chat_id=chat_id,
        text=text,
        http_proxy=http_proxy,
        run_id=run_id,
    )


def send_telegram_failure_notification(
    *,
    bot_token: str,
    chat_id: str,
    run_id: int,
    trigger_source: str,
    error_message: str,
    schedule_name: str | None = None,
    run_type: str | None = None,
    detail_url: str | None = None,
    http_proxy: str | None = None,
) -> None:
    """Send a Telegram notification when a run terminates with `failed` status.

    Mirrors :func:`send_telegram_trade_notification` so callers can treat both
    paths the same way (best-effort, never raises).
    """
    if not bot_token or not chat_id:
        return

    text = _build_failure_message(
        run_id=run_id,
        trigger_source=trigger_source,
        schedule_name=schedule_name,
        run_type=run_type,
        error_message=error_message,
        detail_url=detail_url,
    )

    _send(
        bot_token=bot_token,
        chat_id=chat_id,
        text=text,
        http_proxy=http_proxy,
        run_id=run_id,
    )


def _send(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    http_proxy: str | None,
    run_id: int,
) -> None:
    url = f"{_TG_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        # Long messages with embedded URLs render cleaner without the link
        # preview card overshadowing the structured content.
        "disable_web_page_preview": True,
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


def _format_now_shanghai() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _trigger_label(trigger_source: str) -> str:
    key = str(trigger_source or "").lower().strip()
    return _TRIGGER_SOURCE_LABEL.get(key, trigger_source or "")


def _run_type_label(run_type: str | None) -> str | None:
    if not run_type:
        return None
    key = str(run_type or "").lower().strip()
    return _RUN_TYPE_LABEL.get(key, run_type)


def _build_header(
    *,
    title: str,
    title_emoji: str,
    run_id: int,
    trigger_source: str,
    schedule_name: str | None,
    run_type: str | None,
) -> list[str]:
    """Render the shared header block (escaped, ready to join with newlines)."""
    lines = [
        f"{title_emoji} <b>{html.escape(title)}</b>",
        f"⏰ {html.escape(_format_now_shanghai())}",
    ]
    rt_label = _run_type_label(run_type)
    if rt_label:
        lines.append(f"\U0001f3f7\ufe0f 类型: {html.escape(rt_label)}")
    if schedule_name:
        lines.append(f"\U0001f4cb 任务: {html.escape(schedule_name)}")
    trigger = _trigger_label(trigger_source)
    lines.append(
        f"\U0001f3af 来源: {html.escape(trigger)} | 运行 #{int(run_id)}"
    )
    return lines


def _build_summary_line(orders: Iterable[dict]) -> str | None:
    """Render `本轮 N 笔: 买 X(¥yyy) / 卖 Y(¥zzz)` if there are 2+ trade orders.

    Returns None when there's only 0 or 1 trade orders to avoid noise.
    """
    buy_count = sell_count = 0
    buy_amount = sell_amount = 0.0
    for order in orders:
        action = str(order.get("action") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        amount = _order_amount(order)
        if action == "BUY":
            buy_count += 1
            if amount is not None:
                buy_amount += amount
        elif action == "SELL":
            sell_count += 1
            if amount is not None:
                sell_amount += amount

    total = buy_count + sell_count
    if total < 2:
        return None

    parts: list[str] = []
    if buy_count:
        parts.append(f"买 {buy_count}{_format_amount_suffix(buy_amount)}")
    if sell_count:
        parts.append(f"卖 {sell_count}{_format_amount_suffix(sell_amount)}")
    return f"\U0001f4ca 本轮 {total} 笔: {' / '.join(parts)}"


def _order_amount(order: dict) -> float | None:
    """Best-effort estimate of the cash value of a single order line."""
    price = order.get("price")
    quantity = order.get("quantity") or 0
    if price is None:
        return None
    try:
        return float(price) * float(quantity)
    except (TypeError, ValueError):
        return None


def _format_amount_suffix(amount: float) -> str:
    """Format `(¥XX,XXX)` if amount > 0, otherwise empty string."""
    if not amount:
        return ""
    return f"(¥{amount:,.0f})"


def _format_account_summary(summary: dict | None) -> list[str]:
    """Render an optional account snapshot block.

    Recognised keys (any subset, all numeric or formatted strings):
    - `total_assets` (number): 总资产
    - `cash_balance` (number): 现金
    - `daily_profit` (number): 当日盈亏(可为负)
    - `daily_return_ratio` (number/string): 当日收益率
    """
    if not summary:
        return []

    parts: list[str] = []
    total_assets = _format_currency(summary.get("total_assets"))
    if total_assets is not None:
        parts.append(f"总资产 {total_assets}")
    cash_balance = _format_currency(summary.get("cash_balance"))
    if cash_balance is not None:
        parts.append(f"现金 {cash_balance}")
    daily_profit = _format_signed_currency(summary.get("daily_profit"))
    if daily_profit is not None:
        ratio = _format_percent(summary.get("daily_return_ratio"))
        if ratio:
            parts.append(f"当日盈亏 {daily_profit} ({ratio})")
        else:
            parts.append(f"当日盈亏 {daily_profit}")

    if not parts:
        return []
    return ["", f"\U0001f4b0 {html.escape(' | '.join(parts))}"]


def _format_currency(value: Any) -> str | None:
    try:
        if value is None:
            return None
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return f"¥{numeric:,.2f}"


def _format_signed_currency(value: Any) -> str | None:
    try:
        if value is None:
            return None
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    sign = "+" if numeric >= 0 else "-"
    return f"{sign}¥{abs(numeric):,.2f}"


def _format_percent(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return text if text.endswith("%") else f"{text}%"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return f"{numeric:+.2f}%"


def _format_order_line(order: dict) -> str:
    """Render a single order line, fully HTML-escaped.

    Format: `<emoji> <b>买入</b> 600000 (浦发银行) x100 市价 · 已挂出`
    """
    action_emoji = {"BUY": "\U0001f7e2", "SELL": "\U0001f534", "CANCEL": "\u26a0\ufe0f"}
    action_text = {"BUY": "买入", "SELL": "卖出", "CANCEL": "撤单"}

    action = str(order.get("action") or "").upper()
    emoji = action_emoji.get(action, "")
    label = action_text.get(action, action or "")
    symbol = str(order.get("symbol") or "?")
    name = str(order.get("name") or "")
    quantity = order.get("quantity") or 0
    price_type = str(order.get("price_type") or "MARKET")
    price = order.get("price")
    status_raw = str(order.get("status") or "").lower().strip()

    name_part = f" ({html.escape(name)})" if name else ""
    if action == "CANCEL":
        price_part = ""
    elif price_type == "MARKET":
        price_part = " 市价"
    elif price is not None:
        try:
            price_part = f" {float(price):.2f}元"
        except (TypeError, ValueError):
            price_part = ""
    else:
        price_part = ""

    status_label = _ORDER_STATUS_LABEL.get(status_raw)
    if status_label is None and status_raw:
        status_label = status_raw
    status_part = f" · {html.escape(status_label)}" if status_label else ""

    return (
        f"{emoji} <b>{html.escape(label)}</b> "
        f"{html.escape(symbol)}{name_part} x{int(quantity)}{price_part}{status_part}"
    )


def _build_trade_message(
    *,
    trade_orders: list[dict],
    run_id: int,
    trigger_source: str,
    schedule_name: str | None,
    run_type: str | None,
    account_summary: dict | None,
    detail_url: str | None,
) -> str:
    lines = _build_header(
        title="交易执行通知",
        title_emoji="\U0001f4c8",
        run_id=run_id,
        trigger_source=trigger_source,
        schedule_name=schedule_name,
        run_type=run_type,
    )

    summary_line = _build_summary_line(trade_orders)
    if summary_line:
        lines.append(summary_line)

    lines.append("")
    for order in trade_orders:
        lines.append(_format_order_line(order))

    lines.extend(_format_account_summary(account_summary))

    detail_line = _build_detail_link(detail_url)
    if detail_line:
        lines.extend(["", detail_line])

    return "\n".join(lines)


def _build_failure_message(
    *,
    run_id: int,
    trigger_source: str,
    schedule_name: str | None,
    run_type: str | None,
    error_message: str,
    detail_url: str | None,
) -> str:
    lines = _build_header(
        title="任务执行失败",
        title_emoji="\u274c",
        run_id=run_id,
        trigger_source=trigger_source,
        schedule_name=schedule_name,
        run_type=run_type,
    )

    snippet = str(error_message or "").strip()
    if len(snippet) > 800:
        snippet = snippet[:800] + "…"
    if snippet:
        lines.append("")
        lines.append("<b>错误信息</b>")
        lines.append(f"<pre>{html.escape(snippet)}</pre>")

    detail_line = _build_detail_link(detail_url)
    if detail_line:
        lines.extend(["", detail_line])

    return "\n".join(lines)


def _build_detail_link(detail_url: str | None) -> str | None:
    url = str(detail_url or "").strip()
    if not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    # html.escape on the URL handles & and quotes inside query strings safely.
    return f"\U0001f517 <a href=\"{html.escape(url, quote=True)}\">查看详情</a>"
