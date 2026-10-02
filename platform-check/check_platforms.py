# -*- coding: utf-8 -*-
"""
逐曜AI 技术论坛巡检工具

做什么（三项，都是"AI 收录"目标链上的真指标，不是虚荣指标）：
  1. 收录检测  —— 文章是否已被百度收录（site: 查询，实测可自动化）
  2. 账号名可读性 —— 文章页服务端 HTML 里能否读到账号名（AI 爬虫不执行 JS，
                     这一项不过，账号名策略就是白做）
  3. 主页指标  —— 公开主页能抓到的字段（腾讯云已实测支持）

不做什么：
  - 不登录任何平台，不碰后台数据，只读公开页
  - 不做评论/点赞/粉丝的横向比较（口径不同、可刷，无决策价值）

用法：
  python check_platforms.py            # 全量巡检
  python check_platforms.py --only-index   # 只查收录
  python check_platforms.py --only-author  # 只查账号名可读性

频率提醒：低频，发文后 D+1 / D+7 各一次。跑太勤会触发平台风控。

结果追加到同目录 history.jsonl，用于看趋势。
"""
import json
import os
import re
import ssl
import sys
import time
import gzip
import urllib.parse
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "platforms.json")
HISTORY = os.path.join(BASE, "history.jsonl")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# 默认走系统证书校验。公开仓库里关闭证书校验是坏示范，会有人直接复制去用。
# 仅当目标站点证书异常、且你明确接受风险时，再显式关闭：
#   python check_platforms.py --insecure     或     CHECK_INSECURE=1 python check_platforms.py
INSECURE = ("--insecure" in sys.argv) or (os.environ.get("CHECK_INSECURE") == "1")
CTX = ssl.create_default_context()
if INSECURE:
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE


BLOCK_SIGNALS = ("Security Verification", "请输入验证码", "人机验证",
                 "滑动验证", "访问验证", "captcha", "Verify you are human")


def looks_blocked(body, require_html=True):
    """
    判断响应是不是"验证页/限流页/残缺页"。
    这一层不是可选项：如果把限流页当成正常页面去判定，
    会把"没抓到"误报成"账号名不可读"，得出完全相反的错误结论。

    require_html=False 用于 sitemap 等 XML 资源——XML 天生没有 title/doctype，
    用 HTML 结构去卡它会把所有 sitemap 误判为异常（已实测踩过）。
    """
    if not body:
        return True, "空响应"
    if len(body) < 5000:
        return True, f"响应过短({len(body)}字符)"
    for kw in BLOCK_SIGNALS:
        if kw.lower() in body.lower():
            return True, f"命中验证特征「{kw}」"
    low = body.lower()
    has_title = "<title" in low
    # EdgeOne/CDN JS 挑战页特征（2026-10-02 实测 29182 字节）：
    # 有 DOCTYPE、超 5000 字、不含"验证/captcha"字样，但无 <title>，开头即内联脚本。
    # 旧判据（doctype 在场即放行）会把它当正常页 → 把"被风控"误判成"账号名不可读"。
    if require_html and not has_title and "doctype" in low:
        return True, "无<title>的HTML壳（疑似JS挑战/风控页）"
    if require_html and not has_title:
        return True, "响应不像完整 HTML（缺 title/doctype）"
    return False, ""


def fetch(url, timeout=15, retries=2, pause=4, require_html=True):
    """
    只读抓取公开页，带退避重试。
    返回 (status, body, blocked_reason)；blocked_reason 为空表示页面正常。
    实测结论：连续快速请求会被限流，单次请求正常 —— 所以必须退避，且不要高频跑。
    """
    last = ""
    for i in range(retries + 1):
        status, body = _fetch_once(url, timeout)
        blocked, why = looks_blocked(body, require_html=require_html)
        if status == 200 and not blocked:
            return status, body, ""
        last = f"HTTP={status}" if status != 200 else why
        if i < retries:
            time.sleep(pause * (i + 1))   # 4s、8s 退避
    return status, "", f"重试{retries}次仍异常：{last}"


