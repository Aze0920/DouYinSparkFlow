import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from utils.logger import LOG_FILE as APP_LOG_PATH, setup_logger
from webui.envfile import cookie_key, env_path, load_env, parse_accounts, write_env
from webui.qr_login import cancel_qr_login, snapshot as qr_snapshot, start_qr_login
from webui.users import (
    admin_count,
    find_user,
    load_users,
    make_token,
    parse_token,
    public_user,
    save_users,
    verify_user,
    _hash_password,
)

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
LOG_FILE = Path(APP_LOG_PATH)
LOCK_FILE = ROOT / "logs" / "task.lock"
DEFAULT_REPO = "Aze0920/DouYinSparkFlow"
logger = setup_logger("app", os.getenv("LOG_LEVEL", "DEBUG"))

app = FastAPI(title="DouYinSparkFlow")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_run_lock = threading.Lock()
_run_state = {"running": False, "message": "空闲", "started_at": 0}
_remote_cache = {"version": "", "sha": "", "ts": 0, "busy": False}


@app.middleware("http")
async def log_api_calls(request: Request, call_next):
    path = request.url.path
    quiet = path in {
        "/api/status",
        "/api/logs",
        "/api/me",
        "/api/douyin/login/status",
        "/favicon.ico",
        "/",
    } or path.startswith("/static/")
    if not quiet:
        logger.info("请求 %s %s", request.method, path)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("请求异常 %s %s", request.method, path)
        raise
    if not quiet and response.status_code >= 400:
        logger.warning("请求失败 %s %s -> %s", request.method, path, response.status_code)
    return response


@app.on_event("startup")
def on_startup():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger.info("控制台启动 version=%s cwd=%s log=%s", read_version(), os.getcwd(), LOG_FILE)


def read_version() -> str:
    if VERSION_FILE.is_file():
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    return "0.0.0"


def repo_name() -> str:
    env = load_env()
    return (env.get("GITHUB_REPO") or os.getenv("GITHUB_REPO") or DEFAULT_REPO).strip()


def web_password() -> str:
    env = load_env()
    return env.get("WEB_PASSWORD") or os.getenv("WEB_PASSWORD") or "sparkflow"


def current_user(request: Request):
    return parse_token(request.cookies.get("dsf_auth") or "")


def authed(request: Request) -> bool:
    return current_user(request) is not None


