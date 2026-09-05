"""开机自启单元：只检查仓库里的模板和安装脚本，不真的调 systemd。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "deploy" / "douyin-sparkflow.service"
INSTALL = ROOT / "deploy" / "install-service.sh"
START = ROOT / "start.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


class SystemdUnitTests(unittest.TestCase):
    def test_unit_survives_reboot(self):
        text = read(UNIT)
        self.assertIn("Restart=always", text)
        self.assertIn("WantedBy=multi-user.target", text)
        self.assertIn("python -m webui.app", text)
        self.assertIn("HEADLESS=true", text)
        self.assertIn("__APP_DIR__", text)
        self.assertNotIn("\ufffd", text)
        self.assertNotIn("\ufeff", text)

    def test_install_script_enables_and_starts(self):
        text = read(INSTALL)
        self.assertIn("systemctl enable --now", text)
        self.assertIn("daemon-reload", text)
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))

    def test_start_sh_defers_to_systemd(self):
        text = read(START)
        self.assertIn("is-enabled --quiet douyin-sparkflow", text)
        self.assertRegex(text, r"systemctl start douyin-sparkflow")


if __name__ == "__main__":
    unittest.main()
