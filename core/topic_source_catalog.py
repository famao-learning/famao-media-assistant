"""Additional public channels. Counts refer to channels, not distinct websites."""
CHANNELS = {
    "qqnews": ("腾讯新闻热榜", "社会与人物", "ranking", "https://r.inews.qq.com/gw/event/hot_ranking_list?page_size=50"),
    "netease": ("网易新闻热榜", "社会与人物", "ranking", "https://m.163.com/fe/api/hot/news/flow"),
    "ifeng": ("凤凰新闻要闻", "社会与人物", "feed", "https://www.ifeng.com/"),
    "kuaishou": ("快手热榜", "其他视频平台", "ranking", "https://www.kuaishou.com/?isHome=1"),
    "redcan": ("红餐网 · 餐饮经营", "财经与行业", "feed", "https://www.canyin88.com/"),
    "wallstreetcn": ("华尔街见闻热榜", "财经与行业", "ranking", "https://api-one.wallstcn.com/apiv1/content/articles/hot?period=all"),
    "tmtpost": ("钛媒体资讯", "财经与行业", "rss", "https://www.tmtpost.com/rss.xml"),
    "woshipm": ("人人都是产品经理", "财经与行业", "rss", "https://www.woshipm.com/feed"),
    "sciencenet": ("科学网新闻", "知识与科技", "rss", "https://www.sciencenet.cn/xml/news-0.aspx?news=0"),
    "nasa": ("NASA · 太空新知（英文）", "国际科学与人文", "rss", "https://www.nasa.gov/feed/"),
    "phys": ("Phys.org · 科学研究（英文）", "国际科学与人文", "rss", "https://phys.org/rss-feed/"),
    "sciencealert": ("ScienceAlert · 科学发现（英文）", "国际科学与人文", "rss", "https://www.sciencealert.com/feed"),
    "nature": ("Nature · 科研与科学新闻（英文）", "国际科学与人文", "rss", "https://www.nature.com/nature.rss"),
    "smithsonian": ("Smithsonian · 历史与自然（英文）", "国际科学与人文", "rss", "https://www.smithsonianmag.com/rss/latest_articles/"),
    "bbc-zh": ("BBC中文", "国际与文化生活", "rss", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
    "bbc-world": ("BBC · 国际新闻（英文）", "国际与文化生活", "rss", "https://feeds.bbci.co.uk/news/world/rss.xml"),
}
for slug, label, group in (
    ("society", "社会", "社会与人物"), ("life", "生活", "国际与文化生活"),
    ("culture", "文化", "国际与文化生活"), ("edu", "教育", "国际与文化生活"),
    ("world", "国际", "国际与文化生活"), ("finance", "财经", "财经与行业"),
    ("jk", "健康", "知识与科技"), ("sports", "体育", "国际与文化生活"),
    ("china", "国内", "社会与人物"), ("dxw", "东西问", "国际与文化生活"),
    ("fz", "法治", "社会与人物"), ("sp", "视频", "其他视频平台"),
):
    CHANNELS["chinanews-" + slug] = ("中新网 · " + label, group, "rss", "https://www.chinanews.com.cn/rss/" + slug + ".xml")
