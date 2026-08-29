"""地区读取的行为测试：真的把 index.html 里那几个函数跑一遍。

这块的 bug 不会报错、不会弹提示，只会让账号的「上网地区」悄悄变空，
下次跑任务就从机房 IP 出去了。字符串断言看不出这种事，得真跑。
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

HENAN = [{"code": "410000", "name": "河南省", "cities": [{"code": "410700", "name": "新乡市"}]}]


def slice_fn(start: str, end: str) -> str:
    a = INDEX.index(start)
    b = INDEX.index(end, a)
    return INDEX[a:b]


HARNESS = """
%(source)s
const cases = %(cases)s;
const out = [];
for (const c of cases) {
  window.__regions = c.regions;
  window.__addRegionSaved = c.saved || "";
  if (c.kind === "read") {
    const node = {
      querySelector: (sel) => (sel in c.dom ? { value: c.dom[sel] } : null),
    };
    out.push(readRegion(node));
  } else {
    globalThis.$ = (id) => (id in c.dom ? { value: c.dom[id] } : null);
    out.push(addRegion());
  }
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "没装 node，跳过前端行为测试")
class RegionReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = "const window = globalThis;\n" + slice_fn(
            "    function regionsReady() {", "    async function loadRegions() {"
        ) + slice_fn("    function addRegion() {", "    function syncAddRegionLock() {")

    def run_cases(self, cases: list) -> list:
        script = HARNESS % {"source": self.source, "cases": json.dumps(cases)}
        with tempfile.TemporaryDirectory() as box:
            path = Path(box) / "case.js"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [NODE, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip())

    def test_reads_the_selected_city(self):
        got = self.run_cases([
            {
                "kind": "read",
                "regions": HENAN,
                "dom": {".region-code": "410000", ".region-province": "410000", ".region-city": "410700"},
            }
        ])
        self.assertEqual(got, ["410700"], "选到市就该用市码，比省码更准")

    def test_province_only_means_whole_province(self):
        got = self.run_cases([
            {
                "kind": "read",
                "regions": HENAN,
                "dom": {".region-code": "410000", ".region-province": "410000", ".region-city": ""},
            }
        ])
        self.assertEqual(got, ["410000"])

    def test_user_can_still_clear_the_region(self):
        """地区表在的时候选「不设置（直连）」必须真的清掉，不能被兜底逻辑挡住。"""
        got = self.run_cases([
            {
                "kind": "read",
                "regions": HENAN,
                "dom": {".region-code": "410700", ".region-province": "", ".region-city": ""},
            }
        ])
        self.assertEqual(got, [""])

    def test_missing_region_table_keeps_the_saved_value(self):
        """/api/regions 挂一次，下拉就是空的。那不是「用户选了直连」，
        当成直连的话随便点一次保存就把这个号的代理关了，而且没有任何提示。"""
        for regions in ([], None):
            got = self.run_cases([
                {
                    "kind": "read",
                    "regions": regions,
                    "dom": {".region-code": "410700", ".region-province": "", ".region-city": ""},
                }
            ])
            self.assertEqual(got, ["410700"], f"regions={regions!r} 时不能把地区读成空")

    def test_readonly_card_keeps_the_saved_value(self):
        got = self.run_cases([
            {"kind": "read", "regions": HENAN, "dom": {".region-code": "410700"}}
        ])
        self.assertEqual(got, ["410700"])

    def test_add_modal_keeps_region_when_table_missing(self):
        got = self.run_cases([
            {
                "kind": "add",
                "regions": [],
                "saved": "410700",
                "dom": {"addProvince": "", "addCity": ""},
            },
            {
                "kind": "add",
                "regions": HENAN,
                "saved": "410700",
                "dom": {"addProvince": "410000", "addCity": "410700"},
            },
        ])
        self.assertEqual(got, ["410700", "410700"])


if __name__ == "__main__":
    unittest.main()
