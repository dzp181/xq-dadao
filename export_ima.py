#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从项目归档生成可导入 ima 知识库的 Markdown。

产物（固定文件名，方便后续用「同名替换」增量更新，不会在文件夹里越堆越多）：
  ima_export/段永平雪球动态.md           <- archive/items.jsonl 全量快照，每次覆盖
  ima_export/段永平雪球发言历史索引.md    <- data/history_index.json 静态索引，一次入库

零第三方依赖。不联网、不推送、不改 state。
注意：ima 只能通过 WorkBuddy 的 ima 连接器写入，GitHub Actions 里跑的 xq_push.py
无法直接调它。所以链路是「CI 持续写 archive/ → 本机跑本脚本 → 由 WorkBuddy 上传到 ima」。

用法：
  python3 export_ima.py                 # 生成两份快照
  python3 export_ima.py --sync-status  # stdout 只输出一行 JSON，供定时同步判断是否需要上传
  python3 export_ima.py --commit <fp>  # 上传成功后记录指纹，下次不再重复上传
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_JSONL = os.path.join(BASE_DIR, "archive", "items.jsonl")
HISTORY_JSON = os.path.join(BASE_DIR, "data", "history_index.json")
CONFIG_JSON = os.path.join(BASE_DIR, "config.json")
OUT_DIR = os.path.join(BASE_DIR, "ima_export")

CST = dt.timezone(dt.timedelta(hours=8))
LIVE_MD = "段永平雪球动态.md"
HISTORY_MD = "段永平雪球发言历史索引.md"
SYNC_STATE = os.path.join(OUT_DIR, ".sync.json")


def now_str():
    return dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")


def cst_line(ts_ms):
    return dt.datetime.fromtimestamp(int(ts_ms or 0) / 1000, CST).strftime("%Y-%m-%d %H:%M CST")


def load_items():
    items = []
    try:
        with open(ARCHIVE_JSONL, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    it = json.loads(ln)
                    if it.get("id"):
                        items.append(it)
                except (ValueError, json.JSONDecodeError):
                    continue
    except OSError:
        pass
    # 同一 id 只留一份，按时间倒序
    uniq = {str(it["id"]): it for it in items}
    return sorted(uniq.values(), key=lambda x: int(x.get("ts") or 0), reverse=True)


def one_item(it):
    head = f"## {cst_line(it['ts'])} · {it.get('kind') or '动态'}"
    lines = [head, ""]
    if it.get("title"):
        lines += [f"**{it['title']}**", ""]
    text = (it.get("text") or "").strip()
    lines.append(text if text else "（无正文）")
    if it.get("context"):
        lines += ["", it["context"]]
    meta = [f"原帖：{it.get('url') or ''}"]
    if it.get("source"):
        meta.append(f"来自 {it['source']}")
    if it.get("reply_count") or it.get("like_count"):
        meta.append(f"回复 {it.get('reply_count', 0)} · 赞 {it.get('like_count', 0)}")
    lines += ["", "> " + " · ".join(meta), ""]
    return "\n".join(lines)


def build_live(cfg, items):
    kinds = {}
    for it in items:
        k = it.get("kind") or "动态"
        kinds[k] = kinds.get(k, 0) + 1
    kind_line = " · ".join(f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda x: -x[1]))
    head = [
        "# 段永平（大道无形我有型）· 雪球动态全文",
        "",
        f"> 更新于 {now_str()} ｜ 共 {len(items)} 条 ｜ {kind_line or '暂无数据'}",
        "",
        "## 关于这份资料",
        "",
        f"- 账号：雪球 [{cfg.get('profile_name') or '大道无形我有型'}]({cfg.get('profile_url')})，UID `{cfg.get('uid')}`",
        f"- 实时订阅：ntfy 主题 `{cfg.get('ntfy_topic')}` → `{cfg.get('subscribe_url') or cfg.get('ntfy_url', '') + '/' + cfg.get('ntfy_topic', '')}`",
        "- 采集：`xq-dadao` 项目每 30 分钟抓一次用户时间线（`type=10`，含主帖 / 长文 / 转发 / 回复），",
        "  回复保留完整对话链（`回复@A: … //@B: …`），必要时补 `/statuses/show.json` 拿长文全文。",
        "- 归档源：`archive/items.jsonl`（机器可读全量）+ `archive/by-month/*.md`（按月人可读）。",
        "- 边界：只覆盖同步进他本人时间线的动态，**不含**散落在别人帖子评论区、未同步到时间线的回复；",
        "  付费 / 仅好友可见 / 已删除内容拿不到。时间均为北京时间（CST）。",
        "- 本文件由 `export_ima.py` 生成，同名覆盖即可增量更新，不必逐条追加。",
        "",
        "---",
        "",
    ]
    if not items:
        head += ["（归档为空。先跑一次 `python3 xq_push.py` 或 `--import-raw` 冷启动。）", ""]
    return "\n".join(head) + "\n".join(one_item(it) for it in items)


