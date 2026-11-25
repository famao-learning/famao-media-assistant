# 第三方组件与资料

根目录 MIT 许可证仅覆盖本项目自有代码和文档，不替代依赖、已有声明文件或独立应用的许可。`packaging/licenses/` 保留原项目随包整理的声明；它们对应历史构建环境，不是本次安装环境的版本锁文件。

- Python、Flask 及其依赖：由 Python 包管理器安装，使用各自许可证。
- Playwright 及其浏览器依赖：分别适用对应项目和浏览器的许可证；本仓库不携带浏览器二进制。
- PySide6 / Qt：依组件和使用方式适用其开源或商业许可；重新分发桌面程序时需检查实际构建所用组件与许可，并随附相应声明。
- imageio-ffmpeg / FFmpeg：实际二进制与构建配置适用各自许可；本仓库不分发 FFmpeg 二进制。
- 矩媒（MatrixMedia）：通过本机 HTTP 接口连接的独立可选应用；本仓库不收录其源码或二进制。原集成参考项目为 <https://github.com/hanliang97/MatrixMedia>，使用或分发时以该上游实际许可为准。
- `docs/hotspot-design/` 中的 TrendRadar、NewsNow、RSSHub 等链接是历史选型与设计参考，不代表本仓库收录这些项目的源码。

打包工具 `scripts/collect_runtime_licenses.py` 用于收集当前构建环境声明；重新构建后应检查生成结果，不能把历史声明目录视为自动完成了全部依赖审查。
