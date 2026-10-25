#!/usr/bin/env python3
"""Bootstrap a local n8n instance for the AI Inbox Triage demo.

Standard library only. Idempotent: safe to run again after changing the
workflow JSON or switching between mock and live mode.

What it does, against the n8n URL given by --url (default http://localhost:5678):
  1. waits until n8n is healthy;
  2. on a fresh instance, completes the owner setup (N8N_OWNER_EMAIL / N8N_OWNER_PASSWORD);
  3. obtains a public-API key (N8N_API_KEY, the cached key in --state-file, or a new one);
  4. creates the Data Tables `triage_log` and `triage_errors` if they are missing;
  5. creates or updates the credential "OpenAI account" from OPENAI_API_KEY
     (a harmless placeholder "not-configured" when the variable is not set);
  6. imports the workflow (or updates the existing one with the same name),
     wires the credential, sets the workflow as its own error workflow,
     sets mock_mode, and publishes (activates) it.

The OpenAI key is read from the environment only. It is never printed or written to disk.

Examples:
  # mock mode, no AI key needed
  N8N_OWNER_EMAIL=you@example.com N8N_OWNER_PASSWORD='Choose-a-strong-1' \
      python3 scripts/bootstrap.py --mock

  # live mode
  OPENAI_API_KEY=... N8N_OWNER_EMAIL=... N8N_OWNER_PASSWORD=... python3 scripts/bootstrap.py
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW = ROOT / "workflows" / "ai-inbox-triage.json"
DEFAULT_STATE = ROOT / ".n8n-local.json"
CREDENTIAL_NAME = "OpenAI account"
PLACEHOLDER_KEY = "not-configured"
API_KEY_SCOPE_PREFIXES = ("workflow", "credential", "dataTable", "dataTableRow", "dataTableColumn", "execution", "tag")

DATA_TABLES = {
    "triage_log": [
        ("received_at", "date"),
        ("channel", "string"),
        ("sender", "string"),
        ("subject", "string"),
        ("message", "string"),
        ("route", "string"),
        ("queue", "string"),
        ("category", "string"),
        ("urgency", "string"),
        ("sentiment", "string"),
        ("language", "string"),
        ("order_number", "string"),
        ("product", "string"),
        ("customer_email", "string"),
        ("order_status", "string"),
        ("summary", "string"),
        ("reply_draft", "string"),
        ("confidence", "number"),
        ("needs_review", "boolean"),
        ("validation_errors", "string"),
        ("mode", "string"),
        ("execution_id", "string"),
    ],
    "triage_errors": [
        ("failed_at", "date"),
        ("workflow", "string"),
        ("node", "string"),
        ("error_message", "string"),
        ("execution_id", "string"),
        ("execution_url", "string"),
    ],
}

# Fields the public API accepts when creating or updating a workflow.
WORKFLOW_FIELDS = ("name", "nodes", "connections", "settings")
SETTINGS_FIELDS = (
    "saveExecutionProgress",
    "saveManualExecutions",
    "saveDataErrorExecution",
    "saveDataSuccessExecution",
    "executionTimeout",
    "errorWorkflow",
    "timezone",
    "executionOrder",
    "callerPolicy",
    "callerIds",
)


class N8nError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key: str | None = None
        # n8n marks its auth cookie "Secure"; Python's cookie jar would not send it over
        # plain http://localhost, so the session cookie is carried by hand.
        self.auth_cookie: str | None = None

    def request(self, method: str, path: str, body=None, *, api: bool = False, ok=(200, 201)):
        url = self.base + path
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if api:
            if not self.api_key:
                raise N8nError("no API key available")
            req.add_header("X-N8N-API-KEY", self.api_key)
        if self.auth_cookie:
            req.add_header("Cookie", f"n8n-auth={self.auth_cookie}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status, raw = resp.status, resp.read()
                for header in resp.headers.get_all("Set-Cookie") or []:
                    name, _, rest = header.partition("=")
                    if name.strip() == "n8n-auth":
                        self.auth_cookie = rest.split(";", 1)[0]
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (OSError, http.client.HTTPException) as exc:  # connection refused/reset while n8n starts
            raise N8nError(f"{method} {path}: {getattr(exc, 'reason', exc)}") from exc
        if status not in ok:
            text = raw.decode(errors="replace")[:500]
            raise N8nError(f"{method} {path} -> HTTP {status}: {text}")
        return json.loads(raw) if raw else None


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def wait_healthy(client: Client, seconds: int = 120) -> None:
    # /healthz answers before the REST API is mounted on a fresh start,
    # so wait for readiness and for the settings route as well.
    deadline = time.time() + seconds
    while True:
        try:
            client.request("GET", "/healthz/readiness")
            client.request("GET", "/rest/settings")
            return
        except N8nError:
            if time.time() > deadline:
                raise
            time.sleep(2)


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2))
    path.chmod(0o600)


def api_key_works(client: Client, key: str) -> bool:
    client.api_key = key
    try:
        client.request("GET", "/api/v1/workflows?limit=1", api=True)
        return True
    except N8nError:
        client.api_key = None
        return False


def ensure_api_key(client: Client, state_path: Path) -> None:
    env_key = os.environ.get("N8N_API_KEY")
    if env_key and api_key_works(client, env_key):
        log("using N8N_API_KEY from the environment")
        return
    state = load_state(state_path)
    if state.get("apiKey") and api_key_works(client, state["apiKey"]):
        log(f"using cached n8n API key from {state_path.name}")
        return

    email = os.environ.get("N8N_OWNER_EMAIL")
    password = os.environ.get("N8N_OWNER_PASSWORD")
    if not email or not password:
        raise N8nError("set N8N_OWNER_EMAIL and N8N_OWNER_PASSWORD (or N8N_API_KEY)")

    settings = client.request("GET", "/rest/settings")["data"]
    if settings.get("userManagement", {}).get("showSetupOnFirstLoad"):
        log(f"fresh instance: creating owner account {email}")
        client.request(
            "POST",
            "/rest/owner/setup",
            {"email": email, "firstName": "Demo", "lastName": "Owner", "password": password},
        )
    client.request("POST", "/rest/login", {"emailOrLdapLoginId": email, "password": password})
    scopes = client.request("GET", "/rest/api-keys/scopes")["data"]
    wanted = [s for s in scopes if s.split(":")[0] in API_KEY_SCOPE_PREFIXES]
    created = client.request(
        "POST",
        "/rest/api-keys",
        {"label": f"inbox-triage-bootstrap-{int(time.time())}", "expiresAt": None, "scopes": wanted},
    )["data"]
    key = created.get("rawApiKey") or created.get("apiKey")
    if not key or not api_key_works(client, key):
        raise N8nError("could not create a working n8n API key")
    save_state(state_path, {"apiKey": key})
    log(f"created an n8n API key (cached in {state_path.name}, git-ignored)")


def ensure_data_tables(client: Client) -> None:
    for name, columns in DATA_TABLES.items():
        query = urllib.parse.urlencode({"filter": json.dumps({"name": name})})
        found = client.request("GET", f"/api/v1/data-tables?{query}", api=True)["data"]
        if any(t["name"] == name for t in found):
            log(f"data table {name}: exists")
            continue
        client.request(
            "POST",
            "/api/v1/data-tables",
            {"name": name, "columns": [{"name": c, "type": t} for c, t in columns]},
            api=True,
        )
        log(f"data table {name}: created ({len(columns)} columns)")


def ensure_credential(client: Client) -> str:
    api_key = os.environ.get("OPENAI_API_KEY") or PLACEHOLDER_KEY
    data = {"apiKey": api_key}
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        data["url"] = base_url
    label = "real key from OPENAI_API_KEY" if api_key != PLACEHOLDER_KEY else f'placeholder "{PLACEHOLDER_KEY}"'
    if base_url:
        label += f", base URL {base_url}"

    existing = client.request("GET", "/api/v1/credentials?limit=250", api=True)["data"]
    for cred in existing:
        if cred["name"] == CREDENTIAL_NAME and cred["type"] == "openAiApi":
            client.request(
                "PATCH",
                f"/api/v1/credentials/{cred['id']}",
                {"name": CREDENTIAL_NAME, "type": "openAiApi", "data": data},
                api=True,
            )
            log(f'credential "{CREDENTIAL_NAME}": updated ({label})')
            return cred["id"]
    created = client.request(
        "POST", "/api/v1/credentials", {"name": CREDENTIAL_NAME, "type": "openAiApi", "data": data}, api=True
    )
    log(f'credential "{CREDENTIAL_NAME}": created ({label})')
    return created["id"]


def prepare_workflow(path: Path, credential_id: str, mock: bool) -> dict:
    wf = json.loads(path.read_text())
    for node in wf["nodes"]:
        creds = node.get("credentials") or {}
        if "openAiApi" in creds:
            creds["openAiApi"] = {"id": credential_id, "name": CREDENTIAL_NAME}
        if node["name"] == "Settings & normalize":
            for a in node["parameters"]["assignments"]["assignments"]:
                if a["name"] == "mock_mode":
                    a["value"] = mock
    body = {k: wf[k] for k in WORKFLOW_FIELDS if k in wf}
    body["settings"] = {k: v for k, v in wf.get("settings", {}).items() if k in SETTINGS_FIELDS}
    return body


def publish(client: Client, wf_id: str) -> None:
    try:
        client.request("POST", f"/api/v1/workflows/{wf_id}/publish", {}, api=True)
    except N8nError as exc:
        if "HTTP 404" not in str(exc):
            raise
        client.request("POST", f"/api/v1/workflows/{wf_id}/activate", api=True)  # n8n 1.x


def ensure_workflow(client: Client, body: dict) -> str:
    query = urllib.parse.urlencode({"name": body["name"]})
    listed = client.request("GET", f"/api/v1/workflows?{query}", api=True)["data"]
    found = [w for w in listed if w["name"] == body["name"]]
    if found:
        wf_id = found[0]["id"]
        client.request("PUT", f"/api/v1/workflows/{wf_id}", body, api=True)
        log(f"workflow exists ({wf_id}): updated")
    else:
        wf_id = client.request("POST", "/api/v1/workflows", body, api=True)["id"]
        log(f"workflow imported ({wf_id})")
    publish(client, wf_id)
    # The workflow is its own error workflow: its Error Trigger logs failed runs.
    # n8n only accepts a published workflow here, hence publish -> set -> publish.
    # The published version is registered asynchronously, so retry for a while.
    body["settings"]["errorWorkflow"] = wf_id
    for attempt in range(20):
        try:
            client.request("PUT", f"/api/v1/workflows/{wf_id}", body, api=True)
            break
        except N8nError as exc:
            if "no published version" not in str(exc) or attempt == 19:
                raise
            time.sleep(2)
    publish(client, wf_id)
    log("workflow published (active), error workflow = itself")
    return wf_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("N8N_URL", "http://localhost:5678"))
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE, help="where the n8n API key is cached")
    parser.add_argument("--mock", action="store_true", help="import with mock_mode = true (no AI calls)")
    args = parser.parse_args()

    client = Client(args.url)
    try:
        wait_healthy(client)
        ensure_api_key(client, args.state_file)
        ensure_data_tables(client)
        credential_id = ensure_credential(client)
        body = prepare_workflow(args.workflow, credential_id, args.mock)
        wf_id = ensure_workflow(client, body)
    except N8nError as exc:
        log(f"ERROR: {exc}")
        return 1

    mode = "MOCK (keyword rules, no AI calls)" if args.mock else "LIVE (OpenAI via the AI Agent)"
    log(f"mode: {mode}")
    log(f"editor:  {args.url.rstrip('/')}/workflow/{wf_id}")
    log(f"webhook: {args.url.rstrip('/')}/webhook/inbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