def require_auth(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def require_admin(request: Request):
    user = require_auth(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def run_git(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def origin_url() -> str:
    if not (ROOT / ".git").exists():
        return ""
    try:
        result = run_git("remote", "get-url", "origin", timeout=5)
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def parse_version_text(text: str) -> str:
    first = (text or "").strip().splitlines()[0].strip() if text else ""
    if first and first[0].isdigit():
        return first
    return ""


def git_remote_version() -> tuple[str, str]:
    """走服务器 origin（包括已配置的 Git 镜像），不走网页 CDN。"""
    if not (ROOT / ".git").exists():
        return "", ""
    try:
        fetch = run_git("fetch", "--prune", "origin", "main", timeout=25)
        if fetch.returncode != 0:
            logger.warning("git fetch origin main 失败: %s", (fetch.stderr or fetch.stdout or "").strip()[:500])
            fetch = run_git("fetch", "--prune", "origin", timeout=25)
            if fetch.returncode != 0:
                logger.warning("git fetch origin 失败: %s", (fetch.stderr or fetch.stdout or "").strip()[:500])
        else:
            logger.debug("git fetch origin main 成功")
    except Exception:
        logger.exception("git fetch 异常")
    version = ""
    sha = ""
    try:
        shown = run_git("show", "origin/main:VERSION", timeout=5)
        if shown.returncode == 0:
            version = parse_version_text(shown.stdout)
    except Exception:
        pass
    try:
        parsed = run_git("rev-parse", "origin/main", timeout=5)
        if parsed.returncode == 0:
            sha = parsed.stdout.strip()
    except Exception:
        pass
    return version, sha


def fetch_remote_version() -> str:
    repo = repo_name()
    stamp = int(time.time())
    headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
    urls = [
        f"https://ghproxy.net/https://raw.githubusercontent.com/{repo}/main/VERSION?t={stamp}",
        f"https://mirror.ghproxy.com/https://raw.githubusercontent.com/{repo}/main/VERSION?t={stamp}",
        f"https://raw.gitmirror.com/{repo}/main/VERSION?t={stamp}",
        f"https://raw.githubusercontent.com/{repo}/main/VERSION?t={stamp}",
    ]
    origin = origin_url()
    if "http" in origin:
        # 如果 origin 本身就是镜像地址，优先按同样前缀拼 raw
        stripped = origin.rstrip("/")
        if stripped.endswith(".git"):
            stripped = stripped[:-4]
        if "github.com/" in stripped:
            prefix, _, path = stripped.partition("github.com/")
            urls.insert(0, f"{prefix}raw.githubusercontent.com/{path}/main/VERSION?t={stamp}")
    for url in urls:
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True, headers=headers) as client:
                resp = client.get(url)
                version = parse_version_text(resp.text if resp.status_code == 200 else "")
                if version:
                    return version
        except Exception:
            continue
    return ""


def local_git_sha() -> str:
    if not (ROOT / ".git").exists():
        return ""
    try:
        result = run_git("rev-parse", "HEAD", timeout=5)
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def fetch_remote_sha() -> str:
    if not (ROOT / ".git").exists():
        return ""
    try:
        result = run_git("ls-remote", "origin", "refs/heads/main", timeout=12)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.split()[0]
    except Exception:
        pass
    return ""


def _refresh_remote_version():
    if _remote_cache["busy"]:
        return
    _remote_cache["busy"] = True
    try:
        version, sha = git_remote_version()
        if version and version != _remote_cache.get("version"):
            logger.info("远程版本(git)=%s sha=%s", version, sha[:10] if sha else "")
        elif not version:
            version = fetch_remote_version()
            if version and version != _remote_cache.get("version"):
                logger.info("远程版本(HTTP)=%s", version)
            elif not version:
                logger.warning("未能获取远程版本号")
        if not sha:
            sha = fetch_remote_sha()
        if version:
            _remote_cache["version"] = version
        if sha:
            _remote_cache["sha"] = sha
        _remote_cache["ts"] = time.time()
    except Exception:
        logger.exception("刷新远程版本失败")
    finally:
        _remote_cache["busy"] = False


def remote_version_fast() -> str:
    stale = time.time() - _remote_cache["ts"] > 20
    if stale or not _remote_cache["version"]:
        threading.Thread(target=_refresh_remote_version, daemon=True).start()
    return _remote_cache["version"]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "version": read_version(),
        },
    )


@app.get("/api/me")
def me(request: Request):
    user = current_user(request)
    if not user:
        return {"ok": True, "authed": False}
    return {"ok": True, "authed": True, "user": public_user(user)}


@app.post("/api/login")
def login(payload: dict):
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    user = verify_user(username, password)
    if not user:
        logger.warning("登录失败：用户名或密码错误 user=%s", username)
        raise HTTPException(status_code=403, detail="用户名或密码错误")
    logger.info("控制台登录成功 user=%s role=%s", user.get("username"), user.get("role"))
    resp = JSONResponse({"ok": True, "user": public_user(user)})
    resp.set_cookie("dsf_auth", make_token(user["username"]), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("dsf_auth")
    return resp


@app.get("/api/users")
def list_users(request: Request):
    require_admin(request)
    return {"ok": True, "users": [public_user(u) for u in load_users()]}


@app.post("/api/users")
def create_user(request: Request, payload: dict):
    require_admin(request)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    role = str(payload.get("role") or "user").strip() or "user"
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="角色只能是 admin 或 user")
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码都要填")
    if find_user(username):
        raise HTTPException(status_code=400, detail="用户名已存在")
    users = load_users()
    users.append(
        {
            "username": username,
            "password_hash": _hash_password(username, password),
            "role": role,
        }
    )
    save_users(users)
    return {"ok": True, "users": [public_user(u) for u in users]}


