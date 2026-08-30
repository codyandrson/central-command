# The probe protocol — register → probe → declare → record

A model behind a private gateway is a hypothesis, not a known quantity: its
real context, output cap, tool calling, vision, reasoning and structured
output may not match the public card, and the gateway itself may speak either
the OpenAI or the Anthropic wire format. Guessing any of this into
`model_info` is how an undeclared flag reads as false to the rest of the
fleet (see below). The fix is to measure, then declare only what was
measured.

## The four steps

1. **Register with what is certain.** `litellm.add_model` with the provider
   prefix for the gateway's real format (see `registration-recipes.md`), the
   credential by name, and `mode: chat`. Nothing about capability yet — that
   comes from the probe, not a guess dressed as a declaration.
2. **Probe.** `litellm_probe_model(model, measure_context=False)`. Evidence
   only — it never writes.
3. **Declare** the diff it found via `litellm.update_model`.
4. **Record.** `propose_skill_doc` onto this skill (`model-evaluation`) with
   the record template from `public-library.md` — the next session, and the
   next install, inherits the measurement instead of re-running it.

A probe result is never itself a write. Only step 3 touches the proxy, and
only through the normal gated `litellm.update_model` proposal.

## `litellm_probe_model(model, measure_context=False)`

Implemented in `central_command/integrations/litellm.py::probe_model` (read it
alongside the constants just above it — `PROBE_MAX_TOKENS`,
`PROBE_CONTEXT_CEILING`, `PROBE_CONTEXT_STEPS`, `_PROBE_PNG`, `_PROBE_TOOL`,
`_PROBE_SCHEMA` — before trusting a description of what it sends). Every
check rides through the proxy under the ADMIN key (a freshly added model is
not yet scoped to any agent key), one real small request per capability,
`max_tokens=1024` unless noted. It returns:

- `declared` — the model's current `model_info` for the `CAPABILITY_FIELDS`
  vocabulary (see `skills/litellm/references/models-and-model-info.md`).
- `observed` — one entry per check, `{"ok": True|False|None, "detail": ...}`.
- `suggested_model_info` — the declared→observed **diff**, ready to paste
  into a `litellm.update_model` proposal's `model_info`.
- `notes` — free-text findings that don't fit a single field (e.g. a 404
  hint, or "declared tool-capable but produced no tool_calls").

### Reading `observed[<check>].ok`

- **`True`** — the capability was demonstrated (content came back, or the
  right shape appeared: a `tool_calls` entry, valid JSON, non-empty vision
  answer, a `reasoning_content` field).
- **`False`** — the request failed outright (non-200) or came back 200 with
  the wrong shape (e.g. no `tool_calls` when one was forced).
- **`None`** — **inconclusive**, not "no". The one production case: the
  model returned 200 with `finish_reason == "length"` and empty content — the
  **output budget ran out before an answer**, which on a thinking model means
  it spent the whole 1024-token budget reasoning and never got to the reply.
  `_verdict()` is the one place this is decided; every check up through
  vision routes through it for exactly this reason, so a thinking model isn't
  scored "no vision" just because it thought too long. `max_output_tokens`
  and `max_input_tokens` are also always `None` — they report a `value`, not
  a yes/no verdict (see below).

### What each check sends, in probe order

| # | check | request | what a positive looks like |
|---|---|---|---|
| 1 | `chat` | `{"role":"user","content":"Reply with the single word OK."}` | non-empty content. **Gates everything else** — a non-200 here ends the battery immediately (every other check would only repeat the same failure) and returns with empty `suggested_model_info` |
| 2 | `system_messages` | the same message with a system turn prepended | `st == 200` |
| 3 | `tool_choice` | a forced call: `tool_choice={"type":"function","function":{"name":"get_weather"}}` | a `tool_calls` entry present |
| 4 | `function_calling` + `parallel_function_calling` | `tool_choice="auto"`, asked for weather in two cities in one turn | ≥1 call for `function_calling`; ≥2 calls for `parallel_function_calling` |
| 5 | `response_schema` | `response_format={"type":"json_schema", "json_schema": <strict answer:int schema>}`, "What is 2+2? Answer as JSON." | reply parses as JSON containing `answer` |
| 5b | `json_object` | looser `response_format={"type":"json_object"}` | reply parses as any JSON object |
| 6 | `vision` | a 1×1 red PNG data URL + "What color is this image? One word." | non-empty content (any answer counts; a 4xx/5xx is a no) |
| 7 | `reasoning` | `reasoning_effort="low"`, "Is 17 prime? One word." | `msg.get("reasoning_content") or msg.get("reasoning")` present — **note this is a separate fact from "the request was accepted"** |
| 7b | `pdf_input` | an OpenAI `file` content part carrying a blank one-page PDF (`_PROBE_PDF`) + "How many pages does this document have?" | non-empty content (a 4xx/5xx is a no) — feeds `suggested_model_info["supports_pdf_input"]` directly, both directions |
| 7c | `thinking_template` (only when `declared["thinking_mechanism"]` is `None`) | the OK prompt with `chat_template_kwargs={"enable_thinking": true}` | `reasoning_content`/`reasoning` present — see below, this is a **note**, never a `suggested_model_info` write |
| 8 | `max_output_tokens` | the same OK prompt with `max_tokens=1_000_000` | see below — always inconclusive |
| 9 | `streaming` | the OK prompt with `stream=True` | 200 and `"data:"` appears in the first 200 chars of the body |
| 10 | `max_input_tokens` (opt-in, `measure_context=True`) | see the bisect section | reports a `value`, never ok/false |

