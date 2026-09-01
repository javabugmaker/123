"""Proxy helpers used only by the low-frequency AKShare fundamentals path."""

from __future__ import annotations

import os
import sys
import threading

_AKSHARE_PROXY_LOCK = threading.Lock()
_AKSHARE_MANAGED_PROXY_ENV: dict[str, str] = {}


def _proxy_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return value if "://" in value else f"http://{value}"


def _windows_system_proxy() -> dict[str, str]:
    """Read the WinINET proxy used by Clash system-proxy mode on Windows."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
            if not enabled:
                return {}
            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
    except (OSError, ValueError, TypeError):
        return {}
    if not raw:
        return {}
    if ";" not in raw and "=" not in raw:
        proxy = _proxy_url(raw)
        return {"http": proxy, "https": proxy} if proxy else {}
    result: dict[str, str] = {}
    for item in raw.split(";"):
        if "=" not in item:
            continue
        protocol, server = item.split("=", 1)
        protocol = protocol.strip().lower()
        if protocol in {"http", "https"}:
            proxy = _proxy_url(server)
            if proxy:
                result[protocol] = proxy
    if "http" in result and "https" not in result:
        result["https"] = result["http"]
    if "https" in result and "http" not in result:
        result["http"] = result["https"]
    return result


def configure_akshare_proxy_from_system() -> dict[str, str]:
    """Mirror an active Windows/Clash proxy into Requests-compatible env vars."""
    with _AKSHARE_PROXY_LOCK:
        system_proxy = _windows_system_proxy()
        explicit_http = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        explicit_https = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if not system_proxy:
            for key, managed_value in list(_AKSHARE_MANAGED_PROXY_ENV.items()):
                if os.environ.get(key) == managed_value:
                    os.environ.pop(key, None)
                _AKSHARE_MANAGED_PROXY_ENV.pop(key, None)
            return {
                key: value
                for key, value in {
                    "http": explicit_http or "",
                    "https": explicit_https or "",
                }.items()
                if value
            }

        resolved = {
            "http": _proxy_url(explicit_http or system_proxy.get("http", "")),
            "https": _proxy_url(
                explicit_https
                or system_proxy.get("https", system_proxy.get("http", ""))
            ),
        }
        for protocol, value in resolved.items():
            if not value:
                continue
            upper = f"{protocol.upper()}_PROXY"
            lower = f"{protocol}_proxy"
            if not os.environ.get(upper) and not os.environ.get(lower):
                os.environ[upper] = value
                os.environ[lower] = value
                _AKSHARE_MANAGED_PROXY_ENV[upper] = value
                _AKSHARE_MANAGED_PROXY_ENV[lower] = value
        return {key: value for key, value in resolved.items() if value}
