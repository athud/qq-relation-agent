#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_multi.py —— 生成"多群切换"总览页

用法:
    python build_multi.py --all
    python build_multi.py 756770020 123456789
    python build_multi.py --all -o data/reports/all.html

会做两件事:
1. 为每个群生成/更新 data/reports/g<群号>.html
2. 生成总览页 data/reports/all.html —— 顶部 tab 切换各群
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def find_groups_with_history(data_dir="data"):
    d = Path(data_dir) / "history"
    if not d.exists():
        return []
    gids = []
    for f in d.glob("g*.json"):
        try:
            gids.append(int(f.stem[1:]))
        except ValueError:
            pass
    return sorted(gids)


def build_index(gids, names=None, data_dir="data"):
    names = names or {}

    tabs = []
    frames = []
    for i, gid in enumerate(gids):
        active = " active" if i == 0 else ""
        label = names.get(gid, f"群 {gid}")
        tabs.append(
            f'<button class="tab{active}" data-gid="{gid}">{label}</button>'
        )
        frames.append(
            f'<iframe class="frame{active}" data-gid="{gid}" '
            f'src="g{gid}.html" loading="lazy"></iframe>'
        )

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>多群关系图</title>
<style>
  html,body{{margin:0;height:100%;overflow:hidden;
    font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    background:#f5f6fa;}}
  #tabs{{display:flex;background:#fff;border-bottom:1px solid #eee;
    padding:0 12px;height:48px;align-items:center;gap:4px;
    box-shadow:0 1px 4px rgba(0,0,0,.04);
    position:relative;z-index:10;overflow-x:auto;}}
  .tab{{padding:8px 18px;border:none;background:none;cursor:pointer;
    font-size:14px;color:#666;border-radius:8px;white-space:nowrap;
    transition:all .15s;font-family:inherit;}}
  .tab:hover{{color:#333;background:#f5f6fa;}}
  .tab.active{{color:#3b4cca;background:#eef2ff;font-weight:600;}}
  #frames{{position:absolute;top:48px;left:0;right:0;bottom:0;}}
  .frame{{width:100%;height:100%;border:none;display:none;}}
  .frame.active{{display:block;}}
</style>
</head>
<body>
<div id="tabs">{''.join(tabs)}</div>
<div id="frames">{''.join(frames)}</div>
<script>
document.querySelectorAll('.tab').forEach(function(t){{
  t.addEventListener('click', function(){{
    var gid = this.dataset.gid;
    document.querySelectorAll('.tab').forEach(function(x){{
      x.classList.toggle('active', x.dataset.gid === gid);
    }});
    document.querySelectorAll('.frame').forEach(function(x){{
      x.classList.toggle('active', x.dataset.gid === gid);
    }});
  }});
}});
</script>
</body>
</html>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gids", nargs="*", type=int, help="群号列表")
    ap.add_argument("--all", action="store_true",
                    help="自动扫描 data/history 里的所有群")
    ap.add_argument("--names", default="",
                    help="自定义群名，格式：756770020:摸鱼群,123456789:工作群")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("-o", "--output", help="输出 HTML 路径")
    args = ap.parse_args()

    gids = args.gids
    if args.all or not gids:
        gids = find_groups_with_history(args.data_dir)
    if not gids:
        print("[错误] 没有找到任何群历史文件")
        return 1

    # 解析自定义名称
    names = {}
    if args.names:
        for part in args.names.split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                try:
                    names[int(k.strip())] = v.strip()
                except ValueError:
                    pass

    print(f"[信息] 共 {len(gids)} 个群：{gids}")

    # 为每个群生成单群 HTML
    for gid in gids:
        print(f"[1/2] 生成群 {gid} 的 HTML ...")
        r = subprocess.run(
            [sys.executable, "merge_reports.py", str(gid)],
            cwd=str(Path(__file__).parent),
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  [警告] 群 {gid} 生成失败：{r.stderr.strip()}")
        else:
            print(f"  ✓")

    # 生成总览页
    print(f"[2/2] 生成总览页 ...")
    html = build_index(gids, names, args.data_dir)
    out = args.output or str(Path(args.data_dir) / "reports" / "all.html")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html, encoding="utf-8")
    print(f"[完成] 已输出：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())