def build_history():
    with open(HISTORY_JSON, encoding="utf-8") as f:
        d = json.load(f)
    records = d.get("records") or []
    by_year = {}
    for r in records:
        y = (r.get("display_time") or "未知")[:4]
        by_year.setdefault(y, []).append(r)

    head = [
        "# 段永平 · 雪球发言历史索引（截至 2026-10-21）",
        "",
        f"> 共 {len(records)} 条 ｜ 来源：{d.get('source') or ''}",
        "",
        f"- 原始项目：{d.get('source_url') or ''}",
        f"- 说明：{d.get('note') or ''}",
        "- 用途：作为冷启动底库与检索线索；每条给出发帖时间、分类、摘要与原帖链接，",
        "  点开链接可读全文。分类里的「误命中 / 关联候选」是原归档的检索噪声，不是本人观点。",
        "- 与实时动态的关系：本索引用于给 `baseline_ids.txt` 提供历史去重 id，",
        "  实时全文请看另一份《段永平雪球动态》。",
        "",
        "---",
        "",
    ]
    parts = []
    for y in sorted(by_year.keys(), reverse=True):
        group = sorted(by_year[y], key=lambda r: r.get("display_time") or "", reverse=True)
        parts.append(f"## {y} 年（{len(group)} 条）\n")
        for r in group:
            t = r.get("display_time") or "时间未知"
            cat = r.get("category") or "未分类"
            summary = (r.get("summary") or "").strip().replace("\n", " ")
            url = r.get("url") or ""
            sid = r.get("status_id") or ""
            parts.append(f"- **{t}** · {cat} · {summary} · [原帖]({url}) · `{sid}`")
        parts.append("")
    return "\n".join(head + parts)


def write(name, text):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    os.replace(tmp, p)
    return p, os.path.getsize(p)


def fingerprint(items):
    """用 id + 正文长度 + 时间戳做指纹：只有抓到新内容（或正文被补全）才变化。"""
    payload = json.dumps(
        [[it.get("id"), len(it.get("text") or ""), it.get("ts")] for it in items],
        ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def read_sync():
    try:
        with open(SYNC_STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def main():
    ap = argparse.ArgumentParser(description="生成 ima 知识库快照")
    ap.add_argument("--sync-status", action="store_true",
                    help="stdout 只输出一行 JSON：action=upload / none")
    ap.add_argument("--commit", default=None, metavar="FINGERPRINT",
                    help="上传成功后写入指纹")
    args = ap.parse_args()

    with open(CONFIG_JSON, encoding="utf-8") as f:
        cfg = json.load(f)
    items = load_items()
    fp = fingerprint(items)

    if args.commit:
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, LIVE_MD)
        size = os.path.getsize(p) if os.path.exists(p) else 0
        tmp = SYNC_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": args.commit, "file_size": size,
                       "count": len(items),
                       "synced_at": dt.datetime.now(CST).isoformat(timespec="seconds")},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, SYNC_STATE)
        print(f"[ima] 同步状态已记录 fp={args.commit} size={size} count={len(items)}")
        return 0

    hp = os.path.join(OUT_DIR, HISTORY_MD)
    lp = os.path.join(OUT_DIR, LIVE_MD)

    if args.sync_status:
        prev = read_sync()
        # 没变化时连文件都不重写，避免无谓地改时间戳
        if (prev.get("fingerprint") == fp and os.path.exists(lp)
                and os.path.getsize(lp) == prev.get("file_size")):
            print(json.dumps({"action": "none", "count": len(items)}, ensure_ascii=False))
            return 0
        p1, n1 = write(LIVE_MD, build_live(cfg, items))
        if not os.path.exists(hp):
            write(HISTORY_MD, build_history())
        print(json.dumps({"action": "upload", "path": p1, "file_name": LIVE_MD,
                          "file_size": n1, "count": len(items), "fingerprint": fp},
                         ensure_ascii=False))
        return 0

    p1, n1 = write(LIVE_MD, build_live(cfg, items))
    # 历史索引是静态的，已生成过就不重复写
    if not os.path.exists(hp):
        _, n2 = write(HISTORY_MD, build_history())
    else:
        n2 = os.path.getsize(hp)
    print(f"[ima] {os.path.basename(p1)}  {n1:,} 字节  ({len(items)} 条动态)")
    print(f"[ima] {os.path.basename(hp)}  {n2:,} 字节")
    print(f"[ima] 输出目录：{OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
