"""代理总开关关掉后，前端不该再显示任何「上网地区」的东西。

需求原话：把后台的 IP 关了，账号卡片和「添加账号」都不用显示地区。
关键是：藏归藏，账号里原来存好的地区码不能被弄丢——将来重开代理还得用。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INDEX = (ROOT / "webui" / "templates" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def slice_fn(start: str, end: str) -> str:
    a = INDEX.index(start)
    b = INDEX.index(end, a)
    return INDEX[a:b]


class SourceGuardTests(unittest.TestCase):
    """不依赖 node，直接盯住几处关键判断在，防止以后被人删掉。"""

    def test_region_field_hides_when_proxy_off(self):
        body = slice_fn("    function regionFieldHtml(", "    function onProvinceChange(")
        self.assertIn("if (!window.__proxyEnabled)", body)
        self.assertIn('class="region-code"', body, "藏起来时也得留隐藏域存住地区码")

    def test_add_dialog_gates_on_proxy_switch(self):
        choose = slice_fn("    function chooseAddAccount(", "    function closeCkModal(")
        self.assertIn("window.__proxyEnabled", choose, "关了代理还拦着要选地区就没法登录了")
        lock = slice_fn("    function syncAddRegionLock(", "    function closeAddAccount(")
        self.assertIn("window.__proxyEnabled", lock)

    def test_status_and_config_carry_the_flag(self):
        app = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(app.count('"proxy_enabled": proxy_enabled()'), 2, "config 和 status 两个接口都要带这个开关")


HARNESS = """
const window = globalThis;
window.__regions = [];
function escapeAttr(s) { return String(s == null ? "" : s); }
function escapeHtml(s) { return String(s == null ? "" : s); }
function provinceOf() { return null; }
%(source)s
const out = [];
for (const c of %(cases)s) {
  window.__proxyEnabled = c.enabled;
  out.push(regionFieldHtml(c.region, 0, true));
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "没装 node，跳过前端行为测试")
class RegionFieldBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # regionFieldHtml 关代理时会提前 return，只用到 escapeAttr，其它依赖不会触发
        cls.source = slice_fn(
            "    function regionFieldHtml(", "    function onProvinceChange("
        )

    def run_cases(self, cases):
        script = HARNESS % {"source": self.source, "cases": json.dumps(cases)}
        with tempfile.TemporaryDirectory() as box:
            path = Path(box) / "case.js"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [NODE, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip())

    def test_proxy_off_renders_hidden_only_and_keeps_the_code(self):
        [html] = self.run_cases([{"enabled": False, "region": "410700"}])
        self.assertIn('class="region-code"', html)
        self.assertIn('value="410700"', html, "地区码必须原样留在隐藏域里，不能丢")
        self.assertNotIn("region-province", html, "关了代理就不该再渲染省市下拉")
        self.assertNotIn("上网地区", html, "连标题都不该出现")

    def test_proxy_on_renders_the_full_selector(self):
        [html] = self.run_cases([{"enabled": True, "region": "410700"}])
        self.assertIn("region-province", html, "开着代理要照常显示省市下拉")
        self.assertIn("上网地区", html)


if __name__ == "__main__":
    unittest.main()
