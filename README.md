# AI Inbox Triage for an online shop (n8n + AI agent)

An importable **n8n workflow** that triages incoming customer messages for an e-commerce shop:
an AI agent classifies each message, extracts the order number, product and email, sets the
urgency, looks the order up, drafts a reply in the customer's language, and the workflow
validates, routes, logs and answers - automatically.

Built for the fictional pet-supplies shop **PawPantry**. Tested on **n8n 2.44.6** (Docker).

![Workflow canvas](assets/screenshots/n8n-1-workflow.png)

> **Status.** Build, structure and plumbing are done and tested in **mock mode** (no API key,
> no AI calls). The run with a real OpenAI key comes later; see [Live run results](#live-run-results)
> and [`scripts/live_run.md`](scripts/live_run.md).

---

## The problem

A small online shop gets the same mix every day: "where is my order?", returns, product
questions, damaged parcels, the occasional angry complaint, B2B requests and spam. Someone has
to read every message, decide who handles it, look up the order and type a reply. Urgent cases
(a sick pet, a double charge, a lost parcel) wait in the same queue as spam.

## What the workflow does

1. **Intake** - `POST /webhook/inbox` with `{from, subject, message, channel}`. Input is trimmed
   and defaulted; an empty message is rejected with HTTP 400 before any AI cost.
2. **AI triage** - an n8n **AI Agent** with an **OpenAI Chat Model** (`gpt-4.1-mini`) and a
   **Structured Output Parser** (strict JSON schema). The system prompt contains the category
   definitions, urgency rules and the shop's tone guide and policies. The agent has an
   **Order lookup tool** (Code tool over a mock order database) and is told to call it before
   drafting a reply; the tool hides order details when the email does not match the order.
3. **Validation** - a Code node checks the AI answer (required fields, enums, empty reply,
   confidence below `min_confidence`). Agent failures arrive here too via the agent's error
   output. Anything wrong goes to **human review** instead of being guessed.
4. **Routing** - a Switch node, first match wins:

   | Route | Rule | Queue |
   |---|---|---|
   | Needs review | validation failed | `triage-review` |
   | Spam | `category = spam` | `discarded` (logged, no notification) |
   | Urgent | `urgency = high` **or** `category = complaint` | `support-urgent` |
   | Wholesale | `category = wholesale_inquiry` | `sales` |
   | Support | everything else | `support` |

5. **Logging** - one row per message in the n8n **Data Table** `triage_log` (22 columns:
   input, triage result, route, validation errors, mode, execution id).
6. **Response** - the webhook answers with the structured result (below).
7. **Notifications** - after the response, per route: Slack (urgent), email (sales),
   Telegram (support), Slack (review). They ship **disabled** with a "configure your
   credentials" note.
8. **Error handling** - the workflow is its own *error workflow*: an Error Trigger logs any
   failed production run (node, message, execution link) to the Data Table `triage_errors`,
   with an optional email alert (disabled).

Categories: `order_status`, `return_refund`, `product_question`, `delivery_problem`,
`complaint`, `wholesale_inquiry`, `spam`. Urgency: `low` / `normal` / `high`.
Sentiment: `positive` / `neutral` / `negative`. Language: ISO 639-1 code.

## Repository

```
workflows/ai-inbox-triage.json   the n8n workflow (26 nodes, 10 sticky notes)
docker-compose.yml               local n8n 2.44.6 (image pinned)
scripts/bootstrap.py             one-command setup of a local n8n via its API (stdlib only)
scripts/send_samples.py          posts the samples, prints a results table (stdlib only)
scripts/screenshot.py            Playwright screenshots of the canvas / executions / tables
scripts/live_run.md              checklist for the run with a real OpenAI key
samples/inquiries.json           14 customer messages (12 English, 1 German, 1 French)
samples/edge_cases.json          empty message, malformed AI output, crash (last two: mock only)
assets/screenshots/              README images
```

## Quick start (local, mock mode, no API key)

```bash
docker compose -p n8n-triage-demo up -d

export N8N_OWNER_EMAIL=you@example.com N8N_OWNER_PASSWORD='Choose-a-local-passw0rd'
python3 scripts/bootstrap.py --mock      # owner account, API key, Data Tables, credential, import, publish
python3 scripts/send_samples.py --edge-cases --show-replies
```

Open <http://localhost:5678> to watch the executions. `bootstrap.py` is idempotent: run it
again after editing the JSON, or without `--mock` to switch to live mode.

## Import into your own n8n (no scripts)

1. **Import**: create a new workflow, then *... -> Import from File* (or *Import from URL* with
   the raw GitHub link of `workflows/ai-inbox-triage.json`).
2. **Data Tables**: create `triage_log` and `triage_errors` (*Overview -> Data tables*). Column
   names and types are listed in `DATA_TABLES` at the top of `scripts/bootstrap.py`
   (`received_at`/`failed_at` = date, `confidence` = number, `needs_review` = boolean, the rest string).
   The Data Table nodes select tables **by name**, so no IDs need editing.
3. **OpenAI credential**: open *OpenAI Chat Model* and select or create a credential named
   **OpenAI account**. The JSON contains only a reference by name, never a key.
4. **Publish**, then *Workflow settings -> Error workflow ->* select this same workflow and
   publish again (n8n only offers published workflows that contain an Error Trigger).
5. **Notifications** (optional): add Slack / SMTP / Telegram credentials, set channel, chat id
   and addresses, then enable the four notification nodes and the ops email node.
6. Test:

```bash
curl -s -X POST http://localhost:5678/webhook/inbox -H 'Content-Type: application/json' \
  -d '{"from":"buyer.4821@example.com","subject":"Where is my order?","message":"Hi, where is order PP-10418?","channel":"email"}'
```

Before exposing the webhook publicly, enable authentication on the *Inbox webhook* node
(header auth or basic auth) or put it behind your reverse proxy.

## Webhook response

Real response from the mock run (sample `13-de-order-status`). In live mode the summary and
reply are written by the model and `order_status` is filled from the order lookup tool.

```json
{
  "ok": true,
  "log_id": 45,
  "route": "support",
  "queue": "support",
  "needs_human_review": false,
  "triage": {
    "category": "order_status",
    "urgency": "normal",
    "sentiment": "neutral",
    "language": "de",
    "confidence": 0.9,
    "summary": "[mock] Customer asks about the status of an order. Order: PP-10431. Product: CalmCat Clumping Litter."
  },
  "extracted": {
    "order_number": "PP-10431",
    "product": "CalmCat Clumping Litter",
    "customer_email": "katzenhaushalt@example.de",
    "order_status": null
  },
  "reply_draft": "Vielen Dank für Ihre Nachricht. Wir prüfen gerade den Status von PP-10431 und senden Ihnen innerhalb eines Werktages die aktuellen Sendungsdaten.\n\nIhr PawPantry-Team",
  "validation_errors": [],
  "mode": "mock",
  "execution_id": "51"
}
```

When validation fails, `route` is `human_review`, `needs_human_review` is `true` and
`validation_errors` lists the reasons (for example `missing urgency`, `low confidence (<value>)`,
`AI step failed: Connection error.`). An empty message returns HTTP 400 `{"ok": false, "error": "Field 'message' is required."}`.

## Mock mode vs live mode

| | Mock mode | Live mode |
|---|---|---|
| Switch | `mock_mode = true` in **Settings & normalize**, or `bootstrap.py --mock` | `mock_mode = false` (the default in the JSON), or `bootstrap.py` |
| What answers | **Mock AI output** Code node: keyword rules and reply templates (en/de/fr) | AI Triage Agent + `gpt-4.1-mini` + Order lookup tool |
| Needs | nothing | an OpenAI credential named "OpenAI account" |
| Output shape | identical `{ output: {...} }`, so validation, routing, logging, response and notifications run unchanged | |
| Extra test hooks | `[[mock:invalid]]` in a message -> malformed answer -> human review; `[[mock:crash]]` -> failed run -> error log | |

Mock mode exists to test the plumbing without spending tokens. Its labels come from simple
rules written alongside the samples, so mock results say nothing about AI accuracy.

## What was tested (mock mode, n8n 2.44.6)

On a freshly created n8n instance (`down -v`, `up -d`, `bootstrap.py --mock`):

```
id                      http  lang  category             urg     route           order      conf  sec
-------------------------------------------------------------------------------------------------------
01-order-status         200   en    = order_status       normal  = support       PP-10418   0.9   0.2
02-late-delivery        200   en    = delivery_problem   high    = urgent        PP-10427   0.9   0.1
03-return-size          200   en    = return_refund      normal  = support       PP-10433   0.9   0.1
04-charged-twice        200   en    = return_refund      high    = urgent        PP-10441   0.9   0.1
05-puppy-food           200   en    = product_question   low     = support       -          0.9   0.1
06-litter-question      200   en    = product_question   low     = support       -          0.9   0.1
07-damaged-parcel       200   en    = delivery_problem   normal  = support       PP-10425   0.9   0.1
08-marked-delivered     200   en    = delivery_problem   high    = urgent        PP-10439   0.9   0.1
09-pet-sick             200   en    = complaint          high    = urgent        PP-10436   0.9   0.1
10-no-reply             200   en    = complaint          normal  = urgent        -          0.9   0.1
11-wholesale            200   en    = wholesale_inquiry  low     = wholesale     -          0.9   0.1
12-spam                 200   en    = spam               low     = spam          -          0.9   0.1
13-de-order-status      200   de    = order_status       normal  = support       PP-10431   0.9   0.1
14-fr-return            200   fr    = return_refund      normal  = support       PP-10412   0.9   0.1
e1-missing-message      400=  Field 'message' is required.  (0.1 s)
e2-malformed-ai-output  200   -       not_a_category     -       = human_review  -          0     0.1
e3-workflow-crash       500=  Error in workflow  (0.1 s)
```

Checked afterwards through the n8n API:

- `triage_log` holds 15 rows (14 samples + the malformed case; the HTTP 400 request is not
  logged), with the expected routes and queues; `triage_errors` holds 1 row for the crash with
  node name, message and execution link written by the Error Trigger run.
- Disabled notification nodes pass data through, so the dispatch Switch is exercised.
- **Agent wiring without AI calls**: live mode with the credential pointed at an unreachable
  local address (`OPENAI_BASE_URL=http://127.0.0.1:9/v1`, placeholder key). The agent built its
  prompt (system message, tone guide and the rendered customer message are visible in the
  execution), the model call failed with `Connection error.`, the agent's error output sent the
  item to validation, and the request ended as `human_review` with HTTP 200. No request left the machine.
- **Output schema** compiled with n8n's own JSON-schema-to-zod converter: all 14 mock answers
  accepted; an incomplete object and an object with an extra key rejected.
- **Order lookup tool** code run directly in Node with 4 inputs: match, number without prefix,
  wrong email (details withheld), unknown number.
- The raw repository JSON also imports through the n8n CLI (`n8n import:workflow`); there the
  credential placeholder was re-linked by name to the existing "OpenAI account" credential.

## Adapting it to a real shop

The triage core (agent, validation, routing, logging) stays the same; only the edges change.
All nodes named below exist in n8n 2.44.6, and the WooCommerce, Shopify, Zendesk, Gmail,
Microsoft Outlook, Freshdesk and Help Scout nodes can be attached to the agent as tools.

| Source / target | Replace | With |
|---|---|---|
| **Gmail** | Inbox webhook | *Gmail Trigger* (label filter), map sender, subject and text in *Settings & normalize*; write the reply as a Gmail **draft** so a person approves it |
| **Outlook / Microsoft 365** | Inbox webhook | *Microsoft Outlook Trigger*, reply as a draft with the *Microsoft Outlook* node |
| **Zendesk** (or Freshdesk, Help Scout) | Inbox webhook + notifications | *Zendesk Trigger* on new tickets; instead of Slack/Telegram, update the ticket: tags from category and urgency, group from route, draft reply as an internal note |
| **WooCommerce** | Order lookup tool | the *WooCommerce* node as an agent tool (order: get) or an HTTP Request tool on `/wp-json/wc/v3/orders`; keep the email check before returning details |
| **Shopify** | Order lookup tool | the *Shopify* node as an agent tool |
| Logging at scale | Data Tables | Postgres / Google Sheets / your helpdesk; Data Tables are fine for a demo and small volumes |

Also adjust: the order number pattern (`PP-12345`) in the system prompt, the policies and
tone guide, `min_confidence`, and the queue names.

## Cost notes

Estimate only; measured numbers will replace it after the live run.

- One message is typically 2-3 model calls (tool call, then the final structured answer).
  n8n estimated **about 1,000 prompt tokens** for the first call of a sample message (system
  prompt plus message, before tool definitions). Rough total: 3,000-6,000 input and 300-600
  output tokens per message.
- At the `gpt-4.1-mini` list price at the time of writing (USD 0.40 per 1M input, 1.60 per 1M
  output tokens - check the current OpenAI pricing) that is roughly **USD 0.002-0.004 per
  message**, or USD 2-4 per 1,000 messages.
- Empty messages are rejected before the agent, and mock mode costs nothing.

## Limitations

- **Not yet run against a real model.** Prompt, tool use and confidence threshold may need
  tuning after the live run. Mock results only prove the plumbing.
- Confidence is the model's own estimate, not a calibrated probability.
- One message at a time: no thread history, deduplication or attachment handling.
- Reply drafts are never sent automatically - by design a person approves them.
- Customer text goes to the model. The agent only has a read-only lookup that withholds
  details on email mismatch and every answer is schema-validated, but treat drafts as untrusted.
- Messages and email addresses are stored in a Data Table and sent to OpenAI: define retention
  and check your data-processing terms (GDPR) before production use.
- The webhook ships without authentication (local demo); enable it before going public.
- The error workflow must be set after the first publish (n8n only accepts published workflows).
- n8n runs its task runner in internal mode here (fine locally; n8n recommends external runners in production).

## Live run results

> **Placeholder - to be filled after the live run with a real OpenAI key**
> (procedure: [`scripts/live_run.md`](scripts/live_run.md)).
>
> - Date, n8n version, model:
> - Category matches / route matches (of 14):
> - Average response time:
> - Tokens and cost for the run:
> - Example replies (2-3):
> - Messages routed to human review and why:
> - Screenshots: execution view, agent tool call, results table, Data Table

## License

MIT - see [LICENSE](LICENSE). Author: Aleks T.
PawPantry, its products, orders and customers are fictional; all email addresses use example domains.
