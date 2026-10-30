---
paths:
  - "web/**"
  - "central_command/api/nerve_gateway.py"
  - "tests/*wire*"
---

# Cockpit (web/) bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **`@container` must sit on a PARENT of whatever uses `@3xl:`.** A container
  query resolves against an ANCESTOR container, never the element's own — so
  the variant silently never applies on the element that declares the
  container, while its children's variants work. jsdom has no layout engine,
  so no render test can catch this — `web/src/features/container-query-scope.test.ts`
  reads the SOURCE instead.
- **`tsc` does not delete removed sources from `server-dist`.** `npm run
  build` starts with `rm -rf server-dist`, so a full build ships no orphan;
  a bare `tsc` or an incremental dev run does — delete a `server/` module and
  its compiled `.js` ships on after that rebuild, so remove it by hand in the
  same change.
- **The cockpit hand-declares its own interfaces over untyped RPC payloads,
  so a field the gateway never sends is invisible to `tsc` AND to every
  frontend test** — the feature silently never renders. The defence is a
  BACKEND test asserting the wire shape; a frontend test building its own
  payload object proves nothing.
- **`nerve_gateway._COMPOSER_DISABLING` is an ALLOWLIST, not a check.** A new
  `why_not_sendable` refusal code missing from that tuple leaves the cockpit
  rendering an ENABLED message box that `_chat_send` then 409s. Adding a
  refusal code means adding it there too, with a guard test.
- **The cockpit believes push frames, not hope.** The chat spinner clears
  only on `agent/lifecycle` end frames and the panel updates on
  `cc.conversation.ended` / `cc.session.*` events. If you add a new
  turn/close path, push the frames, or the UI lies.
- **The file browser's mutations answer 403 until `FILE_BROWSER_WRITABLE=1`,
  and the override panels refresh on override events** (epic small-fixes,
  COCKPIT-028). `routes/file-browser.ts` is mounted whatever the UI flag says
  and upstream gave its write, rename, move, trash and restore routes no off
  switch, so `routes/cc-file-browser-guard.ts` is middleware on `/api/files/*`
  (the one upstream edit, recorded in `web/VENDORED.md`) that refuses every
  non-read request unless the flag is `1`. A `FILE_BROWSER_ROOT` holding `.env*`,
  `.git` or certificates is refused at config load (the default workspace
  is used instead), so the browser cannot be aimed at a secret. A new route in that file is guarded by the prefix, not by
  remembering. The gate-override hooks (`useOverrides`) subscribe to the
  `cc.override*` push events (400 ms debounce) and refresh silently, so an
  override changed in another tab or by a decision shows without a reload.
  Guarded by `web/server/routes/cc-file-browser-guard.test.ts` and
  `web/src/features/gates/useOverrides.test.tsx`.
