"""住宅代理总开关关掉后，关于 IP 的一切都不能再跑。

需求：设置页那个「启用住宅代理 IP」关掉，续火花/检测/选好友/扫码登录一律直连，
不取 IP、不探活、不按地区换 IP 重试，地区字段被彻底忽略。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import tasks
from webui import proxy as proxy_mod
from webui import qr_login


def cfg(enabled, key="k", phone="123"):
    return {"enabled": enabled, "api_key": key, "phone": phone, "base_url": "http://x"}


class ProxyEnabledTests(unittest.TestCase):
    def test_enabled_needs_switch_and_credentials(self):
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(True)):
            self.assertTrue(proxy_mod.proxy_enabled())
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(False)):
            self.assertFalse(proxy_mod.proxy_enabled())
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(True, key="")):
            self.assertFalse(proxy_mod.proxy_enabled(), "没配密钥也不算启用")
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(True, phone="")):
            self.assertFalse(proxy_mod.proxy_enabled(), "没配账号也不算启用")

    def test_github_jump_uses_key_even_when_douyin_switch_is_off(self):
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(False)):
            self.assertTrue(proxy_mod.github_credentials_ready())
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(False, key="")):
            self.assertFalse(proxy_mod.github_credentials_ready())


class SwitchOffMeansDirectTests(unittest.TestCase):
    """开关关掉：哪怕账号设了地区，也全部当直连处理。"""

    def test_task_takes_no_ip_and_no_retry_when_off(self):
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(False)):
            self.assertTrue(tasks._proxy_off("河南省"))
            self.assertEqual(tasks._proxy_ip_tries("河南省"), 1, "关了还按地区换 IP 重试")
            self.assertIsNone(tasks._account_proxy("阮言泽", "河南省"), "关了还去取 IP")

    def test_login_takes_no_ip_when_off(self):
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(False)):
            self.assertIsNone(qr_login._login_proxy("河南省"), "关了扫码登录还去取 IP")

    def test_no_region_is_always_direct(self):
        # 没设地区，无论开关如何都是直连
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(True)):
            self.assertTrue(tasks._proxy_off(""))
            self.assertEqual(tasks._proxy_ip_tries(""), 1)
            self.assertIsNone(tasks._account_proxy("阮言泽", ""))


class SwitchOnKeepsProxyTests(unittest.TestCase):
    """开关开着 + 设了地区：仍然走「按地区换 IP 重试」那套。"""

    def test_task_enables_ip_and_retry_when_on(self):
        with patch.object(proxy_mod, "load_proxy", return_value=cfg(True)):
            tasks.config["proxyIpTries"] = 3
            self.assertFalse(tasks._proxy_off("河南省"))
            self.assertEqual(tasks._proxy_ip_tries("河南省"), 3)


if __name__ == "__main__":
    unittest.main()
