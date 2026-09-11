"""通过已登录的浏览器会话监听 USTC 学术报告余量。"""

import json
import os
import smtplib
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "ustc_browser_profile"
NOTIFIED_FILE = BASE_DIR / "notified_courses.json"
CHINA_TZ = timezone(timedelta(hours=8))
PORTAL_URL = "https://yjs1.ustc.edu.cn/gsapp/sys/yjsemaphome/portal/index.do?forceCas=1"
APP_URL = "https://yjs1.ustc.edu.cn/gsapp/sys/xsbgglappustc/*default/index.do"
URL = "https://yjs1.ustc.edu.cn/gsapp/sys/xsbgglappustc/modules/xsbgxk/wxbgbgdz.do"
CHECK_INTERVAL = 300
PAGE_SIZE = 50
MAX_PAGES = 100
MAX_ERRORS = 5

# 默认关注以下院系；可按研究生系统实际的 querySetting 修改。
QUERY_SETTING = [
    {
        "name": "YXDM",
        "caption": "院系",
        "linkOpt": "AND",
        "builderList": "cbl_m_List",
        "builder": "m_value_equal",
        "value": "011,210,219,221,225,229,A13,A14",
        "value_display": (
            "计算机科学与技术学院,"
            "信息科学技术学院,"
            "微电子学院,"
            "网络空间安全学院,"
            "软件学院,"
            "人工智能与数据科学学院,"
            "软件学院合肥,"
            "软件学院苏州"
        ),
    },
    {
        "name": "*order",
        "value": "-BGSJ",
        "linkOpt": "AND",
        "builder": "equal",
    },
    {
        "name": "_gotoFirstPage",
        "value": True,
        "linkOpt": "AND",
        "builder": "equal",
    },
]


@dataclass(frozen=True)
class EmailConfig:
    sender: str
    password: str
    receiver: str
    host: str = "smtp.163.com"
    port: int = 465


class MonitorError(Exception):
    """可重试的请求或响应结构异常；消息不得包含完整服务端数据。"""


class StopMonitoring(MonitorError):
    """登录失效、拒绝访问或限流时停止请求。"""


def read_email_config():
    """未配置邮箱时只做本地提醒；部分配置缺失时尽早报错。"""
    sender = os.environ.get("COURSE_EMAIL", "").strip()
    password = os.environ.get("COURSE_EMAIL_AUTH_CODE", "")
    receiver = os.environ.get("COURSE_EMAIL_TO", "").strip()
    if not any((sender, password, receiver)):
        return None
    if not sender or not password:
        raise ValueError("请同时设置 COURSE_EMAIL 和 COURSE_EMAIL_AUTH_CODE。")
    host = os.environ.get("COURSE_SMTP_HOST", "smtp.163.com").strip()
    try:
        port = int(os.environ.get("COURSE_SMTP_PORT", "465"))
    except ValueError:
        raise ValueError("COURSE_SMTP_PORT 必须是整数。") from None
    if not host or not 1 <= port <= 65535:
        raise ValueError("SMTP 主机不能为空，端口须在 1～65535 之间。")
    if any("\r" in value or "\n" in value for value in (sender, receiver, host)):
        raise ValueError("邮箱地址和 SMTP 主机不能包含换行。")
    return EmailConfig(sender, password, receiver or sender, host, port)


