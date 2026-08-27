# Marketplace macros: discovery before authoring

`storage-format.md` gives verified recipes for built-in macros — parameter
names sourced from Atlassian's own docs. **Marketplace app macros are
different in kind, not just detail**: Table Filter/Transformer/Spreadsheet,
the Forms family, Epic Sum Up gadgets, Scroll export macros, the
test-management report family, BibTeX, LaTeX, and Markdown are each a
separate vendor's product. Their storage-format parameter names are set by
that vendor, change across app versions, and are not reliably documented in
any place this skill can point to and trust. **The Jira-gadget bite mark
applies unchanged: a wrong parameter key doesn't error, it renders broken
silently, on a page someone may already be reading.**

## Which macros this applies to

From `instance-profiles.md`'s `work-server-9.2` categories:

- **Tables & data apps** — Table Filter, Table from CSV, Table from JSON,
  Table Spreadsheet(+Include), Table Excerpt(+Include), Table Transformer,
  Pivot Table, Spreadsheet from Table, Chart from Table, AI Table, BibTeX
  Display Table/Referencing.
- **Forms** — the Forms container and every field macro (Input, Dropdown,
  Checkbox/Radio group, Datepicker, Textarea, User Picker, Star rating, …),
  Forms Responses Table.
- **Scroll export** — every Scroll Bookmark/Content Block/Export
  Button/Ignore/Indexterm/Landscape/Only/Pagebreak/Pagetitle/Table
  Layout/Title macro.
- **Test-management reports** — the whole "Test cases by …", "Test
  execution … by …", "Issues reported during testing …" family.
- **LaTeX Formatting**, **Markdown** / **Markdown From URL** — these render
  from a body or a URL, but their exact parameter names (e.g. whether
  Markdown takes a `format` or `source` parameter) are app-specific too.
- **Multiexcerpt / Multiexcerpt Include** — ambiguous for a different
  reason: see `storage-format.md`'s section on it. Same discovery path
  applies.
- **draw.io** beyond the two-step already documented in `storage-format.md`
  (e.g. board-diagram-specific parameters) — the two-step's `diagramName`/
  `attachment` parameters are confirmed; anything beyond that is discovery
  territory too.

Built-in macros (layout, panels, TOC, Jira, Page Properties, etc.) are NOT in
this class — those have verified recipes in `storage-format.md`.

## The discovery procedure