### The 404-on-chat meaning

If check 1 fails with HTTP 404, `notes` says so explicitly: check that
**`api_base` ends in `/v1`** and that **the provider prefix matches the
gateway's actual format** (`openai/` for a chat-completions-speaking gateway,
`anthropic/` for one that speaks `/v1/messages`). A 404 here is a wiring
mistake, not a capability finding — fix registration before re-probing
anything else.

### "Reasoning accepted but no `reasoning_content`"

Check 7 can come back `ok=False` with the request itself succeeding (200):
the endpoint silently ignored `reasoning_effort` rather than rejecting it.
`observed["reasoning"]["detail"]` says this explicitly: *"accepted, no
reasoning_content in the reply — a template-driven model (qwen_template)
needs its mechanism declared by hand"*. This is the signal to **hand-declare**
two custom `model_info` fields the probe cannot infer on its own:

```
thinking_mechanism: qwen_template
thinking_levels: ["off", "low", "medium", "xhigh"]
```

(the exact vocabulary `runtime/models.py::_thinking_body` reads — see its
`_ANTHROPIC_EFFORT` map and the `qwen_template` branch, and
`deploy/pi/litellm/model-preferences.yaml`'s comments on `qwen3.8-27b`/
`cc-default`: their chat template takes `enable_thinking`/`reasoning_effort`
through `chat_template_kwargs`, and there is **no** `reasoning_effort: "none"`
for that template — the template raises and every request 500s, so `"off"`
must mean `enable_thinking: false`, not an omitted field). If instead the
gateway is genuinely OpenAI-`reasoning_effort`-native (Anthropic-style), the
matching declaration is `thinking_mechanism: anthropic` with
`thinking_levels: ["off", "low", "medium", "xhigh"]` (`"off"` **quoted** —
bare `off` parses as YAML `false`). Do not declare `supports_reasoning: true`
in place of this — that flag says "the request format is accepted", not "the
model tells you it thought."

### `thinking_template` — a note, never a declaration

