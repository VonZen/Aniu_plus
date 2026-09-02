from __future__ import annotations

from typing import Any

from app.domain.trading.intents import (
    PolicyDecision,
    TradeProposal,
    with_revised_price_type,
)

DEFAULT_RISK_SETTINGS: dict[str, float | int | bool] = {
    "trade_enabled": True,
    "effective_capital": 30000.0,
    "max_position_pct": 0.3,
    "max_total_position_pct": 0.8,
    "max_order_amount": 30000.0,
    "max_daily_loss": 3000.0,
    "max_drawdown_pct": 0.15,
    "max_consecutive_losses": 3,
    "min_market_trend_score": 0.0,
    "allow_short": False,
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _find_position(
    account_snapshot: dict[str, Any] | None,
    symbol: str,
) -> dict[str, Any] | None:
    if not account_snapshot:
        return None
    positions = account_snapshot.get("positions")
    if not isinstance(positions, list):
        return None
    normalized_symbol = str(symbol or "").strip().upper().split(".")[0]
    for position in positions:
        if not isinstance(position, dict):
            continue
        pos_symbol = str(position.get("symbol") or "").strip().upper().split(".")[0]
        if pos_symbol == normalized_symbol:
            return position
    return None


class RiskGate:
    def evaluate(
        self,
        *,
        proposal: TradeProposal,
        run_type: str,
        trade_enabled: bool,
        enforce_trade_run_type: bool = True,
        settings: dict[str, Any] | None = None,
        account_snapshot: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        normalized_run_type = str(run_type or "analysis").strip().lower()
        normalized_action = str(proposal.action or "").upper()

        if normalized_action in {"BUY", "SELL", "CANCEL"} and not trade_enabled:
            return PolicyDecision(
                decision="rejected",
                proposal=proposal,
                message="trade disabled by settings",
                retryable=False,
            )

        if (
            enforce_trade_run_type
            and normalized_run_type != "trade"
            and normalized_action in {"BUY", "SELL", "CANCEL"}
        ):
            return PolicyDecision(
                decision="rejected",
                proposal=proposal,
                message="trade actions require trade run type",
                retryable=False,
            )

        if normalized_action == "CANCEL":
            if not str(proposal.symbol or "").strip():
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message="cancel symbol is required",
                    retryable=False,
                )
            return PolicyDecision(
                decision="approved",
                proposal=proposal,
                message="approved by risk gate",
                retryable=False,
            )

        if normalized_action not in {"BUY", "SELL"}:
            return PolicyDecision(
                decision="rejected",
                proposal=proposal,
                message="unsupported trade action",
                retryable=False,
            )

        # --- shared basic checks -------------------------------------------------
        if int(proposal.quantity or 0) <= 0:
            return PolicyDecision(
                decision="rejected",
                proposal=proposal,
                message="trade quantity must be positive",
                retryable=False,
            )
        if not str(proposal.symbol or "").strip():
            return PolicyDecision(
                decision="rejected",
                proposal=proposal,
                message="trade symbol is required",
                retryable=False,
            )
        normalized_price_type = str(proposal.price_type or "MARKET").upper()
        if normalized_price_type not in {"MARKET", "LIMIT"}:
            return PolicyDecision(
                decision="revise",
                proposal=proposal,
                revised_proposal=with_revised_price_type(proposal, "MARKET"),
                message="unsupported price_type revised to MARKET",
                retryable=True,
            )

        # --- pull configured risk limits -----------------------------------------
        merged: dict[str, Any] = dict(DEFAULT_RISK_SETTINGS)
        if isinstance(settings, dict):
            merged.update(settings)
        effective_capital = max(0.0, _to_float(merged.get("effective_capital"), 30000.0))
        max_position_pct = max(0.0, min(1.0, _to_float(merged.get("max_position_pct"), 0.3)))
        max_total_position_pct = max(
            0.0, min(1.0, _to_float(merged.get("max_total_position_pct"), 0.8))
        )
        max_order_amount = max(0.0, _to_float(merged.get("max_order_amount"), 30000.0))
        max_daily_loss = max(0.0, _to_float(merged.get("max_daily_loss"), 3000.0))
        max_drawdown_pct = max(
            0.0, min(1.0, _to_float(merged.get("max_drawdown_pct"), 0.15))
        )
        max_consecutive_losses = max(
            0, _to_int(merged.get("max_consecutive_losses"), 3)
        )
        min_market_trend_score = _to_float(
            merged.get("min_market_trend_score"), 0.0
        )

        # --- market regime guard ------------------------------------------------
        market_trend_score = _to_float(
            (account_snapshot or {}).get("market_trend_score"),
            default=min_market_trend_score,
        )
        if market_trend_score < min_market_trend_score:
            return PolicyDecision(
                decision="rejected",
                proposal=proposal,
                message=f"market trend score {market_trend_score:.2f} below threshold {min_market_trend_score:.2f}",
                retryable=False,
            )

        # --- portfolio-level loss guards (block new buying only) ------------------
        if normalized_action == "BUY":
            daily_profit = _to_float((account_snapshot or {}).get("daily_profit"))
            if daily_profit < 0 and abs(daily_profit) >= max_daily_loss:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message="daily loss limit reached",
                    retryable=False,
                )

            total_return_ratio = _to_float(
                (account_snapshot or {}).get("total_return_ratio")
            )
            if total_return_ratio <= -max_drawdown_pct:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message="max drawdown reached",
                    retryable=False,
                )

            recent_losses = _to_int((account_snapshot or {}).get("recent_losses"), 0)
            if max_consecutive_losses > 0 and recent_losses >= max_consecutive_losses:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message="max consecutive losses reached",
                    retryable=False,
                )

        symbol = str(proposal.symbol or "").strip().upper()
        price_symbol = symbol.split(".")[0]
        position = _find_position(account_snapshot, symbol)
        quantity = int(proposal.quantity or 0)
        position_volume = _to_int((position or {}).get("volume"), 0)
        available_volume = _to_int(
            (position or {}).get("available_volume"),
            position_volume,
        )

        if normalized_action == "SELL":
            if position is None or position_volume <= 0:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message="no position to sell",
                    retryable=False,
                )
            if quantity > available_volume:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message=f"insufficient available shares: have {available_volume}, need {quantity}",
                    retryable=False,
                )
            return PolicyDecision(
                decision="approved",
                proposal=proposal,
                message="approved by risk gate",
                retryable=False,
            )

        # --- BUY capital / position sizing guards --------------------------------
        current_prices = (account_snapshot or {}).get("current_prices", {})
        if not isinstance(current_prices, dict):
            current_prices = {}
        current_price = _to_float(
            (position or {}).get("current_price")
            or current_prices.get(price_symbol)
            or current_prices.get(symbol)
        )
        estimated_price = current_price
        if normalized_price_type == "LIMIT":
            estimated_price = _to_float(proposal.price, estimated_price)
        if estimated_price <= 0:
            # Cannot verify capital limits without a price; leave a clear marker
            # in the decision message but do not reject solely on missing data.
            message = "approved by risk gate (price unavailable, capital checks skipped)"
        else:
            order_amount = estimated_price * quantity
            if order_amount > max_order_amount > 0:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message=f"order amount {order_amount:.2f} exceeds max order amount {max_order_amount:.2f}",
                    retryable=False,
                )
            if order_amount > effective_capital * max_position_pct:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message=f"order amount {order_amount:.2f} exceeds single position limit {effective_capital * max_position_pct:.2f}",
                    retryable=False,
                )

            current_position_amount = _to_float((position or {}).get("amount"), 0.0)
            total_position_amount = _to_float(
                (account_snapshot or {}).get("total_position_amount"),
                current_position_amount,
            )
            projected_total = total_position_amount + order_amount
            total_limit = effective_capital * max_total_position_pct
            if projected_total > total_limit:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message=f"projected total position {projected_total:.2f} exceeds limit {total_limit:.2f}",
                    retryable=False,
                )

            cash_balance = _to_float((account_snapshot or {}).get("cash_balance"))
            if cash_balance > 0 and order_amount > cash_balance:
                return PolicyDecision(
                    decision="rejected",
                    proposal=proposal,
                    message=f"insufficient cash: need {order_amount:.2f}, have {cash_balance:.2f}",
                    retryable=False,
                )

            message = "approved by risk gate"

        return PolicyDecision(
            decision="approved",
            proposal=proposal,
            message=message,
            retryable=False,
        )


risk_gate = RiskGate()
