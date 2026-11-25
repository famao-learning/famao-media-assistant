# 本次开源整理验证

日期：2026-11-24。验证不使用真实平台登录或投稿，不调用付费 AI 服务。

## 环境

macOS / Apple Silicon；Python 3.15.2、Flask 3.1.3、Playwright 1.65.2、PySide6 6.12.0、imageio-ffmpeg 0.8.1。测试在独立源码副本中执行，未访问原目录中的真实账号数据。

## 实际通过

| 检查 | 结果 |
| --- | --- |
| Python 源码静态语法解析 | 98 个文件通过 |
| 存储恢复、草稿、发布规则与热点核心测试 | 40 项通过 |
| 矩媒本机模拟服务集成 | 12 项通过 |
| 矩媒浏览器界面专项 | 2 项通过 |
| 发布文件检查 | 未收录数据目录、真实凭据、浏览器状态或数据库；密钥模式命中项为测试占位值 |

上述 54 项专项测试的复现命令（先安装依赖及 Chromium，在项目根目录运行）：

```sh
python -m unittest tests.test_data_retention tests.test_publication tests.test_publishing_workflow tests.test_topic_radar
python -m unittest tests.test_matrixmedia
python -m unittest tests.test_matrixmedia_ui.MatrixUITests
```

`.github/workflows/source-checks.yml` 在 Linux 上执行前两组 52 项无浏览器专项和语法检查；它不是 Windows 安装验收，也不代表整套测试通过。

## 整套历史测试并未通过

执行 `python -m unittest discover -s tests -p 'test_*.py'`：运行 252 项，**10 项失败、30 项错误**。

- 30 项错误涉及 Windows DPAPI、`ctypes.WinDLL`、`subprocess.CREATE_NO_WINDOW` 等当前 Mac 环境不具备的能力。
- 10 项断言失败涉及 Mac 临时路径规范化、删除账号测试缺少账号前提、旧小红书原发布与定时流程、编辑器及热点页面、AI 连接提示和账号忙碌状态等。部分预期与 1.3.0 矩媒绑定要求不一致，其他项目仍需独立排查，不能全部归因于环境。
- 另执行的 50 项核心组合中有 3 项失败：Mac 临时路径比较 1 项、手动浏览器删除账号前提 2 项。保留原测试和应用实现，未为获得全绿而修改产品行为。

本次任务是整理与公开现有版本，没有补完平台功能或修复全部旧测试。已在 README 中明确开发状态，并以预发布形式提供。

## Windows 安装包

用户在本次任务明确确认 Windows 安装包可以使用，并要求一并分享。发布附件取自原目录最新的 `MediaAssistant-Setup-1.3.0.exe`：

- 大小：264,036,649 字节。
- SHA-256：`1e2b54942d68361949e442836b96dd0f2bd0f6a77cc7541c02d08f09ff19e146`。
- 本次为原文件复制与完整性校验，没有重新编译、签名或在 Windows 上运行。
- 当前源代码构建定义不包含账号 `data/`；本次未解包安装程序逐文件复核，不能据此宣称旧安装包与开源提交完全一致。
- 原目录现存 `package-check-result.json` 报告为 1.2.0，不能拿它作为 1.3.0 的验收结果。

## 未验证

真实抖音/B 站当前投稿、其他平台完整发布、真实矩媒账号发布、Windows 安装升级与卸载、付费 AI 模型和实时热点接口。历史文档中的成功描述属于原记录，不是本次重新实测结果。