Check 7c only runs when `declared["thinking_mechanism"]` is `None` — a model
that already declares a mechanism doesn't need this guess, so `observed` has
no `thinking_template` key at all in that case. When it does run and comes
back with `reasoning_content` present, the probe adds a **note** —
*"template-driven thinking works — consider declaring `thinking_mechanism:
qwen_template` (plus `thinking_levels`) so the runtime can control it"* — and
stops there. It never writes `thinking_mechanism` into
`suggested_model_info`: declaring the mechanism changes how
`runtime/models.py::_thinking_body` **controls** the model on every future
call, which is a decision with behavioral consequences the operator makes
deliberately, not a fact the probe can respond to a diff and be done with
(unlike a `supports_*` flag, which is purely descriptive).

### `max_output_tokens` — "accepted 1,000,000 → no ceiling"

Check 8 asks for `max_tokens=1_000_000`. Two outcomes, and **both are
`ok=None`** — this check never reports a capability, only a measurement (or
its absence):

- **HTTP 200**: the gateway accepted it. `detail` reads *"max_tokens=1000000
  accepted — no request-time ceiling; declare from the model card"* — i.e.
  the endpoint enforces no request-side cap, so `max_output_tokens` has to
  come from the model card or the operator, not from this probe.
- **non-200**: the error text is scraped for 3+ digit numbers (excluding the
  1,000,000 you sent) and the largest is reported as `value` — often the
  real ceiling the gateway just told you in its rejection message.

### The context bisect (`measure_context=True`)

Opt-in, off by default. Runs ≤`PROBE_CONTEXT_STEPS` (10) requests, each
`max_tokens=1`, binary-searching between 1024 and
`min 2048..context_ceiling` (default `PROBE_CONTEXT_CEILING = 262144`) filler
tokens (`"0 " * mid`) for the largest prompt the endpoint accepts. Reports
`{"ok": None, "value": <largest accepted>, "detail": "largest accepted ≈N
filler tokens (ceiling searched: M)"}` — `ok` is always `None` here too,
because this is a measurement, not a verdict. **Expensive on a queued local
model**: up to 10 full requests against whatever is behind the gateway, which
on a shared llama-swap-style slot means up to 10 turns through the FIFO
queue. Only ask for it when the input ceiling is actually in question.

## `suggested_model_info` — the declared→observed diff

Built once, at the end of `probe_model`, from only the checks the probe can
actually speak to:

```python
flags = {
    "supports_system_messages": observed["system_messages"]["ok"],
    "supports_tool_choice": observed["tool_choice"]["ok"],
    "supports_function_calling": observed["function_calling"]["ok"],
    "supports_parallel_function_calling": observed["parallel_function_calling"]["ok"],
    "supports_response_schema": observed["response_schema"]["ok"],
    "supports_vision": observed["vision"]["ok"],
    "supports_reasoning": observed["reasoning"]["ok"],
    "supports_pdf_input": observed["pdf_input"]["ok"],
}
```

Only entries where `v is not None and declared.get(k) != v` survive into
`suggested_model_info` — an inconclusive (`None`) check contributes nothing,
and an already-correct declaration is not re-suggested. `mode: "chat"` is
added if undeclared. `max_output_tokens`/`max_input_tokens` are added from
`observed[...]["value"]` when present and different from what's declared. One
extra note fires if `supports_function_calling` came back `False` while the
model was already *declared* tool-capable: *"declared tool-capable but
produced no tool_calls — re-run before trusting either"* — a contradiction
worth a second look, not an automatic overwrite.

**This dict is the payload for a `litellm.update_model` proposal's
`model_info` — nothing more, nothing less.** The probe is evidence; the
proposal (and the operator's approval) is the write.

## A probe is evidence, never a write

`probe_model` calls nothing but `GET /model/info` and `POST
/v1/chat/completions` under the admin key — no `add_model`, `update_model`,
or `delete_model` call anywhere in it. Running the probe as often as needed
costs tokens and provider calls, never proxy state. What you do with its
output — declare it via `litellm.update_model` — still goes through the
normal propose → operator-approves → Executor-applies gate like every other
`litellm.*` capability.

## Two different capability vocabularies — declare the whole fleet

Central Command's own runtime reads a narrower set than LiteLLM's own
aggregate view does, and they disagree on what "undeclared" means:

- **Central Command reads**: `supports_vision` (the attachment gate,
  `litellm.model_accepts_images`), `thinking_mechanism`/`thinking_levels`
  (`runtime/models.py::_thinking_body`), `max_input_tokens` (context-pressure
  tracking, `runtime/context.py`). For these, **`None`/absent means "nobody
  said"** — treated as false/off, not as unknown-therefore-assume-yes.
- **LiteLLM's own `/model_group/info` aggregate** coerces the *same* absence
  to **`False`** outright — it has no third state.

Both readers punish an undeclared model identically in practice (no
capability), but for different reasons, and neither will ever surface a
real capability you simply forgot to write down. So: **declare the whole
fleet**, not just the model you're currently probing — an old model that
predates this protocol and was never probed reads as capability-less to both
consumers until someone runs the battery on it too.

## Non-chat aliases — a battery each, not the chat battery

Embedding and rerank aliases are not chat models, so the chat battery above
never runs against them — `probe_model` dispatches on `battery` (an explicit
override) or, absent that, the declared `mode`, before sending a single chat
request:

- **`battery="embedding"`** (or a declared `mode: embedding`): one `POST
  /v1/embeddings` with `{"input": "probe"}`. `observed["embedding"]` reports
  `ok` (200 and a non-empty vector) and `value` = the vector's dimension
  count — the number to declare as the embedding size. No chat request is
  made and `suggested_model_info` is always `{}` (nothing in
  `CAPABILITY_FIELDS`'s chat vocabulary applies to an embedder).
- **`battery="rerank"`** (or a declared `mode: rerank`): one `POST
  /v1/rerank` with a query and two documents. `observed["rerank"]["ok"]` is
  true when the proxy answers 200 with a non-empty `results` list.
  `suggested_model_info` is again always `{}`.
- Use `battery=` explicitly for a model whose `mode` isn't declared yet —
  which is exactly the state a fresh registration is in before its first
  probe. Once `mode` is declared, the plain `litellm_probe_model(model)` call
  dispatches on its own.

## Aliases the chat battery cannot probe

- **A Responses-bridge alias** (`openai/chat_completions/<model>`, e.g.
  `graphiti-llm`) serves only `/v1/responses` — the plain chat check 404s
  with "no router for requested model" and the probe's note says so. Probe
  the underlying model's own alias; the bridge alias inherits its answers.
- **A registered-but-unauthenticated model** fails the chat gate with a 401
  naming the missing provider key — a credential problem, not a capability
  one (the dormant `claude-*` entries read exactly this way on a fresh
  install until an Anthropic credential is stored).