@app.post("/api/users/update")
def update_user(request: Request, payload: dict):
    admin = require_admin(request)
    username = str(payload.get("username") or "").strip()
    user = find_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    users = load_users()
    role = payload.get("role")
    password = payload.get("password")
    for item in users:
        if item.get("username") != username:
            continue
        if role in ("admin", "user"):
            if item.get("role") == "admin" and role != "admin" and admin_count(users) <= 1:
                raise HTTPException(status_code=400, detail="至少保留一个管理员")
            item["role"] = role
        if password:
            item["password_hash"] = _hash_password(username, str(password))
        break
    save_users(users)
    return {"ok": True, "users": [public_user(u) for u in users]}


@app.post("/api/users/delete")
def delete_user(request: Request, payload: dict):
    admin = require_admin(request)
    username = str(payload.get("username") or "").strip()
    if username == admin.get("username"):
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    users = load_users()
    target = next((u for u in users if u.get("username") == username), None)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.get("role") == "admin" and admin_count(users) <= 1:
        raise HTTPException(status_code=400, detail="至少保留一个管理员")
    users = [u for u in users if u.get("username") != username]
    save_users(users)
    return {"ok": True, "users": [public_user(u) for u in users]}


@app.get("/api/status")
def status(request: Request):
    require_auth(request)
    env = load_env()
    local = read_version()
    remote = remote_version_fast()
    local_sha = local_git_sha()
    remote_sha = _remote_cache.get("sha") or ""
    return {
        "ok": True,
        "local_version": local,
        "remote_version": remote,
        "update_available": bool(
            (remote and remote != local) or (remote_sha and local_sha and remote_sha != local_sha)
        ),
        "github_repo": repo_name(),
        "env_file": str(env_path()),
        "is_git_repo": (ROOT / ".git").exists(),
        "cron": f"{env.get('CRON_HOUR', '9')}:{env.get('CRON_MINUTE', '0').zfill(2)}",
        "tz": env.get("TZ", "Asia/Shanghai"),
        "running": _run_state["running"],
        "run_message": _run_state["message"],
        "accounts": parse_accounts(env),
    }


@app.get("/api/config")
def get_config(request: Request):
    require_auth(request)
    env = load_env()
    try:
        hitokoto = json.loads(env.get("HITOKOTO_TYPES", '["文学","影视","诗词","哲学"]'))
    except json.JSONDecodeError:
        hitokoto = ["文学", "影视", "诗词", "哲学"]
    accounts = []
    for item in parse_accounts(env):
        cookie_raw = env.get(cookie_key(item["unique_id"]), "")
        accounts.append(
            {
                **item,
                "cookies": cookie_raw if isinstance(cookie_raw, str) else json.dumps(cookie_raw, ensure_ascii=False),
            }
        )
    return {
        "cron_hour": int(env.get("CRON_HOUR") or 9),
        "cron_minute": int(env.get("CRON_MINUTE") or 0),
        "cron_second": int(env.get("CRON_SECOND") or 0),
        "tz": env.get("TZ") or "Asia/Shanghai",
        "message_template": env.get("MESSAGE_TEMPLATE")
        or "[盖瑞]今日火花[加一]\\n—— [右边] 每日一言 [左边] ——\\n[API]",
        "hitokoto_types": hitokoto,
        "browser_timeout": int(env.get("BROWSER_TIMEOUT") or 120000),
        "friend_list_wait_time": int(env.get("FRIEND_LIST_WAIT_TIME") or 2000),
        "task_retry_times": int(env.get("TASK_RETRY_TIMES") or 3),
        "log_level": env.get("LOG_LEVEL") or "DEBUG",
        "github_repo": repo_name(),
        "accounts": accounts,
    }


