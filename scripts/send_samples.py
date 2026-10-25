#!/usr/bin/env python3
"""Post the sample customer messages to the triage webhook and print a results table.

Standard library only.

  python3 scripts/send_samples.py                       # samples/inquiries.json
  python3 scripts/send_samples.py --edge-cases          # + samples/edge_cases.json
  python3 scripts/send_samples.py --show-replies        # also print the drafted replies
  python3 scripts/send_samples.py --json-out results.json

Each sample carries the expected category and route, so the table doubles as a quick
accuracy check for the live AI run. Edge cases marked "mock_only" are skipped automatically
when the workflow answers in live mode.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post(url: str, payload: dict, timeout: float) -> tuple[int, dict | str, float]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, f"request failed: {getattr(exc, 'reason', exc)}", time.perf_counter() - started
    elapsed = time.perf_counter() - started
    try:
        return status, json.loads(raw), elapsed
    except ValueError:
        return status, raw.decode(errors="replace"), elapsed


def cut(value, width: int) -> str:
    text = "-" if value in (None, "") else str(value)
    return text if len(text) <= width else text[: width - 1] + "~"


def mark(got, expected) -> str:
    if expected is None:
        return " "
    return "=" if got == expected else "x"


COLUMNS = [
    ("id", 22),
    ("http", 4),
    ("lang", 4),
    ("category", 19),
    ("urg", 6),
    ("route", 14),
    ("order", 9),
    ("conf", 4),
    ("sec", 5),
]


def header() -> str:
    line = "  ".join(name.ljust(width) for name, width in COLUMNS)
    return f"{line}\n{'-' * len(line)}"


def row(sample: dict, status: int, body, elapsed: float) -> str:
    exp = sample.get("expected", {})
    if isinstance(body, dict) and body.get("ok"):
        tri, ext = body.get("triage", {}), body.get("extracted", {})
        cells = [
            sample["id"],
            status,
            tri.get("language"),
            f"{mark(tri.get('category'), exp.get('category'))} {cut(tri.get('category'), 17)}",
            tri.get("urgency"),
            f"{mark(body.get('route'), exp.get('route'))} {cut(body.get('route'), 12)}",
            ext.get("order_number"),
            tri.get("confidence"),
            f"{elapsed:.1f}",
        ]
    else:
        message = (body.get("error") or body.get("message")) if isinstance(body, dict) else body
        status_cell = f"{status}{mark(status, exp.get('http_status')).strip()}"
        return f"{cut(sample['id'], 22).ljust(22)}  {status_cell.ljust(4)}  {cut(message, 70)}  ({elapsed:.1f} s)"
    return "  ".join(cut(c, w).ljust(w) for c, (_n, w) in zip(cells, COLUMNS, strict=True))


def run(url: str, samples: list[dict], timeout: float, show_replies: bool, results: list, mode_hint: str | None):
    for sample in samples:
        if sample.get("mock_only") and mode_hint != "mock":
            mode = mode_hint or "live or unknown"
            print(f"{cut(sample['id'], 22).ljust(22)}  skipped (mock-only test; workflow mode is {mode})")
            continue
        payload = {k: sample.get(k, "") for k in ("from", "subject", "message", "channel")}
        status, body, elapsed = post(url, payload, timeout)
        print(row(sample, status, body, elapsed), flush=True)
        if show_replies and isinstance(body, dict) and body.get("ok"):
            summary = body.get("triage", {}).get("summary")
            print(f"    summary: {summary}")
            if body.get("validation_errors"):
                print(f"    review:  {'; '.join(body['validation_errors'])}")
            empty = "(none - spam is discarded)" if body.get("route") == "spam" else "(none)"
            reply = body.get("reply_draft") or empty
            print("    reply:   " + reply.replace("\n", "\n             "))
        results.append(
            {
                "id": sample["id"],
                "expected": sample.get("expected"),
                "http_status": status,
                "seconds": round(elapsed, 2),
                "response": body,
            }
        )
        if isinstance(body, dict) and body.get("mode"):
            mode_hint = body["mode"]
    return mode_hint


def summarize(results: list) -> int:
    ok = [r for r in results if isinstance(r["response"], dict) and r["response"].get("ok")]
    expected_status = [r for r in results if (r["expected"] or {}).get("http_status") is not None]
    unexpected_http = [r for r in results if r["http_status"] != (r["expected"] or {}).get("http_status", 200)]
    cat = [r for r in ok if (r["expected"] or {}).get("category")]
    cat_hits = sum(1 for r in cat if r["response"]["triage"]["category"] == r["expected"]["category"])
    rte = [r for r in ok if (r["expected"] or {}).get("route")]
    rte_hits = sum(1 for r in rte if r["response"]["route"] == r["expected"]["route"])
    routes: dict[str, int] = {}
    for r in ok:
        routes[r["response"]["route"]] = routes.get(r["response"]["route"], 0) + 1
    avg = sum(r["seconds"] for r in ok) / len(ok) if ok else 0
    print()
    print(
        f"sent: {len(results)}   triaged: {len(ok)}   unexpected HTTP status: {len(unexpected_http)}"
        f"   (edge cases with an expected status: {len(expected_status)})"
    )
    if cat:
        print(f"category matches expected: {cat_hits}/{len(cat)}   route matches expected: {rte_hits}/{len(rte)}")
    print("routes: " + ", ".join(f"{k}={v}" for k, v in sorted(routes.items())))
    print(f"average response time (triaged): {avg:.2f} s")
    print("legend: '=' matches the expected value in the sample file, 'x' differs")
    return 1 if unexpected_http else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:5678/webhook/inbox")
    parser.add_argument("--file", type=Path, default=ROOT / "samples" / "inquiries.json")
    parser.add_argument("--edge-cases", action="store_true", help="also run samples/edge_cases.json")
    parser.add_argument("--only", help="run only samples whose id contains this text")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--show-replies", action="store_true")
    parser.add_argument("--json-out", type=Path, help="write the raw responses to this file")
    args = parser.parse_args()

    samples = json.loads(args.file.read_text(encoding="utf-8"))
    edge = json.loads((ROOT / "samples" / "edge_cases.json").read_text(encoding="utf-8")) if args.edge_cases else []
    if args.only:
        samples = [s for s in samples if args.only in s["id"]]
        edge = [s for s in edge if args.only in s["id"]]

    print(f"POST {args.url}  ({len(samples)} samples{f' + {len(edge)} edge cases' if edge else ''})\n")
    print(header())
    results: list = []
    mode = run(args.url, samples, args.timeout, args.show_replies, results, None)
    if edge:
        run(args.url, edge, args.timeout, args.show_replies, results, mode)

    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nraw responses written to {args.json_out}")
    return summarize(results)


if __name__ == "__main__":
    sys.exit(main())
