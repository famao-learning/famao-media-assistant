"""RSS/Atom/RDF and additional public JSON/HTML adapters. Never execute page scripts."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin
from xml.etree import ElementTree as ET


def parse_feed(raw):
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        if not re.search(br'encoding=[\"\'](?:gb2312|gbk|gb18030)[\"\']', raw[:150], re.I):
            raise ValueError("订阅源不是有效的 RSS / Atom / RDF") from None
        root = ET.fromstring(raw.decode("gb18030"))
    items = root.findall("./channel/item")
    if not items:
        items = root.findall("{http://purl.org/rss/1.0/}item")
    if not items:
        items = root.findall("{http://www.w3.org/2005/Atom}entry")
    entries = []
    for item in items[:100]:
        fields = {}
        for child in item:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "link" and child.get("href"):
                if child.get("rel", "alternate") == "alternate":
                    fields["link"] = child.get("href")
            elif child.text and name not in fields:
                fields[name] = child.text.strip()
        published = None
        date = fields.get("pubDate") or fields.get("published") or fields.get("date") or fields.get("updated")
        if date:
            try:
                try:
                    parsed = parsedate_to_datetime(date)
                except (ValueError, TypeError):
                    parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
                if parsed.tzinfo:
                    published = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
            except (ValueError, TypeError, OverflowError):
                pass
        entries.append({"native_key": fields.get("guid") or fields.get("id") or fields.get("link", ""),
                        "title": fields.get("title", ""), "url": fields.get("link", ""),
                        "rank": 0, "published_at": published})
    return entries


class ArticleHeadings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.heading, self.href, self.text, self.items = False, None, [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("h2", "h3"):
            self.heading = True
        if tag == "a" and self.heading:
            self.href, self.text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            if re.fullmatch(r"/(?:zixun|zhuanlan|video)/[\w/-]+\.html", self.href):
                self.items.append({"native_key": self.href, "title": "".join(self.text).strip(),
                                   "url": urljoin("https://www.canyin88.com", self.href), "rank": 0})
            self.href = None
        if tag in ("h2", "h3"):
            self.heading = False


def _embedded(raw, pattern):
    text = raw.decode("utf-8")
    match = re.search(pattern, text)
    if not match:
        raise ValueError("公开页面结构发生变化")
    value, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
    return value


def parse_extended(source, raw):
    if source == "redcan":
        parser = ArticleHeadings()
        parser.feed(raw.decode("utf-8"))
        return parser.items
    if source == "ifeng":
        rows = _embedded(raw, r"var\s+allData\s*=\s*").get("hotNews1", [])
        return [{"native_key": row.get("url", ""), "title": row.get("title"), "url": row.get("url", ""), "rank": 0}
                for row in rows if isinstance(row, dict)]
    if source == "kuaishou":
        data = _embedded(raw, r"window\.__APOLLO_STATE__\s*=\s*").get("defaultClient", {})
        board = next((value for key, value in data.items() if "visionHotRank(" in key), {})
        entries = []
        for ref in board.get("items", [])[:100]:
            row = data.get(ref.get("id"), {})
            videos = (row.get("photoIds") or {}).get("json") or []
            if row.get("tagType") == "置顶" or not videos or not re.fullmatch(r"[A-Za-z0-9_-]+", str(videos[0])):
                continue
            entries.append({"native_key": str(row.get("id") or row.get("name") or ""), "title": row.get("name"),
                            "url": "https://www.kuaishou.com/short-video/" + str(videos[0]), "rank": len(entries) + 1})
        return entries
    data = json.loads(raw)
    if source == "qqnews":
        groups = data.get("idlist") or []
        rows = groups[0].get("newslist", []) if groups else []
    elif source == "netease":
        rows = (data.get("data") or {}).get("list", [])
    else:
        rows = (data.get("data") or {}).get("day_items", [])
    entries = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        title = row.get("title")
        if source == "qqnews":
            key = str(row.get("id") or "")
            if not re.fullmatch(r"[0-9A-Za-z]{8,40}", key) or key.startswith("TIP"):
                continue
            url = "https://new.qq.com/rain/a/" + key
        elif source == "netease":
            key = str(row.get("docid") or "")
            if not re.fullmatch(r"[0-9A-Za-z]{8,40}", key):
                continue
            url = "https://www.163.com/dy/article/" + key + ".html"
        else:
            key, url = str(row.get("id") or ""), row.get("uri", "")
        entries.append({"native_key": key, "title": title, "url": url, "rank": len(entries) + 1})
    return entries
