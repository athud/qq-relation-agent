#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 data/history/g<群号>.json 重新生成 HTML（自包含）。

主流程每轮已自动生成，本脚本用于手动重新生成（比如想改过滤参数）。

用法:
    python merge_reports.py 756770020
    python merge_reports.py 756770020 --min-conf 0.6
    python merge_reports.py 756770020 --days 7
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from agent import _clean_name


def load_history(gid, data_dir="data"):
    p = Path(data_dir) / "history" / f"g{gid}.json"
    if not p.exists():
        print(f"[错误] 找不到历史文件：{p}", file=sys.stderr)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[错误] 历史文件损坏：{e}", file=sys.stderr)
        return None


def filter_relations(hist, min_conf, days=None):
    now = time.time()
    cutoff = now - days * 86400 if days else 0

    out = []
    for r in hist.get("relations", {}).values():
        if r.get("confidence", 0) < min_conf:
            continue
        if days and r.get("last_seen", 0) < cutoff:
            continue
        out.append(r)

    out.sort(key=lambda x: (x["type"] != "explicit", -x.get("confidence", 0)))
    return out


def load_avatar_map(gid, data_dir="data"):
    db = Path(data_dir) / "agent.db"
    avatar_map = {}
    if not db.exists():
        return avatar_map
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    for row in conn.execute(
        "SELECT user_id, nickname, card FROM members WHERE group_id=?",
        (gid,),
    ):
        n = _clean_name(row["card"] or row["nickname"] or "")
        if n:
            avatar_map[n] = f"../avatars/{row['user_id']}.jpg"
    conn.close()
    return avatar_map


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gid", type=int, help="群号")
    ap.add_argument("--min-conf", type=float, default=0.0,
                    help="最低置信度（默认 0）")
    ap.add_argument("--days", type=int, default=None,
                    help="只保留最近 N 天活跃的关系")
    ap.add_argument("-o", "--output", help="输出 HTML 路径")
    args = ap.parse_args()

    data_dir = "data"

    hist = load_history(args.gid, data_dir)
    if hist is None:
        return 1

    all_rel_count = len(hist.get("relations", {}))
    relations = filter_relations(hist, args.min_conf, args.days)
    print(f"[信息] 累积关系 {all_rel_count} 条，过滤后 {len(relations)} 条")

    pc = Counter()
    for r in relations:
        pc[r["source"]] += 1
        pc[r["target"]] += 1

    result = {
        "persons": [{"name": n, "count": c} for n, c in pc.most_common()],
        "relations": relations,
    }

    avatar_map = load_avatar_map(args.gid, data_dir)
        # 构造 cross_map
    cross_map = {}
    try:
        import sqlite3
        db = sqlite3.connect(str(Path(data_dir) / "agent.db"))
        db.row_factory = sqlite3.Row
        local_uids = {}
        for row in db.execute(
            "SELECT user_id, nickname, card FROM members WHERE group_id=?",
            (args.gid,)
        ):
            nm = (row["card"] or row["nickname"] or "").strip()
            if nm:
                local_uids[row["user_id"]] = nm
        for uid, local_name in local_uids.items():
            rows = db.execute(
                "SELECT group_id, nickname, card FROM members WHERE user_id=?",
                (uid,)
            ).fetchall()
            groups = []
            for r in rows:
                other_name = (r["card"] or r["nickname"] or "").strip()
                if other_name:
                    groups.append({
                        "gid": r["group_id"],
                        "name": other_name,
                        "is_local": r["group_id"] == args.gid,
                    })
            groups.sort(key=lambda x: x["gid"])
            if len(groups) > 1:
                cross_map[local_name] = {
                    "user_id": uid,
                    "groups": groups,
                }
        db.close()
    except Exception as e:
        print(f"[警告] 构造 cross_map 失败：{e}")
    personas = hist.get("personas", {})

    import person_relation as pr
    html = pr.to_html(
        result,
        title=f"群 {args.gid} 关系图",
        avatar_map=avatar_map,
        personas=personas,
        cross_map=cross_map,
        embed=True,
        static_dir=str(Path(data_dir) / "static"),
    )

    out_path = args.output or f"data/reports/g{args.gid}.html"
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"[完成] 已输出: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())