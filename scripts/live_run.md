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
- [ ] Token usage per execution: n8n records it in the execution data. Sum it with:

```bash
python3 - <<'PY'
import json, os, urllib.request
base, key = "http://localhost:5678/api/v1", os.environ["N8N_API_KEY"]  # or the key cached in .n8n-local.json
def get(path):
    req = urllib.request.Request(base + path, headers={"X-N8N-API-KEY": key})
    return json.load(urllib.request.urlopen(req))
def walk(node, acc):
    if isinstance(node, dict):
        usage = node.get("tokenUsage")
        if isinstance(usage, dict):
            acc["prompt"] += usage.get("promptTokens", 0)
            acc["completion"] += usage.get("completionTokens", 0)
        for value in node.values():
            walk(value, acc)
    elif isinstance(node, list):
        for value in node:
            walk(value, acc)
results = json.load(open("results-live.json"))
total = {"prompt": 0, "completion": 0}
ids = [r["response"]["execution_id"] for r in results if isinstance(r["response"], dict) and r["response"].get("ok")]
for exec_id in ids:
    acc = {"prompt": 0, "completion": 0}
    walk(get(f"/executions/{exec_id}?includeData=true"), acc)
    total = {k: total[k] + acc[k] for k in total}
print(f"{len(ids)} executions, prompt tokens {total['prompt']}, completion tokens {total['completion']}")
PY
```

  (`N8N_API_KEY`: `export N8N_API_KEY=$(python3 -c "import json;print(json.load(open('.n8n-local.json'))['apiKey'])")`.
  The exact location of `tokenUsage` in the execution data was not verifiable without a live run;
  the script searches the whole execution, so cross-check the total once with the OpenAI usage page.)

## 6. Screenshots (1600x900, scale 2)

```bash
WF=<workflow id>
EXEC=$(python3 -c "import json;r=json.load(open('results-live.json'));print(next(x['response']['execution_id'] for x in r if x['id']=='09-pet-sick'))")
python3 scripts/screenshot.py --workflow-id $WF --out assets/screenshots/n8n-1-workflow.png
python3 scripts/screenshot.py --workflow-id $WF --execution-id $EXEC --out assets/screenshots/n8n-2-execution.png
python3 scripts/screenshot.py --text-file results-live.txt --out assets/screenshots/n8n-3-results.png
```

- [ ] `n8n-2-execution.png`: the green path through Urgent; open the AI Triage Agent in the
      execution view and take a second shot of the tool call if it is readable (`n8n-4-agent.png`, manual).
- [ ] Data Table view: Overview -> Data tables -> `triage_log`, or
      `python3 scripts/screenshot.py --path /projects/<projectId>/datatables/<tableId> --out assets/screenshots/n8n-5-data-table.png`
      (ids: `GET /api/v1/data-tables`).
- [ ] Open every PNG and check that nothing overlaps and the text is readable.

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
