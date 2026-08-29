"""打开抖音私信页只等响应头，别等页面加载完。

私信页是个很重的 IM 应用。等 load / domcontentloaded 意味着要等所有同步脚本
（甚至图片）跑完，走住宅代理时 45 秒都不够 —— 线上就是这么超时的。
我们真正关心的是「会话列表元素出没出来」，那件事每个调用点后面都有专门的轮询在等，
所以导航只需要等到 commit。
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 打开私信页的三个入口：检测、选好友、续火花
CHAT_ENTRIES = {
    "webui/cookie_probe.py": "检测",
    "webui/chat_list.py": "选好友",
    "core/tasks.py": "续火花",
}


def goto_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # page.goto(...) 和 retry_operation(..., page.goto, ...) 两种写法都要看
        if isinstance(func, ast.Attribute) and func.attr == "goto":
            yield node
        elif isinstance(func, ast.Name) and func.id == "retry_operation":
            yield node


def chat_gotos(path: Path):
    """只挑真正打开 /chat 的那些调用。"""
    for call in goto_calls(path):
        blob = ast.dump(call)
        if "/chat" in blob or "CHAT" in blob:
            yield call


class ChatNavigationTests(unittest.TestCase):
    def test_every_chat_entry_only_waits_for_commit(self):
        for rel, label in CHAT_ENTRIES.items():
            path = ROOT / rel
            calls = list(chat_gotos(path))
            self.assertTrue(calls, f"{rel} 里没找到打开私信页的调用，测试该更新了")
            for call in calls:
                waits = [kw.value for kw in call.keywords if kw.arg == "wait_until"]
                self.assertTrue(
                    waits,
                    f"{label}（{rel}:{call.lineno}）没写 wait_until，会用默认的 load，走代理必超时",
                )
                self.assertEqual(
                    getattr(waits[0], "value", None),
                    "commit",
                    f"{label}（{rel}:{call.lineno}）等的不是 commit",
                )

    def test_chat_entries_give_the_proxy_a_longer_element_wait(self):
        """导航快了，省下来的时间要留给等会话列表，而不是白白缩短。"""
        for rel in ("webui/cookie_probe.py", "webui/chat_list.py"):
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("if proxy else", source, f"{rel} 的等待时长没有区分代理和直连")


class TaskListBudgetTests(unittest.TestCase):
    """续火花要真的等到好友列表出来，代理下的预算必须和检测那边一样宽。

    线上出过「检测说正常、续火花却打不开会话列表」：检测给会话列表 25 秒，
    续火花只给 15 秒，慢代理下就差这一截。
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "core" / "tasks.py").read_text(encoding="utf-8")

    def test_conversation_list_wait_is_proxy_aware_and_generous(self):
        self.assertIn("40000 if slow else 15000", self.source)

    def test_task_knows_whether_it_is_behind_a_proxy(self):
        self.assertIn("slow = bool(proxy)", self.source)


class ProbeNoiseTests(unittest.TestCase):
    """已经处理好的超时不该甩一大段 traceback，用户会以为程序崩了。"""

    def test_chat_timeout_logs_one_line(self):
        source = (ROOT / "webui" / "cookie_probe.py").read_text(encoding="utf-8")
        start = source.index("打开私信页超时")
        line = source[source.rindex("\n", 0, start):source.index("\n", start)]
        self.assertNotIn("exc_info=True", line)

    def test_blocked_page_fetch_logs_one_line(self):
        source = (ROOT / "webui" / "qr_login.py").read_text(encoding="utf-8")
        self.assertIn("页面内取码被抖音安全 SDK 拦掉了", source)


if __name__ == "__main__":
    unittest.main()
