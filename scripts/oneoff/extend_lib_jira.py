"""Extend lib-jira with getTransitions / transitionIssue / linkIssues.

Applied via the n8n REST API (the MCP update path 400s against this n8n
version — the documented workaround). Every lib convention is honoured:
- raw REST + fullResponse + neverError + continueRegularOutput
- the fail()/s()/classifyAndThrow block is EXTRACTED from `Return: setDueDate`
  so the new copies are byte-identical to the existing four
- failures name the op AND the issue key; no ": " in message bodies
- link_type allowlisted; transition resolved by NAME against availability
"""

import json
import os
import re
import urllib.request

WF_ID = "22VH6mOTbyzfRaYY"
BASE = "http://localhost:5678/api/v1"

key = None
for line in open("/home/codys-lab/n8n-agentic-team/.env", encoding="utf-8"):
    if line.startswith("N8N_API_KEY="):
        key = line.split("=", 1)[1].strip()
assert key, "no N8N_API_KEY"
HDRS = {"X-N8N-API-KEY": key, "content-type": "application/json"}


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method, headers=HDRS,
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


wf = call("GET", f"/workflows/{WF_ID}")
nodes = wf["nodes"]
conns = wf["connections"]


def node(name):
    return next(n for n in nodes if n["name"] == name)


# ── 1. Validate request: new ops + branches ───────────────────────────────────
validate = node("Validate request")
code = validate["parameters"]["jsCode"]

OPS_OLD = "const OPS = ['getIssue', 'searchIssues', 'updateLabels', 'createIssue', 'addComment', 'setDueDate', 'updateAttributes'];"
OPS_NEW = "const OPS = ['getIssue', 'searchIssues', 'updateLabels', 'createIssue', 'addComment', 'setDueDate', 'updateAttributes', 'getTransitions', 'transitionIssue', 'linkIssues'];"
assert OPS_OLD in code, "OPS line not found"
code = code.replace(OPS_OLD, OPS_NEW)

TAIL = "    return l;\n  });\n}\n\nreturn [{ json: out }];"
assert code.count(TAIL) == 1, "validate tail not unique"
BRANCHES = """    return l;
  });
} else if (op === 'getTransitions') {
  // Central Command (2026-07-20): read op — the legal next statuses for an issue.
  out.issue_key = issueKey(inp.issue_key);

} else if (op === 'transitionIssue') {
  out.issue_key = issueKey(inp.issue_key);
  // §3.0.3 contract class: EVERY transitionIssue failure names the op AND the key.
  const ref = ` for ${s(out.issue_key, 64)}`;
  // Transition arrives as a NAME (model-facing callers never see Cloud ids); it is
  // resolved against the issue's actually-available transitions downstream, so a bad
  // name fails with the available set logged rather than a provider 400.
  out.transition = clean(inp.transition, 'transition', { max: 64, ctx: ref });

} else if (op === 'linkIssues') {
  // Both keys are URL/JSON-body payloads: strict Jira key shape each (KEY_RE), same
  // reasoning as issueKey(). Direction contract (note_contract): from_key is the
  // OUTWARD side — for Blocks, from_key BLOCKS to_key (to_key reads 'is blocked by').
  const fk = clean(inp.from_key, 'from_key', { max: 64 });
  if (!KEY_RE.test(fk)) fail(`linkIssues — bad from_key '${s(fk)}' — must match [A-Z][A-Z0-9_]*-<number>`);
  out.from_key = fk;
  const tk = clean(inp.to_key, 'to_key', { max: 64 });
  if (!KEY_RE.test(tk)) fail(`linkIssues — bad to_key '${s(tk)}' — must match [A-Z][A-Z0-9_]*-<number>`);
  out.to_key = tk;
  if (out.from_key === out.to_key) fail(`linkIssues — from_key and to_key are the same issue '${s(out.from_key)}'`);
  // link_type is model-controlled: allowlist, reject-don't-coerce. Standard Jira Cloud
  // types only — hierarchy is NEVER faked with links, so no 'Cloners' and no custom
  // types until the operator adds them here.
  const LINK_TYPES = ['Blocks', 'Relates', 'Duplicate'];
  if (typeof inp.link_type !== 'string' || !LINK_TYPES.includes(inp.link_type)) {
    fail(`linkIssues — bad link_type '${s(inp.link_type)}' (must be one of ${LINK_TYPES.join(', ')})`);
  }
  out.link_type = inp.link_type;
}

return [{ json: out }];"""
code = code.replace(TAIL, BRANCHES)
validate["parameters"]["jsCode"] = code