def is_available(course, now=None):
    """按北京时间判断截止时间和余量；最终报名资格以系统为准。"""
    try:
        deadline = datetime.strptime(course["JZSJ"], "%Y-%m-%d %H:%M:%S")
        deadline = deadline.replace(tzinfo=CHINA_TZ)
        max_count = int(course["KXRS"])
        current_count = int(course["YXRS"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    now = now or datetime.now(CHINA_TZ)
    return now <= deadline and 0 <= current_count < max_count


def course_title(course):
    return str(course.get("BGTMZW") or course.get("BGTMYW") or "未知报告")


def send_email(course, config):
    msg = EmailMessage()
    # 外部数据不应把换行带入邮件头。
    title = " ".join(course_title(course).splitlines())
    msg["Subject"] = f"发现可选学术报告：{title}"
    msg["From"] = config.sender
    msg["To"] = config.receiver
    msg.set_content(
        f"发现可选学术报告！\n\n"
        f"名称：{course_title(course)}\n"
        f"当前人数 / 容量：{course.get('YXRS', '?')} / {course.get('KXRS', '?')}\n"
        f"报告时间：{course.get('BGSJ', '未知')}\n"
        f"选课截止：{course.get('JZSJ', '未知')}\n"
        f"地点：{course.get('DD', '未知')}\n\n"
        f"请进入研究生系统查看并手动报名：{APP_URL}\n"
    )
    with smtplib.SMTP_SSL(
        config.host, config.port, timeout=15, context=ssl.create_default_context()
    ) as smtp:
        smtp.login(config.sender, config.password)
        refused = smtp.send_message(msg)
        if refused:
            raise smtplib.SMTPException("部分收件人被拒绝。")
    print("邮件提醒已发送。")


def notify(course):
    """始终先输出终端提醒；系统提示失败不影响监控或邮件。"""
    title = course_title(course)
    message = (
        f"当前人数 / 容量：{course.get('YXRS', '?')}/{course.get('KXRS', '?')}\n"
        f"时间：{course.get('BGSJ', '未知')}\n"
        f"地点：{course.get('DD', '未知')}"
    )
    print(f"\n{'=' * 60}\n发现可选报告：{title}\n{message}")
    print(f"截止时间：{course.get('JZSJ', '未知')}\n{'=' * 60}\n")

    try:
        if sys.platform == "darwin":
            # 通过 argv 传递网页内容，不把外部字符串拼接进 AppleScript。
            script = (
                "on run argv\n"
                'display notification (item 2 of argv) with title "发现可选学术报告" '
                'subtitle (item 1 of argv) sound name "Glass"\n'
                "end run"
            )
            subprocess.run(
                ["osascript", "-e", script, "--", title, message],
                check=True,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            print("\a", end="", flush=True)
    except (OSError, subprocess.SubprocessError, RuntimeError, ImportError) as exc:
        print(f"系统提示不可用（{type(exc).__name__}），请查看上方终端提醒。")


def load_notified(path):
    if not path.exists():
        return set()
    # 损坏时停止并保留原文件，避免悄悄清空去重记录。
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(type(item) not in (str, int) for item in data):
        raise ValueError("提醒记录应为报告编号列表。")
    return {str(item) for item in data}


def save_notified(path, course_ids):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(sorted(course_ids), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def fetch_courses(frame, page_number=1):
    # 后续分页不能携带“跳到首页”条件。
    query = [
        item for item in QUERY_SETTING
        if page_number == 1 or item["name"] != "_gotoFirstPage"
    ]
    payload = {
        "*order": "-BGSJ",
        "querySetting": json.dumps(query, ensure_ascii=False, separators=(",", ":")),
        "pageSize": str(PAGE_SIZE),
        "pageNumber": str(page_number),
    }
    return frame.evaluate(
        """
        async ({ url, payload }) => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 30000);
            try {
                const response = await fetch(url, {
                    method: "POST",
                    headers: {
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest"
                    },
                    credentials: "include",
                    signal: controller.signal,
                    body: new URLSearchParams(payload).toString()
                });
                return {
                    status: response.status,
                    ok: response.ok,
                    redirected: response.redirected,
                    text: await response.text()
                };
            } finally {
                clearTimeout(timer);
            }
        }
        """,
        {"url": URL, "payload": payload},
    )


def parse_response(result):
    status = result["status"]
    if status in (401, 403, 429):
        raise StopMonitoring(f"HTTP {status}：登录失效、拒绝访问或限流，请手动检查。")
    if result.get("redirected"):
        raise StopMonitoring("请求被重定向，登录可能已失效，请重新登录。")
    if not result["ok"]:
        raise MonitorError(f"HTTP {status}，稍后重试。")
    try:
        data = json.loads(result["text"])
    except (ValueError, TypeError):
        raise MonitorError("响应不是 JSON，可能登录失效或服务暂时不可用。") from None
    if not isinstance(data, dict) or str(data.get("code")) != "0":
        raise MonitorError("服务器返回业务异常，请在浏览器中检查登录和应用状态。")
    try:
        rows = data["datas"]["wxbgbgdz"]["rows"]
    except (KeyError, TypeError):
        raise MonitorError("响应结构发生变化，未找到报告列表。") from None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MonitorError("报告列表格式异常。")
    return rows


def fetch_all_courses(frame):
    """逐页读取直到不足一页；限制页数并检测重复页，避免无限请求。"""
    courses = {}
    for page_number in range(1, MAX_PAGES + 1):
        rows = parse_response(fetch_courses(frame, page_number))
        previous_count = len(courses)
        for course in rows:
            course_id = course.get("BGBM")
            if type(course_id) not in (str, int) or str(course_id) == "":
                raise MonitorError("报告缺少有效编号，无法可靠去重。")
            courses[str(course_id)] = course
        if rows and len(courses) == previous_count:
            raise MonitorError("分页未返回新报告，可能是接口忽略了页码。")
        if len(rows) < PAGE_SIZE:
            return list(courses.values())
        if page_number < MAX_PAGES:
            time.sleep(1)
    raise MonitorError("报告数量超过分页安全上限，请检查接口或缩小院系范围。")


def process_courses(rows, notified_courses, notified_file, email_config):
    available = [course for course in rows if is_available(course)]
    print(
        f"[{datetime.now(CHINA_TZ):%H:%M:%S}] 检查完成，共 {len(rows)} 条，"
        f"当前有空位 {len(available)} 条，历史已提醒 {len(notified_courses)} 条。"
    )
    for course in available:
        course_id = str(course["BGBM"])
        if course_id in notified_courses:
            continue
        notify(course)
        if email_config is not None:
            try:
                send_email(course, email_config)
            except (OSError, smtplib.SMTPException, ValueError) as exc:
                # 不输出 SMTP 原始错误，以免带出邮箱地址或服务端信息。
                print(f"邮件发送失败（{type(exc).__name__}），后续检查仍可选时重试。")
                continue
        updated = notified_courses | {course_id}
        # 只有落盘成功才更新内存；写入失败交给主循环计入连续错误。
        save_notified(notified_file, updated)
        notified_courses.add(course_id)


def monitor(page, notified_courses, email_config):
    consecutive_errors = 0
    while not page.is_closed():
        try:
            rows = fetch_all_courses(page.main_frame)
            process_courses(rows, notified_courses,
                            NOTIFIED_FILE, email_config)
            # 整轮解析、处理和保存都成功后再清零。
            consecutive_errors = 0
        except StopMonitoring as exc:
            print(str(exc))
            break
        except Exception as exc:
            consecutive_errors += 1
            detail = str(exc) if isinstance(
                exc, MonitorError) else type(exc).__name__
            print(f"检查失败：{detail}（连续 {consecutive_errors}/{MAX_ERRORS} 次）。")
            if consecutive_errors >= MAX_ERRORS:
                print("连续异常达到上限，监控自动停止。")
                break
        # 使用 Playwright 等待让浏览器事件继续处理；关窗后及时停止。
        import random

        # 基础间隔最低 120 秒，每轮额外随机等待 30～60 秒。
        remaining_ms = (max(CHECK_INTERVAL, 120) + random.randint(30, 60)) * 1000
        while remaining_ms > 0 and not page.is_closed():
            step_ms = min(1000, remaining_ms)
            try:
                page.wait_for_timeout(step_ms)
            except Exception:
                if page.is_closed():
                    return
                raise
            remaining_ms -= step_ms


def main():
    # Windows 的旧终端编码遇到不支持的报告字符时也不应终止监控。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        email_config = read_email_config()
    except ValueError as exc:
        print(f"邮箱配置错误：{exc}")
        return 1
    try:
        notified_courses = load_notified(NOTIFIED_FILE)
    except (OSError, ValueError) as exc:
        print(f"读取提醒记录失败（{type(exc).__name__}）。请修复或备份后移走该文件。")
        return 1
    print(f"已读取 {len(notified_courses)} 条历史提醒记录。")
    print("已启用邮件提醒。" if email_config else "未配置邮箱，仅启用本地提醒。")

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR), headless=False,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page(
                )
                page.goto(PORTAL_URL, wait_until="domcontentloaded",
                          timeout=60000)
                input("请在浏览器中登录研究生系统，成功后回到终端按 Enter：")
                page.goto(APP_URL, wait_until="domcontentloaded", timeout=60000)
                print("开始监控学术报告；按 Ctrl+C 或关闭监控页面停止。")
                monitor(page, notified_courses, email_config)
            finally:
                context.close()
    except (KeyboardInterrupt, EOFError):
        print("已停止监控。")
        return 0
    except Exception as exc:
        print(
            f"程序退出（{type(exc).__name__}）。请检查网络、Chromium 安装及浏览器"
            "配置目录是否被其他实例占用；参考 README 排查。"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
