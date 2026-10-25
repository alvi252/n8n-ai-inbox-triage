#!/usr/bin/env python3
"""Turn the send_samples.py results (results-live.json) into a styled HTML table.

  python3 scripts/results_table.py results-live.json --out results-live.html
  python3 scripts/screenshot.py --html-file results-live.html --out assets/screenshots/n8n-3-results.png

Standard library only. The "Lookup" column shows whether the agent called the order lookup
tool; it reads each execution through the n8n API (key from N8N_API_KEY or .n8n-local.json).
Without a key the column is left empty. Email addresses are not shown.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STYLE = """
body { margin:0; background:#f5f6f8; font:13px/1.4 "Inter","DejaVu Sans",Arial,sans-serif; color:#1f2933; }
.wrap { padding:22px 26px; }
h1 { font-size:19px; margin:0 0 4px; }
.meta { color:#52606d; margin-bottom:14px; }
table { border-collapse:collapse; width:100%; background:#fff; border:1px solid #d9dee4; }
th { text-align:left; font-weight:600; background:#eef1f4; padding:7px 8px;
     border-bottom:1px solid #d9dee4; white-space:nowrap; }
td { padding:7px 8px; border-bottom:1px solid #edf0f3; vertical-align:top; }
td.id { font-weight:600; white-space:nowrap; }
.sub { font-weight:400; color:#616e7c; font-size:12px; white-space:normal; max-width:190px; }
td.c { text-align:center; } td.txt { max-width:380px; }
td.miss { background:#fff4e5; } .exp { color:#9a5b00; font-size:12px; }
.u { padding:1px 7px; border-radius:9px; font-size:12px; }
.u-high { background:#fde2e1; color:#a61b1b; } .u-normal { background:#e6effa; color:#1c4f8c; }
.u-low { background:#ecefe9; color:#4b5a3c; }
"""


def api_key() -> str | None:
    if os.environ.get("N8N_API_KEY"):
        return os.environ["N8N_API_KEY"]
    try:
        return json.loads((ROOT / ".n8n-local.json").read_text())["apiKey"]
    except (OSError, ValueError, KeyError):
        return None


def lookup_calls(base: str, key: str | None, exec_id: str) -> int | None:
    if not key:
        return None
    req = urllib.request.Request(f"{base}/api/v1/executions/{exec_id}?includeData=true", headers={"X-N8N-API-KEY": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except OSError:
        return None
    return len(data["data"]["resultData"]["runData"].get("Order lookup tool", []))


def excerpt(text: str, limit: int = 150) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def cell(value: str, ok: bool, expected: str) -> str:
    if ok:
        return f"<td>{html.escape(value)}</td>"
    return f'<td class="miss">{html.escape(value)}<div class="exp">expected: {html.escape(expected)}</div></td>'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=Path, default=ROOT / "samples" / "inquiries.json")
    parser.add_argument("--url", default=os.environ.get("N8N_URL", "http://localhost:5678"))
    parser.add_argument("--title", default="PawPantry inbox triage - live run")
    parser.add_argument("--meta", default="", help="text shown before the computed totals")
    args = parser.parse_args()

    results = json.loads(args.results.read_text(encoding="utf-8"))
    samples = json.loads(args.samples.read_text(encoding="utf-8"))
    subjects = {s["id"]: s.get("subject") or "(no subject)" for s in samples}
    key = api_key()
    rows, n, cat_ok, route_ok, with_order, looked_up = [], 0, 0, 0, 0, 0
    for r in results:
        body = r["response"]
        if not (isinstance(body, dict) and body.get("ok")):
            continue
        n += 1
        exp, tri, ext = r.get("expected") or {}, body["triage"], body["extracted"]
        c_ok = tri.get("category") == exp.get("category")
        r_ok = body.get("route") == exp.get("route")
        cat_ok += c_ok
        route_ok += r_ok
        calls = lookup_calls(args.url.rstrip("/"), key, body["execution_id"])
        if ext.get("order_number"):
            with_order += 1
            looked_up += 1 if calls else 0
        lookup = "" if calls is None else ("yes" if calls else "-")
        reply = excerpt(body.get("reply_draft") or "")
        urgency = tri.get("urgency") or ""
        rows.append(
            f"<tr><td class=id>{html.escape(r['id'])}<div class=sub>{html.escape(subjects.get(r['id'], ''))}</div></td>"
            f"<td>{html.escape(tri.get('language') or '')}</td>"
            + cell(tri.get("category") or "", c_ok, exp.get("category", ""))
            + f"<td><span class='u u-{html.escape(urgency)}'>{html.escape(urgency)}</span></td>"
            + cell(body.get("route") or "", r_ok, exp.get("route", ""))
            + f"<td class=c>{lookup}</td>"
            f"<td class=txt>{html.escape(tri.get('summary') or '')}</td>"
            f"<td class=txt>{html.escape(reply) if reply else '<em>none, spam is discarded</em>'}</td></tr>"
        )
    totals = f"Category matched {cat_ok} of {n}, route matched {route_ok} of {n}."
    if key:
        totals += (
            f" The order lookup tool was used for {looked_up} of {with_order} messages that contain an order number."
        )
    page = (
        f"<!doctype html><html><head><meta charset=utf-8><style>{STYLE}</style></head><body><div class=wrap>"
        f"<h1>{html.escape(args.title)}</h1><div class=meta>{html.escape(args.meta)} {totals}</div>"
        "<table><tr><th>Sample</th><th>Lang</th><th>Category</th><th>Urgency</th><th>Route</th><th>Lookup</th>"
        "<th>Summary (model)</th><th>Reply draft (start)</th></tr>"
        f"{''.join(rows)}</table></div></body></html>"
    )
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out}: {n} rows. {totals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
