"""把方案 Markdown 转成可打印的 HTML（A4），供浏览器导出 PDF。

没有 pandoc，就用 python-markdown + 一份固定样式，保证每次生成一致。
用法：PYTHONPATH=. .venv/bin/python tools/md2html.py docs/xxx.md outputs/xxx.html
"""
from __future__ import annotations

import sys
from pathlib import Path

import markdown

CSS = """
:root{--ink:#1a1f2b;--dim:#5b6478;--line:#dfe4ee;--cyan:#0e7490}
*{box-sizing:border-box}
body{margin:0;background:#eef1f6;font-family:"PingFang SC","Songti SC",-apple-system,sans-serif;
  color:var(--ink);font-size:13.2px;line-height:1.85}
.page{max-width:820px;margin:24px auto;background:#fff;padding:44px 52px 56px;
  box-shadow:0 8px 40px -18px rgba(20,30,60,.28);border-radius:6px}
h1{font-size:25px;line-height:1.45;margin:0 0 6px;letter-spacing:.5px}
h1+p{margin-top:0}
h2{font-size:17.5px;margin:30px 0 10px;padding-left:11px;border-left:3px solid var(--cyan);
  line-height:1.5}
h3{font-size:14.6px;margin:20px 0 8px;color:#22304a}
p{margin:8px 0}
strong{color:#0b1220}
blockquote{margin:12px 0;padding:9px 14px;background:#f4f7fb;border-left:3px solid #b9c6dd;
  color:#41506b;font-size:12.6px}
blockquote p{margin:3px 0}
table{width:100%;border-collapse:collapse;margin:12px 0 16px;font-size:12.2px}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f5f8fc;font-weight:600;color:#22304a}
tr:nth-child(even) td{background:#fbfcfe}
ul,ol{margin:8px 0 8px 20px;padding:0}
li{margin:4px 0}
code{background:#f1f4f9;padding:1px 5px;border-radius:4px;font-size:11.8px;
  font-family:"SF Mono",Menlo,monospace}
pre{background:#0f172a;color:#dbeafe;padding:14px 16px;border-radius:8px;overflow:auto;
  font-size:11.4px;line-height:1.6}
pre code{background:none;color:inherit;padding:0}
hr{border:0;border-top:1px solid var(--line);margin:22px 0}
a{color:var(--cyan);text-decoration:none}
@page{size:A4;margin:16mm 14mm}
@media print{
  body{background:#fff;font-size:10.6pt}
  .page{box-shadow:none;margin:0;padding:0;max-width:none;border-radius:0}
  h2{break-after:avoid}
  table,pre,blockquote{break-inside:avoid}
  tr{break-inside:avoid}
}
"""


def convert(src: Path, out: Path) -> Path:
    text = src.read_text(encoding="utf-8")
    body = markdown.markdown(
        text, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{src.stem}</title><style>{CSS}</style></head>
<body><div class="page">{body}</div></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    s, o = Path(sys.argv[1]), Path(sys.argv[2])
    print(convert(s, o))
