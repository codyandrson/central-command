# Storage format: authoring page bodies and macros

**Storage representation (XHTML)** is the one body format that round-trips
on both Cloud and Server/DC, and the only practical way to author macros
programmatically. Never send ADF — it's Cloud-only and this client doesn't
speak it.

## Base syntax

A macro is:

```xml
<ac:structured-macro ac:name="MACRO_NAME">
  <ac:parameter ac:name="PARAM_NAME">value</ac:parameter>
  <ac:rich-text-body>
    <p>Body content, itself storage-format XHTML.</p>
  </ac:rich-text-body>
</ac:structured-macro>
</ac:structured-macro>
```

Plain XHTML — `<p>`, `<h1>`–`<h6>`, `<table>`/`<tr>`/`<td>`, `<a href="...">`,
`<ul>`/`<li>` — is valid alongside macros anywhere in the body.

**A macro name the active instance doesn't have renders as an "unknown
macro" placeholder — the write itself succeeds with a 200.** There is no
write-time validation of macro names. Compose only from the profile named by
`CC_CONFLUENCE_PROFILE` (`instance-profiles`); anything else is a capability
gap to declare, not a guess to test live.

## Version-increment rule

Every `confluence.update_page` must carry `version = current_version + 1`,
where `current_version` comes from a `confluence_get_page` or
`confluence_page_versions` read taken **this turn**. This is Confluence's
built-in optimistic-concurrency guard — send the wrong number and the write
is rejected outright, not silently applied over someone else's edit.

## Table of Contents

```xml
<ac:structured-macro ac:name="toc">
  <ac:parameter ac:name="maxLevel">3</ac:parameter>
</ac:structured-macro>
```

## Info / Note / Warning / Tip panels

Same shape, different `ac:name`:

```xml
<ac:structured-macro ac:name="info">
  <ac:rich-text-body>
    <p>This page is auto-generated. Edits may be overwritten.</p>
  </ac:rich-text-body>
</ac:structured-macro>
```

Swap `info` for `note`, `warning`, or `tip`.

## Expand (collapsible section)

```xml
<ac:structured-macro ac:name="expand">
  <ac:parameter ac:name="title">Click to expand</ac:parameter>
  <ac:rich-text-body>
    <p>Hidden detail goes here.</p>
  </ac:rich-text-body>
</ac:structured-macro>
```

## Status lozenge

```xml
<ac:structured-macro ac:name="status">
  <ac:parameter ac:name="colour">Green</ac:parameter>
  <ac:parameter ac:name="title">DONE</ac:parameter>
</ac:structured-macro>
```

