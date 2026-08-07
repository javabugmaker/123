from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected block not found in {path}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_downloader() -> None:
    path = ROOT / "downloader.py"
    replace_once(
        path,
        "import math\nimport re\nimport sys\n",
        "import math\nimport os\nimport re\nimport sys\n",
    )
    replace_once(
        path,
        '_HTTP = requests.Session()\n_HTTP.trust_env = False\n_HTTP.headers.update({"User-Agent": "Mozilla/5.0"})\n',
        '''_HTTP = requests.Session()\n# Direct requests used by Eastmoney/Sina/Tencent stay isolated from a local\n# proxy.  AkShare does not use this Session; its proxy policy is configured\n# separately below so Clash system proxy can be honoured without contaminating\n# provider-specific request behaviour.\n_HTTP.trust_env = False\n_HTTP.headers.update({"User-Agent": "Mozilla/5.0"})\n\n_AKSHARE_PROXY_LOCK = threading.Lock()\n_AKSHARE_MANAGED_PROXY_ENV: dict[str, str] = {}\n\n\ndef _proxy_url(value: str) -> str:\n    value = str(value or "").strip()\n    if not value:\n        return ""\n    return value if "://" in value else f"http://{value}"\n\n\ndef _windows_system_proxy() -> dict[str, str]:\n    """Read the current WinINET proxy used by Clash system-proxy mode."""\n    if sys.platform != "win32":\n        return {}\n    try:\n        import winreg\n\n        with winreg.OpenKey(\n            winreg.HKEY_CURRENT_USER,\n            r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings",\n        ) as key:\n            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)\n            if not enabled:\n                return {}\n            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()\n    except (OSError, ValueError, TypeError):\n        return {}\n    if not raw:\n        return {}\n    if ";" not in raw and "=" not in raw:\n        proxy = _proxy_url(raw)\n        return {"http": proxy, "https": proxy} if proxy else {}\n    parsed: dict[str, str] = {}\n    for item in raw.split(";"):\n        if "=" not in item:\n            continue\n        protocol, server = item.split("=", 1)\n        protocol = protocol.strip().lower()\n        proxy = _proxy_url(server)\n        if protocol in {"http", "https"} and proxy:\n            parsed[protocol] = proxy\n    if "http" in parsed and "https" not in parsed:\n        parsed["https"] = parsed["http"]\n    if "https" in parsed and "http" not in parsed:\n        parsed["http"] = parsed["https"]\n    return parsed\n\n\ndef configure_akshare_proxy_from_system() -> dict[str, str]:\n    """Make AkShare honour Clash/Windows system proxy without touching _HTTP.\n\n    Requests used internally by AkShare normally consult process proxy settings.\n    On Windows, Clash often configures WinINET only, so explicitly mirror that\n    proxy into Requests-compatible environment variables before AkShare workers\n    start. User-provided HTTP(S)_PROXY values always take precedence.\n    """\n    with _AKSHARE_PROXY_LOCK:\n        system_proxy = _windows_system_proxy()\n        explicit_http = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")\n        explicit_https = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")\n\n        # Remove only values that this helper installed during an earlier call\n        # when Clash/system proxy has since been disabled. Never delete values\n        # supplied by the user or shell.\n        if not system_proxy:\n            for key, managed_value in list(_AKSHARE_MANAGED_PROXY_ENV.items()):\n                if os.environ.get(key) == managed_value:\n                    os.environ.pop(key, None)\n                _AKSHARE_MANAGED_PROXY_ENV.pop(key, None)\n            return {\n                key: value\n                for key, value in {"http": explicit_http or "", "https": explicit_https or ""}.items()\n                if value\n            }\n\n        resolved = {\n            "http": _proxy_url(explicit_http or system_proxy.get("http", "")),\n            "https": _proxy_url(explicit_https or system_proxy.get("https", system_proxy.get("http", ""))),\n        }\n        for protocol, value in resolved.items():\n            if not value:\n                continue\n            upper = f"{protocol.upper()}_PROXY"\n            lower = f"{protocol.lower()}_proxy"\n            if not os.environ.get(upper) and not os.environ.get(lower):\n                os.environ[upper] = value\n                os.environ[lower] = value\n                _AKSHARE_MANAGED_PROXY_ENV[upper] = value\n                _AKSHARE_MANAGED_PROXY_ENV[lower] = value\n        return {key: value for key, value in resolved.items() if value}\n''',
    )
    replace_once(
        path,
        '''    selected_source = normalize_data_source(source)\n    worker_count = _download_worker_count(selected_source, total)\n    logger.info(\n''',
        '''    selected_source = normalize_data_source(source)\n    if selected_source in {"akshare", "auto"}:\n        proxies = configure_akshare_proxy_from_system()\n        if proxies:\n            logger.info(\n                "AkShare network: system/environment proxy enabled (%s).",\n                proxies.get("https") or proxies.get("http"),\n            )\n        else:\n            logger.info("AkShare network: no system/environment proxy detected; using direct connection.")\n    worker_count = _download_worker_count(selected_source, total)\n    logger.info(\n''',
    )


