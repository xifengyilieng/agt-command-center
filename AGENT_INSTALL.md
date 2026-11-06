# 给安装 Agent 的执行指南

目标：在用户自己的 Windows 10/11 上安装、运行 AGT 大屏并验证服务和页面。仓库地址：<https://github.com/xifengyilieng/agt-command-center>。

## 1. 检查

阅读 README.md、AGENTS.md。确认 Windows 10/11、Python 3.10+、现代浏览器可用。macOS/Linux 尚未提供正式安装支持。

选择可写、长期保留的目录。已有同名目录时先检查内容和 Git 状态，不覆盖用户修改。Python 可使用 `python`、`py -3` 或 `python3`；缺少时按用户与 Agent 的权限规则从官方发行源安装后检查。不要关闭系统保护或要求管理员权限来代替定位问题。

Codex、DSH 不必同时安装。未发现来源也可以继续安装，最后明确说明等待数据，不能虚构任务。默认浏览器模式；用户提出桌面需求时使用 `-Wallpaper`，提出登录启动需求时使用 `-Startup`。默认不要购买软件、开启登录启动或更换壁纸。

## 2. 下载和安装

```powershell
git clone https://github.com/xifengyilieng/agt-command-center.git
cd agt-command-center
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

没有 Git 时可下载 <https://github.com/xifengyilieng/agt-command-center/archive/refs/heads/main.zip> 并解压，在包含 `setup.ps1` 的目录执行安装命令。不要只下载 README，也不要将源码放到 Agent 技能目录后当作已安装。

`-CheckOnly` 仅检查环境；`-NoBrowser` 正常安装与启动但不弹浏览器。执行策略只对这次 PowerShell 进程设置，不修改系统策略。运行无第三方 Python 依赖，不需要 npm。

## 3. 自定义数据目录

默认读取当前用户的 `~/.codex` 与 `~/.dsh`；可在启动前设置：

```powershell
$env:CODEX_HOME = 'D:\AgentData\codex'
$env:DSH_HOME = 'D:\AgentData\dsh'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

变量指向对应软件数据根目录。需要登录启动也沿用时，按用户需求保存为当前用户环境变量。已有健康采集器会被复用，环境变量不会自动替换运行中的进程配置；须仅退出本项目旧进程并重启。不要读取凭据或修改来源软件的数据库来生成数据。

## 4. 可选 Wallpaper Engine

用户已安装该软件且提出桌面需求时：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Wallpaper
```

自动定位失败时检查本机位置或询问用户，再指定：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -WeDir 'D:\SteamLibrary\steamapps\common\wallpaper_engine'
```

安装器创建 `projects/myprojects/AGT-Live-Command`，生成本机随机令牌及 `connection.js`，保留原作署名。不要复制他人的令牌。目标目录被其他工程占用时停止，不能强制覆盖。

安装后让用户在「已安装」选择「AGT 真实任务作战桌面」及所需屏幕。默认不修改当前选择、宿主帧率、其他显示器或系统主题。`-Startup` 创建当前用户的 `AGT Task Collector.lnk`，指向仓库内启动器，所以应保留安装目录。

对已安装的自身壁纸提供受限异常恢复：只有该工程仍选中、宿主存在且连续确认 renderer 退出时，才尝试恢复同一屏，具有冷却和次数上限。DeskGo 点击兼容仅处理命中本大屏已发布控件的桌面点击，不用于操纵其他软件或收集输入。

## 5. 验证和交付

```powershell
$health = Invoke-RestMethod 'http://127.0.0.1:8767/api/health'
if ($health.app -ne 'agt-command-center' -or -not $health.ok -or $health.version -lt 11) { throw 'AGT health check failed' }
$page = Invoke-WebRequest 'http://127.0.0.1:8767/' -UseBasicParsing
if ($page.StatusCode -ne 200) { throw 'AGT page check failed' }
```

有浏览器工具时实际打开地址，确认圆盘、中文界面及六块底栏正常，菜单可以展开并打开设置。不要仅凭命令退出码声称视觉验证通过。

向用户报告安装目录、打开地址、启动方式、来源是否发现，以及是否安装了壁纸或登录启动项。没有数据时明确说明空状态和待接入来源，不能把页面打开说成已读到真实任务。

## 故障、更新和卸载

- 错误先检查本项目 `server-error.log`，必要时前台执行 `python server.py`；日志可能含本机信息，分享前检查。
- 端口冲突先检查所有者，不结束未知进程。重启本项目时仅操作匹配安装目录 `server.py` 绝对路径的 Python 进程。
- 令牌变化后需重启旧服务、重新安装壁纸；不要公开 `.wallpaper-token` 或 `connection.js`。
- 从 v1.0.0 更新时，先用 `git status` 检查并保留本地修改，再执行 `git pull --ff-only` 和安装器。新版启动器会重启同一安装目录中的旧版采集器。已使用壁纸的用户需用 `-Wallpaper` 或原来的 `-WeDir` 同步资源，然后在 Wallpaper Engine 中重新选择 AGT 壁纸。
- 卸载：停止本项目服务，删除本项目登录快捷方式；壁纸停用后可删除本项目工程与仓库。不删除 `.codex`、`.dsh` 等应用数据。

## 开发验证

```powershell
python build_screen.py
python -m unittest discover -s tests -v
node --check runtime.js
```

测试使用临时数据库和合成记录，不依赖发布者本机数据。可选前端测试：

```powershell
npm ci
npx playwright install chromium
python server.py --port 18767
```

另一个终端执行 `npm run test:public`。默认访问 `http://127.0.0.1:18767`，浏览器拦截 API 使用空状态和合成记录，检查加载、菜单、设置、暂停和手机布局，不安装或更换壁纸。`AGT_TEST_URL` 可覆盖地址，`AGT_PREVIEW_PATH` 可保存空状态截图。Node.js/Playwright 仅用于开发。
