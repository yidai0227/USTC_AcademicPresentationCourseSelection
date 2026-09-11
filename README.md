# USTC 学术报告监听脚本

你还在为抢不到学术报告苦恼么？还在因不知道什么时候上新而慢人一步么？
来试试2026最新版学术报告监听脚本吧!

觉得脚本好用的话，还请点点star，ORZ

如果有任何问题（或者交友？）可以发送给我邮件:daiyi031227@gmail.com

## 功能

监听学术报告系统，出现“尚未截止，人数没有报满”的报告会立即通知你！
简单几步就可以配置自己的邮箱，推荐使用网易163邮箱（只测试了163邮箱是否可用），当出现可选课的报告就会立即向你发送邮件！

## 环境要求

- Python 3.10 或更新版本。
- 可访问研究生系统的网络，以及本人有权限使用的学校账号。
- 带桌面界面的电脑，运行期间保持浏览器和终端开启。
- 安装 `requirements.txt` 中的 Playwright，并安装它对应的 Chromium。

## 安装与运行

命令如下
### Windows（PowerShell）

```powershell
py -3 -m venv .venv
source 
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe main.py
```

如果找不到 `py`，安装 Python 并重新打开终端，或使用已配置好的 `python` 替换第一行的 `py -3`。

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
python3 main.py
```

### 首次使用

1. 运行脚本，在弹出的浏览器中手动登录研究生系统。
2. 登录成功后，回到终端按 Enter，脚本进入学术报告应用并开始检查。
3. 出现提醒后，前往研究生系统自行查看和报名。
4. 按 Ctrl+C 或关闭监控页面即可停止。

浏览器会话保存在脚本旁的 `ustc_browser_profile/`，下次启动可能无需再次登录，但仍需确认并按 Enter。请勿同时运行多个实例，它们会争用浏览器目录和提醒记录。电脑休眠、断网或程序关闭时无法继续监听。

## 邮件提醒（可选）

不设置邮箱时，仅进行本地提醒。启用邮件时需在**运行脚本的同一终端**设置环境变量：

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `COURSE_EMAIL` | 发件邮箱 / SMTP 登录账号 | 无 |
| `COURSE_EMAIL_AUTH_CODE` | 邮箱 SMTP 授权码 | 无 |
| `COURSE_EMAIL_TO` | 单个收件邮箱 | 与发件邮箱相同 |
| `COURSE_SMTP_HOST` | SMTP SSL 主机 | `smtp.163.com` |
| `COURSE_SMTP_PORT` | SMTP SSL 端口 | `465` |

需先在邮箱服务中开启 SMTP，并按服务商说明取得授权码（可以自行google一下，很简单）

开启邮箱命令如下，将其中的"your-address@example.com"，"recipient@example.com"替换成你自己的邮箱地址
SMTP码会

### Windows（PowerShell）

```powershell
$env:COURSE_EMAIL = "your-address@example.com"
$secret = Read-Host "请输入邮箱 SMTP 授权码" -AsSecureString
$env:COURSE_EMAIL_AUTH_CODE = [System.Net.NetworkCredential]::new("", $secret).Password
$env:COURSE_EMAIL_TO = "recipient@example.com"
py3 main.py

# 退出脚本后，可清除本终端中的授权码
Remove-Item Env:COURSE_EMAIL_AUTH_CODE
Remove-Variable secret
```

### macOS / Linux

```bash
export COURSE_EMAIL="your-address@example.com"
export COURSE_EMAIL_AUTH_CODE="$(.venv/bin/python -c 'import getpass; print(getpass.getpass("SMTP 授权码: "))')"
export COURSE_EMAIL_TO="recipient@example.com"
.venv/bin/python main.py

# 退出脚本后，可清除本终端中的授权码
unset COURSE_EMAIL_AUTH_CODE
```

启用邮件后，只有 SMTP 接受邮件且提醒记录保存成功，才会标记为已提醒；发送失败时，如果报告下一轮仍可选，会再次本地提醒并重试邮件。SMTP 接受不代表邮件一定进入收件箱，请同时检查垃圾邮件。若邮件已发送但程序在保存前中断，下次可能重复提醒。

## 自定义与提醒记录

在 `main.py` 顶部修改：

- `CHECK_INTERVAL`：检查间隔，单位为秒，默认 120。建议保持温和的访问频率。
- `QUERY_SETTING`：院系筛选。默认包含计算机科学与技术学院、信息科学技术学院、微电子学院、网络空间安全学院、软件学院、人工智能与数据科学学院、软件学院合肥、软件学院苏州。（信智学部设计的院系，可以根据你自己的需要修改，但是不保证只修改这里就可以成功）

如需更换，按当前学校系统的查询条件同步修改 `value` 和 `value_display`。（可以询问AI，或者发邮件给我）

`notified_courses.json` 只保存已提醒的报告编号。同一编号只提醒一次，满员后重新出现空位也不会再次提醒；修改院系或邮箱不会自动重置记录。想重新提醒时，先停止脚本，再备份或删除该文件。文件损坏时程序会停止并保留原文件，修复或备份后移走即可重新运行。

浏览器目录和提醒记录均相对于 `main.py` 定位，因此从其他工作目录启动也会使用同一份数据。


```bash
git init
git add main.py README.md requirements.txt .gitignore tests
git status --short
git diff --cached --stat
```

