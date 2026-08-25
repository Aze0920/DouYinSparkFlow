import os, sys
import subprocess
import traceback
from playwright.sync_api import sync_playwright
from utils.config import DEBUG, get_environment, Environment

PLAYWRIGHT_BROWSERS_PATH = "../chrome"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def install_browser():
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    subprocess.run(cmd, check=True)
    print("浏览器安装完成")


def get_browser(retried=False):
    env = get_environment()
    explicit_headless = os.getenv("HEADLESS")
    if explicit_headless is not None:
        headless = explicit_headless.strip().lower() in ("1", "true", "yes")
    elif env == Environment.LOCAL and DEBUG and os.name == "nt":
        headless = False
    else:
        headless = True

    # Linux 服务器用 Playwright 默认缓存；Windows 本地才用项目里的 chrome 目录
    if os.name == "nt" and env == Environment.LOCAL:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.abspath(
            os.path.join(os.path.dirname(__file__), PLAYWRIGHT_BROWSERS_PATH)
        )
        if DEBUG and explicit_headless is None:
            headless = False
    elif env == Environment.PACKED:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.abspath(
            os.path.join(os.path.dirname(sys.executable), PLAYWRIGHT_BROWSERS_PATH)
        )
    else:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        return playwright, browser
    except Exception as e:
        if "Executable doesn't exist" in str(e) and env != Environment.GITHUBACTION and not retried:
            print("浏览器可执行文件不存在，正在安装 Chromium...")
            install_browser()
            return get_browser(retried=True)
        traceback.print_exc()
        raise


def make_context(browser):
    context = browser.new_context(
        user_agent=BROWSER_UA,
        locale="zh-CN",
        viewport={"width": 1280, "height": 860},
    )
    context.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9"})
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return context
