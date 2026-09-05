"""前端静态资源的结构自检。

这些不是渲染测试，而是挡住几类会静默上线的低级错误：
文件编码坏掉、CSS 括号不配对、HTML 标签数量对不上、
以及删掉某个组件后忘了同步删掉它的样式/脚本（留下永远不生效的死规则）。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "webui" / "static" / "app.css"
HTML = ROOT / "webui" / "templates" / "index.html"


def read(path: Path) -> str:
    # 用严格模式：任何非法 UTF-8 字节都会在这里直接抛错
    return path.read_text(encoding="utf-8", errors="strict")


class EncodingTests(unittest.TestCase):
    def test_files_are_clean_utf8(self):
        for path in (CSS, HTML):
            text = read(path)
            self.assertNotIn("\ufffd", text, f"{path.name} 里有替换字符，说明编码已经坏了")
            self.assertNotIn("\ufeff", text, f"{path.name} 不该带 BOM")

    def test_chinese_still_readable(self):
        # 挑几个必然存在的中文串，编码一旦串味这里就会挂
        html = read(HTML)
        for word in ("仪表盘", "账号列表", "立即续火花", "今日不再提醒"):
            self.assertIn(word, html)


class CssStructureTests(unittest.TestCase):
    def test_braces_balanced(self):
        text = read(CSS)
        self.assertEqual(text.count("{"), text.count("}"), "CSS 大括号不配对")

    def test_media_queries_balanced(self):
        text = read(CSS)
        depth = 0
        for ch in text:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                self.assertGreaterEqual(depth, 0, "CSS 出现多余的右大括号")
        self.assertEqual(depth, 0)

    def test_no_dead_component_rules(self):
        """删掉的组件不能在 CSS 里留下永远不生效的规则。"""
        text = read(CSS)
        for dead in (".menu-btn", ".sidebar-backdrop", "sidebar-open"):
            self.assertNotIn(dead, text, f"CSS 里还留着已删除组件 {dead} 的样式")


class AccountFoldTests(unittest.TestCase):
    """手机端账号卡折叠：只影响手机，且不能把可编辑字段搬出卡片。"""

    def test_desktop_wrapper_is_layout_neutral(self):
        css = read(CSS)
        # display:contents 让包装层在桌面端不参与布局，卡片间距才保持原样
        self.assertRegex(css, r"\.account-body\s*\{\s*display:\s*contents;\s*\}")

    def test_mobile_collapses_by_default(self):
        mobile = read(CSS).split("@media (max-width: 860px)", 1)[1]
        self.assertRegex(mobile, r"\.account-body\s*\{\s*display:\s*none;")
        self.assertIn(".account.is-open > .account-body", mobile)

    def test_body_wraps_all_editable_fields(self):
        """折叠靠隐藏 .account-body，字段必须都在里面；
        漏在外面的字段收起时会露馅，读取逻辑也会错位。"""
        html = read(HTML)
        card = html.split("function accountCard(", 1)[1].split("\n    }", 1)[0]
        body = card.split('<div class="account-body">', 1)[1]
        for field in ("cron-time", "message-template", "targets-json", "account-actions", "unique-id"):
            self.assertIn(field, body, f"{field} 不在 .account-body 里，收起时会露出来")

    def test_toggle_ignores_clicks_on_buttons(self):
        """头部有「检测 / 删除」，折叠不能抢走它们的点击。"""
        html = read(HTML)
        fn = html.split("function toggleAccountCard(", 1)[1].split("\n    }", 1)[0]
        self.assertIn('closest("button")', fn)


class HtmlStructureTests(unittest.TestCase):
    def test_div_tags_balanced(self):
        text = read(HTML)
        opens = len(re.findall(r"<div\b", text))
        closes = len(re.findall(r"</div>", text))
        self.assertEqual(opens, closes, "index.html 的 div 开合数量对不上")

    def test_no_dead_component_markup(self):
        text = read(HTML)
        for dead in ("menu-btn", "sidebarBackdrop", "closeMobileSidebar", "sidebar-open"):
            self.assertNotIn(dead, text, f"index.html 里还引用着已删除的 {dead}")

    def test_mobile_viewport_meta_present(self):
        text = read(HTML)
        self.assertIn('name="viewport"', text)
        self.assertIn("viewport-fit=cover", text)

    def test_boot_does_not_poll_github(self):
        text = read(HTML)
        self.assertNotIn("checkGithub(true)", text)
        self.assertIn("/api/logs?lines=4000", text)

    def test_every_onclick_handler_is_defined(self):
        """onclick 里调用的函数必须真的在页面脚本里定义，否则手机上点了没反应。"""
        text = read(HTML)
        keywords = {"if", "for", "while", "switch", "return", "typeof", "void"}
        called = set(re.findall(r'onclick="(?:return\s+)?([A-Za-z_$][\w$]*)\s*\(', text)) - keywords
        defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", text))
        missing = sorted(called - defined)
        self.assertEqual(missing, [], f"这些 onclick 处理函数没有定义：{missing}")


if __name__ == "__main__":
    unittest.main()