# ── 2. Extract the byte-identical helper/classifier block ─────────────────────
sdd = node("Return: setDueDate")["parameters"]["jsCode"]
start = sdd.index("// ── THE throw helper")
end = sdd.index("const req = $('Load jira config')")
CLASSIFIER = sdd[start:end]
assert "classifyAndThrow" in CLASSIFIER and "function fail" in CLASSIFIER

CRED = node("Set due date (Jira REST)")["credentials"]
HOST = os.environ.get("CC_JIRA_BASE_URL", "")
if not HOST:
    raise SystemExit("FATAL: set CC_JIRA_BASE_URL (e.g. https://yourorg.atlassian.net)")


def http_node(name, method, url, json_body, pos):
    p = {
        "method": method,
        "url": url,
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "options": {
            "timeout": 15000,
            "response": {"response": {"fullResponse": True, "neverError": True,
                                       "responseFormat": "json"}},
        },
    }
    if json_body is not None:
        p["sendBody"] = True
        p["specifyBody"] = "json"
        p["jsonBody"] = json_body
    if method == "GET":
        del p["method"]  # GET is the node default
        p["method"] = "GET"
    return {
        "id": name, "name": name, "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2, "position": pos, "parameters": p,
        "credentials": CRED, "onError": "continueRegularOutput",
    }


def code_node(name, js, pos):
    return {"id": name, "name": name, "type": "n8n-nodes-base.code",
            "typeVersion": 2, "position": pos, "parameters": {"jsCode": js}}


NON2XX_DETAIL = """const b0 = (r.body && typeof r.body === 'object') ? r.body : {};
const parts = [];
if (Array.isArray(b0.errorMessages)) parts.push(...b0.errorMessages);
if (b0.errors && typeof b0.errors === 'object' && !Array.isArray(b0.errors)) {
  for (const [f, msg] of Object.entries(b0.errors)) parts.push(`${f}: ${String(msg)}`);
}
const detail = parts.length ? parts.join(' | ') : `HTTP ${st}`;"""

ret_get_transitions = ("""// getTransitions -> the legal next statuses for an issue (Central Command, 2026-07-20).
// Raw REST because the Jira node v1 has no transitions op. fullResponse + neverError +
// continueRegularOutput, so every outcome arrives here as data (the lib pattern).
""" + CLASSIFIER + """const req = $('Load jira config').first(0, 0).json;
const key = req.issue_key;
const r = $input.first().json;
if (r && r.error !== undefined && r.statusCode === undefined) classifyAndThrow(r, 'getTransitions', key);
if (!r || typeof r !== 'object' || r.statusCode === undefined) fail(`getTransitions — unexpected response shape for ${s(key, 64)} (no statusCode) — refusing to report success`);
const st = r.statusCode;
if (st < 200 || st >= 300) {
""" + NON2XX_DETAIL + """
classifyAndThrow({ error: { httpCode: st, message: detail } }, 'getTransitions', key);
}
const b = (r.body && typeof r.body === 'object') ? r.body : {};
const transitions = (Array.isArray(b.transitions) ? b.transitions : [])
  .map(t => ({ id: (t && t.id) ?? null, name: (t && t.name) ?? null, to_status: (t && t.to && t.to.name) ?? null }))
  .filter(t => t.id && t.name);
return [{ json: { ok: true, kind: 'jira', operation: 'getTransitions', issue: { issue_key: key }, transitions, count: transitions.length } }];
""")

