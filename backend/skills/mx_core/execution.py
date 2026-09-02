from __future__ import annotations

import re
from typing import Any, Callable

from skills.mx_core.client import MXClient
from skills.mx_core.tool_specs import TOOL_PROFILES, TOOL_SPECS, build_tools


ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("401", "API Key 可能错误、失效或未正确配置，请检查 MX_APIKEY。"),
    ("API密钥不存在", "API Key 可能错误、失效或未正确配置，请检查 MX_APIKEY。"),
    ("code=113", "今日调用次数可能已达上限，请前往妙想 Skills 页面获取更多调用次数。"),
    ("今日调用次数已达上限", "今日调用次数可能已达上限，请前往妙想 Skills 页面获取更多调用次数。"),
    ("Connection refused", "当前网络可能无法访问东方财富妙想接口，请检查网络或稍后重试。"),
    ("connect:", "当前网络可能无法访问东方财富妙想接口，请检查网络或稍后重试。"),
    ("未绑定模拟组合账户", "当前账户可能尚未绑定模拟组合，请先在妙想 Skills 页面创建并绑定模拟账户。"),
    ("code=404", "当前账户可能尚未绑定模拟组合，请先在妙想 Skills 页面创建并绑定模拟账户。"),
    ("No dataTable found", "本次查询没有返回可用数据表，请放宽查询条件或到东方财富妙想 AI 页面确认查询方式。"),
    ("筛选结果为空", "本次筛选没有匹配到股票，请放宽选股条件。"),
)

MAINBOARD_PREFIXES_BY_MARKET: dict[str, tuple[str, ...]] = {
    "SH": ("600", "601", "603", "605"),
    "SZ": ("000", "001", "002"),
}


def _normalize_trade_symbol(symbol: str) -> tuple[str, str, str]:
    text = str(symbol or "").strip().upper()
    matched = re.fullmatch(r"(\d{6})(?:\.(SH|SZ))?", text)
    if not matched:
        raise RuntimeError("股票代码格式无效，买卖 A 股时请使用 6 位代码或带交易所后缀的代码。")

    code, explicit_market = matched.groups()
    inferred_market = ""
    for market, prefixes in MAINBOARD_PREFIXES_BY_MARKET.items():
        if code.startswith(prefixes):
            inferred_market = market
            break

    if explicit_market and inferred_market and explicit_market != inferred_market:
        raise RuntimeError("股票代码与交易所后缀不匹配，请检查 symbol。")

    market = explicit_market or inferred_market
    normalized_symbol = f"{code}.{market}" if market else code
    return code, market, normalized_symbol


def _is_mainboard_symbol(symbol: str) -> bool:
    try:
        code, market, _ = _normalize_trade_symbol(symbol)
    except RuntimeError:
        return False

    if market not in MAINBOARD_PREFIXES_BY_MARKET:
        return False
    return code.startswith(MAINBOARD_PREFIXES_BY_MARKET[market])