def patch_fundamental_data() -> None:
    path = ROOT / "fundamental_data.py"
    replace_once(
        path,
        "from downloader import normalize_ticker\n",
        "from downloader import configure_akshare_proxy_from_system, normalize_ticker\n",
    )
    replace_once(
        path,
        "_NETWORK_ENV_LOCK = threading.Lock()\n",
        "_NETWORK_ENV_LOCK = threading.Lock()\n_AKSHARE_CALL_LOCK = threading.Lock()\n",
    )
    old_context = '''@contextmanager\ndef _direct_network_environment():\n    """Scope proxy bypass to one AKShare call and always restore the environment."""\n    with _NETWORK_ENV_LOCK:\n        previous = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}\n        for key in _PROXY_ENV_KEYS:\n            os.environ.pop(key, None)\n        os.environ["NO_PROXY"] = "*"\n        os.environ["no_proxy"] = "*"\n        try:\n            yield\n        finally:\n            for key in _PROXY_ENV_KEYS:\n                os.environ.pop(key, None)\n            for key, value in previous.items():\n                if value is not None:\n                    os.environ[key] = value\n'''
    new_context = '''@contextmanager\ndef _direct_network_environment():\n    """Compatibility wrapper that preserves proxy state for AkShare calls.\n\n    Older code cleared HTTP(S)_PROXY and set NO_PROXY=* here. Because os.environ\n    is process-global, a timed-out daemon request could leave every later AkShare\n    download forced to direct-connect. Keep the context manager API, but never\n    mutate proxy variables; instead mirror the active Windows/Clash system proxy.\n    """\n    configure_akshare_proxy_from_system()\n    yield\n'''
    replace_once(path, old_context, new_context)
    old_run = '''    def run() -> None:\n        try:\n            with _direct_network_environment():\n                outcome["frame"] = operation()\n        except Exception as exc:\n            outcome["error"] = exc\n'''
    new_run = '''    def run() -> None:\n        # Do not stack provider threads when a previous AKShare call timed out\n        # but is still alive. This also prevents concurrent code from repeatedly\n        # reconfiguring process proxy state.\n        if not _AKSHARE_CALL_LOCK.acquire(blocking=False):\n            outcome["error"] = RuntimeError("previous AKShare request is still active")\n            return\n        try:\n            with _direct_network_environment():\n                outcome["frame"] = operation()\n        except Exception as exc:\n            outcome["error"] = exc\n        finally:\n            _AKSHARE_CALL_LOCK.release()\n'''
    replace_once(path, old_run, new_run)


def main() -> None:
    patch_downloader()
    patch_fundamental_data()
    print("Clash/AkShare proxy hotfix applied")


if __name__ == "__main__":
    main()
