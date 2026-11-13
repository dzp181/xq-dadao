#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
段永平（大道无形我有型）雪球动态 → ntfy 推送。

零第三方依赖，只用 Python 标准库。与 x2ding 完全独立：独立的目录、
独立的配置、独立的 state.json、独立的 ntfy 主题，互不影响。

数据源（2026-11-13 实测可用，无需登录 / 无需 xq_a_token / 无需浏览器）：
  1) GET https://xueqiu.com/{uid}              <- 别名路径（不是 /u/{uid}，那个走 WAF）
     返回真实主页 HTML，下发 u / cookiesu 两个匿名 cookie。
  2) GET https://api.xueqiu.com/v4/statuses/user_timeline.json?user_id={uid}&type=10
     带上一步的 cookie + Referer，返回 20 条完整时间线（含回复上下文）。

失败域隔离：抓取失败不影响 x2ding；本脚本的任何异常都不会写 state，
下一轮自动重试，不会漏推。

用法：
  python3 xq_push.py              # 正常跑一轮
  python3 xq_push.py --dry-run    # 只打印将推送的内容，不推送、不写 state
  python3 xq_push.py --init       # 只把历史底库灌入 state（首次部署用，避免刷屏）
  python3 xq_push.py --test       # 发一条自检消息，验 ntfy 链路