@app.post("/api/config")
def save_config(request: Request, payload: dict):
    require_admin(request)
    accounts = payload.get("accounts") or []
    tasks = []
    extra = {}
    for account in accounts:
        unique_id = str(account.get("unique_id") or "").strip()
        if not unique_id:
            raise HTTPException(status_code=400, detail="每个账号都要填写抖音号/编号")
        targets = account.get("targets") or []
        if isinstance(targets, str):
            targets = [x.strip() for x in targets.replace("，", ",").split(",") if x.strip()]
        tasks.append(
            {
                "username": str(account.get("username") or "账号").strip(),
                "unique_id": unique_id,
                "targets": targets,
            }
        )
        cookies = account.get("cookies")
        if cookies in (None, ""):
            continue
        if isinstance(cookies, str):
            try:
                cookies_obj = json.loads(cookies)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"账号 {unique_id} 的 Cookie 不是合法 JSON") from exc
        else:
            cookies_obj = cookies
        if not isinstance(cookies_obj, list):
            raise HTTPException(status_code=400, detail=f"账号 {unique_id} 的 Cookie 必须是 JSON 数组")
        extra[cookie_key(unique_id)] = cookies_obj

    data = {
        "PROXY_ADDRESS": payload.get("proxy_address") or "",
        "CRON_HOUR": str(payload.get("cron_hour") if payload.get("cron_hour") is not None else 9),
        "CRON_MINUTE": str(payload.get("cron_minute") if payload.get("cron_minute") is not None else 0),
        "CRON_SECOND": str(payload.get("cron_second") if payload.get("cron_second") is not None else 0),
        "TZ": payload.get("tz") or "Asia/Shanghai",
        "MESSAGE_TEMPLATE": payload.get("message_template")
        or "[盖瑞]今日火花[加一]\\n—— [右边] 每日一言 [左边] ——\\n[API]",
        "HITOKOTO_TYPES": payload.get("hitokoto_types") or ["文学", "影视", "诗词", "哲学"],
        "BROWSER_TIMEOUT": str(payload.get("browser_timeout") or 120000),
        "FRIEND_LIST_WAIT_TIME": str(payload.get("friend_list_wait_time") or 2000),
        "TASK_RETRY_TIMES": str(payload.get("task_retry_times") or 3),
        "LOG_LEVEL": payload.get("log_level") or "DEBUG",
        "GITHUB_REPO": payload.get("github_repo") or repo_name(),
        "HEADLESS": "true",
        "TASKS": tasks,
    }
    path = write_env(data, extra)
    logger.info("已保存配置 path=%s accounts=%s", path, len(tasks))
    return {"ok": True, "path": str(path), "account_count": len(tasks)}


@app.get("/api/logs")
def logs(request: Request, lines: int = 200):
    require_auth(request)
    if not LOG_FILE.is_file():
        return {"ok": True, "text": "还没有日志。扫码登录、从 GitHub 更新、续火花都会写到这里。"}
    content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"ok": True, "text": "\n".join(content[-max(20, min(lines, 2000)):])}


def _run_task():
    env = os.environ.copy()
    env["HEADLESS"] = "true"
    env_file = env_path()
    if env_file.is_file():
        env["CONFIG_ENV_FILE"] = str(env_file)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run_state["running"] = True
        _run_state["message"] = "正在执行续火花任务"
        _run_state["started_at"] = time.time()
        logger.info("开始执行续火花任务")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "main.py")],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        extra = (proc.stdout or "") + "\n" + (proc.stderr or "")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"\n----- 手动执行 exit={proc.returncode} -----\n")
            fh.write(extra[-8000:])
        _run_state["message"] = "执行完成" if proc.returncode == 0 else f"执行失败，退出码 {proc.returncode}"
        if proc.returncode == 0:
            logger.info("续火花任务执行完成")
        else:
            logger.error("续火花任务失败 exit=%s", proc.returncode)
    except Exception as exc:
        _run_state["message"] = f"执行异常：{exc}"
        logger.exception("续火花任务异常")
    finally:
        _run_state["running"] = False
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/api/douyin/login/start")
def douyin_login_start(request: Request, payload: dict | None = None):
    require_admin(request)
    if _run_state["running"]:
        logger.warning("扫码登录被拒绝：任务正在运行")
        raise HTTPException(status_code=409, detail="续火花任务正在跑，请等它结束后再扫码")
    payload = payload or {}
    try:
        replace_index = int(payload.get("replace_index", -1))
    except (TypeError, ValueError):
        replace_index = -1
    user = current_user(request) or {}
    logger.info("开始抖音扫码登录 user=%s replace_index=%s", user.get("username"), replace_index)
    return {"ok": True, **start_qr_login(replace_index)}


