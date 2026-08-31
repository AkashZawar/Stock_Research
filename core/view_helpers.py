"""Shared request helpers used by views across the apps.

Functions:
- ``client_ip(request)``: safe client IP (honours ``X-Forwarded-For``).
- ``describe_device(user_agent)``: classify the user agent into a device
  type/label (mobile, tablet, desktop, bot, unknown).
- ``read_json_body(request)``: parse and validate a JSON request body.

Nothing here persists anything. Request metadata is derived where a view needs
it for the current response only; it is never written down.
"""
import json
from ipaddress import ip_address


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