"""

import argparse
import datetime as dt
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
BASELINE_FILE = os.path.join(BASE_DIR, "baseline_ids.txt")

CST = dt.timezone(dt.timedelta(hours=8))
SITE = "https://xueqiu.com"
API = "https://api.xueqiu.com"

WAF_MARKS = ("aliyun_waf", "请求异常已被安全策略拦截", "访问提示 - 雪球")


def log(msg):
    print(f"[xq] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 配置 / 状态

def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("timeline_pages", 1)
    cfg.setdefault("max_per_run", 3)
    cfg.setdefault("max_age_hours", 18)
    cfg.setdefault("attach_image", False)
    return cfg


def empty_state():
    return {"seen": [], "seeded": False, "last_run": "", "last_status_id": ""}


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        if not isinstance(s.get("seen"), list):
            raise ValueError("seen 字段格式不对")
        return s
    except (OSError, ValueError, json.JSONDecodeError) as e:
        log(f"state.json 读取失败，按空状态处理：{e}")
        return empty_state()


def save_state(state):
    """原子写，避免中途失败留下半截文件。"""
    state["seen"] = list(dict.fromkeys(str(x) for x in state["seen"]))[-3000:]
    state["last_run"] = dt.datetime.now(CST).isoformat(timespec="seconds")
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def baseline_ids():
    try:
        with open(BASELINE_FILE, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip().isdigit()]
    except OSError:
        return []


# ---------------------------------------------------------------- HTTP 会话

def new_opener(cfg):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", cfg.get("user_agent") or
         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        ("Accept-Language", "zh-CN,zh;q=0.9"),
    ]
    return opener


def request(opener, url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace"), r.status


def is_waf(text):
    return any(m in text for m in WAF_MARKS)


def establish_session(opener, cfg):
    """别名路径建匿名会话。/u/{uid} 会撞 WAF，必须走 /{uid}。"""
    url = cfg.get("profile_url") or f"{SITE}/{cfg['uid']}"
    body, _ = request(opener, url, headers={"Accept": "text/html,application/xhtml+xml"})
    page = html.unescape(body)
    title = (re.search(r"<title>(.*?)</title>", page, re.S) or ["", ""])[1].strip()
    if is_waf(page):
        raise RuntimeError("别名主页返回了 WAF 拦截页")
    # 主页上是雪球昵称（大道无形我有型），不是显示名（段永平），别拿错字段校验
    nickname = cfg.get("profile_name") or cfg.get("name")
    if nickname and nickname not in page:
        raise RuntimeError(f"别名主页内容异常（没找到昵称 {nickname}），title={title[:60]!r}")
    log(f"匿名会话已建立（title={title[:40]}）")
    return True


def fetch_timeline(opener, cfg, page=1):
    q = urllib.parse.urlencode({
        "user_id": cfg["uid"],
        "type": 10,          # 10 = 全部动态（含回复、转发、长文）
        "page": page,
    })
    url = f"{API}/v4/statuses/user_timeline.json?{q}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": cfg.get("profile_url") or f"{SITE}/{cfg['uid']}",
        "X-Requested-With": "XMLHttpRequest",
    }
    body, _ = request(opener, url, headers=headers)
    data = json.loads(body)
    if isinstance(data, dict) and data.get("error_description"):
        raise RuntimeError(f"API 拒绝：{data['error_description']}")
    statuses = data.get("statuses") or []
    if not statuses:
        raise RuntimeError("时间线为空（接口形态可能变了）")
    return statuses


def fetch_detail(opener, cfg, status_id):
    """长文被截断时，补一次详情拿全文。失败就退回列表摘要。"""
    url = f"{API}/statuses/show.json?{urllib.parse.urlencode({'id': status_id})}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": cfg.get("profile_url") or f"{SITE}/{cfg['uid']}",
    }
    try:
        body, _ = request(opener, url, headers=headers, timeout=15)
        d = json.loads(body)
        if d.get("error_description") or d.get("error_code"):
            return ""
        return clean_html(d.get("text") or d.get("description") or "")
    except Exception as e:
        log(f"详情补全失败 {status_id}: {e}")
        return ""


# ---------------------------------------------------------------- 正文清洗

BR_RE = re.compile(r"<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
IMG_RE = re.compile(r"<img[^>]*>", re.I)
WS_RE = re.compile(r"[ \t\u00a0]+")
NL_RE = re.compile(r"\n{3,}")


def clean_html(raw):
    if not raw:
        return ""
    s = SCRIPT_RE.sub("", raw)
    s = IMG_RE.sub("", s)
    s = BR_RE.sub("\n", s)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = TAG_RE.sub("", s)          # 去掉 a 标签但保留 @某某 的文字
    s = html.unescape(s)
    s = WS_RE.sub(" ", s)
    s = "\n".join(ln.strip() for ln in s.splitlines())
    s = NL_RE.sub("\n\n", s)
    return s.strip()


def status_url(cfg, s):
    target = (s.get("target") or "").lstrip("/")
    if target and re.match(r"^\d+/\d+$", target):
        return f"{SITE}/{target}"
    return f"{SITE}/{cfg['uid']}/{s['id']}"


def build_item(opener, cfg, s):
    """把一条 timeline status 转成待推送的消息对象。"""
    sid = str(s["id"])
    text = clean_html(s.get("description") or s.get("text") or "")

    # 列表里的长文只有 ~150 字摘要，补一次详情
    if s.get("truncated") or s.get("is_column") or text.endswith("..."):
        full = fetch_detail(opener, cfg, sid)
        if len(full) > len(text):
            text = full

    # 时间线的 description 通常已经带「回复@X: ... //@Y: ...」整条引用链；
    # 只有在没有引用链时，才把 retweeted_status 的原帖上下文补上。
    context = ""
    rt = s.get("retweeted_status") or {}
    if rt and "//@" not in text and not text.startswith("回复@"):
        who = ((rt.get("user") or {}).get("screen_name") or "").strip()
        rt_text = clean_html(rt.get("description") or rt.get("text") or "")
        if rt_text:
            context = f"↪ 原帖{' @' + who if who else ''}：{rt_text[:300]}"

    title = (s.get("title") or "").strip()
    if title:
        kind = "长文"
    elif text.startswith("回复@") or "//@" in text:
        kind = "回复"
    elif rt:
        kind = "转发"
    else:
        kind = "发帖"

    ts = int(s.get("created_at") or 0)
    return {
        "id": sid,
        "kind": kind,
        "title": title,
        "text": text,
        "context": context,
        "url": status_url(cfg, s),
        "ts": ts,
        "source": s.get("source") or "",
        "reply_count": s.get("reply_count") or 0,
        "like_count": s.get("like_count") or 0,
    }


def cst_line(ts_ms):
    d = dt.datetime.fromtimestamp(ts_ms / 1000, CST)
    return d.strftime("%Y-%m-%d %H:%M CST")


def format_message(cfg, item):
    body = item["text"]
    if item["title"]:
        body = f"【{item['title']}】\n\n{body}"
    if item["context"]:
        body = f"{body}\n\n{item['context']}"

    # 正文里不放网址（避免 iOS 折行乱版），链接走 click —— 与 x2ding 一致
    # 雪球把 <a> 渲染成「网页链接」占位词，清掉避免留下无意义的尾巴
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\s*网页链接\s*", " ", body).strip()
    body = f"{body}\n\n{cst_line(item['ts'])}"

    # 长文通知正文太长会被截断，留一个上限
    if len(body) > 1800:
        body = body[:1800].rstrip() + "\n…（全文点开看）"

    tags = ["speech_balloon"] if item["kind"] == "回复" else ["memo"]
    payload = {
        "topic": cfg["ntfy_topic"],
        "title": f"{cfg['name']} · {item['kind']}",
        "message": body,
        "click": item["url"],
        "tags": tags,
    }
    if item["source"]:
        payload["message"] = payload["message"].replace(
            f"\n\n{cst_line(item['ts'])}",
            f"\n\n来自 {item['source']} · {cst_line(item['ts'])}", 1)
    return payload


# ---------------------------------------------------------------- 推送

def publish(cfg, payload, dry_run=False):
    if dry_run:
        log("--- DRY RUN 将推送 ---")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return True
    token = os.environ.get("XQ_NTFY_TOKEN") or ""
    url = cfg["ntfy_url"].rstrip("/") + "/"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        log(f"推送失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        return False
    except Exception as e:
        log(f"推送失败: {e}")
        return False


# ---------------------------------------------------------------- 主流程

def cmd_init():
    state = load_state()
    ids = baseline_ids()
    state["seen"] = list(dict.fromkeys(list(state["seen"]) + ids))
    state["seeded"] = True
    save_state(state)
    log(f"历史底库已灌入：{len(ids)} 个 id，state.seen 共 {len(state['seen'])} 条")


def cmd_test(cfg, dry_run):
    payload = {
        "topic": cfg["ntfy_topic"],
        "title": f"{cfg['name']} · 自检",
        "message": f"链路自检：{dt.datetime.now(CST):%Y-%m-%d %H:%M} CST",
        "click": cfg.get("profile_url"),
    }
    ok = publish(cfg, payload, dry_run)
    log("自检推送成功" if ok else "自检推送失败")
    return 0 if ok else 1


def run(cfg, dry_run=False, max_override=None):
    state = load_state()
    seen = set(str(x) for x in state["seen"])

    opener = new_opener(cfg)
    try:
        establish_session(opener, cfg)
    except Exception as e:
        log(f"建会话失败：{e}")
        return 1

    status_list = []
    for page in range(1, int(cfg["timeline_pages"]) + 1):
        try:
            status_list.extend(fetch_timeline(opener, cfg, page))
        except Exception as e:
            log(f"第 {page} 页抓取失败：{e}")
            if not status_list:
                return 1
            break
        time.sleep(0.6)

    fresh = [s for s in status_list if str(s["id"]) not in seen]
    fresh.sort(key=lambda s: int(s.get("created_at") or 0))  # 老 → 新

    # 首次运行：历史底库 + 本轮全部标为已见，只推最新 1 条做种子，避免刷屏
    seed_only = not state["seeded"]
    if seed_only:
        all_ids = [str(s["id"]) for s in status_list] + baseline_ids()
        seen.update(all_ids)
        fresh = fresh[-1:] if fresh else []
        log(f"首次运行：灌入 {len(all_ids)} 个历史 id，只推最新 1 条做种子")

    max_age = float(cfg["max_age_hours"]) * 3600 * 1000
    now_ms = time.time() * 1000
    cap = int(max_override if max_override is not None else cfg["max_per_run"])

    if not seed_only:
        fresh = [s for s in fresh if now_ms - int(s.get("created_at") or 0) <= max_age]

    if not fresh:
        log("没有新动态，本轮结束")
        if seed_only and not dry_run:
            state["seen"] = list(seen)
            state["seeded"] = True
            save_state(state)
        return 0

    if len(fresh) > cap:
        log(f"本轮新增 {len(fresh)} 条，按上限只推最新 {cap} 条，其余标记已见避免堆积")
        skipped = fresh[:-cap]
        seen.update(str(s["id"]) for s in skipped)
        fresh = fresh[-cap:]

    pushed, failed = [], []
    for s in fresh:
        item = build_item(opener, cfg, s)
        payload = format_message(cfg, item)
        if publish(cfg, payload, dry_run):
            pushed.append(str(s["id"]))
            seen.add(str(s["id"]))
            log(f"已推 {item['kind']} {item['id']} · {cst_line(item['ts'])}")
        else:
            failed.append(str(s["id"]))
            log(f"推送失败，下轮重试：{item['id']}")
        time.sleep(0.5)

    if dry_run:
        log(f"DRY RUN 结束：将推 {len(pushed)} 条，state 未改动")
        return 0

    state["seen"] = list(seen)
    state["seeded"] = True
    if pushed:
        state["last_status_id"] = pushed[-1]
    save_state(state)
    log(f"本轮完成：成功 {len(pushed)} 条，失败 {len(failed)} 条，seen 共 {len(state['seen'])}")
    return 0 if not failed else 1


def main():
    ap = argparse.ArgumentParser(description="段永平雪球动态 → ntfy 推送")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送、不写 state")
    ap.add_argument("--init", action="store_true", help="只灌历史底库到 state")
    ap.add_argument("--test", action="store_true", help="发一条链路自检消息")
    ap.add_argument("--max", type=int, default=None, help="覆盖单轮推送上限")
    args = ap.parse_args()

    cfg = load_config()
    if args.init:
        cmd_init()
        return 0
    if args.test:
        return cmd_test(cfg, args.dry_run)
    return run(cfg, dry_run=args.dry_run, max_override=args.max)


if __name__ == "__main__":
    sys.exit(main())
