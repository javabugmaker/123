from __future__ import annotations

import os
import sys
from unittest import TestCase
from unittest.mock import patch

import fundamental_data
import network_proxy


class _FakeRegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = object()

    @staticmethod
    def OpenKey(*args, **kwargs):
        return _FakeRegistryKey()

    @staticmethod
    def QueryValueEx(key, name):
        if name == "ProxyEnable":
            return 1, 4
        if name == "ProxyServer":
            return "127.0.0.1:7897", 1
        raise OSError(name)


class ClashAkShareProxyTests(TestCase):
    def tearDown(self):
        network_proxy._AKSHARE_MANAGED_PROXY_ENV.clear()

    def test_windows_clash_system_proxy_is_detected(self):
        with patch.object(network_proxy.sys, "platform", "win32"), patch.dict(
            sys.modules, {"winreg": _FakeWinreg}
        ):
            proxies = network_proxy._windows_system_proxy()

        self.assertEqual(proxies["http"], "http://127.0.0.1:7897")
        self.assertEqual(proxies["https"], "http://127.0.0.1:7897")

    def test_akshare_proxy_is_mirrored_to_requests_environment(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            network_proxy,
            "_windows_system_proxy",
            return_value={
                "http": "http://127.0.0.1:7897",
                "https": "http://127.0.0.1:7897",
            },
        ):
            proxies = network_proxy.configure_akshare_proxy_from_system()
            self.assertEqual(proxies["https"], "http://127.0.0.1:7897")
            self.assertEqual(os.environ["HTTP_PROXY"], "http://127.0.0.1:7897")
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:7897")
            self.assertEqual(os.environ["http_proxy"], "http://127.0.0.1:7897")
            self.assertEqual(os.environ["https_proxy"], "http://127.0.0.1:7897")

    def test_fundamental_akshare_context_never_clears_user_proxy(self):
        proxy = "http://127.0.0.1:7897"
        with patch.dict(
            os.environ,
            {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy},
            clear=False,
        ), patch.object(
            fundamental_data,
            "configure_akshare_proxy_from_system",
            return_value={"http": proxy, "https": proxy},
        ):
            before = (os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"))
            with fundamental_data._direct_network_environment():
                inside = (os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"))
            after = (os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"))

        self.assertEqual(inside, before)
        self.assertEqual(after, before)
