# 法猫自媒体助手

用于本机管理创作账号、热点选题、研究笔记、AI 文案、草稿和视频发布的桌面应用。

**开发中，尚未完成。** 本次开源以本地最新的 **1.3.0 源码快照**为基础；版本号不表示所有平台已经可用。用户确认已完善的是 **抖音、B 站的原生自动发布流程**。本次整理不使用真实账号投稿，不重新认证平台的当前可用性。

## 功能状态

| 功能 | 当前边界 |
| --- | --- |
| 抖音、B 站原发布 | 用户确认已完善；账号独立浏览器、自动发布及核对后发布；平台变化可能需要适配 |
| 热点、选题库、研究与 AI 文案 | 已有集成源码和测试；数据源、模型与接口需使用者自行配置、验证 |
| 账号、草稿、历史、素材预览与封面 | 已有实现；账号数据仅保存在使用者本机 |
| 矩媒连接 | 1.3.0 新增的可选本机接口；仅做过模拟验证，需独立安装矩媒并绑定账号 |
| 小红书 | 目前通过矩媒接口尝试发布；不列为已完成的自动发布功能 |
| 快手、TikTok、YouTube、X、视频号等入口 | 部分仅有基础适配或手动流程，不承诺完整自动发布 |

矩媒入队、存草稿、本机定时、连接中断均不能直接视为公开发布；最终结果需在平台后台核对。

## 源码运行

主要目标为 **Windows 10/11 x64**。建议 Python 3.11 或 3.12；依赖范围见 `requirements.txt`。Mac/Linux 可运行部分 Web 与无浏览器测试，桌面嵌入、Windows DPAPI 密钥加密和 Windows 打包存在平台限制。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python launcher.py
```

也可以双击 `start.bat`。仅运行 Web 界面：

```powershell
.venv\Scripts\python app.py --web
```

非 Windows 环境使用 `python3 -m venv .venv`、`.venv/bin/python`。浏览器也可通过 `MP_CHROME_PATH` 指定本机 Chrome/Edge 可执行文件。`start.sh` 会安装 Python 依赖，但浏览器需单独准备。

首次使用需自行添加账号并登录。源码默认在项目内创建 `data/`，可通过 `MEDIA_ASSISTANT_DATA_DIR` 指定独立数据目录。不要将数据目录、账号浏览器配置、Cookie 或密钥提交到仓库或放进分享包。

## 矩媒与 AI

矩媒是独立可选应用，本仓库不打包其源码或安装程序。连接与确认步骤见 [矩媒说明](docs/matrixmedia.md)；不使用矩媒时，抖音和 B 站可选择原发布功能。

AI 默认需自行填写服务地址、模型与密钥，调用费用由所用服务计算。历史预设与模型名称可能变化，发布整理未重新访问付费服务；详见 [AI 配置](AI_CONFIG.md) 和 [账号定位](PROFILE_ANALYSIS.md)。带密钥配置使用 Windows DPAPI，在 Mac/Linux 上不支持该密钥保存流程。

## 验证与构建

[本次验证记录](docs/VALIDATION.md) 区分实际执行结果与未验证项。`tests/` 保留原有回归测试，部分测试需要 Windows、浏览器或桌面环境；旧测试与 1.3.0 的功能边界可能存在差异。

Windows 构建另外需要 PyInstaller、Inno Setup 6 和所需中文语言文件：

```powershell
.venv\Scripts\python -m pip install pyinstaller
.\scripts\build_windows.ps1 -Python .\.venv\Scripts\python.exe -Iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

GitHub Release 同时提供源码快照和原有的 Windows 1.3.0 安装包。安装包由用户确认可用；本次在 Mac 上核对文件完整性，未重新运行 Windows 安装流程。请核对随包 SHA-256 清单。

## 目录与来源

- `core/`、`platforms/`：本机存储、创作与平台适配。
- `templates/`、`static/`：界面及公共资源。
- `tests/`、`scripts/`、`packaging/`：回归测试、开发工具及 Windows 构建定义。
- `docs/hotspot-design/`：从“热点工具”保留的历史设计资料；PostgreSQL、Redis、TrendRadar fork 等内容是设计方案，并非当前桌面应用的运行依赖。

两个本地目录的版本关系与剔除范围见 [整理记录](docs/SOURCE_PROVENANCE.md)。自有代码以 [MIT](LICENSE) 开源；第三方组件及独立应用遵循各自许可证，见 [第三方声明](THIRD_PARTY_NOTICES.md)。
