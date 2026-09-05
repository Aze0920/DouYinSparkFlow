"""版本检测 / 更新用的镜像列表：别再把已死域名和 IPv6 101 当成「镜像掉了」。"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "webui" / "app.py"


class MirrorListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP.read_text(encoding="utf-8")

    def test_drops_dead_gitmirror_hostname(self):
        self.assertNotIn("raw.gitmirror.com", self.source)

    def test_version_check_forces_ipv4(self):
        self.assertIn('local_address="0.0.0.0"', self.source)
        self.assertIn("def _httpx_client", self.source)

    def test_ssh_over_443_is_available_as_last_resort(self):
        """HTTPS 整片被挡时，SSH over 443 往往还通，必须留这条路。"""
        self.assertIn("ssh://git@ssh.github.com:443/{repo}.git", self.source)
        self.assertIn("StrictHostKeyChecking=accept-new", self.source)
        self.assertIn("BatchMode=yes", self.source)

    def test_version_check_falls_back_to_git(self):
        """一串 raw/CDN 域名随时会死，git 通了版本检测就该跟着通。"""
        self.assertIn("def _version_via_git", self.source)
        self.assertIn("FETCH_HEAD:VERSION", self.source)
        tree = ast.parse(self.source)
        fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "fetch_remote_version"
        )
        self.assertIn("_version_via_git", ast.get_source_segment(self.source, fn) or "")

    def test_github_update_can_borrow_residential_proxy(self):
        """抖音代理开关关掉，GitHub 更新仍应能借提取密钥当跳板。"""
        self.assertIn("def _lease_github_proxy", self.source)
        self.assertIn("def fetch_github_proxy", (ROOT / "webui" / "proxy.py").read_text(encoding="utf-8"))
        self.assertIn("http.proxy", self.source)
        self.assertIn("https.proxy", self.source)
        tree = ast.parse(self.source)
        fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "pull_via_mirrors"
        )
        text = ast.get_source_segment(self.source, fn) or ""
        self.assertIn("_lease_github_proxy", text)
        self.assertIn("github.com/", text)

    def test_status_does_not_auto_refresh_github(self):
        """仪表盘轮询不得再偷偷抽住宅 IP 去查版本。"""
        tree = ast.parse(self.source)
        fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "remote_version_fast"
        )
        text = ast.get_source_segment(self.source, fn) or ""
        self.assertNotIn("Thread", text)
        self.assertNotIn("_refresh_remote_version", text)
        self.assertIn("_remote_cache", text)

    def test_stuck_origin_is_not_tried_first(self):
        tree = ast.parse(self.source)
        fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "git_mirror_urls"
        )
        text = ast.get_source_segment(self.source, fn) or ""
        self.assertLess(text.index("GIT_MIRROR_TEMPLATES"), text.index("origin_url()"))


if __name__ == "__main__":
    unittest.main()
