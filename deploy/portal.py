"""静态论文选择主页与发布历史，无浏览器端服务依赖。"""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

STYLE = '''
:root{--paper:#f4f0e7;--ink:#222f2b;--muted:#63716a;--line:#d5d9cc;--accent:#205c49}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:"PingFang SC","Noto Sans CJK SC",sans-serif}
a{color:inherit}main{max-width:1120px;margin:auto;padding:52px 36px 64px}.eyebrow{font:12px "Courier New",monospace;letter-spacing:.18em;color:var(--accent)}
header{border-bottom:1px solid var(--ink);padding:28px 0 36px;display:flex;justify-content:space-between;gap:24px;align-items:end}
h1{font-family:"Songti SC","Noto Serif CJK SC",serif;font-size:clamp(32px,5vw,56px);font-weight:500;margin:0 0 14px;letter-spacing:-.04em}p{line-height:1.8;margin:0;color:var(--muted)}
.badge{font-size:12px;white-space:nowrap;border:1px solid var(--line);border-radius:20px;padding:7px 12px;color:var(--accent)}
.paper{display:grid;grid-template-columns:64px 1fr auto;gap:28px;padding:36px 0;border-bottom:1px solid var(--line);align-items:start}
.number{font:24px Georgia,serif;color:#93a297}.paper h2{font:500 26px Georgia,"Songti SC",serif;margin:0 0 10px}.meta{font-size:13px}.primary{background:var(--accent);color:white;padding:12px 20px;text-decoration:none;border-radius:3px;font-size:14px;display:inline-block;transition:background .15s}.primary:hover{background:#153d31}
a:focus-visible,summary:focus-visible{outline:3px solid #bd7d35;outline-offset:5px}.empty{font-size:13px;padding-top:6px;color:var(--muted)}details{margin-top:20px}summary{cursor:pointer;font-size:13px;color:var(--accent);width:fit-content}.history{list-style:none;padding:0;margin:16px 0 0}.history li{padding:12px 0;border-top:1px solid var(--line);display:flex;gap:20px;justify-content:space-between;font-size:13px}.history span{color:var(--muted);font-family:"Courier New",monospace;font-size:12px}
footer{display:flex;justify-content:space-between;gap:20px;font-size:12px;color:var(--muted);padding-top:30px}.back{display:inline-block;margin-bottom:32px;font-size:13px;text-underline-offset:5px}
footer button{border:0;background:none;padding:0;color:var(--accent);font:inherit;cursor:pointer;text-decoration:underline;text-underline-offset:4px}
@media(max-width:650px){main{padding:28px 20px}header{display:block}.badge{display:inline-block;margin-top:16px}.paper{grid-template-columns:32px 1fr;gap:16px}.action{grid-column:2}.history li{display:block}.history span{display:block;margin-top:7px}footer{display:block}footer span{display:block;margin-bottom:8px}}
'''


def _date(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


def render_portal(papers, catalog, selected=None):
    rows = []
    for number, (slug, title) in enumerate(papers.items(), 1):
        if selected and slug != selected:
            continue
        history = catalog.get(slug, [])
        if history:
            latest = history[0]
            meta = f'{escape(_date(latest["published_at"]))} · {escape(latest["comparison"])}'
            action = f'<a class="primary" href="/{slug}/latest/report.html">查看最新报告 ↗</a>'
            entries = ''.join(f'<li><a href="/{slug}/releases/{item["release"]}/report.html">{escape(_date(item["published_at"]))}</a><span>{escape(item["comparison"])}</span></li>' for item in history)
            details = f'<details><summary>发布历史 · {len(history)} 个版本</summary><ol class="history">{entries}</ol></details>'
        else:
            meta, action, details = '尚未发布报告', '<span class="empty">等待首次发布</span>', ''
        rows.append(f'<article class="paper"><div class="number">{number:02d}</div><div><h2>{escape(title)}</h2><p class="meta">{meta}</p>{details}</div><div class="action">{action}</div></article>')
    title = papers[selected] if selected else '论文审阅档案'
    intro = '查看最新审阅报告，或回看此前的修改记录。'
    back = '<a class="back" href="/">← 返回论文列表</a>' if selected else ''
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{escape(title)} · LaTeX Review</title><style>{STYLE}</style></head><body><main>{back}<div class="eyebrow">LATEX REVIEW / RESEARCH ARCHIVE</div><header><div><h1>{escape(title)}</h1><p>{intro}</p></div><span class="badge">私有审阅空间</span></header><section aria-label="论文报告">{''.join(rows)}</section><footer><span>LaTeX Review</span><span>每篇保留最近 10 次发布 · 时间：北京时间</span><form action="/logout" method="post"><button type="submit">退出登录</button></form></footer></main></body></html>'''
