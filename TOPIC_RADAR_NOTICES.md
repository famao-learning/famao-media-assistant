# 热点选题模块：来源与开源声明

本模块在现有 Flask 桌面平台内新增本地选题功能。公开来源协议和响应结构参考以下项目，并针对 Python、本地账号与 SQLite 重新适配：

- NewsNow：https://github.com/ourongxing/newsnow ，参考 server/sources/baidu.ts、bilibili.ts、douyin.ts、toutiao.ts、thepaper.ts、zhihu.ts 的原站结构。
- DailyHotApi：https://github.com/imsyy/DailyHotApi ，参考 src/routes/baidu.ts、douyin.ts、weibo.ts 的公开数据结构和匿名会话处理。

当前40个频道、26个网站均读取原站公开入口或 RSS，不依赖公共聚合服务。在原有12个频道基础上，增加腾讯、网易、凤凰、快手、红餐网、华尔街见闻、钛媒体、人人都是产品经理、科学网、NASA、Phys.org、ScienceAlert、Nature、Smithsonian、BBC两类订阅及中新网12个分类订阅。抖音仅建立内存中的匿名临时 CookieJar，不读取发布账号登录信息；同轮两个抖音来源共享一次响应。最多4个主机并行，同一主机串行读取。

新增数据结构还参考 DailyHotApi 的 qq-news.ts、netease-news.ts、kuaishou.ts，以及 NewsNow 的 ifeng.ts、wallstreetcn.ts。页面中的 JSON 仅作数据解析，不执行 JavaScript。仅保存标题、原文链接、可确认的发布时间与榜单指标；来源全文及图片不自动下载或再发布。中新网官方 RSS 目录：https://www.chinanews.com.cn/rss/ 。

ScienceDaily RSS 使用标题、链接与发布时间，标明英文来源及原站归属，不保存全文。订阅说明：https://www.sciencedaily.com/newsfeeds.htm 。RSS 顺序与平台推荐顺序不当作热度排名。抖音的 hot_value 仅标记为来源热度，不是播放量；event_time 不当作视频发布时间。

未将 TrendRadar 的 GPL 源码并入本模块。TrendRadar 的独立部署与进一步 AI 能力复用保留为后续路线。

以下保留上述两个 MIT 项目的版权与许可声明。

MIT License

Copyright (c) 2024 ourongxing

Copyright (c) 2023 imsyy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
