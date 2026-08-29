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
        self.assertIn("function copyAccountCk", INDEX)
        self.assertIn("复制CK", INDEX)
        self.assertIn("has-copy-ck", INDEX)
        self.assertIn("has-copy-ck", CSS)
        self.assertIn("只有管理员可以复制 Cookie", INDEX)
        start = APP_PY.find('@app.post("/api/account/copy-cookies")')
        self.assertGreaterEqual(start, 0)
        nxt = APP_PY.find("@app.post(", start + 10)
        chunk = APP_PY[start:nxt if nxt > start else start + 500]
        self.assertIn("require_admin", chunk)
        self.assertNotIn("require_spark", chunk)
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
        self.assertIn("function saveAllSettings", INDEX)
        self.assertIn("function syncSettingsVisibility", INDEX)
        self.assertIn("function sendBroadcastNotify", INDEX)
        self.assertIn("启用 NotifyX", INDEX)
        self.assertIn('id="nxApiKey"', INDEX)
        self.assertIn("settings-if-nx", INDEX)
        self.assertIn("send_notifyx", APP_PY)
        self.assertIn("settings-row", CSS)
        self.assertIn("settings-broadcast", CSS)
        self.assertIn('onclick="saveAllSettings()"', INDEX)
        self.assertNotIn("保存邀请设置", INDEX)
        self.assertNotIn("保存并发", INDEX)
        self.assertIn("到期提醒", INDEX)
        self.assertIn("充值成功", INDEX)
        self.assertIn("邀请成功", INDEX)
        self.assertIn("发给已绑定用户", INDEX)
        self.assertIn('id="notifyBroadcastBody"', INDEX)
        start = APP_PY.find('@app.post("/api/settings/notify/broadcast")')
        self.assertGreaterEqual(start, 0)
        nxt = APP_PY.find("@app.", start + 10)
        chunk = APP_PY[start:nxt if nxt > start else start + 400]
        self.assertIn("require_admin", chunk)
        self.assertIn("broadcast_to_bound", chunk)
        self.assertIn("tick_expire_reminders()", APP_PY)
        self.assertIn("notify_recharge_success", APP_PY)
        self.assertIn("render_wxpusher_html", (ROOT / "webui" / "notify.py").read_text(encoding="utf-8"))
        self.assertIn("user-row-fields", INDEX)
        self.assertIn("user-row-fields", CSS)
        self.assertIn("到期时间", INDEX)
        self.assertLess(INDEX.find("class=\"reset-role\""), INDEX.find("class=\"max-accounts\""))
        self.assertLess(INDEX.find("expires-mode"), INDEX.find("class=\"extra-days\""))
        self.assertIn("0 为永久", INDEX)
        self.assertIn('value="permanent"', INDEX)

    def test_user_management_is_a_one_per_row_list(self):
        self.assertIn('id="userList" class="users-list"', INDEX)
        self.assertIn(".users-list", CSS)
        self.assertIn("flex-direction: column", CSS)
        self.assertIn("user-row", INDEX)
        self.assertIn(".user-row {", CSS)
        self.assertNotIn('class="users-grid"', INDEX)
        self.assertNotIn('class="user-card"', INDEX)

    def test_accounts_page_shows_count_and_paginates_by_ten(self):
        self.assertIn('id="accountsTitle"', INDEX)
        self.assertIn("抖音账号【${list.length}】", INDEX)
        self.assertIn("const ACCOUNTS_PER_PAGE = 10;", INDEX)
        self.assertIn('id="accountsPager" class="page-bar hidden"', INDEX)
        self.assertIn("function gotoAccountsPage", INDEX)
        self.assertIn(".page-bar {", CSS)
        # 翻页后要按全局下标定位，否则会保存/续火花到别的账号上
        self.assertIn("accountCard(item, start + offset)", INDEX)
        self.assertIn("function accountNode", INDEX)
        self.assertNotIn('document.querySelectorAll(".account")[index]', INDEX)

    def test_users_page_shows_count_and_paginates_by_ten(self):
        self.assertIn('id="usersTitle"', INDEX)
        self.assertIn("控制台用户【${all.length}】", INDEX)
        self.assertIn("const USERS_PER_PAGE = 10;", INDEX)
        self.assertIn('id="usersPager" class="page-bar hidden"', INDEX)
        self.assertIn("function gotoUsersPage", INDEX)
        self.assertIn("function renderUsers", INDEX)
        self.assertIn("USERS_PER_PAGE)", INDEX)

    def test_account_card_has_province_city_selects(self):
        self.assertIn("function regionFieldHtml", INDEX)
        self.assertIn("function onProvinceChange", INDEX)
        self.assertIn("function readRegion", INDEX)
        self.assertIn("region-province", INDEX)
        self.assertIn("region-city", INDEX)
        self.assertIn("不设置（直连）", INDEX)
        self.assertIn("全省随机", INDEX)
        self.assertIn("region: readRegion(node)", INDEX)
        self.assertIn(".region-selects", CSS)
        # 地区表必须先加载，否则下拉渲染成空的
        self.assertIn("await loadRegions();", INDEX)

    def test_proxy_settings_block(self):
        for el in ("pxEnabled", "pxApiUrl", "pxProtocol", "pxMinute", "pxRetries", "pxStatus"):
            self.assertIn(f'id="{el}"', INDEX)
        self.assertIn("function fillProxySettings", INDEX)
        self.assertIn("function saveProxySettings", INDEX)
        self.assertIn("function testProxySettings", INDEX)
        self.assertIn("settings-if-px", INDEX)
        self.assertIn("await saveProxySettings(true);", INDEX)

    def test_masked_proxy_url_is_not_resubmitted_as_real(self):
        """回显的是打码链接，原样提交会把真密钥冲掉，所以要判等后置空。"""
        start = INDEX.find("async function saveProxySettings")
        self.assertGreaterEqual(start, 0)
        self.assertIn("window.__proxyMasked", INDEX[start:start + 900])

    def test_cards_and_users_have_search_boxes(self):
        for input_id, clear_id in (("cardSearch", "cardSearchClear"), ("userSearch", "userSearchClear")):
            self.assertIn(f'id="{input_id}"', INDEX)
            self.assertIn(f'id="{clear_id}"', INDEX)
        self.assertIn("function filteredCards", INDEX)
        self.assertIn("function filteredUsers", INDEX)
        self.assertIn("function onCardSearch", INDEX)
        self.assertIn("function onUserSearch", INDEX)
        self.assertIn("function matchesQuery", INDEX)
        self.assertIn(".search-box", CSS)
        # 搜索按 code/用户名匹配，分页要基于过滤后的列表
        self.assertIn("[item.code, item.note, item.used_by]", INDEX)
        self.assertIn("[u.username, u.card_code]", INDEX)
        self.assertIn("const list = filteredCards();", INDEX)
        self.assertIn("const list = filteredUsers();", INDEX)

    def test_search_resets_so_new_rows_are_visible(self):
        """带着搜索词生成卡密/新建用户，新数据会被过滤掉，看起来像没成功。"""
        start = INDEX.find("async function generateCards()")
        self.assertGreaterEqual(start, 0)
        self.assertIn("clearCardSearch();", INDEX[start:start + 900])
        start = INDEX.find("async function createUser()")
        self.assertGreaterEqual(start, 0)
        self.assertIn("clearUserSearch();", INDEX[start:start + 900])

    def test_page_bar_styles_do_not_clash_with_card_table_pager(self):
        """卡密表格用的是 .pager，账号/用户分页必须用独立类名，否则会改掉卡密页外观。"""
        self.assertIn('id="cardPager"', INDEX)
        self.assertIn(".pager button.active", CSS)
        self.assertEqual(CSS.count(".pager {"), 1, "卡密表格的 .pager 被重复定义会改掉它的外观")
        self.assertIn(".page-bar-num.is-current", CSS)
        self.assertNotIn('class="pager hidden"', INDEX)

    def test_dashboard_admin_stat_cards(self):
        self.assertIn("dash-admin-stats", INDEX)
        self.assertIn(".dash-admin-stats", CSS)
        # 整块挂 admin-only，普通用户看不到这些全站统计
        start = INDEX.find('class="cards admin-only hidden dash-admin-stats"')
        self.assertGreaterEqual(start, 0)
        block = INDEX[start:INDEX.find("dash-body", start)]
        for element_id in ('id="accountCount"', 'id="userCount"', 'id="cardCount"', 'id="adminSlotCard"'):
            self.assertIn(element_id, block)
        self.assertIn("当前账号数量", block)
        self.assertIn("当前用户数量", block)
        self.assertIn("卡密数量", block)
        self.assertIn("card-empty", block)
        self.assertIn(".card-empty", CSS)

    def test_status_reports_admin_only_totals(self):
        start = APP_PY.find('payload["total_accounts"]')
        self.assertGreaterEqual(start, 0)
        chunk = APP_PY[start:start + 400]
        self.assertIn('payload["total_users"] = len(load_users())', chunk)
        self.assertIn('payload["total_cards"]', chunk)
        self.assertIn('payload["unused_cards"]', chunk)
        # 必须留在 _is_admin 分支里
        guard = APP_PY.rfind("if _is_admin(user):", 0, start)
        self.assertGreaterEqual(guard, 0)
        self.assertNotIn("return payload", APP_PY[guard:start])

    def test_create_forms_sit_on_one_row(self):
        self.assertIn("form-row form-row-6", INDEX)
        self.assertIn("form-row form-row-5", INDEX)
        self.assertIn(".form-row-6 {", CSS)
        self.assertIn(".form-row-5 {", CSS)
        # 按钮不再靠 &nbsp; 撑成一个假 label
        self.assertNotIn('<label class="span-2">&nbsp;<button class="btn-primary" onclick="createUser()"', INDEX)
        self.assertNotIn('<label class="span-2">&nbsp;<button class="btn-primary" onclick="generateCards()"', INDEX)

    def test_collect_accounts_keeps_rows_outside_current_page(self):
        start = INDEX.find("function collectAccounts()")
        self.assertGreaterEqual(start, 0)
        chunk = INDEX[start:start + 700]
        self.assertIn("(window.__accounts || []).slice()", chunk)
        self.assertIn("node.dataset.index", chunk)

    def test_github_update_uses_current_mirrors(self):
        self.assertIn("ghfast.top", APP_PY)
        self.assertIn("cdn.jsdelivr.net", APP_PY)
        self.assertIn("GIT_SSL_NO_VERIFY", APP_PY)
        self.assertIn("http.sslVerify=false", APP_PY)
        self.assertIn("ssl_verify=False", APP_PY)
        self.assertIn("MIRROR_HINTS", APP_PY)

    def test_mobile_uses_bottom_tabs(self):
        self.assertIn("bottom: 0", CSS)
        self.assertIn(".sidebar .nav-btn.admin-only", CSS)
        self.assertIn("88px + env(safe-area-inset-bottom)", CSS)


class PublicOriginTests(unittest.TestCase):
    def test_omits_default_ports_keeps_custom(self):
        from webui.origin import public_origin

        self.assertEqual(public_origin("https", "douyin.lxmz.fun"), "https://douyin.lxmz.fun")
        self.assertEqual(public_origin("https", "douyin.lxmz.fun:443"), "https://douyin.lxmz.fun")
        self.assertEqual(public_origin("http", "127.0.0.1:80"), "http://127.0.0.1")
        self.assertEqual(public_origin("http", "127.0.0.1:8888"), "http://127.0.0.1:8888")
        self.assertEqual(public_origin("https", "douyin.lxmz.fun", "8443"), "https://douyin.lxmz.fun:8443")
        self.assertEqual(public_origin("https", "douyin.lxmz.fun:8443", "443"), "https://douyin.lxmz.fun:8443")
        self.assertEqual(public_origin("http", "[::1]:9000"), "http://[::1]:9000")
        self.assertIn("encodeURIComponent(data.code)", INDEX)


if __name__ == "__main__":
    unittest.main()
