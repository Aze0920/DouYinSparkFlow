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