resolve_transition = ("""// transitionIssue step 1/2 (Central Command, 2026-07-20) — resolve the requested
// transition NAME against the issue's actually-available transitions. Model-facing
// callers never see Cloud transition ids; a name that is not available fails HERE with
// the available set in the worker log, not as an opaque provider 400.
""" + CLASSIFIER + """const req = $('Load jira config').first(0, 0).json;
const key = req.issue_key;
const r = $input.first().json;
if (r && r.error !== undefined && r.statusCode === undefined) classifyAndThrow(r, 'transitionIssue', key);
if (!r || typeof r !== 'object' || r.statusCode === undefined) fail(`transitionIssue — unexpected response shape for ${s(key, 64)} (no statusCode) — refusing to continue`);
const st = r.statusCode;
if (st < 200 || st >= 300) {
""" + NON2XX_DETAIL + """
classifyAndThrow({ error: { httpCode: st, message: detail } }, 'transitionIssue', key);
}
const b = (r.body && typeof r.body === 'object') ? r.body : {};
const avail = (Array.isArray(b.transitions) ? b.transitions : []).filter(t => t && t.id && t.name);
const want = String(req.transition).toLowerCase();
const match = avail.find(t => String(t.name).toLowerCase() === want);
if (!match) {
  // The provider's vocabulary goes to the worker log (the classifier convention).
  console.log(`lib-jira transitionIssue for ${key} - available transitions :: ${avail.map(t => t.name).join(' | ').slice(0, 300)}`);
  fail(`transitionIssue for ${s(key, 64)} — transition '${s(req.transition)}' is not available from the issue's current status — see the worker log for the available set`);
}
return [{ json: { issue_key: key, transition_id: match.id, transition_name: match.name } }];
""")

ret_transition = ("""// transitionIssue step 2/2 (Central Command, 2026-07-20) — the POST outcome. 2xx (Jira
// answers 204) means the transition landed; the envelope echoes the RESOLVED canonical
// name from `Resolve transition` (cross-branch .first(0, 0), the lib convention).
""" + CLASSIFIER + """const req = $('Load jira config').first(0, 0).json;
const key = req.issue_key;
const resolved = $('Resolve transition').first(0, 0).json;
const r = $input.first().json;
if (r && r.error !== undefined && r.statusCode === undefined) classifyAndThrow(r, 'transitionIssue', key);
if (!r || typeof r !== 'object' || r.statusCode === undefined) fail(`transitionIssue — unexpected response shape for ${s(key, 64)} (no statusCode) — refusing to report success`);
const st = r.statusCode;
if (st >= 200 && st < 300) {
  return [{ json: { ok: true, kind: 'jira', operation: 'transitionIssue', issue: { issue_key: key, transition: resolved.transition_name } } }];
}
""" + NON2XX_DETAIL + """
classifyAndThrow({ error: { httpCode: st, message: detail } }, 'transitionIssue', key);
""")

ret_link = ("""// linkIssues (Central Command, 2026-07-20) — POST /rest/api/3/issueLink; the Jira node v1
// has no link op. Direction contract: from_key is the OUTWARD side ('blocks'), to_key
// the INWARD side ('is blocked by'). 2xx (Jira answers 201) means the link landed; the
// envelope echoes the validated request.
""" + CLASSIFIER + """const req = $('Load jira config').first(0, 0).json;
const key = req.from_key;
const r = $input.first().json;
if (r && r.error !== undefined && r.statusCode === undefined) classifyAndThrow(r, 'linkIssues', key);
if (!r || typeof r !== 'object' || r.statusCode === undefined) fail(`linkIssues — unexpected response shape for ${s(key, 64)} (no statusCode) — refusing to report success`);
const st = r.statusCode;
if (st >= 200 && st < 300) {
  return [{ json: { ok: true, kind: 'jira', operation: 'linkIssues', link: { from_key: req.from_key, to_key: req.to_key, link_type: req.link_type } } }];
}
""" + NON2XX_DETAIL + """
classifyAndThrow({ error: { httpCode: st, message: detail } }, 'linkIssues', key);
""")

