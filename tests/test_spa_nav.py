import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PY = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")
INDEX = (ROOT / "webui" / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webui" / "static" / "app.css").read_text(encoding="utf-8")


class SpaNavTests(unittest.TestCase):
    def test_mine_route_is_spa(self):
        self.assertIn('"/mine"', APP_PY)
        self.assertIn('@app.get("/mine"', APP_PY)

    def test_mine_page_has_password_and_wechat(self):
        self.assertIn('id="page-mine"', INDEX)
        self.assertIn('data-page="mine"', INDEX)
        self.assertIn("修改密码", INDEX)
        self.assertIn("绑定微信", INDEX)
        self.assertIn('id="profileOldPass"', INDEX)
        self.assertIn('id="profileWxCode"', INDEX)
        self.assertNotIn('id="profileModal"', INDEX)
        self.assertNotIn('id="wpBindCard"', INDEX)
        self.assertNotIn('id="expiresCard"', INDEX)
        self.assertIn("dash-admin-cards", INDEX)
        self.assertIn("选择好友和群聊", INDEX)
        self.assertIn("function accountHasCookie", INDEX)
        self.assertIn('class="cookies-set"', INDEX)
        self.assertIn("accountHasCookie(item)", INDEX)
        self.assertIn("bindTargetPickerClicks", INDEX)
        self.assertIn("sortPickerRows", INDEX)
        self.assertIn("plausibleSpark", INDEX)
        self.assertIn("CHAT_CACHE_VER", INDEX)
        self.assertIn("正在读取账号快照", INDEX)
        self.assertIn("force: !!force", INDEX)
        self.assertIn("CHAT_CACHE_VER = 9", INDEX)
        self.assertIn("这次没刷新到火花，请重新扫码后再检测", INDEX)
        self.assertIn("force=bool(payload.get(\"force\"))", APP_PY)
        self.assertIn('data-name="${escapeAttr(name)}"', INDEX)
        self.assertNotIn("toggleTargetRow(${JSON.stringify(name)})", INDEX)
        self.assertIn("function avatarImg", INDEX)
        self.assertIn("target-friend-list", INDEX)
        self.assertIn("account-head-copy", INDEX)
        self.assertIn("max-height: 270px", CSS)
        self.assertIn("user-edit-grid", INDEX)
        self.assertIn("user-edit-grid", CSS)
        self.assertIn("到期时间", INDEX)
        self.assertLess(INDEX.find("重置密码"), INDEX.find("user-edit-grid"))
        self.assertLess(INDEX.find("class=\"reset-role\""), INDEX.find("class=\"max-accounts\""))
        self.assertLess(INDEX.find("expires-mode"), INDEX.find("class=\"extra-days\""))
        self.assertIn("0 为永久", INDEX)
        self.assertIn('value="permanent"', INDEX)

    def test_mobile_uses_bottom_tabs(self):
        self.assertIn("bottom: 0", CSS)
        self.assertIn(".sidebar .nav-btn.admin-only", CSS)
        self.assertIn("88px + env(safe-area-inset-bottom)", CSS)


if __name__ == "__main__":
    unittest.main()
