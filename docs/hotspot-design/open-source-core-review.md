> 历史设计与实施记录（2026-10）；不构成本次发布的验收结论，未实现部分仍是方案。

# TrendRadar 与 NewsNow：开源基座源码核验与二开边界

核验日期：2026-10-17。
后续实施更新：已选择直接集成现有 Flask 桌面平台，采用轻量公开源连接器；本次未引入 TrendRadar GPL 源码。实际架构和来源实测见 [集成说明](integration.md)。本文保留独立服务基座的比较依据。
核验范围：仅项目官方 GitHub 仓库、官方原始源码、GitHub 官方 API；未采用第三方介绍推断功能。
验证方式：源码与许可证静态审查；未克隆、安装、启动、部署，未运行测试，未验证每个采集接口当天可用。
“已有”表示在所查源码中存在；“建议新增”是本项目设计，不是上游承诺。
本次可见 HEAD：TrendRadar 657094ed6a527441b917e16d9497fc8435ed091d；NewsNow 0560c993b226cd2fd07d8a79875d50551f52c584。
文件引用使用官方分支 URL，便于持续查阅；真正启动二开时，应将所用源码与依赖锁定到选定提交后再做可运行性验收。

## 1. 结论与选型

首选 fork TrendRadar，保留采集、RSS、模型客户端、筛选、报告与通知模块，新增面向创作账号的业务层和交互前端。
NewsNow 更适合作为自托管热榜来源；需要其阅读体验时，可单独复用界面组件，或保留其独立阅读站。
原因：当前目标包含“监控、AI 筛选、多账号推荐、选题素材包”，TrendRadar 已覆盖较多后台链路；只 fork NewsNow 仍需补齐 AI、历史、调度和通知。
这不意味着 TrendRadar 已经具备事件系统和多账号创作产品；这些是本次二开的主要新增工作。
如果实际目标收缩为“好用的网页热榜阅读器”，则 NewsNow 直接 fork 的改造量更小。

| 关注点 | TrendRadar | NewsNow |
| --- | --- | --- |
| 主实现 | Python 包、命令行、独立 MCP 服务 | TypeScript、React、Nitro 服务端 |
| 热榜采集 | 默认调用 NewsNow API | 内置多个平台采集适配器 |
| RSS | 已有抓取、解析、存储与筛选 | 已有 RSS 与 RSSHub 来源帮助函数 |
| 历史数据 | 按日 SQLite，含排名和标题变化 | 每个来源一条可覆盖缓存，非历史仓库 |
| AI | 兴趣标签、标题分类、分析、翻译 | 所查主干未见内置选题或 LLM 分析流水线 |
| 通知 | 多渠道发送、分批、格式处理 | 所查主干未见对应通知派发模块 |
| 前端 | 可交互 HTML 报告、配置编辑入口 | 正式 React 阅读界面、来源卡片及拖动 |
| 登录用户 | 所查核心模型未见用户与组织权限 | GitHub OAuth 与用户偏好同步 |
| 创作账号 | 需新增 | 需新增 |
| 领域＋表达方式推荐 | 兴趣筛选能复用，表达方式及账号排名需新增 | 需新增 |
| 实际 LICENSE | GNU GPL v3 | MIT |

表中技术依据分别见下文的具体文件；未把 README 的功能名称当作完整产品能力。

## 2. TrendRadar 的实际代码与能力

### 2.1 代码结构与入口