@app.get("/api/douyin/login/status")
def douyin_login_status(request: Request):
    require_admin(request)
    return {"ok": True, **qr_snapshot(include_cookies=True)}


@app.post("/api/douyin/login/cancel")
def douyin_login_cancel(request: Request):
    require_admin(request)
    logger.info("取消抖音扫码登录")
    return {"ok": True, **cancel_qr_login()}


@app.post("/api/run")
def run_now(request: Request):
    require_auth(request)
    if _run_state["running"]:
        raise HTTPException(status_code=409, detail="已有任务在跑，请等它结束")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务在跑，请等它结束")
    try:
        threading.Thread(target=_run_task, daemon=True).start()
    finally:
        _run_lock.release()
    return {"ok": True, "message": "已开始执行，请看日志"}


@app.post("/api/update")
def update_from_github(request: Request):
    require_auth(request)
    if not (ROOT / ".git").exists():
        raise HTTPException(
            status_code=400,
            detail="当前目录不是 Git 仓库。服务器请先执行：git clone https://github.com/Aze0920/DouYinSparkFlow.git",
        )
    if _run_state["running"]:
        raise HTTPException(status_code=409, detail="任务正在跑，先不要更新")
    old_version = read_version()
    old_sha = local_git_sha()
    logger.info("开始从 GitHub 更新 local=%s sha=%s origin=%s", old_version, (old_sha or "")[:10], origin_url())
    try:
        fetch = run_git("fetch", "origin", timeout=45)
    except subprocess.TimeoutExpired as exc:
        logger.exception("git fetch 超时")
        raise HTTPException(status_code=500, detail="连接 GitHub 超时，请稍后重试") from exc
    if fetch.returncode != 0:
        detail = fetch.stderr or fetch.stdout or "git fetch 失败"
        logger.error("git fetch 失败: %s", detail.strip()[:800])
        raise HTTPException(status_code=500, detail=detail)
    try:
        reset = run_git("reset", "--hard", "origin/main", timeout=20)
    except subprocess.TimeoutExpired as exc:
        logger.exception("git reset 超时")
        raise HTTPException(status_code=500, detail="git reset 超时") from exc
    if reset.returncode != 0:
        detail = reset.stderr or reset.stdout or "git reset 失败"
        logger.error("git reset 失败: %s", detail.strip()[:800])
        raise HTTPException(status_code=500, detail=detail)
    new_version = read_version()
    new_sha = local_git_sha()
    changed = bool(new_sha and new_sha != old_sha)
    logger.info("更新结果 changed=%s %s -> %s", changed, old_version, new_version)
    _remote_cache["version"] = new_version
    _remote_cache["sha"] = new_sha
    _remote_cache["ts"] = time.time()

    if changed:
        def _restart():
            time.sleep(1.2)
            os.execv(sys.executable, [sys.executable, "-m", "webui.app"])

        threading.Thread(target=_restart, daemon=True).start()
        message = f"已更新到 v{new_version}，控制台即将自动重启，请几秒后刷新页面。"
    else:
        message = (
            f"GitHub 上还是 v{new_version}，没有拉到新代码。"
            "请先在电脑运行「一键推送更新.bat」，看到推送成功后再点更新。"
        )
    return {
        "ok": True,
        "changed": changed,
        "version": new_version,
        "old_version": old_version,
        "log": (reset.stdout or "").strip(),
        "message": message,
    }


@app.get("/favicon.ico")
def favicon():
    icon = ROOT / "docs" / "static" / "favicon.ico"
    if icon.is_file():
        return FileResponse(icon)
    raise HTTPException(status_code=404)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEB_PORT", "8787"))
    uvicorn.run("webui.app:app", host="0.0.0.0", port=port, reload=False)
