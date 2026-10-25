#!/usr/bin/env python3
"""Screenshots for the README, taken with Playwright against a local n8n.

  pip install playwright && playwright install chromium
  export N8N_OWNER_EMAIL=... N8N_OWNER_PASSWORD=...

  # workflow canvas (zoomed to fit)
  python3 scripts/screenshot.py --workflow-id <id> --out assets/screenshots/n8n-1-workflow.png

  # one execution of the workflow
  python3 scripts/screenshot.py --workflow-id <id> --execution-id <exec> --out assets/screenshots/n8n-2-execution.png

  # any other editor page, e.g. a Data Table
  python3 scripts/screenshot.py --path /projects/<projectId>/datatables/<tableId> \
      --out assets/screenshots/n8n-5-data-table.png

  # a text file (e.g. the send_samples.py results table) rendered as an image, no login needed
  python3 scripts/screenshot.py --text-file results-live.txt --out assets/screenshots/n8n-5-results.png

Set PLAYWRIGHT_CHROMIUM_EXECUTABLE to use a specific Chromium build.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

TEXT_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin: 0; background: #f6f7f9; }}
pre {{ margin: 24px; padding: 20px 24px; background: #fff; border: 1px solid #dfe3e8; border-radius: 8px;
       font: 13px/1.45 "DejaVu Sans Mono", "Menlo", "Consolas", monospace; color: #1f2933; display: inline-block; }}
</style></head><body><pre>{text}</pre></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--workflow-id")
    target.add_argument("--path", help="editor path to open, e.g. /home/workflows")
    target.add_argument("--text-file", type=Path, help="render this text file instead of an n8n page")
    parser.add_argument("--execution-id", help="with --workflow-id: open this execution")
    parser.add_argument("--url", default=os.environ.get("N8N_URL", "http://localhost:5678"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--scale", type=float, default=2, help="device scale factor (2 = crisp text)")
    parser.add_argument("--wait-ms", type=int, default=2500, help="settle time before the screenshot")
    args = parser.parse_args()

    launch = {"headless": True}
    if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
        launch["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.scale,
            color_scheme="light",
        )
        page = context.new_page()

        if args.text_file:
            text = args.text_file.read_text(encoding="utf-8")
            page.set_content(TEXT_PAGE.format(text=html.escape(text)))
            page.locator("pre").screenshot(path=str(args.out))
            browser.close()
            print(f"saved {args.out}")
            return 0

        email, password = os.environ.get("N8N_OWNER_EMAIL"), os.environ.get("N8N_OWNER_PASSWORD")
        if not email or not password:
            print("set N8N_OWNER_EMAIL and N8N_OWNER_PASSWORD", file=sys.stderr)
            return 1
        base = args.url.rstrip("/")
        resp = context.request.post(f"{base}/rest/login", data={"emailOrLdapLoginId": email, "password": password})
        if not resp.ok:
            print(f"login failed: HTTP {resp.status}", file=sys.stderr)
            return 1

        if args.workflow_id:
            path = f"/workflow/{args.workflow_id}"
            if args.execution_id:
                path += f"/executions/{args.execution_id}"
        else:
            path = args.path if args.path.startswith("/") else f"/{args.path}"
        page.goto(base + path, wait_until="networkidle")

        if args.workflow_id:
            page.wait_for_selector(".vue-flow__node", timeout=30000)
            page.keyboard.press("Escape")
            if not args.execution_id:
                # zoom to fit: n8n shortcut "1" on the focused canvas
                page.mouse.click(args.width - 40, args.height // 2)
                page.keyboard.press("1")
        page.wait_for_timeout(args.wait_ms)
        page.screenshot(path=str(args.out))
        browser.close()
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