[pyproject.toml](https://github.com/sansan0/TrendRadar/blob/master/pyproject.toml) 声明版本 6.11.0、Python >= 3.12。
声明依赖包括 requests、feedparser、PyYAML、LiteLLM、FastMCP、boto3；命令入口为 trendradar.__main__:main，MCP 入口为 mcp_server.server:run_server。
目录按 crawler、storage、core、ai、notification、report、commands 拆分，已经具备可复用模块边界，而不是单文件脚本。
[context.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/context.py) 已有 AppContext(config)，封装时间、平台配置、存储、筛选和通知依赖。
因此不应描述成“先消灭所有全局 CONFIG”；应检查剩余进程状态及文件路径，再增加明确的依赖注入。

### 2.2 数据实际从哪里来

[热榜 fetcher](https://github.com/sansan0/TrendRadar/blob/master/trendradar/crawler/fetcher.py) 默认访问 NewsNow 的 /api/s，以 id 和 latest 参数取数据。
其 API 地址允许构造参数覆盖，具有超时、重试、代理与预期域名校验；可指向自托管 NewsNow。
读取的主要内容是标题、URL、mobileUrl，以及数组顺序形成的排名；不是通用正文采集器，也不是全网搜索引擎。
抓取结果在单个平台内以标题作字典键；相同标题可能合并排名，因此适配时要核查原始条目 ID、URL 和标题的关系，必要时在该处补充稳定标识。
[RSS fetcher](https://github.com/sansan0/TrendRadar/blob/master/trendradar/crawler/rss/fetcher.py) 是独立路径，可保留 RSS 抓取与错误处理。
结论：保留已有热榜与 RSS 接入很省工；需要正文、评论、视频字幕或权威原文证据时，另加取证适配器，不能声称已有。

### 2.3 存储确有历史，但以“天”为边界

[local.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/storage/local.py) 将数据写入 output/news/YYYY-MM-DD.db 与 output/rss/YYYY-MM-DD.db。
其 SQLite 连接按数据库路径缓存；保留期清理也是按日期文件执行，适合个人定时采集。
[热榜 schema](https://github.com/sansan0/TrendRadar/blob/master/trendradar/storage/schema.sql) 包含 news_items、title_changes、rank_history、crawl_records、crawl_source_status。
news_items 对非空 URL 使用 URL＋platform_id 唯一索引；已保存首次/最后抓取时间、抓取次数与标题变化。
period_executions 按 execution_date、period_key、action 唯一，支持时段内分析/推送只执行一次。
[RSS schema](https://github.com/sansan0/TrendRadar/blob/master/trendradar/storage/rss_schema.sql) 保存 published_at、summary、author、guid 与抓取时间，含 URL＋feed_id 及非空 GUID＋feed_id 唯一索引。
上述结构能复用采集记录与日内趋势；跨日稳定事件、转载族、正文版本、账号选题、审核和发布反馈仍要新建。
[manager.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/storage/manager.py) 支持 local/remote/auto 存储与远程数据拉取；远程配置是 S3 兼容对象存储参数，不等于多租户数据库能力。
关键边界：该文件仍有模块级 _storage_manager，get_storage_manager 默认返回进程单例。
AppContext 调用此工厂时没有传 force_new；多个 AppContext 并不自动获得各自独立的存储配置。
二开可直接注入独立 StorageManager/业务 repository；不能靠并发修改全局配置或反复 force_new 获得可靠隔离。

### 2.4 AI 已有多少

[client.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/ai/client.py) 已封装 LiteLLM completion，接受模型、API 地址、密钥、超时、重试及备用模型等配置。
[filter.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/ai/filter.py) 分“兴趣描述提取标签”和“标题批量分类”两步，并解析相关度结果。
分类输入主要是新闻 ID、来源和标题；每条新闻在一次分类中只保留最高分标签，不能直接当作多维领域标签系统。
[filter_pipeline.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/ai/filter_pipeline.py) 已编排标签更新、待分析内容收集、分批调用、存储及报告转换。
[AI schema](https://github.com/sansan0/TrendRadar/blob/master/trendradar/storage/ai_filter_schema.sql) 已使用 interests_file、prompt_hash、标签版本和已分析记录减少重复请求。
因此“多个兴趣文件”是可复用能力；它不是创作账号的所有权、成员权限、独立预算、表达风格或独立发布反馈。
[analyzer.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/ai/analyzer.py) 将新闻标题、来源、排名区间、出现次数及可选排名轨迹组织为模型输入，RSS 部分也主要拼接标题和时间。
[分析提示词](https://github.com/sansan0/TrendRadar/blob/master/config/ai_analysis_prompt.txt) 要求输出趋势、争议、弱信号、RSS 洞察和策略等内容。
这属于分析简报，不能替代逐条事实与原文对应的选题素材包；提示词要求交叉验证，不代表程序已经完成证据核验。
建议复用模型调用与批处理，另建 evidence_set、claim、account_recommendation、topic_package，记录依据版本及引用位置。

### 2.5 通知、报告与调度

[dispatcher.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/notification/dispatcher.py) 负责多渠道分发，包含同渠道多接收地址、区域过滤和内容格式处理。
[docker-compose.yml](https://github.com/sansan0/TrendRadar/blob/master/docker/docker-compose.yml) 可见飞书、Telegram、钉钉、企业微信、邮件、ntfy、Bark、Slack、通用 Webhook 环境配置。
这里的“多账号”主要是通知接收端；共享一份 report_data 发送给多个接收端，不等于为多个创作账号分别排序和生成内容。
[report/html.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/report/html.py) 是 HTML 报告渲染实现；可以保留作为邮件与导出报告，不必为了新前端全部重写。
[README](https://github.com/sansan0/TrendRadar#readme) 描述报告搜索、暗色主题、标签切换和导出；这些交互不等于账号设置、选题看板和审核工作流。
[core/scheduler.py](https://github.com/sansan0/TrendRadar/blob/master/trendradar/core/scheduler.py) 解析 timeline、星期、时段、collect/analyze/push 以及 once 规则。
Docker 配置另有 CRON_SCHEDULE 与 RUN_MODE；[Actions workflow](https://github.com/sansan0/TrendRadar/blob/master/.github/workflows/crawler.yml) 也可触发采集。
新系统接管调度时，必须明确停用哪些旧入口及旧直接发送路径，避免一次采集被两套调度器触发、一次选题重复推送。

### 2.6 许可证与可见维护事实

已读取 [实际 LICENSE](https://github.com/sansan0/TrendRadar/blob/master/LICENSE)，内容为 GNU General Public License Version 3，不是 MIT。
可以修改和运行；向他人分发所覆盖修改版本时，应按许可证履行告知、保留许可和对应源码等义务。
许可证本身区分网络交互与副本分发，不应简单写成“任何内部修改都必须公开”或“只要做 SaaS 就完全没有义务”。
本次建议以遵循 GPLv3 的 fork 为前提；若以后要求分发闭源派生产品，需重新审视基座和具体组合方式。
官方 API 可见最新提交为 [657094e](https://github.com/sansan0/TrendRadar/commit/657094ed6a527441b917e16d9497fc8435ed091d)，提交者时间 2026-10-15T11:21:37Z，内容是文档更新。
README 可见 2026-10-13 的 v6.11.0 更新说明；日期能证明当时有更新，不能据此承诺长期维护或采集可用率。

## 3. NewsNow 的实际代码与能力

### 3.1 交互应用与代码组织

[package.json](https://github.com/newsnext/newsnow/blob/main/package.json) 声明版本 0.0.43、TypeScript、React、Vite、TanStack Router/Query、Jotai、UnoCSS 与 pnpm。
[nitro.config.ts](https://github.com/newsnext/newsnow/blob/main/nitro.config.ts) 配置 Nitro 服务端；默认 Node 服务与 better-sqlite3，Cloudflare Pages 分支使用 D1。
server/sources 放平台适配器，shared/pre-sources.ts 等定义来源元信息，server/api 提供接口，src 放交互前端。
[卡片拖动界面](https://github.com/newsnext/newsnow/blob/main/src/components/column/dnd.tsx) 已实现来源卡片布局和关注栏目排序，桌面与移动端有各自布局。
这些 UI、来源选择与刷新交互值得复用；但事件详情、创作账号切换、推荐理由、素材包编辑和审核仍需增加。

### 3.2 采集链路与缓存边界

[getters.ts](https://github.com/newsnext/newsnow/blob/main/server/getters.ts) 从 sources 目录加载来源函数，按 source ID 映射调用。
[微博适配器](https://github.com/newsnext/newsnow/blob/main/server/sources/weibo.ts) 通过网页请求与 Cheerio 解析热搜页面；说明该项目确有直接来源抓取代码。
这也意味着来源可用性依赖各平台页面、请求条件和限流变化；本次未运行，不能承诺所有源稳定可用。
[source.ts](https://github.com/newsnext/newsnow/blob/main/server/utils/source.ts) 提供 defineSource、defineRSSSource、defineRSSHubSource，适合增加自有来源。
其中 RSSHub 基础地址在所查代码里固定为公共实例地址；如采用该路径，应改为显式配置和自托管地址。
[/api/s](https://github.com/newsnext/newsnow/blob/main/server/api/s/index.ts) 根据请求读取缓存，满足刷新条件时才调用 getter，是请求驱动的更新路径。
接口当前对 getter 结果使用 slice(0, 30)；默认只拿这一路 API 不能声称取到了来源全部条目。
[常量](https://github.com/newsnext/newsnow/blob/main/shared/consts.ts) 中 TTL 为 30 分钟，默认 Interval 为 10 分钟；具体来源可另设 interval。
注意：在 source.interval 内，/api/s 直接复用缓存，却返回 status=success 和 updatedTime=now。
因此 updatedTime 不能直接当作内容实际更新时间，success 也不能当作本次重新抓取上游的证据。
新管线应自己保存 observed_at、返回状态、内容哈希；需要更准的刷新语义时，扩展自托管 API 返回 cache_written_at 和 fetched_at。
[cache.ts](https://github.com/newsnext/newsnow/blob/main/server/database/cache.ts) 的 cache 表只有 id、updated、data；set 使用 INSERT OR REPLACE。
这是每个来源的最新快照缓存，不是新闻版本库、排名时间序列或跨日事件历史；完整监控必须在外层周期采集并落历史。

### 3.3 用户、AI、账号推荐与通知

[用户表](https://github.com/newsnext/newsnow/blob/main/server/database/user.ts) 保存 id、email、data、type、created、updated。
[OAuth 回调](https://github.com/newsnext/newsnow/blob/main/server/api/oauth/github.ts) 通过 GitHub 取得身份并签发 JWT；[auth 中间件](https://github.com/newsnext/newsnow/blob/main/server/middleware/auth.ts) 校验相关 API 请求。
[shared/types.ts](https://github.com/newsnext/newsnow/blob/main/shared/types.ts) 中 PrimitiveMetadata 的核心数据是栏目到来源 ID 列表的映射。
据这些模型及 README，现有登录侧重用户阅读偏好同步；不能把 user.data 直接认作创作账号配置和权限模型。
已检查的目录、依赖和 API 未见账户表达风格、领域适配评分、选题生成或通知分发流水线。
文件名 aihot 容易误判；[aihot.ts](https://github.com/newsnext/newsnow/blob/main/server/sources/aihot.ts) 实际只是接入一个 AI 新闻 RSS 源，不是 AI 分析实现。
README 的 MCP 使用说明指向独立 newsnow-mcp-server 包；接入 MCP 也不等于应用内已经完成自动推荐与素材包生产。

### 3.4 许可证与可见维护事实

已读取 [实际 LICENSE](https://github.com/newsnext/newsnow/blob/main/LICENSE)，为 MIT，版权署名为 2024 ourongxing。
允许使用、修改、分发和销售等，同时要求在副本或实质部分保留版权和许可通知；这与 TrendRadar 的 GPLv3 不同。
MIT 代码许可不等于第三方新闻内容、图片与平台数据接口获得了统一再授权。
官方 API 可见最新提交为 [0560c99](https://github.com/newsnext/newsnow/commit/0560c993b226cd2fd07d8a79875d50551f52c584)，时间 2026-10-13T04:47:07Z，内容为 release v0.0.43。
[README](https://github.com/newsnext/newsnow#readme) 提示下一代 NewsNext 将推出，并声明不再接收贡献；这是明确的协作边界。
不能把这句话写成“仓库已停更”：本次 API 可见更近提交；同样不能把尚未审查的新一代产品功能计入当前 fork。
部分网页缓存的 commits 列表比官方 API 旧；本文维护日期采用本次实际 API 响应。

## 4. 推荐的二开方式：保留上游模块，新增产品层

以下全部是建议方案，不是 TrendRadar 或 NewsNow 的现成实现。

1. fork TrendRadar 并保留上游目录；新增产品模块，尽量把修改压在配置、数据转换、依赖注入和任务入口。
2. 用 adapter 包装 DataFetcher 与 RSS 路径，将抓取结果转换为自己的 SourceItem/Observation；接口可用性先做小型 POC。
3. 需要稳定来源控制时自托管 NewsNow，配置 TrendRadar 的 api_url；不把公共演示 API 当作可承诺的生产依赖。
4. 新建 PostgreSQL 业务库，作为事件、账号、推荐、素材包和通知任务的权威数据源。
5. 旧 SQLite 暂留作上游行为回归、历史导入和过渡存档；无需为了第一批产品功能先重写全部 storage。
6. 导入旧数据时使用“来源库日期＋表类型＋本地 ID”等可追踪来源键，映射到新全局 ID；跨日相同 URL 再去重。
7. 将数据库连接、账号配置、模型参数、通知凭证作为显式依赖传入；删除业务调用对进程单例存储的依赖。
8. 新增 FastAPI 产品 API 与 React 工作台；原 HTML 报告保留作导出、邮件正文和回归参照。
9. 先接一个明确的任务调度入口；新管线负责共享采集、按账号推荐与生成，停用对应旧 cron/Actions 自动入口。
10. 复用通知 sender 与格式转换，但由新 outbox/幂等任务决定发送时机；避免调用旧整套“分析后直接群发”流程。
11. 复用 AIClient，扩展“领域相关性”和“表达方式可制作性”两维评分，再增加证据包及引用验证。
12. 新增创作账号表与 ownership/membership，独立保存风格、领域、受众、预算、选题状态和反馈；兴趣文件可作为导入来源。

主 fork 选择不强制双前端：NewsNow 默认只提供来源接口，先建设一个与创作流程一致的工作台。
若直接拼接两套完整 UI，还要维护两套登录、路由和设置，未必省工；组件级复用需要单独估算适配成本。
必须承担的成本是业务模型、跨日事件、来源证据、账号隔离、后台任务和创作 UI；不能用“已有 AI”掩盖这些工作。

## 5. 第一轮 POC 应验收什么

- 保留一个热榜源和一个 RSS 源，验证官方源慢更新、请求失败、重复返回以及榜单内容变更。
- 核对原始 API ID/URL、TrendRadar 标题键和新库稳定 ID 的映射，不让同标题不同事件被提前合并。
- 连续两次抓到相同内容时仍保存需要的观测，但不重复创建事件、推荐和素材包。
- 跨午夜采集相同事件时，能正确续接历史；旧日库 ID 与新日库相同不能发生串联。
- 同一事件分别生成两个创作账号的推荐，领域可相同而表达方式不同，配置、预算、缓存和通知互不串用。
- 给出一个只能作为讨论信号、缺少可靠正文证据的事件，确保素材包显示缺口而不凭标题编事实。
- NewsNow 缓存命中时，不伪造“刚刚更新”的内容时间；Top 30 截断应作为来源覆盖限制记录。
- 关掉旧调度与直接发送后，重跑失败任务也不重复推送；新任务日志能够追溯具体输入版本。

本轮源码审查的最终判断：TrendRadar 最适合作为当前需求的主 fork，NewsNow 是可直接复用的热榜接入底座；多账号创作工作流仍由新业务层完成。