1. **Find a page that already uses the macro**, via CQL's `macro` field
   (confirmed against Atlassian's CQL field reference for Server/Data
   Center, not Cloud-only):

   ```
   macro = "table-filter"
   ```

   The value is the macro's `ac:name`, not its display name — display names
   in the macro browser ("Table Filter") don't always match the storage
   `ac:name` verbatim (compare `chart` vs. "Chart from Table"'s own name).
   If a guessed name comes back empty, that's weak evidence only — try the
   display-name-derived slug, and if still empty, search the macro's usage
   report in Confluence admin (`instance-profiles.md` notes this exists) or
   fall back to a plain `text ~` search for pages likely to use it.

2. **Read that page's real body**, not a search snippet:
   `confluence_get_page(..., expand="body.storage")`. CQL search results
   don't reliably include the raw macro XML — go to the direct read.

3. **Copy the whole `<ac:structured-macro>` block**, including its
   `ac:schema-version` and `ac:local-id`/`ac:macro-id` attributes if
   present — some apps' rendering depends on schema version, and copying an
   incomplete skeleton is exactly the "invented key" failure this doctrine
   exists to avoid.

4. **Adapt values only.** Change the table reference, the JQL, the field
   labels — never add, rename, or remove an `ac:parameter ac:name`. An
   undeclared or misspelled parameter key is silently ignored by the macro,
   not rejected — the write succeeds and the feature you wanted (a filter,
   a computed column, a form validation rule) is just absent.

5. **If no existing page uses the macro anywhere the calling account can
   see**, that is a capability gap, not a green light to guess:
   `declare_gap` and let the operator author the first instance by hand (or
   confirm the app is even installed and configured) — the same escalation
   this skill already prescribes for a macro outside the active profile.

## The sampler-page harvest: discovery once, at onboarding

The per-use procedure above assumes some page somewhere already uses the
macro. Onboarding is the moment to stop depending on that: a **macro sampler
page** turns the whole marketplace surface into instance-verified recipes in
one sitting (operator-proposed 2026-08-25; wired into `/setup`'s Phase 6
Confluence step).

Why the operator authors it and not the agent: the Confluence macro browser
UI generates **correct-by-construction** storage XML — real parameter names,
real `ac:schema-version`, the installed app's own defaults. An agent
authoring the same macros from guesses produces the silent-breakage page this
whole doctrine exists to prevent. The division of labor:

1. **Agent/Claude Code self-serves the built-ins**: author the verified
   `storage-format.md` recipes onto one test page in a scratch space, then
   round-trip `expand=body.view` and scan the rendered HTML for
   unknown-macro placeholder markup. This catches *unrenderable* macros
   only — a macro that renders wrong (empty grid, ignored parameter) passes
   this check, which is exactly why it doesn't extend to marketplace guesses.
2. **Operator builds the sampler for the rest**: hand them the specific
   list of macros with no verified recipe (the families above), they insert
   each via the macro browser with simple defaults onto one page ("Central Command Macro
   Sampler"), and name the page.
3. **Harvest**: `confluence_get_page(..., expand="body.storage")` on the
   sampler; extract every `<ac:structured-macro>` block whole (schema
   version and all); record each as an instance-verified recipe — in an
   instance-profile note or a skill doc — stamped with the harvest date and
   the instance version it came from.
4. **Re-harvest is the upgrade path**: a marketplace app install or upgrade
   can change a macro's parameter surface; add/refresh the macro on the
   sampler page and harvest again. The stamp from step 3 is what tells you
   the recipe predates the app version you're now on.

Governance: during `/setup` this runs as the operator's own hands (Claude
Code + operator present, before any agent exists). After onboarding, an
agent refreshing the harvest creates pages through `confluence-propose` like
any other write — the sampler page is not an exception to the gate.

## Why this can't collapse into a verified recipe

A built-in macro's shape is fixed by Confluence core and documented by
Atlassian at the same URL for every Server/DC install running that version.
A marketplace macro's shape is fixed by whichever version of that app is
installed on **this** instance, and Atlassian's docs know nothing about it.
Writing a "Table Filter recipe" here would either be copied from someone
else's app version (silently wrong the moment it diverges) or fabricated
from the macro's display description (silently wrong from the start) — both
are the guess this doctrine exists to replace with a five-minute read.

## Macro selection: common intent → macro

| Intent | Macro | Notes |
|---|---|---|
| Status callout / colored lozenge | `status` or `panel` | `status` = one-word lozenge with a fixed color set; `panel` = free-form box, verified recipe in `storage-format.md`. |
| Collapsible detail | `expand` | Verified recipe. |
| Live single Jira issue | `jira` (key form) | Verified recipe; needs an app link (`storage-format.md`). |
| Live Jira query results table | `jira` (jqlQuery form) | Same app-link caveat. |
| Diagram | `drawio` two-step | Verified — upload attachment, then embed by filename. |
| Tabular data with filtering/sorting/computed columns | Table Filter / Table Transformer / Table Spreadsheet | Marketplace — discovery-first, this file. |
| Form for structured input | Forms family | Marketplace — discovery-first. |
| One-row-per-page rollup (contacts, inventories, status pages) | Page Properties + Report | Verified recipe in `storage-format.md`. |
| Document export control (PDF/Word via Scroll) | Scroll export macros | Marketplace — discovery-first, and **display-time only**: they change what an export includes, not what's shown on the live wiki page. |
| Test-coverage / QA report | the Test cases/execution family | Marketplace — discovery-first; only reach for these when the deliverable is genuinely a QA report. |
| Roadmap-looking timeline | **Not** Roadmap Planner | Cannot be authored via storage format at all, any version — see `storage-format.md`'s dedicated section for the three substitutes. |
| Citation list | BibTeX Display Table + BibTeX Referencing | Marketplace — discovery-first. |
| Math/scientific notation | LaTeX Formatting | Marketplace — discovery-first. |
| Rendered Markdown content | Markdown / Markdown From URL | Marketplace — discovery-first despite being "built-in"-sounding; parameter surface not confirmed. |