`colour` is one of Grey, Red, Yellow, Green, Blue (case-sensitive per
Atlassian's own docs).

## Children Display

```xml
<ac:structured-macro ac:name="children">
  <ac:parameter ac:name="depth">2</ac:parameter>
</ac:structured-macro>
```

## Code block

```xml
<ac:structured-macro ac:name="code">
  <ac:parameter ac:name="language">python</ac:parameter>
  <ac:plain-text-body><![CDATA[print("hello")]]></ac:plain-text-body>
</ac:structured-macro>
```

Note: `code` uses `<ac:plain-text-body>` + `CDATA`, not `<ac:rich-text-body>`
— the content is literal text, not nested storage XHTML.

## Section / Column (side-by-side layout)

```xml
<ac:structured-macro ac:name="section">
  <ac:rich-text-body>
    <ac:structured-macro ac:name="column">
      <ac:parameter ac:name="width">50%</ac:parameter>
      <ac:rich-text-body><p>Left column.</p></ac:rich-text-body>
    </ac:structured-macro>
    <ac:structured-macro ac:name="column">
      <ac:parameter ac:name="width">50%</ac:parameter>
      <ac:rich-text-body><p>Right column.</p></ac:rich-text-body>
    </ac:structured-macro>
  </ac:rich-text-body>
</ac:structured-macro>
```

`column` macros must be direct children of a `section`'s body — a bare
`column` outside a `section` is another unknown-macro placeholder.

## Excerpt / Excerpt Include

Mark a reusable snippet on a source page:

```xml
<ac:structured-macro ac:name="excerpt">
  <ac:rich-text-body><p>Reusable summary text.</p></ac:rich-text-body>
</ac:structured-macro>
```

Pull it into another page (needs the source page's title, and its space key
if the include crosses spaces):

```xml
<ac:structured-macro ac:name="excerpt-include">
  <ac:parameter ac:name="">SPACEKEY:Source Page Title</ac:parameter>
</ac:structured-macro>
```

## Jira macro (single issue)

```xml
<ac:structured-macro ac:name="jira">
  <ac:parameter ac:name="key">TASKS-42</ac:parameter>
  <ac:parameter ac:name="showSummary">true</ac:parameter>
</ac:structured-macro>
```

## Jira macro (JQL results table)

```xml
<ac:structured-macro ac:name="jira">
  <ac:parameter ac:name="jqlQuery">project = TASKS and statusCategory != Done</ac:parameter>
  <ac:parameter ac:name="columns">key,summary,status,assignee</ac:parameter>
  <ac:parameter ac:name="maximumIssues">20</ac:parameter>
</ac:structured-macro>
```

On **Server**, embedding Jira issues requires an application link between
the Confluence and Jira instances to already exist (`ac:parameter
ac:name="server"` / `ac:name="serverId"` identify which linked Jira to query
when more than one is linked) — this is instance configuration, not
something a page write can create. If the embed renders empty or errors,
check whether the application link exists before assuming the macro syntax
is wrong.

## Page Properties + Page Properties Report (contact/inventory pages)

Source page — wrap a summary table in `details`, and label the page so the
report macro can find it:

```xml
<ac:structured-macro ac:name="details">
  <ac:parameter ac:name="id">contact-info</ac:parameter>
  <ac:rich-text-body>
    <table>
      <tbody>
        <tr><th>Owner</th><td>Jane Smith</td></tr>
        <tr><th>Team</th><td>Platform</td></tr>
        <tr><th>Status</th><td>Active</td></tr>
      </tbody>
    </table>
  </ac:rich-text-body>
</ac:structured-macro>
```

The page must also carry the label the report filters on (via
`confluence.set_labels`, a separate proposal — the `details` macro itself
does not apply the label).

Report page — aggregates every page carrying that label:

```xml
<ac:structured-macro ac:name="detailssummary">
  <ac:parameter ac:name="id">contact-info</ac:parameter>
  <ac:parameter ac:name="label">team-contact</ac:parameter>
  <ac:parameter ac:name="sortBy">Owner</ac:parameter>
</ac:structured-macro>
```

Use this pattern for any "one row per X, one page per X, roll them all up"
need — team contacts, service inventories, project status pages — instead of
hand-maintaining a single giant table.

## draw.io diagrams: the two-step

A draw.io diagram is **not** self-contained in the macro — it's an
attachment (the diagram's XML, optionally with a rendered PNG) plus a macro
that references it by filename. Same shape on both flavors.

**Step 1 — upload the diagram as an attachment:**

```
confluence.upload_attachment(page_id=..., filename="architecture.drawio", content=<xml bytes>)
```

The multipart request needs header `X-Atlassian-Token: no-check` (exact
string) — the client sets this for you.

**Step 2 — embed it**, only after confirming the attachment landed
(`confluence_list_attachments`):

```xml
<ac:structured-macro ac:name="drawio">
  <ac:parameter ac:name="diagramName">architecture.drawio</ac:parameter>
  <ac:parameter ac:name="attachment">architecture.drawio</ac:parameter>
</ac:structured-macro>
```

Re-uploading the same filename creates a new attachment version — the macro
keeps pointing at the same name, so it picks up the new version
automatically without re-editing the page body.

## Roadmap Planner — cannot be authored via storage format

Atlassian's own documentation states the Roadmap Planner macro's
configuration is **not parameter-encoded** — there is no `ac:parameter` set
that produces a working roadmap. This is true on both flavors, at every
version. Do not attempt it, and do not propose a `roadmap` macro block
expecting it to render.

**Substitutes for a roadmap-looking page:**

- A **draw.io timeline diagram** (the two-step above) — full visual control,
  authorable end to end.
- A **status + table layout** — a table with columns for phase/quarter and
  `status` macro lozenges per cell, built from the recipes above.
- A **Jira macro with a chart-capable JQL view**, or (work-server-9.2 only)
  the **Advanced Roadmaps for Jira in Confluence** embed macro, if the
  active profile lists it — see `instance-profiles`.

## Panel

Like Info/Note/Warning/Tip but every color and border independently
configurable:

```xml
<ac:structured-macro ac:name="panel">
  <ac:parameter ac:name="title">Deployment window</ac:parameter>
  <ac:parameter ac:name="borderStyle">solid</ac:parameter>
  <ac:parameter ac:name="borderColor">#cccccc</ac:parameter>
  <ac:parameter ac:name="borderWidth">1</ac:parameter>
  <ac:parameter ac:name="bgColor">#f5f5f5</ac:parameter>
  <ac:parameter ac:name="titleBGColor">#e0e0e0</ac:parameter>
  <ac:parameter ac:name="titleColor">#000000</ac:parameter>
  <ac:rich-text-body><p>Body content.</p></ac:rich-text-body>
</ac:structured-macro>
```

Parameter names sourced from Atlassian's Panel Macro doc, which only shows
the wiki-markup form (`{panel:title=...|borderStyle=...}`); the
`ac:parameter ac:name="..."` mapping is the standard, mechanical wiki-markup
→ storage-format translation used consistently across every macro on this
page, but the storage XML itself was not directly shown for this macro —
sanity-check the first write against the rendered page.

## Table of Content Zone (scoped TOC)

Same idea as `toc`, but the contents-list is built only from headings inside
the macro's own body — useful when a page has one "real" TOC-worthy section
and a lot of other content:

```xml
<ac:structured-macro ac:name="toc-zone">
  <ac:parameter ac:name="type">flat</ac:parameter>
  <ac:parameter ac:name="outline">true</ac:parameter>
  <ac:parameter ac:name="maxLevel">3</ac:parameter>
  <ac:parameter ac:name="minLevel">1</ac:parameter>
  <ac:parameter ac:name="location">top</ac:parameter>
  <ac:parameter ac:name="printable">true</ac:parameter>
  <ac:rich-text-body>
    <h2>Section with its own contents list</h2>
    <p>...</p>
  </ac:rich-text-body>
</ac:structured-macro>
```

Parameters sourced from Atlassian's Table of Content Zone Macro doc
(`type`, `outline`, `maxLevel`, `minLevel`, `location`, `printable`,
`separator` — `separator` only applies when `type=list`, omitted above).

## Include Page

Transcludes another page's rendered content in place — different from
Excerpt Include, which pulls only the marked excerpt:

```xml
<ac:structured-macro ac:name="include">
  <ac:parameter ac:name="">
    <ac:link>
      <ri:page ri:content-title="Standard Footer" ri:space-key="OPS"/>
    </ac:link>
  </ac:parameter>
</ac:structured-macro>
```

Source: Atlassian's Include Page Macro doc. `ri:space-key` is required only
when the included page lives in a different space than the one being
authored.

## Attachments

Lists a page's own attachments, or another page's (via the `page` parameter,
same `ac:link`/`ri:page` shape as Include Page):

```xml
<ac:structured-macro ac:name="attachments">
  <ac:parameter ac:name="old">false</ac:parameter>
  <ac:parameter ac:name="patterns">.*png,.*jpg</ac:parameter>
  <ac:parameter ac:name="sortBy">name</ac:parameter>
  <ac:parameter ac:name="sortOrder">ascending</ac:parameter>
  <ac:parameter ac:name="labels">diagram</ac:parameter>
  <ac:parameter ac:name="upload">false</ac:parameter>
</ac:structured-macro>
```

Source: Atlassian's Attachments Macro doc.

## Gallery

```xml
<ac:structured-macro ac:name="gallery">
  <ac:parameter ac:name="title">Screenshots</ac:parameter>
  <ac:parameter ac:name="columns">3</ac:parameter>
  <ac:parameter ac:name="sort">name</ac:parameter>
  <ac:parameter ac:name="reverse">false</ac:parameter>
  <ac:parameter ac:name="excludeLabel">wip</ac:parameter>
</ac:structured-macro>
```

Parameter names (`title`, `columns`, `sort`, `reverse`, `page`,
`excludeLabel`, `exclude`) sourced from Atlassian's Gallery Macro doc — but
that doc's own example wraps them in `<ac:macro ac:name="gallery">`, not
`<ac:structured-macro>`, which is inconsistent with every other macro on
this page and every other Atlassian doc. Treat that tag as a documentation
artifact and use `ac:structured-macro` as shown above (the pattern this
whole skill file is built on); if the write renders unexpectedly, check the
actual tag Confluence expects rather than assuming the parameters are wrong.

## Anchor

```xml
<ac:structured-macro ac:name="anchor">
  <ac:parameter ac:name="">deployment-steps</ac:parameter>
</ac:structured-macro>
```

Link to it from elsewhere on the same page with `<a href="#deployment-steps">`
(the anchor name, not the macro). Source: Atlassian's Anchor Macro doc — the
single parameter is unnamed (`ac:name=""`), matching the `toc`-adjacent
positional-parameter convention already used by `excerpt-include` above.

## Task Report

```xml
<ac:structured-macro ac:name="tasks-report-macro">
  <ac:parameter ac:name="spaces">OPS</ac:parameter>
  <ac:parameter ac:name="status">incomplete</ac:parameter>
  <ac:parameter ac:name="pageSize">50</ac:parameter>
</ac:structured-macro>
```

Source: Atlassian's Task Report Macro doc — confirms `ac:name="tasks-report-macro"`
and the parameter set `spaces`, `status`, `assignees`, `spaceAndPage`,
`pages`, `labels`, `pageSize`. Has no macro body.

## Chart (built-in)

Built from a `<table>` in the macro body — this is the classic bundled Chart
macro, not the marketplace "Chart from Table" app listed in
`instance-profiles`:

```xml
<ac:structured-macro ac:name="chart">
  <ac:parameter ac:name="type">pie</ac:parameter>
  <ac:parameter ac:name="dataOrientation">vertical</ac:parameter>
  <ac:rich-text-body>
    <table>
      <tbody>
        <tr><th>Category</th><th>Count</th></tr>
        <tr><td>Open</td><td>12</td></tr>
        <tr><td>Closed</td><td>40</td></tr>
      </tbody>
    </table>
  </ac:rich-text-body>
</ac:structured-macro>
```

Only `type` and `dataOrientation` are confirmed against Atlassian's Chart
Macro doc. It documents many more display parameters (`width`, `height`,
`title`, `legend`, `colors`, `3d`, …) that could not be individually verified
here — start with this minimal set, and if you need a specific visual
option, read `docs/reference/work-environment-compatibility.md` or the live
Chart Macro doc for that parameter's exact name before guessing it.

## Team Calendars embed

```xml
<ac:structured-macro ac:name="calendar">
  <ac:parameter ac:name="id">4f5f9524-f588-468e-a48c-668ea480a77c</ac:parameter>
  <ac:parameter ac:name="defaultView">month</ac:parameter>
  <ac:parameter ac:name="width">400</ac:parameter>
</ac:structured-macro>
```

**The `id` is an opaque per-calendar UUID — it cannot be guessed or
constructed**, only read off an existing embed (view a page's storage body
that already embeds the calendar you want, or the calendar's own share
link in the Team Calendars UI). This is confirmed only in Team Calendars'
wiki-markup form (`{calendar:id=...|defaultView=month|width=400}`); the
storage-format `ac:structured-macro` mapping follows the same mechanical
translation as Panel/Gallery above but was not independently confirmed in
storage form.

## Multiexcerpt / Multiexcerpt Include — verify before authoring

**Do not guess this one's macro name.** Public Atlassian docs only clearly
describe the storage format for Appfire's commercial "MultiExcerpt" app
(`ac:name="multiexcerpt-macro"`, parameters `name` and `hidden`), which is a
different product from whatever ships bundled as "Multiexcerpt" /
"Multiexcerpt include" in the work instance's macro browser
(`instance-profiles`). The two may or may not share a macro name and
parameter set on this instance. Treat Multiexcerpt/Multiexcerpt Include as a
**discovery-first** macro (see `macro-discovery.md`): find a page on the
instance that already uses it via `macro = "multiexcerpt"` (and
`"multiexcerpt-macro"` if the first comes back empty), read its real
`<ac:structured-macro>` block, and copy the actual name and parameters from
that — don't write one from the Appfire doc above without confirming it
matches what this instance's macro browser is actually running.

## Marketplace macros — a different authoring path entirely

Table Filter/Transformer/Spreadsheet, Forms, Epic Sum Up, Scroll export,
BibTeX, LaTeX, Markdown, the test-management report family, and anything
else in `instance-profiles`' marketplace categories are **not covered by
recipes in this file** — their parameter surface is app-version-specific and
not reliably documented publicly. See `macro-discovery.md` for the
find-a-real-example-first doctrine that applies to all of them.

## Never write

- **ADF.** Cloud-only, and this client's write path is storage format on
  both flavors. Sending ADF where storage-format XHTML is expected produces
  either a rejected write or literal escaped text on the page, not a
  rendered document.
- **A macro name outside the active profile.** It won't error — it'll sit on
  the page as a broken placeholder until someone notices.
- **Roadmap Planner**, ever, regardless of profile.
- **A marketplace macro's parameters invented from memory or a generic
  doc.** Discover the real block from an existing page first
  (`macro-discovery.md`) — a wrong key doesn't error, same failure mode as
  the Jira gadget bite mark.