new_nodes = [
    http_node("Get transitions (Jira REST)", "GET",
              "=" + HOST + "/rest/api/3/issue/{{ $json.issue_key }}/transitions",
              None, [300, 1140]),
    code_node("Return: getTransitions", ret_get_transitions, [560, 1140]),
    http_node("Fetch transitions (transitionIssue)", "GET",
              "=" + HOST + "/rest/api/3/issue/{{ $json.issue_key }}/transitions",
              None, [300, 1300]),
    code_node("Resolve transition", resolve_transition, [520, 1300]),
    http_node("Do transition (Jira REST)", "POST",
              "=" + HOST + "/rest/api/3/issue/{{ $json.issue_key }}/transitions",
              "={{ JSON.stringify({ transition: { id: $json.transition_id } }) }}",
              [740, 1300]),
    code_node("Return: transitionIssue", ret_transition, [960, 1300]),
    http_node("Link issues (Jira REST)", "POST",
              HOST + "/rest/api/3/issueLink",
              "={{ JSON.stringify({ type: { name: $json.link_type }, outwardIssue: { key: $json.from_key }, inwardIssue: { key: $json.to_key } }) }}",
              [300, 1460]),
    code_node("Return: linkIssues", ret_link, [560, 1460]),
]
nodes.extend(new_nodes)

# ── 3. Switch: three new rules (before the fallback, which stays last) ───────
route = node("Route (operation)")
values = route["parameters"]["rules"]["values"]


def rule(op):
    return {
        "conditions": {
            "combinator": "and",
            "conditions": [{
                "id": op,
                "leftValue": "={{ $json.operation }}",
                "operator": {"operation": "equals", "type": "string"},
                "rightValue": op,
            }],
            "options": {"caseSensitive": True, "leftValue": "",
                        "typeValidation": "strict", "version": 2},
        },
        "outputKey": op,
        "renameOutput": True,
    }


for op in ("getTransitions", "transitionIssue", "linkIssues"):
    values.append(rule(op))

# ── 4. Connections: insert the three chains before the fallback entry ────────
route_main = conns["Route (operation)"]["main"]
assert len(route_main) == 8, f"expected 8 route outputs, got {len(route_main)}"
unmatched = route_main.pop()


def to(name):
    return [{"node": name, "type": "main", "index": 0}]


route_main.append(to("Get transitions (Jira REST)"))
route_main.append(to("Fetch transitions (transitionIssue)"))
route_main.append(to("Link issues (Jira REST)"))
route_main.append(unmatched)

conns["Get transitions (Jira REST)"] = {"main": [to("Return: getTransitions")]}
conns["Fetch transitions (transitionIssue)"] = {"main": [to("Resolve transition")]}
conns["Resolve transition"] = {"main": [to("Do transition (Jira REST)")]}
conns["Do transition (Jira REST)"] = {"main": [to("Return: transitionIssue")]}
conns["Link issues (Jira REST)"] = {"main": [to("Return: linkIssues")]}

# ── 5. Contract sticky: document the additions ───────────────────────────────
note = node("note_contract")
note["parameters"]["content"] += (
    "\n\n**Central Command additions (2026-07-20 — cc-jira-facade / JiraExpert phase B):**\n"
    "`getTransitions`  : `issue_key` → `transitions:[{id,name,to_status}]`, `count` (read)\n"
    "`transitionIssue` : `issue_key` · `transition` (NAME, matched case-insensitively "
    "against the issue's available transitions; unavailable → fail with the set in the "
    "worker log) → `issue:{issue_key, transition}`\n"
    "`linkIssues`      : `from_key` · `to_key` · `link_type` (allowlist Blocks|Relates|"
    "Duplicate) — **direction: `from_key` is the OUTWARD side** (`from` *blocks* `to`) "
    "→ `link:{from_key,to_key,link_type}`\n"
    "All three raw REST (the Jira node v1 has none of them), same envelope + shared "
    "classifier + op-and-key failure contract. Mutating ops are called ONLY by the "
    "Central Command Executor downstream of its approval gate (via cc-jira-facade)."
)

body = {"name": wf["name"], "nodes": nodes, "connections": conns,
        "settings": wf.get("settings") or {}}
out = call("PUT", f"/workflows/{WF_ID}", body)
print("updated:", out.get("id"), "versionId:", out.get("versionId"),
      "active:", out.get("active"))
