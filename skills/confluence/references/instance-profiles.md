# Instance profiles: what macros actually exist, per instance

Confluence has no universal macro set. What renders on one instance is an
"unknown macro" placeholder on another — installed marketplace apps differ,
and even built-ins vary by edition and version. `CC_CONFLUENCE_PROFILE`
names which allowlist is live for the instance you're currently talking to.
**A macro outside the active profile is a capability gap to `declare_gap` on
— never a guess to try and see what happens**, because a wrong guess doesn't
error, it just sits broken on the page (`storage-format`).

## Profile: `cloud-free`

The dev instance — Confluence Cloud, free plan (10 users, 2GB).

**Have:** the built-in Cloud macro set — panels (info/note/warning/tip),
Table of Contents, Expand, Status, Children Display, Page Properties +
Report, Excerpt/Excerpt Include, Code Block, Section/Column, Attachments,
Labels List, layout macros (Section, Column, Div), Jira macro (single issue
and JQL table — needs an app link to the Jira Cloud site), and **draw.io**
once the app is installed (it has a generous free tier and installs cleanly
on a free-plan Cloud site).

**Do NOT have:** Table Filter, Forms, any Scroll export macro, Epic Sum Up
gadgets, or any test-management (Xray/Zephyr-style) report macro — none of
these ship free-plan-compatible on a fresh Cloud dev site, and a free plan
in particular won't have paid marketplace apps installed. Advanced Roadmaps
embed likewise requires a paid plan tier this instance doesn't have.

## Profile: `work-server-9.2`

The WORK instance — Confluence Server/DC 9.x. Distilled from the full
macro browser inventory in `docs/reference/work-environment-compatibility.md`
(lines 239–964) into grouped categories. Treat that doc as the authoritative
list for anything not named here; this is a working summary, not a
substitute.

- **Layout** — Section, Column, Div, Span, Center, Align, Anchor, Tabs
  Container/Page, Horizontal Navigation Bar(+Page), Round Rectangle, Dialog.
- **Panels & callouts** — Info, Note, Warning, Tip, Panel, Message Box,
  Highlight, Background Color, Alert, Status, Lozenge.
- **Tables & data apps** (marketplace) — Table Filter, Table from CSV, Table
  from JSON, Table Spreadsheet(+Include), Table Excerpt(+Include), Table
  Transformer, Pivot Table, Spreadsheet from Table, Chart from Table, AI
  Table, BibTeX Display Table/Referencing.
- **Jira & gadget embeds** — Jira (issue/JQL), Jira Charts, JIRA Issues
  Calendar, Jira Road Map, Filter Results, Pie Chart, Created vs. Resolved
  Chart, Two Dimensional Filter Statistics, Heat Map, Resolution Time,
  Average/Recently Created/Time Since charts, Version Report, Sprint
  Burndown/Health, Labels Gadget, Issue Statistics, Activity Stream, Agile
  Wallboard Gadget, **Advanced Roadmaps for Jira in Confluence** (the roadmap
  substitute — see `storage-format`), (Epic Sum Up) Filter Results/Grid
  Gadget. **All of these need the Confluence↔Jira application link already
  configured on this instance** — an empty/erroring embed may mean the link
  is missing, not that the macro syntax is wrong.
- **draw.io** — draw.io Diagram, draw.io Board Diagram, Embed draw.io
  Diagram. **DO NOT USE Diagramly** — the instance's own macro description
  reads "DO NOT USE, this is a legacy macro. Please use drawio macro
  instead." It is present in the macro browser but explicitly deprecated.
- **Forms** — Forms (container) plus its field macros (Input, Dropdown,
  Checkbox/Radio button group, Datepicker, Textarea, User Picker, Star
  rating, …) and Forms Responses Table for reading submissions back.
- **Scroll export** (Scroll Exporter product) — Scroll Bookmark, Scroll
  Content Block, Scroll Export Button, Scroll Ignore(+inline), Scroll
  Indexterm, Scroll Landscape/Portrait, Scroll Only(+inline), Scroll
  Pagebreak, Scroll Pagetitle, Scroll Table Layout, Scroll Title — these
  only matter when the deliverable is an exported document (PDF/Word), not
  the live wiki page.
- **Test-management reports** (Xray/Zephyr-style app) — the large "Test
  cases by …", "Test execution … by …", and "Issues reported during testing
  …" families. Use these only when the task is genuinely a QA/test-coverage
  report; they are numerous but narrow-purpose.
- **Core built-ins present on essentially every Confluence** — Table of
  Contents, Table of Content Zone, Children Display, Page Tree(+Search),
  Page Index, Content by Label/User, Content Report Table, Recently
  Updated(+Dashboard), Attachments, Gallery, Multimedia, Excerpt(+Include),
  Multiexcerpt(+Include), Include Page, Page Properties(+Report), Labels
  List, Popular/Recently Used/Related Labels, Change History,
  Contributors(+Summary), Code Block, Markdown(+From URL), HTML
  Table/Image/Comment, Numbered Headings, Task report, Countdown, Team
  Calendars, PDF, Office Word/Excel/Powerpoint, User Profile/List, Spaces
  List, Create from Template.

## Which families need discovery, not a recipe

Layout, panels & callouts, and the "core built-ins" line above have verified
storage-format recipes in `storage-format.md` — parameter names sourced from
Atlassian's own macro docs. **Tables & data apps, Forms, Scroll export,
test-management reports, BibTeX/LaTeX/Markdown, and Multiexcerpt are
discovery-first**: their parameter surface is app-version-specific (or, for
Multiexcerpt, ambiguous between two similarly-named products) and not
something this skill can hand you a trustworthy recipe for. Find a real
example on the instance and copy it — `macro-discovery.md` has the procedure
and the full macro-to-doctrine mapping. Jira & gadget embeds have verified
skeletons but a separate live dependency (the application link) that no
amount of correct XML substitutes for.

## The rule

`CC_CONFLUENCE_PROFILE` names the active profile. Compose a macro body only
from that profile's allowlist. A macro that would be useful but isn't listed
for the active instance is a capability gap — `declare_gap`, name the macro
and the need, and let the operator decide whether to install the app or
choose a substitute. Do not try it "to see" — the failure mode is a broken
placeholder on a page someone else may already be reading, not a clean
error.
