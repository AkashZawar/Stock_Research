"""Shared request/form helpers used by views across the apps.

Functions:
- ``record_stock_search(...)``: best-effort persist of a ``StockSearchLog``
  row for an analyze/search request (never raises).
- ``client_ip(request)``: safe client IP (honours ``X-Forwarded-For``).
- ``describe_device(user_agent)``: classify the user agent into a device
  type/label (mobile, tablet, desktop, bot, unknown).
- ``read_json_body(request)``: parse and validate a JSON request body.
- ``save_trade_reference(...)`` / ``save_watchlist_item(...)``: validate a
  payload and persist the related model (used by the core CRUD APIs).
- ``decimal_from_payload(...)``: parse an optional/required decimal field.
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address

from .models import StockSearchLog
from .models import TradeReference
from .models import WatchlistItem


logger = logging.getLogger(__name__)


def record_stock_search(request, raw_input, symbol, status_code, error_message):
    if not raw_input and not symbol:
        return

    try:
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_type, device_label = describe_device(user_agent)
        StockSearchLog.objects.create(
            raw_input=raw_input[:160],
            symbol=(symbol or "").upper()[:32],
            ip_address=client_ip(request),
            device_type=device_type,
            device_label=device_label,
            user_agent=user_agent[:1000],
            status_code=status_code,
            success=200 <= status_code < 400,
            error_message=(error_message or "")[:280],
        )
    except Exception:
        logger.exception("Could not record stock search log.")


def client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    raw_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else request.META.get("REMOTE_ADDR", "").strip()
    if not raw_ip:
        return None

    try:
        return str(ip_address(raw_ip))
    except ValueError:
        return None


def describe_device(user_agent):
    agent = (user_agent or "").lower()
    if not agent:
        return "unknown", "Unknown device"
    if any(marker in agent for marker in ["bot", "crawler", "spider", "slurp"]):
        return "bot", "Bot / crawler"
    if "ipad" in agent or "tablet" in agent or ("android" in agent and "mobile" not in agent):
        return "tablet", "Tablet"
    if any(marker in agent for marker in ["mobi", "iphone", "android", "phone"]):
        return "mobile", "Mobile"
    if "windows" in agent:
        return "desktop", "Windows desktop"
    if "macintosh" in agent or "mac os x" in agent:
        return "desktop", "Mac desktop"
    if "linux" in agent or "x11" in agent:
        return "desktop", "Linux desktop"
    return "unknown", "Unknown device"


def read_json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Request body must be valid JSON.") from error


def save_trade_reference(trade, payload, partial=False):
    symbol = str(payload.get("symbol", trade.symbol if partial else "")).strip().upper()
    stock_name = str(payload.get("stockName", trade.stock_name if partial else "")).strip()
    status = str(payload.get("status", trade.status if partial else "watch")).strip().lower()
    note = str(payload.get("note", trade.note if partial else "")).strip()

    if not symbol:
        raise ValueError("Symbol is required.")
    if status not in {"watch", "active", "closed"}:
        raise ValueError("Status must be watch, active, or closed.")

    buy_price = decimal_from_payload(payload, "buyPrice", trade.buy_price if partial else None, required=True)
    sell_price = decimal_from_payload(payload, "sellPrice", trade.sell_price if partial else None, required=True)
    stop_loss = decimal_from_payload(payload, "stopLoss", trade.stop_loss if partial else None, required=False)

    if buy_price <= 0 or sell_price <= 0:
        raise ValueError("Buy price and sell price must be greater than zero.")
    if stop_loss is not None and stop_loss < 0:
        raise ValueError("Stop loss cannot be negative.")

    trade.symbol = symbol
    trade.stock_name = stock_name
    trade.buy_price = buy_price
    trade.sell_price = sell_price
    trade.stop_loss = stop_loss
    trade.status = status
    trade.note = note
    trade.save()
    return trade


def save_watchlist_item(item, payload, partial=False):
    symbol = str(payload.get("symbol", item.symbol if partial else "")).strip().upper()
    stock_name = str(payload.get("stockName", item.stock_name if partial else "")).strip()

    if not symbol:
        raise ValueError("Symbol is required.")

    buy_price = decimal_from_payload(payload, "buyPrice", item.buy_price if partial else None, required=False)
    sell_price = decimal_from_payload(payload, "sellPrice", item.sell_price if partial else None, required=False)
    check_price = decimal_from_payload(payload, "checkPrice", item.check_price if partial else None, required=False)

    for label, value in [("Buy price", buy_price), ("Sell price", sell_price), ("Check price", check_price)]:
        if value is not None and value <= 0:
            raise ValueError(f"{label} must be greater than zero.")

    item.symbol = symbol[:32]
    item.stock_name = stock_name[:160]
    item.buy_price = buy_price
    item.sell_price = sell_price
    item.check_price = check_price
    item.save()
    return item


def decimal_from_payload(payload, key, current_value, required):
    if key not in payload:
        if required and current_value is None:
            raise ValueError(f"{key} is required.")
        return current_value

    value = payload.get(key)
    if value in ("", None):
        if required:
            raise ValueError(f"{key} is required.")
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{key} must be a valid number.") from error
