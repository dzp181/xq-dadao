#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从项目归档生成可导入 ima 知识库的 Markdown（纯内容 · 按同步当天日期命名）。

产物：ima_export/YYYY-MM-DD.md —— 截至当天的 archive/items.jsonl 全量发言快照。

规则：
  - 文件里只有段永平本人的发言：时间、类型、标题、正文、回复对话链。
    不含《关于这份资料》之类的说明，也不含账号、订阅地址、采集方式、边界、
    原帖链接 / 来源 / 互动数等元信息。
  - 文件名用同步当天（北京时间）日期。跨天有新内容时新建一份，历史日期文件不再改动。
  - 同一天内多次有新内容时，同名 REPLACE 更新当天文件，不叠加条目。
  - 内容没有变化时 --sync-status 输出 action=none，不产生重复文件。

零第三方依赖。不联网、不推送、不改 state。
ima 只能通过 WorkBuddy 的 ima 连接器写入：CI 沉淀 archive/ → 本机跑本脚本 → WorkBuddy 上传。

用法：
  python3 export_ima.py                    # 生成当天日期的纯内容快照
  python3 export_ima.py --sync-status     # stdout 只输出一行 JSON，供定时同步判断
  python3 export_ima.py --commit <fp>    # 上传成功后记录指纹
  python3 export_ima.py --legacy-clean   # 额外生成《段永平雪球动态.md》纯内容版，
                                        #   仅用于一次性覆盖 ima 里带说明的旧条目
  python3 export_ima.py --history        # 按需重建静态历史索引（早已入库，通常不用）
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
LEGACY_MD = "段永平雪球动态.md"          # 只用于一次性覆盖旧条目，不再持续更新
HISTORY_MD = "段永平雪球发言历史索引.md"  # 静态索引，已入库，不再上传
SYNC_STATE = os.path.join(OUT_DIR, ".sync.json")
PENDING = os.path.join(OUT_DIR, ".pending.json")


def today():
    return dt.datetime.now(CST).strftime("%Y-%m-%d")


def today_name():
    return today() + ".md"


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
    """一条发言：只保留他写的内容 + 必要的时间/类型分隔。"""
    lines = [f"## {cst_line(it['ts'])} · {it.get('kind') or '动态'}", ""]
    if it.get("title"):
        lines += [f"**{it['title']}**", ""]
    text = (it.get("text") or "").strip()
    lines.append(text if text else "（无正文）")
    if it.get("context"):
        lines += ["", it["context"]]
    lines += ["", "---", ""]
    return "\n".join(lines)


def build_snapshot(items, day):
    """纯内容快照：标题只有日期与人名，正文全是他的发言。"""
    head = [f"# 段永平 · {day}", ""]
    if not items:
        head += ["（归档为空。先跑一次 `python3 xq_push.py` 或 `--import-raw` 冷启动。）", ""]
    return "\n".join(head) + "\n".join(one_item(it) for it in items)


def build_history():
    """静态历史索引（已入库，保留函数供按需重建；不再默认生成）。"""
    with open(HISTORY_JSON, encoding="utf-8") as f:
        d = json.load(f)
    records = d.get("records") or []
    by_year = {}
    for r in records:
        y = (r.get("display_time") or "未知")[:4]
        by_year.setdefault(y, []).append(r)

    parts = [f"# 段永平 · 雪球发言历史索引（截至 2026-10-21）", "",
             f"> 共 {len(records)} 条 ｜ 来源：{d.get('source') or ''}", "", "---", ""]
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
    return "\n".join(parts)


def write(name, text):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    os.replace(tmp, p)
    return p, os.path.getsize(p)


def fingerprint(items):
    """内容指纹（不含日期）：只有抓到新内容 / 正文被补全才变化。"""
    payload = json.dumps(
        [[it.get("id"), len(it.get("text") or ""), it.get("ts")] for it in items],
        ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="生成 ima 知识库纯内容快照（按日期命名）")
    ap.add_argument("--sync-status", action="store_true",
                    help="stdout 只输出一行 JSON：action=upload / none")
    ap.add_argument("--commit", default=None, metavar="FINGERPRINT",
                    help="上传成功后记录指纹")
    ap.add_argument("--legacy-clean", action="store_true",
                    help="额外生成旧文件名的纯内容版，用于一次性覆盖 ima 旧条目")
    ap.add_argument("--history", action="store_true",
                    help="额外重建静态历史索引（本地文件，不上传）")
    args = ap.parse_args()

    # 读配置只为确认文件存在，纯内容快照不引用其中任何字段
    with open(CONFIG_JSON, encoding="utf-8"):
        pass

    items = load_items()
    fp = fingerprint(items)
    name = today_name()
    day = today()

    if args.commit:
        pend = read_json(PENDING)
        if not pend or pend.get("fingerprint") != args.commit:
            print(f"[ima] 提交失败：找不到匹配的待同步指纹 {args.commit}", file=sys.stderr)
            return 1
        save_json(SYNC_STATE, {
            "fingerprint": args.commit,
            "file_name": pend.get("file_name"),
            "file_size": pend.get("file_size"),
            "count": pend.get("count"),
            "synced_at": dt.datetime.now(CST).isoformat(timespec="seconds"),
        })
        try:
            os.remove(PENDING)
        except OSError:
            pass
        print(f"[ima] 同步状态已记录 fp={args.commit} file={pend.get('file_name')} "
              f"size={pend.get('file_size')} count={pend.get('count')}")
        return 0

    if args.sync_status:
        prev = read_json(SYNC_STATE)
        # 内容没变就不上传（哪怕已经跨天），避免在 ima 里堆重复快照
        prev_name = prev.get("file_name") or ""
        prev_ok = (prev_name and os.path.exists(os.path.join(OUT_DIR, prev_name)))
        if prev.get("fingerprint") == fp and prev_ok:
            print(json.dumps({"action": "none", "count": len(items)}, ensure_ascii=False))
            return 0
        p, size = write(name, build_snapshot(items, day))
        save_json(PENDING, {"fingerprint": fp, "file_name": name,
                           "file_size": size, "count": len(items), "date": day})
        print(json.dumps({"action": "upload", "path": p, "file_name": name,
                          "file_size": size, "count": len(items),
                          "fingerprint": fp, "date": day}, ensure_ascii=False))
        return 0

    p, size = write(name, build_snapshot(items, day))
    print(f"[ima] {os.path.basename(p)}  {size:,} 字节  ({len(items)} 条发言，纯内容)")
    if args.legacy_clean:
        lp, lsize = write(LEGACY_MD, build_snapshot(items, day))
        print(f"[ima] {os.path.basename(lp)}  {lsize:,} 字节  (一次性覆盖旧条目用)")
    if args.history:
        hp, hsize = write(HISTORY_MD, build_history())
        print(f"[ima] {os.path.basename(hp)}  {hsize:,} 字节  (静态索引，不上传)")
    print(f"[ima] 输出目录：{OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
