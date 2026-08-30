# Virtual keys, credentials, teams and spend

## Virtual keys

A virtual key is the proxy's unit of **attribution and scope**. Fields you can
propose: `key_alias` (human name), `models` (list of aliases the key may call;
omit = all), `metadata` (object), `tags` (list), `duration` (lifetime, e.g.
`'30d'`).

- The proxy returns the **plaintext key once**. The control plane never retains
  or shows it — the operator captures it from the LiteLLM UI.
- `litellm.delete_key` deletes **by alias** (the control plane holds no
  plaintext). `litellm.update_key` is addressed by the **token hash/id** from
  `litellm_list_keys`, and is patch semantics.
- Deleting a key that carries real traffic **breaks that traffic** until it is
  re-issued. Say so in the intent if that is a risk.
- **Do not set budgets or rate limits.** `max_budget`, `budget_duration`,
  `rpm_limit`, `tpm_limit`, `model_max_budget` are deliberately out of scope
  here — advise on them, do not propose them.

## The Enterprise-tags 403

**Creating a key with a non-empty top-level `tags` parameter returns 403 on this
deployment** — the UI form's top-level `tags` param is Enterprise-gated on the
free tier, and the litellm container log shows the 403 (diagnosed 2026-07-22).

- **Workaround, and why our own path never hit it:** the gated `create_key`
  capability stores tags under **`metadata.tags`**, which is *not* gated.
- **If the operator is creating a key in the UI: leave the tags field empty.**
  Attribution rides the key alias anyway.

More generally: **Enterprise features are not licensed here.** Endpoints like
`/global/spend/report` return 400 `"must be a LiteLLM Enterprise user"`. Don't
propose or rely on them; use the non-enterprise reads (`/spend/logs`) instead.

## Credentials — the key-safe way to attach auth

LiteLLM has its own encrypted **credential store**, separate from per-model
keys. A credential is created **once by the operator** — you never create one,
because that means handling a secret, and **secrets never ride a proposal**.

A model authenticates by **referencing** a stored credential by name:

```
litellm_params.litellm_credential_name = '<credential name>'
```

The stored credential SET changes over time (the 2026-08-29 clean install
recreated the proxy database, taking the old `anthropic-main` with it), so
**always read `litellm_list_credentials` for the current names** — never a
remembered one. The key-safe way to register or re-point a model against a
stored credential is:

- `add_model` with `'extra': {'litellm_credential_name': '<credential name>'}`, or
- `update_model` with `'litellm_params': {'model': 'anthropic/…',
  'litellm_credential_name': '<credential name>'}`

and **no `api_key`**. A model referencing a named credential can never be
dropped into an unauthenticated state by a re-register, because the proposal
carries a reference, not a secret. Prefer this over relying on the Executor's
fallback key injection.

Call `litellm_list_credentials` to confirm a name exists. **If the credential
you need is absent, say so and ask the operator to create it — do not invent a
name.**

## The salt key

`LITELLM_SALT_KEY` encrypts the stored provider credentials and virtual keys.
**Never propose changing it while models exist** — a restore or a re-key under
a different salt leaves the rows present and undecryptable. This is also a
deployment-wide rule (`deploy/k3s/`), not just a LiteLLM one.

## Teams, users, orgs

Hierarchy: **Organization → Team → User → Key.** Keys inherit team models and
limits. Team/user `models` accept access-**group** names, so membership changes
don't touch keys.

- **Actionable:** `litellm.create_team` (`team_alias`, optional `models`),
  `litellm.delete_team` (by `team_id` from `litellm_list_teams`). No budgets.
- **Advisory only:** orgs, users, and team **membership**. Advise accurately;
  say plainly that the capability isn't built, rather than fabricating one.

## Spend attribution

- Central Command's traffic is attributed to a virtual key aliased **`central_command`**.
- **Per-agent keys** are the designed refinement: `create_key` with a clear
  alias (e.g. `cc-inbox-triage`) scoped to the models that agent uses; the
  **operator** then captures the plaintext once and sets
  `CC_LLM_PROXY_KEY_<AGENT_ID>` in Central Command's env, and the runtime picks it
  up. You propose the key; the operator wires it. Until then all agents share
  the `central_command` key.
- `litellm_get_spend` reads recent spend aggregated per (model, key) from the
  request log — a **rolling window, not a ledger**.
- Underneath it is `/spend/logs` (filter by date/request_id). `api_key`/
  `user_id` filters exist, but **verify the exact date format on this build**
  before relying on them: a live probe returned 0 rows for a plain
  `YYYY-MM-DD` range.
- Per-call cost is also returned in the **`x-litellm-response-cost`** response
  header.
- Cost comes from LiteLLM's model-price map unless overridden by custom pricing
  in `model_info`.

Measured context: agent traffic was **$4.69 over 14 days**. Cost is not the
constraint here; loops and context are. Don't propose spend controls as if it
were.