def _extract_security_name_from_market_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        entity_name = str(payload.get("entityName") or "").strip()
        if entity_name:
            return entity_name.split("(")[0].strip()
        for key in ("name", "stockName", "SECURITY_SHORT_NAME", "securityName"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        for value in payload.values():
            resolved = _extract_security_name_from_market_payload(value)
            if resolved:
                return resolved
        return ""
    if isinstance(payload, list):
        for item in payload:
            resolved = _extract_security_name_from_market_payload(item)
            if resolved:
                return resolved
    return ""


def _is_st_stock_name(name: str) -> bool:
    normalized = str(name or "").upper().replace(" ", "")
    return "ST" in normalized if normalized else False


class MXExecutionService:
    def __init__(self) -> None:
        self._tool_specs = TOOL_SPECS
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "mx_query_market": self._handle_query_market,
            "mx_search_news": self._handle_search_news,
            "mx_screen_stocks": self._handle_screen_stocks,
            "mx_get_positions": self._handle_get_positions,
            "mx_get_balance": self._handle_get_balance,
            "mx_get_orders": self._handle_get_orders,
            "mx_get_self_selects": self._handle_get_self_selects,
            "mx_manage_self_select": self._handle_manage_self_select,
            "mx_moni_trade": self._handle_moni_trade,
            "mx_moni_cancel": self._handle_moni_cancel,
        }

    def build_tools(self, run_type: str | None = None) -> list[dict[str, Any]]:
        return build_tools(run_type=run_type)

    def execute_tool(
        self,
        *,
        client: MXClient,
        app_settings: Any,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handler = self._handlers.get(tool_name)
        if handler is None:
            return {
                "ok": False,
                "tool_name": tool_name,
                "error": f"未知工具调用: {tool_name}",
            }

        handler_kwargs: dict[str, Any] = {
            "client": client,
            "app_settings": app_settings,
            "arguments": arguments,
        }
        if tool_name in {"mx_moni_trade", "mx_moni_cancel"}:
            handler_kwargs["context"] = context or {}

        try:
            return handler(**handler_kwargs)
        except Exception as exc:
            guidance = self._build_error_guidance(str(exc))
            return {
                "ok": False,
                "tool_name": tool_name,
                "error": f"{str(exc)}{guidance}",
            }

    def _handle_query_market(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = self._resolve_query(arguments, app_settings)
        result = client.query_market(query)
        return {
            "ok": True,
            "tool_name": "mx_query_market",
            "summary": f"已查询市场数据：{query}。",
            "result": result,
        }

    def _handle_search_news(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = self._resolve_query(arguments, app_settings)
        result = client.search_news(query)
        return {
            "ok": True,
            "tool_name": "mx_search_news",
            "summary": f"已查询资讯：{query}。",
            "result": result,
        }

    def _handle_screen_stocks(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = self._resolve_query(arguments, app_settings)
        result = client.screen_stocks(query)
        return {
            "ok": True,
            "tool_name": "mx_screen_stocks",
            "summary": f"已执行选股：{query}。",
            "result": result,
        }

    def _handle_get_positions(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings, arguments
        result = client.get_positions()
        return {
            "ok": True,
            "tool_name": "mx_get_positions",
            "summary": "已查询持仓。",
            "result": result,
        }

    def _handle_get_balance(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings, arguments
        result = client.get_balance()
        return {
            "ok": True,
            "tool_name": "mx_get_balance",
            "summary": "已查询账户资金。",
            "result": result,
        }

    def _handle_get_orders(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings, arguments
        result = client.get_orders()
        return {
            "ok": True,
            "tool_name": "mx_get_orders",
            "summary": "已查询委托记录。",
            "result": result,
        }

    def _handle_get_self_selects(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings, arguments
        result = client.get_self_selects()
        return {
            "ok": True,
            "tool_name": "mx_get_self_selects",
            "summary": "已查询自选股列表。",
            "result": result,
        }

    def _handle_manage_self_select(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = self._resolve_query(arguments, app_settings)
        result = client.manage_self_select(query)
        return {
            "ok": True,
            "tool_name": "mx_manage_self_select",
            "summary": f"已执行自选股操作：{query}",
            "result": result,
            "executed_action": {
                "action": "MANAGE_SELF_SELECT",
                "query": query,
            },
        }

    def _handle_moni_trade(
        self,
        *,
        client: MXClient,
        app_settings: Any,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trade_mode = str((context or {}).get("trade_mode") or "execute").strip().lower()
        if not bool(getattr(app_settings, "trade_enabled", True)):
            raise RuntimeError("trade disabled by settings")
        action = str(arguments.get("action") or "").upper()
        symbol = str(arguments.get("symbol") or "").strip()
        price_type = str(arguments.get("price_type") or "MARKET").upper()
        quantity = int(arguments.get("quantity") or 0)
        price = arguments.get("price")
        reason = str(arguments.get("reason") or "").strip()

        if action not in {"BUY", "SELL"}:
            raise RuntimeError("模拟交易工具的 action 只能是 BUY 或 SELL。")
        if not symbol:
            raise RuntimeError("模拟交易工具缺少股票代码。")
        if quantity <= 0:
            raise RuntimeError("模拟交易工具的 quantity 必须大于 0。")
        if quantity % 100 != 0:
            raise RuntimeError("A 股交易数量必须是 100 的整数倍。")
        if price_type not in {"MARKET", "LIMIT"}:
            raise RuntimeError("price_type 只能是 MARKET 或 LIMIT。")
        if price_type == "LIMIT":
            try:
                normalized_price = float(price)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("LIMIT 委托必须提供有效价格。") from exc
            if normalized_price <= 0:
                raise RuntimeError("LIMIT 委托价格必须大于 0。")
            price = normalized_price
        elif price is not None:
            try:
                price = float(price)
            except (TypeError, ValueError):
                price = None

        trade_symbol = symbol
        display_symbol = symbol
        parsed_trade_symbol: tuple[str, str, str] | None = None
        try:
            parsed_trade_symbol = _normalize_trade_symbol(symbol)
            trade_symbol = parsed_trade_symbol[0]
            display_symbol = parsed_trade_symbol[2]
        except RuntimeError:
            if action == "BUY":
                raise

        if action == "BUY":
            if parsed_trade_symbol is None:
                raise RuntimeError(
                    "股票代码格式无效，买卖 A 股时请使用 6 位代码或带交易所后缀的代码。"
                )
            code, market, normalized_symbol = parsed_trade_symbol
            if market not in MAINBOARD_PREFIXES_BY_MARKET or not code.startswith(
                MAINBOARD_PREFIXES_BY_MARKET[market]
            ):
                raise RuntimeError(
                    "当前仅允许买入沪深主板股票，不支持创业板、科创板、北交所或其他非主板标的。"
                )

            security_name = str(arguments.get("name") or "").strip()
            if not security_name:
                try:
                    market_payload = client.query_market(code)
                except Exception as exc:
                    raise RuntimeError(
                        "买入前无法校验股票是否属于沪深主板且非 ST，请稍后重试。"
                    ) from exc
                security_name = _extract_security_name_from_market_payload(
                    market_payload
                )

            if not security_name:
                raise RuntimeError("买入前无法确认股票名称，当前禁止买入。")
            if _is_st_stock_name(security_name):
                raise RuntimeError("当前禁止买入 ST 或 *ST 股票。")

        if trade_mode == "proposal":
            return {
                "ok": True,
                "tool_name": "mx_moni_trade",
                "summary": f"已生成{action}交易提案：{display_symbol} {quantity} 股（待风控审批后执行）。",
                "result": {
                    "preview_only": True,
                    "message": "交易提案已生成，等待后端风控审批。",
                },
                "executed_action": {
                    "symbol": display_symbol,
                    "name": str(arguments.get("name") or "").strip(),
                    "action": action,
                    "quantity": quantity,
                    "price_type": price_type,
                    "price": price,
                    "reason": reason,
                    "status": "pending",
                },
            }

        result = client.trade(
            action=action,
            symbol=trade_symbol,
            quantity=quantity,
            price_type=price_type,
            price=price,
        )
        return {
            "ok": True,
            "tool_name": "mx_moni_trade",
            "summary": f"已提交{action}委托：{display_symbol} {quantity} 股。",
            "result": result,
            "executed_action": {
                "symbol": display_symbol,
                "name": str(arguments.get("name") or "").strip(),
                "action": action,
                "quantity": quantity,
                "price_type": price_type,
                "price": price,
                "reason": reason,
            },
        }

    def _handle_moni_cancel(
        self,
        *,
        client: MXClient,
        app_settings: Any,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trade_mode = str((context or {}).get("trade_mode") or "execute").strip().lower()
        if not bool(getattr(app_settings, "trade_enabled", True)):
            raise RuntimeError("trade disabled by settings")
        cancel_type = str(arguments.get("cancel_type") or "").strip().lower()
        order_id = str(arguments.get("order_id") or "").strip() or None
        stock_code = str(arguments.get("stock_code") or "").strip() or None
        reason = str(arguments.get("reason") or "").strip()
        cancel_stock_code = stock_code
        display_stock_code = stock_code

        if cancel_type not in {"all", "order"}:
            raise RuntimeError("cancel_type 只能是 all 或 order。")
        if cancel_type == "order" and not order_id:
            raise RuntimeError("按委托编号撤单时必须提供 order_id。")
        if stock_code:
            try:
                code, _, normalized_symbol = _normalize_trade_symbol(stock_code)
                cancel_stock_code = code
                display_stock_code = normalized_symbol
            except RuntimeError:
                pass

        if trade_mode == "proposal":
            return {
                "ok": True,
                "tool_name": "mx_moni_cancel",
                "summary": "已生成撤单提案（待风控审批后执行）。"
                if cancel_type == "all"
                else f"已生成撤单提案：{order_id}（待风控审批后执行）。",
                "result": {
                    "preview_only": True,
                    "message": "撤单提案已生成，等待后端风控审批。",
                },
                "executed_action": {
                    "action": "CANCEL",
                    "cancel_type": cancel_type,
                    "order_id": order_id,
                    "stock_code": display_stock_code,
                    "reason": reason,
                    "status": "pending",
                },
            }

        result = client.cancel_order(
            cancel_type=cancel_type,
            order_id=order_id,
            stock_code=cancel_stock_code,
        )
        return {
            "ok": True,
            "tool_name": "mx_moni_cancel",
            "summary": "已提交撤单请求。"
            if cancel_type == "all"
            else f"已提交撤单请求：{order_id}",
            "result": result,
            "executed_action": {
                "action": "CANCEL",
                "cancel_type": cancel_type,
                "order_id": order_id,
                "stock_code": display_stock_code,
                "reason": reason,
            },
        }

    def _resolve_query(self, arguments: dict[str, Any], app_settings: Any) -> str:
        query = str(arguments.get("query") or "").strip()
        if query:
            return query
        fallback = str(getattr(app_settings, "task_prompt", "") or "").strip()
        if fallback:
            return fallback
        raise RuntimeError("缺少 query 参数。")

    def _build_error_guidance(self, message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return ""
        for needle, hint in ERROR_HINTS:
            if needle in text:
                return f"；建议：{hint}"
        return ""


mx_execution_service = MXExecutionService()
