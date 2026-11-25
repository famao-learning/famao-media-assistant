> 历史设计与实施记录（2026-10）；不构成本次发布的验收结论，未实现部分仍是方案。

# 补充数据源项目与接入验证

核验日期：2026-10-17。本文基于官方仓库与文件检查，尚未本地部署或执行来源接口。

## 1. 选型结论

RSSHub 适合补充人物报道、垂直行业和地方内容的订阅入口；DailyHotApi 适合作为可替换的热榜 API 候选。两者都属于数据接入层，账号定位、事件聚合、趋势历史和选题工作流由主业务平台承担。

先选择一个热榜聚合服务作为主入口，避免对同一上游平台同时运行多套采集器。候选服务可以作为备用连接器，但必须记录原始平台与上游依赖，不能把三个聚合服务返回的同一微博榜单视为三个独立平台。

## 2. RSSHub

| 项目 | 核验结果 |
|---|---|
| 仓库 | [DIYgod/RSSHub](https://github.com/DIYgod/RSSHub) |
| 定位 | 将不同网站内容转换为可订阅信息流 |
| 实现 | Node.js / TypeScript；依赖与运行版本以固定提交的 package.json 为准 |
| 声明许可证 | AGPL-3.0 |
| 接入方式 | 独立部署，通过 RSS/Atom 接入；仅启用验证过的路由 |
| 本项目用途 | 扩展人物、地方、行业等选题资料来源 |

上述定位和许可证见[官方仓库](https://github.com/DIYgod/RSSHub)、[package.json](https://github.com/DIYgod/RSSHub/blob/master/package.json)及[LICENSE](https://github.com/DIYgod/RSSHub/blob/master/LICENSE)。

RSSHub 能提供路由，并不保证每个路由在目标服务器持续可用，也不保证返回完整正文或热度指标。逐个记录是否需要凭证、能取得哪些字段、缓存周期与抓取限制。

具体餐饮和人物来源应根据实际账号定位选择；本次没有验证特定公众号、付费媒体或封闭平台的全文接入能力，因此不把它们列为已接通渠道。

网络服务与代码修改需要按其实际许可证处理，保留上游声明。采用独立服务是为了清晰的升级和运行边界，不能据此推断许可证义务自动消失。

## 3. DailyHotApi

| 项目 | 核验结果 |
|---|---|
| 仓库 | [imsyy/DailyHotApi](https://github.com/imsyy/DailyHotApi) |
| 定位 | 热榜聚合 API，提供 JSON 和 RSS 输出 |
| 实现 | TypeScript / Hono；当前核验的 package.json 声明版本为 2.0.8 |
| 声明许可证 | MIT |
| 本项目用途 | 热榜接入备用方案，或采用 API 优先路线时作为数据入口 |
| 需要补充 | 自有历史快照、事件层、AI 证据、账号推荐、权限与创作管理 |

功能和接口候选见[官方 README](https://github.com/imsyy/DailyHotApi)，框架与版本见[package.json](https://github.com/imsyy/DailyHotApi/blob/master/package.json)，许可证见[LICENSE](https://github.com/imsyy/DailyHotApi/blob/master/LICENSE)。版本字段不是最后提交日期，也不能单独用来判断维护活跃程度。

README 列有微博、知乎、百度、抖音、B 站等接口，但这些是仓库声明的支持范围，不是本次运行验证结果。README 也说明示例站点可能访问异常，且部署地区可能影响部分国内来源的访问。

## 4. 每个信息源的验收表

接入前创建一条 source_binding，保存以下资料：

| 字段 | 说明 |
|---|---|
| connector、版本或提交 | official_api / newsnow / dailyhot / rss / rsshub 等 |
| upstream_platform、dependency_group | 区分真实平台与聚合服务，识别共同故障来源 |
| route、board_key、region | 明确实际榜单、栏目和地区 |
| scope_id、credential_ref | 公共或工作空间范围，以及凭证引用 |
| supported_fields | 标题、原文 URL、排名、时间、热度、正文等实际可用字段 |
| source_interval、cache_ttl、poll_interval | 区分上游更新、聚合缓存与本系统轮询 |
| last_success、last_content_change | 成功采集与内容变化分开记录 |
| coverage、freshness、cost | 内容覆盖、延迟与实际调用成本 |
| content_usage_mode | 可保存的摘要/正文/原始响应范围及保留期 |

验证步骤：

1. 用 3—5 个互补来源启动试运行，至少包含热榜和非热榜资料。
2. 同时记录原始响应和标准化结果，确认排序、URL、时间解析正确。
3. 重放相同批次，验证不会生成重复版本、重复推荐或重复通知。
4. 连续采集至少 7 天，观察成功率、来源更新间隔、空响应、缓存和字段变化。
5. 对同一平台的两种入口做抽样比对，仅将结果作为可替换性评估。
6. 接入失败时保留最后成功结果并标记延迟；不将缺失值转成零热度。

## 5. 更新节奏的现实边界

抖音官方“查询热点视频”能力页说明，热点榜约每两个小时刷新，移动/网站应用通过页面申请能力。该能力可作为官方接口候选，但不能据此承诺所有源均为分钟级更新。[官方能力说明](https://developer.open-douyin.com/capacity-center-page/capacity-detail/7180573594794065975)

系统发现延迟应拆成：上游发布延迟 + 聚合服务缓存延迟 + 本系统轮询等待 + 处理排队。界面分别展示来源更新时间和最近成功采集时间，监控指标也分别计量。
