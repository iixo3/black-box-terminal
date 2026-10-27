"""Local review artifacts: every decision, source coverage, and rule snapshot."""
from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path

from core import safe_url


def write_report(data: dict, config: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = directory / data["run_id"]
    stem.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    stem.with_suffix(".rules.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))
    stem.with_suffix(".messages.jsonl").write_text("".join(json.dumps(message, ensure_ascii=False) + "\n"
                                                           for message in data.get("messages", [])))
    with stem.with_suffix(".csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["status", "date", "source", "text", "reason", "matched_terms", "duplicate_of", "source_url", "media_present"])
        for decision in data["decisions"]:
            item = decision["item"]
            values = [decision["status"], item["date"], item["source"], item["text"], decision["reason"],
                      json.dumps(decision["matches"], ensure_ascii=False), decision["duplicate_of"] or "",
                      f"https://t.me/{item['source']}/{item['message_id']}", str(item["media_present"])]
            # Treat source text as data even when a CSV is opened in a spreadsheet.
            writer.writerow(["'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
    esc = html.escape
    counts = Counter(decision["status"] for decision in data["decisions"])
    coverage_rows = []
    for source, info in data["coverage"].items():
        coverage_rows.append(f"<tr><td>{esc(source)}</td><td>{esc(info['status'])}</td>"
                             f"<td>{info.get('messages', 0)}</td><td>{esc(info.get('note', ''))}</td></tr>")
    rows = []
    for decision in data["decisions"]:
        item = decision["item"]
        key = f"{item['source'].lower()}:{item['message_id']}:{item['index']}"
        source_url = f"https://t.me/{item['source']}/{item['message_id']}"
        matched = "<br>".join(f"<strong>{esc(name)}</strong>: {esc(', '.join(terms))}"
                             for name, terms in decision["matches"].items())
        duplicate = decision["duplicate_of"]
        duplicate_link = f"<br>Same story: <a href='#{esc(duplicate, quote=True)}'>{esc(duplicate)}</a>" if duplicate else ""
        links = " ".join(f"<a href='{esc(url, quote=True)}' target='_blank' rel='noreferrer'>Link {i + 1}</a>"
                         for i, url in enumerate(item["links"]) if safe_url(url))
        flags = " · Includes media (text/caption only)" if item["media_present"] else ""
        rows.append(f"""<article id='{esc(key, quote=True)}' data-status='{esc(decision['status'], quote=True)}'>
          <div class='meta'><span class='tag {esc(decision['status'])}'>{esc(decision['status'].upper())}</span>
          {esc(item['date'])} · <a href='{esc(source_url, quote=True)}' target='_blank' rel='noreferrer'>@{esc(item['source'])}</a>{flags}</div>
          <small>{esc(item['context'])}</small><pre>{esc(item['text']) or '[No text or caption]'}</pre>
          <div>{links}</div><p class='reason'>{esc(decision['reason'])}{duplicate_link}</p><div class='matches'>{matched}</div>
          </article>""")
    metadata = esc(json.dumps(data["metadata"], ensure_ascii=False))
    content = """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Crypto news watcher — review</title><style>
    body{margin:0 auto;padding:32px 20px;max-width:1080px;background:#f7f8fa;color:#172133;font:16px/1.5 system-ui}
    h1{font-size:28px;margin:0}h2{font-size:19px}a{color:#195dcc}small,.meta{color:#526174}
    .intro,article{background:white;border:1px solid #dae0e8;border-radius:10px;padding:18px;margin:16px 0}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:14px 0}.reason{font-weight:600;margin-bottom:6px}
    .matches{font-size:13px;color:#526174}.tag{display:inline-block;font-size:12px;font-weight:700;padding:3px 8px;border-radius:5px;margin-right:8px}
    .keep{background:#d9f3e3;color:#165c31}.drop{background:#eef0f3;color:#4a5363}.duplicate{background:#e4e9ff;color:#364891}.hold{background:#fff0ca;color:#745a10}
    table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid #dae0e8}
    .controls{display:flex;gap:12px;position:sticky;top:0;padding:12px 0;background:#f7f8fa;z-index:1;flex-wrap:wrap}
    input,select{font:inherit;padding:9px;border:1px solid #bfc9d7;border-radius:5px}input{flex:1;min-width:160px}
    .notice{font-size:14px;color:#526174;overflow-wrap:anywhere}.hidden{display:none}
    </style><h1>Crypto news watcher · review</h1>
    """
    content += f"<p>{esc(data['run_id'])} · Rules {esc(data['rules_version'])} · {esc(data['mode'])}</p>"
    content += "<div class='intro'>"
    content += f"<strong>{len(data['decisions'])} items</strong> · " + " · ".join(f"{counts[s]} {s}" for s in ["keep", "drop", "duplicate", "hold"])
    album_parts = sum(message.get("grouped_id") is not None and not message.get("text", "").strip()
                      for message in data.get("messages", []))
    if album_parts:
        content += f"<p>{album_parts} captionless album attachments remain in the raw archive; their captioned messages are assessed normally.</p>"
    content += "<p>Rules assess relevance, not factual truth. Rejected headlines and standalone media-only posts remain visible. Captionless album parts are not separate news items. Match terms explain each decision.</p>"
    content += "<p class='notice'>Coverage reflects readable channel history. Deleted posts cannot be recovered; image/video content is not extracted. An empty or inaccessible source is explicitly shown below. Roundup dates are publication dates, not dates of their underlying stories.</p>"
    if data["mode"] == "fixture":
        content += "<p><strong>SYNTHETIC DEMO ONLY — these fictional examples are not Telegram news or a September coverage test.</strong></p>"
    content += f"<p class='notice'>{metadata}</p><table><tr><th>Source</th><th>Coverage</th><th>Messages</th><th>Notes</th></tr>{''.join(coverage_rows)}</table></div>"
    content += "<div class='controls'><select id='status' aria-label='Decision status'><option value='all'>All decisions</option><option>keep</option><option>drop</option><option>duplicate</option><option>hold</option></select><input id='search' placeholder='Search news, source, rule, or matched terms' aria-label='Search'><span id='count'></span></div>"
    content += "".join(rows)
    content += """<script>
    const status = document.getElementById('status'), search = document.getElementById('search');
    const items = [...document.querySelectorAll('article')];
    function filter(){let count=0;const query=search.value.toLowerCase();for(const item of items){const visible=(status.value==='all'||item.dataset.status===status.value)&&item.textContent.toLowerCase().includes(query);item.classList.toggle('hidden',!visible);if(visible)count++;}document.getElementById('count').textContent=count+' shown';}
    status.addEventListener('change',filter);search.addEventListener('input',filter);filter();
    </script></html>"""
    output = stem.with_suffix(".html")
    output.write_text(content)
    return output
