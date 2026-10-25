# Live run checklist

Everything in this repository was built and tested **without an AI key** (mock mode, see the
README). This checklist describes the one live run with a real OpenAI key that fills the
"Live run results" section of the README and adds the execution screenshots.

Expected spend: the 14 samples should cost a few cents at most with `gpt-4.1-mini`
(estimate, see "Cost notes" in the README). Set a low monthly limit on the OpenAI project first.

## 0. Preconditions

- [ ] Docker, Python 3.10+, `pip install playwright` and `playwright install chromium`
      (or `PLAYWRIGHT_CHROMIUM_EXECUTABLE` pointing at an existing Chromium build).
- [ ] An OpenAI API key for a project with a spending limit. Keep it in a password manager;
      it must never be written into a file in this repository.
- [ ] Working directory: the repository root.

## 1. Fresh n8n

```bash
docker compose -p n8n-triage-demo down -v      # only if an old demo stack exists
docker compose -p n8n-triage-demo up -d
```

## 2. Credentials come from environment variables only

```bash
export N8N_OWNER_EMAIL=owner@example.com
export N8N_OWNER_PASSWORD='<throwaway local password, 8+ chars, 1 digit, 1 capital>'
read -rs OPENAI_API_KEY && export OPENAI_API_KEY   # paste, nothing is echoed
unset OPENAI_BASE_URL
```

## 3. Bootstrap in live mode

```bash
python3 scripts/bootstrap.py
unset OPENAI_API_KEY                                # no longer needed: n8n stores it encrypted
```

Check the output:
- [ ] `credential "OpenAI account": created (real key from OPENAI_API_KEY)` - the credential is
      created through the n8n public API (`POST /api/v1/credentials`) from the environment variable.
- [ ] `mode: LIVE (OpenAI via the AI Agent)`
- [ ] note the workflow id from the `editor:` line.

## 4. Smoke test (one message)

```bash
python3 scripts/send_samples.py --only 01 --show-replies
```

- [ ] HTTP 200, `mode` = `live` in the response, a real reply draft, sensible category.
- [ ] In the editor: Executions -> latest -> **AI Triage Agent** shows a call to `Order_lookup_tool`
      and the final structured output. If the agent never calls the tool, tighten the "Tool"
      section of the system message and re-run.
- [ ] If the route is `human_review`, read `validation_errors` first (typical: low confidence,
      or the model returned something the schema rejected).

## 5. Full run

```bash
python3 scripts/send_samples.py --edge-cases --show-replies --json-out results-live.json | tee results-live.txt
```

- [ ] The two mock-only edge cases are skipped automatically; the empty-message case must still return HTTP 400.
- [ ] Record: category matches, route matches, average response time (printed at the end).
- [ ] Read every reply draft: right language, tone guide followed, no invented tracking numbers,
      no promised refunds, empty reply for spam, sign-off "The PawPantry Team".
- [ ] Count the model calls and n8n's token estimate. n8n 2.44.6 does not store real token
      counts for these calls. Each run of *OpenAI Chat Model* in the execution data carries only
      `tokenUsageEstimate` (n8n's estimate of the prompt, completion always 0), at
      `runData["OpenAI Chat Model"][i].data.ai_languageModel[0][0].json`. The estimate leaves
      out the tool definitions and the answers. Billed numbers are on the OpenAI usage page.

```bash
export N8N_API_KEY=$(python3 -c "import json;print(json.load(open('.n8n-local.json'))['apiKey'])")
python3 - <<'PY'
import json, os, urllib.request
base, key = "http://localhost:5678/api/v1", os.environ["N8N_API_KEY"]
def get(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(base + path, headers={"X-N8N-API-KEY": key})))
calls = tools = estimate = 0
for e in get("/executions?limit=250")["data"]:
    if e["mode"] == "retry":          # a retry reuses the stored model runs
        continue
    run = get(f"/executions/{e['id']}?includeData=true")["data"]["resultData"]["runData"]
    model = run.get("OpenAI Chat Model", [])
    calls += len(model)
    tools += len(run.get("Order lookup tool", []))
    estimate += sum(r["data"]["ai_languageModel"][0][0]["json"].get("tokenUsageEstimate", {}).get("promptTokens", 0)
                    for r in model)
print(f"model calls {calls}, order lookups {tools}, n8n prompt-token estimate {estimate}")
PY
```

## 6. Screenshots (1600x900, scale 2)

```bash
WF=<workflow id>
EXEC=$(python3 -c "import json;r=json.load(open('results-live.json'));print(next(x['response']['execution_id'] for x in r if x['id']=='02-late-delivery'))")
python3 scripts/screenshot.py --workflow-id $WF --out assets/screenshots/n8n-1-workflow.png
python3 scripts/screenshot.py --workflow-id $WF --execution-id $EXEC --open-node "AI Triage Agent" \
    --out assets/screenshots/n8n-2-execution.png
python3 scripts/results_table.py results-live.json --out results-live.html \
    --meta "n8n 2.44.6, AI Agent with gpt-4.1-mini, <date>."
python3 scripts/screenshot.py --html-file results-live.html --out assets/screenshots/n8n-3-results.png
```

- [ ] `n8n-2-execution.png` is the agent's detail view inside the execution: the customer message
      on the left, the prompt in the middle, the real output on the right. Pick a message with an
      order number, so `order_status` shows a value that only the lookup tool could return.
- [ ] `n8n-3-results.png` leaves out email addresses. The Lookup column needs `N8N_API_KEY`.
- [ ] Optional Data Table view: `python3 scripts/screenshot.py --path /projects/<projectId>/datatables/<tableId> --out ...`
      (ids from `GET /api/v1/data-tables`).
- [ ] Open every PNG. Check that nothing overlaps, the text is readable, and no key or login
      email is visible.

## 7. Write up and commit

- [ ] Fill the "Live run results" section in `README.md` (date, n8n version, model, accuracy,
      latency, tokens and cost, 2-3 example replies, anything that went to human review and why).
- [ ] `results-live.json` / `results-live.txt` are git-ignored; paste only the table into the README.
- [ ] Secret check before committing:

```bash
grep -rnE 'sk-[A-Za-z0-9_-]{10,}' --exclude-dir=.git . || echo "no secrets found"
git status --short
```

## 8. Clean up

```bash
docker compose -p n8n-triage-demo down -v   # removes n8n, the stored credential and the Data Tables
```

- [ ] If the OpenAI key was created only for this run, revoke it in the OpenAI dashboard.