def _fetch_once(url, timeout):
    """单次抓取。实测：加 Referer 反而触发异常变体，故不发送 Referer。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return 200, raw.decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return type(e).__name__, ""


# ---------------------------------------------------------------- 1. 收录检测
def check_index(url, keyword):
    """
    用百度 site: 查询判断目标 URL 是否被收录。
    做法：搜 `site:<域名> <关键词>`，再看结果页里有没有目标 URL 的路径。
    注意：site: 不支持完整 URL，所以只能"域名 + 关键词"命中后比对路径。
    """
    if not url:
        return "SKIP", "未填写 URL"
    host = urllib.parse.urlparse(url).netloc
    path = urllib.parse.urlparse(url).path
    q = f"site:{host} {keyword}"
    api = "https://www.baidu.com/s?wd=" + urllib.parse.quote(q)
    status, body, why = fetch(api)
    if why:
        return "ERROR", f"百度返回异常页，需人工确认（{why}）"
    # 结果页里出现目标路径即视为收录
    if path and path in body:
        return "收录", f"命中路径 {path}"
    # 退一步：看有没有该域名的任何结果
    n = len(re.findall(re.escape(host), body))
    return "未收录/未命中", f"结果页含 {host} 共 {n} 处，但未命中该路径"


def check_in_sitemap(url, sitemaps):
    """
    检查文章是否已被平台纳入 sitemap。
    为什么需要它：百度 site: 查询实测会被风控（返回验证页），不可靠。
    而平台自己的 sitemap 是公开静态文件、无风控、且直接反映"平台是否已把
    这篇文章纳入索引体系"，是更稳的收录前置指标。
    """
    if not url or not sitemaps:
        return "SKIP", "未配置 sitemap"
    path = urllib.parse.urlparse(url).path
    for sm in sitemaps:
        _st, body, why = fetch(sm, require_html=False)   # sitemap 是 XML，不能用 HTML 结构卡
        if why:
            continue
        if url in body or (path and path in body):
            return "已入 sitemap", sm
        time.sleep(3)
    return "未入 sitemap", f"已查 {len(sitemaps)} 个 sitemap，未命中"


# ------------------------------------------------- 2. 账号名可读性（P2 判据）
def check_author_visible(url, account):
    """
    核心判据：账号名是否出现在服务端返回的 HTML 里。
    AI 爬虫不执行 JS，若账号名靠前端渲染，则整套账号名策略不成立。
    """
    if not url:
        return "SKIP", "未填写 URL"
    status, html, why = fetch(url)
    if why:
        # 关键：抓不到 ≠ 判据不成立。必须区分"页面异常"与"页面正常但没账号名"。
        return "ERROR", f"页面异常，无法判定（{why}）——不要据此认为账号名策略失败"
    # 唯一通过依据：账号名字符串确实出现在服务端 HTML 里。
    # 绝不能用"页面存在作者名容器"当通过依据——那只说明页面有作者区，
    # 与"是不是我们的账号名"无关，会让不存在的名字也判通过（假阳）。
    found = bool(account) and account in html
    extra = []
    if re.search(r'"author"\s*:', html):
        extra.append("JSON-LD author")
    if re.search(r'<meta[^>]+name=["\']author["\']', html, re.I):
        extra.append("meta author")
    if re.search(r'author-info__name|author-name|nickname', html, re.I):
        extra.append("作者名 DOM 容器")
    if not found:
        tail = ""
        if extra:
            tail = ("；页面确有作者区（" + " / ".join(extra) +
                    "），但里面不是我们的账号名 —— 先核对文章 URL 与账号名是否填对")
        return "不通过", "服务端 HTML 中未出现账号名" + tail
    return "通过", "账号名出现在 HTML 中" + ("（另有 " + " / ".join(extra) + "）" if extra else "")


# --------------------------------------------------------------- 3. 主页指标
def fetch_home_metrics(platform):
    """抓公开主页的可见指标。目前仅腾讯云已实测支持。"""
    home = platform.get("home")
    if not home:
        return "SKIP", "未填写主页 URL"
    if platform.get("name") != "腾讯云社区":
        return "SKIP", f"{platform.get('name')} 解析规则未实现（需先抓一次样本确认字段）"
    status, html, why = fetch(home)
    if why:
        return "ERROR", f"页面异常（{why}）"
    out = {}
    m = re.search(r'文章被阅读\s*([\d.]+[KkMm]?)', html)
    if m:
        out["总阅读量"] = m.group(1)
    m = re.search(r'"articleCount"\s*:\s*(\d+)', html)
    if m:
        out["文章数"] = m.group(1)
    m = re.search(r'"followerCount"\s*:\s*(\d+)', html)
    if m:
        out["粉丝数"] = m.group(1)
    return ("OK", out) if out else ("WARN", "页面已抓到但字段未匹配（页面结构可能变了）")


# ---------------------------------------------------------------------- main
def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    account = cfg.get("accountName", "")
    print("=" * 72)
    print(f"逐曜AI 技术论坛巡检  账号名：{account}  时间：{datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 72)

    results = []
    sm_map = {p.get("name"): p.get("sitemaps", []) for p in cfg.get("platforms", [])}
    for art in cfg.get("articles", []):
        kw = art.get("keyword") or art.get("title", "")[:12]
        print(f"\n【文章】{art.get('title')}")
        for u in art.get("urls", []):
            plat, url = u.get("platform"), u.get("url")
            if not url:
                print(f"  - {plat}: 未填写 URL，跳过")
                continue
            if only != "--only-author":
                s, d = check_index(url, kw)
                print(f"  - {plat} 百度收录: {s} ({d})")
                results.append({"type": "index", "platform": plat, "url": url, "status": s, "detail": d})
                s3, d3 = check_in_sitemap(url, sm_map.get(plat, []))
                if s3 != "SKIP":
                    print(f"  - {plat} sitemap: {s3} ({d3})")
                    results.append({"type": "sitemap", "platform": plat, "url": url, "status": s3, "detail": d3})
            if only != "--only-index":
                s2, d2 = check_author_visible(url, account)
                print(f"  - {plat} 账号名可读: {s2} ({d2})")
                results.append({"type": "author", "platform": plat, "url": url, "status": s2, "detail": d2})
            time.sleep(5)  # 实测：间隔低于此值会被限流，进而产生误报

    print("\n【主页指标】")
    for p in cfg.get("platforms", []):
        if not p.get("enabled"):
            continue
        s, d = fetch_home_metrics(p)
        print(f"  - {p.get('name')}: {s} {d if isinstance(d, str) else d}")
        results.append({"type": "home", "platform": p.get("name"), "status": s, "detail": d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)})
        time.sleep(5)

    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                            "results": results}, ensure_ascii=False) + "\n")
    print(f"\n结果已追加：{HISTORY}")


if __name__ == "__main__":
    main()
