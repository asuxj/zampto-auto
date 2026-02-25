import os
import re
import time
import platform
import requests
from datetime import datetime
from typing import Optional, Tuple

from seleniumbase import SB
from pyvirtualdisplay import Display


LOGIN_URL = "https://auth.zampto.net/sign-in?app_id=bmhk6c8qdqxphlyscztgl"
DASHBOARD_URL = "https://dash.zampto.net/overview"

SILENT = False


def log(*args):
    if not SILENT:
        print(*args, flush=True)


# =========================
# Xvfb
# =========================
def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        os.environ["DISPLAY"] = display.new_display_var
        log("🖥️ Xvfb 已启动")
        return display
    return None


# =========================
# Turnstile 处理
# =========================
def handle_turnstile_if_present(sb):
    try:
        if sb.is_element_present('input[name="cf-turnstile-response"]'):
            log("🛡️ Turnstile 检测中...")

            try:
                sb.uc_gui_click_captcha()
            except:
                pass

            for _ in range(20):
                value = sb.get_attribute(
                    'input[name="cf-turnstile-response"]',
                    "value"
                )
                if value and len(value) > 10:
                    log("✅ Turnstile OK")
                    return True
                time.sleep(1)

            log("⚠️ Turnstile 未成功")
    except:
        pass

    return False


# =========================
# 工具函数
# =========================
def mask_account(name: str) -> str:
    if len(name) <= 6:
        return name[0] + "***" + name[-1]
    return f"{name[:3]}***{name[-3:]}"


def tg_send(token: str, chat_id: str, msg: str):
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        log("⚠️ TG 发送失败:", e)


def extract_server_id(text: str) -> Optional[str]:
    m = re.search(r"ID:\s*(\d+)", text)
    return m.group(1) if m else None


# =========================
# 账号加载
# =========================
def load_accounts():
    raw = (os.getenv("ZAMPTO_BATCH") or "").strip()
    if not raw:
        raise RuntimeError("❌ 缺少 ZAMPTO_BATCH")

    accounts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [x.strip() for x in line.split(",")]

        if len(parts) == 2:
            email, password = parts
            tg_token = ""
            tg_chat_id = ""
        elif len(parts) == 4:
            email, password, tg_token, tg_chat_id = parts
        else:
            continue

        accounts.append((email, password, tg_token, tg_chat_id))

    return accounts


# =========================
# 登录流程
# =========================
def login(sb: SB, username: str, password: str) -> bool:
    log("🔐 打开登录页...")
    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5)

    sb.wait_for_element_visible("input[name='identifier']", timeout=30)
    sb.type("input[name='identifier']", username)
    sb.click("button[name='submit']")

    sb.wait_for_element_visible("input[name='password']", timeout=30)
    sb.type("input[name='password']", password)

    handle_turnstile_if_present(sb)

    sb.click("button[name='submit']")
    sb.wait_for_ready_state_complete(timeout=30)

    # 等待跳转
    for _ in range(30):
        if "dash.zampto.net" in sb.get_current_url():
            break
        time.sleep(1)
    else:
        log("❌ 未跳转到 dash")
        return False

    # 通过 info-content 判定登录成功
    if sb.is_element_present("div.info-content"):
        log("✅ 登录成功")
        return True

    log("❌ 登录判定失败")
    return False


# =========================
# 获取 Server ID
# =========================
def get_server_id(sb: SB) -> Optional[str]:
    sb.open(DASHBOARD_URL)
    sb.wait_for_element_visible("div.server-id", timeout=30)

    text = sb.get_text("div.server-id")
    return extract_server_id(text)


# =========================
# 获取续期时间
# =========================
def get_last_renew_time(sb: SB) -> str:
    sb.wait_for_element_visible("#lastRenewalTime", timeout=30)
    return sb.get_text("#lastRenewalTime")


# =========================
# 执行续期
# =========================
def renew_server(sb: SB, server_id: str) -> Tuple[str, str]:
    url = f"https://dash.zampto.net/server?id={server_id}"
    sb.open(url)

    old_time = get_last_renew_time(sb)
    log("旧时间:", old_time)

    sb.click("a.action-purple")

    handle_turnstile_if_present(sb)

    time.sleep(6)

    new_time = get_last_renew_time(sb)
    log("新时间:", new_time)

    return old_time, new_time


# =========================
# 单账号流程
# =========================
def renew_one(email: str, password: str):
    try:
        with SB(uc=True, locale="en", test=True) as sb:
            if not login(sb, email, password):
                return False, "登录失败"

            server_id = get_server_id(sb)
            if not server_id:
                return False, "未找到 Server ID"

            old_time, new_time = renew_server(sb, server_id)

            return True, {
                "server_id": server_id,
                "old_time": old_time,
                "new_time": new_time,
                "success": old_time != new_time,
            }

    except Exception as e:
        log("💥 内部异常:", e)
        return False, str(e)


# =========================
# 主程序
# =========================
def main():
    display = setup_xvfb()
    accounts = load_accounts()

    try:
        for i, (email, password, tg_token, tg_chat_id) in enumerate(accounts, 1):
            masked = mask_account(email)

            log("\n" + "=" * 60)
            log(f"🔐 [{i}/{len(accounts)}] {masked}")
            log("=" * 60)

            ok, data = renew_one(email, password)

            if not ok:
                msg = f"❌ *zampto 执行失败*\n账号: `{masked}`\n错误: `{data}`"
            else:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if data["success"]:
                    msg = (
                        f"🏰 *zampto 续期报告*\n\n"
                        f"🖥️ 服务器 ID: `{data['server_id']}`\n"
                        f"💳 新到期时间: `{data['new_time']}`\n"
                        f"⏰ 时间: `{now}`"
                    )
                else:
                    msg = (
                        f"⚠️ *zampto 续期未变化*\n\n"
                        f"🖥️ 服务器 ID: `{data['server_id']}`\n"
                        f"旧时间: `{data['old_time']}`\n"
                        f"当前时间: `{data['new_time']}`"
                    )

            log(msg)
            tg_send(tg_token, tg_chat_id, msg)
            time.sleep(3)

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()