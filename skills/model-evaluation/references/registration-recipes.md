# Registration recipes — provider prefixes, format detection, per-model knobs

Read `skills/litellm/references/models-and-model-info.md` first for the
general model/`model_info` shape. This file is specifically about **getting a
new or private gateway registered correctly the first time** — the wrong
prefix or a missing knob is a wiring bug, not a capability finding, and the
probe (`probe-protocol.md`) cannot tell the two apart until registration is
right.

## Provider prefixes

- **`openai/<model>` + `api_base`** — the gateway speaks OpenAI
  chat-completions (`POST <api_base>/chat/completions`). `api_base` must
  include the `/v1` segment (LiteLLM/the OpenAI client appends
  `/chat/completions`, not `/v1/chat/completions`). Verified:
  <https://docs.litellm.ai/docs/providers/openai_compatible>.
- **`anthropic/<model>` + `api_base`** — the gateway speaks Anthropic's
  native `/v1/messages`. When the gateway's path is the exact final URL (no
  further suffix should be appended), set
  `LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX` — documented on the same page.
  Verified: <https://docs.litellm.ai/docs/providers/anthropic>.
- **`openai/chat_completions/<model>`** — forces LiteLLM's Responses→chat
  bridge. Needed for any caller that always drives extraction through the
  Responses API (`client.responses.parse`) against a backend that only
  implements `/v1/chat/completions` — see CLAUDE.md's graphiti-llm bite mark
  for the concrete failure this fixes (a plain `openai/` prefix passes the
  request through and the `text.format` json_schema is silently dropped;
  prose comes back with HTTP 200). Response API background:
  <https://docs.litellm.ai/docs/response_api>.
- **`hosted_vllm/<model>`** — a self-hosted vLLM OpenAI-compatible server.
  Same shape as `openai/` but keeps vLLM-specific defaults distinct in
  LiteLLM's routing.

## Detecting an unknown gateway's format

Don't guess — probe cheaply before registering for real:

1. `GET <api_base>/models` (or `<api_base>/v1/models`) — a populated
   `{"data": [...]}` response is the OpenAI-style model-list convention.
2. `POST <api_base>/chat/completions` with a minimal
   `{"model": "...", "messages": [...]}` body vs `POST
   <api_base>/v1/messages` with a minimal `{"model": "...", "max_tokens": 16,
   "messages": [...]}` body **and** an `anthropic-version` header — whichever
   returns a well-formed completion (not a 404/405) is the real format.
3. **Read the error shape** on the one that fails: an OpenAI-format gateway
   returns `{"error": {"message": ..., "type": ...}}`; Anthropic-format
   returns `{"type": "error", "error": {"type": ..., "message": ...}}`. A
   404 with an HTML body (not JSON at all) usually means the path segment is
   wrong (missing `/v1`), not that the format guess was wrong — try the
   sibling path before concluding the gateway speaks neither shape.

Once the format is known, register per the prefixes above, then run
`litellm_probe_model` — step 1 of the probe (plain chat) re-confirms the
wiring for free before any capability check runs.

## `model_info.base_model` — borrowing the public cost map

Setting `model_info.base_model` to the public model's canonical name (e.g.
`base_model: "gpt-4o"` for a gateway serving a fine-tune or a differently
named deployment of it) gives LiteLLM's built-in cost-map defaults for
pricing and, where LiteLLM's static tables know them, context/output limits —
without hand-entering every field. Treat it as a **default**, not a
guarantee: the probe is still what tells you whether *this* deployment
actually matches the public model's behavior (see `public-library.md` for
why the public number is a hypothesis).

## `litellm_params` knobs for a model that rejects normal params

- **`additional_drop_params`** — a list of param names to silently strip
  before the request reaches the backend, for a model that 400s on a param
  LiteLLM sends by default. Verified:
  <https://docs.litellm.ai/docs/completion/drop_params>.
- **`allowed_openai_params`** — the inverse: an explicit allowlist of which
  OpenAI-shaped params are passed through for this deployment, for backends
  that error on anything outside a narrow set. Same doc as above.
- **`supports_system_message: false`** — declare when a model rejects a
  `system` role turn outright (check 2 of the probe, `system_messages`,
  is exactly how you'd catch this) so callers stop sending one instead of
  hitting a 400 every time.

## Per-model health-check knobs

Distinct from `litellm_probe_model` — these govern LiteLLM's own periodic
`/health` sweeps (see `skills/litellm/references/operations.md` for
cheap-vs-expensive health reads):

- **`mode`** — what kind of health check applies (`chat`, `embedding`,
  `rerank`, …); `model-preferences.yaml`'s `qwen3-rerank-local` sets
  `mode: rerank` for exactly this reason.
- **`health_check_max_tokens`**, **`health_check_reasoning_effort`**,
  **`health_check_timeout`** — override the health check's own request
  shape and timeout independently of what live traffic uses, so a
  thinking-heavy or slow-swapping model doesn't fail health checks it would
  otherwise pass under normal traffic. Verified:
  <https://docs.litellm.ai/docs/proxy/health>.

## House rules (unchanged, worth repeating here)

- **Credential by name.** Never a raw `api_key` in a proposal — reference a
  stored credential (`litellm_params.litellm_credential_name`). See
  `skills/litellm/references/keys-and-teams.md`.
- **`update_model` over delete+add**, always, for an existing row — see
  `skills/litellm/references/models-and-model-info.md`. Registering a
  genuinely new alias is the only time `add_model` is right.
- **Never a key in a proposal.** This applies to registration exactly as it
  applies to any other litellm.* proposal.
