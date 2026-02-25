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

# =========================
# Xvfb
# =========================
def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        os.environ["DISPLAY"] = display.new_display_var
        print("🖥️ Xvfb 已启动")
        return display
    return None


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
        print("⚠️ TG 发送失败:", e)


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

    for idx, line in enumerate(raw.splitlines(), start=1):
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
            raise RuntimeError(f"❌ 第 {idx} 行格式错误，应为 2 或 4 列")

        accounts.append((email, password, tg_token, tg_chat_id))

    return accounts


# =========================
# 登录
# =========================
def login(sb: SB, username: str, password: str) -> bool:
    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5)

    # 输入邮箱
    sb.wait_for_element_visible("input[name='identifier']", timeout=30)
    sb.type("input[name='identifier']", username)
    sb.click("button[name='submit']")

    # 输入密码
    sb.wait_for_element_visible("input[name='password']", timeout=30)
    sb.type("input[name='password']", password)
    sb.click("button[name='submit']")

    # ✅ 正确写法：等待 URL 变化
    sb.wait_for_ready_state_complete(timeout=30)

    # 用 get_current_url 判断
    for _ in range(30):
        if "dash.zampto.net" in sb.get_current_url():
            break
        time.sleep(1)
    else:
        return False

    # 再确认页面包含 Username 作为成功标志
    sb.wait_for_text("Username", timeout=20)

    print("✅ 登录成功")
    return True


# =========================
# 获取 Server ID
# =========================
def get_server_id(sb: SB) -> Optional[str]:
    sb.open(DASHBOARD_URL)
    sb.wait_for_element_visible("div.server-id", timeout=30)

    text = sb.get_text("div.server-id")
    return extract_server_id(text)


# =========================
# 获取时间
# =========================
def get_last_renew_time(sb: SB) -> str:
    sb.wait_for_element_visible("#lastRenewalTime", timeout=30)
    return sb.get_text("#lastRenewalTime")


# =========================
# 执行续期
# =========================
def renew_server(sb: SB, server_id: str) -> Tuple[str, str]:
    server_url = f"https://dash.zampto.net/server?id={server_id}"
    sb.open(server_url)

    old_time = get_last_renew_time(sb)
    print("旧时间:", old_time)

    # 点击 Renew
    sb.click("a.action-purple")

    # 等待 Turnstile token
    sb.wait_for_element_present("input[name='cf-turnstile-response']", timeout=60)

    # 等待页面自动刷新
    time.sleep(5)

    new_time = get_last_renew_time(sb)
    print("新时间:", new_time)

    return old_time, new_time


# =========================
# 单账号流程
# =========================
def renew_one(email: str, password: str):
    with SB(uc=True, locale="en", test=True) as sb:
        if not login(sb, email, password):
            return False, "登录失败"

        server_id = get_server_id(sb)
        if not server_id:
            return False, "未找到 Server ID"

        old_time, new_time = renew_server(sb, server_id)

        success = old_time != new_time

        return True, {
            "server_id": server_id,
            "old_time": old_time,
            "new_time": new_time,
            "success": success,
        }


# =========================
# 主程序
# =========================
def main():
    display = setup_xvfb()
    accounts = load_accounts()

    try:
        for i, (email, password, tg_token, tg_chat_id) in enumerate(accounts, 1):
            masked = mask_account(email)

            print("\n" + "=" * 60)
            print(f"🔐 [{i}/{len(accounts)}] {masked}")
            print("=" * 60)

            try:
                ok, data = renew_one(email, password)

                if not ok:
                    msg = f"❌ *zampto 登录失败*\n账号: `{masked}`"
                else:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    if data["success"]:
                        msg = (
                            f"🏰 *zampto 续期报告*\n\n"
                            f"🖥️ 服务器 ID: `{data['server_id']}`\n"
                            f"🚀 开机任务: 已提交\n"
                            f"💳 到期时间: `{data['new_time']}`\n"
                            f"⏰ 时间: `{now}`"
                        )
                    else:
                        msg = (
                            f"⚠️ *zampto 续期未变化*\n\n"
                            f"🖥️ 服务器 ID: `{data['server_id']}`\n"
                            f"旧时间: `{data['old_time']}`\n"
                            f"当前时间: `{data['new_time']}`"
                        )

            except Exception as e:
                msg = f"💥 *zampto 异常*\n账号: `{masked}`\n错误: `{e}`"

            print(msg)
            tg_send(tg_token, tg_chat_id, msg)
            time.sleep(3)

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()