# Work environment — external development compatibility profile

> Provided by the operator 2026-08-21, transcribed from a printed/scanned document
> produced IN the work environment (sanitized there by design — no internal
> endpoints, hostnames, cert details). The scan triplicated its pages; this
> is the deduplicated text, errors-of-transcription possible. This is the
> ground truth for the work transition: what Central Command must be BUILT to
> handle before it can be brought on-site. Design record:
> docs/superpowers/specs/2026-08-21-work-transition-compatibility-design.md

**Goal:** Enable development on another network for software that will later be deployed/run in this environment, **without exporting sensitive configuration** (internal URLS, file paths, certificates, thumbprints, hostnames, proxy endpoints, etc.).
This document focuses on **architecture-impacting requirements** and **environment patterns** you must support in code. All sensitive details are intentionally abstracted as "provided at deployment time."

## 0) What this document is / isn't

### This is:

* A **design-time compatibility profile**: the constraints your software must be built to handle.
* A **sanitized summary**: avoids internal identifiers, paths, and PKI details.

### This is not:

A step-by-step deployment guide.
A list of internal endpoints, exact registries, proxy URLS, cert thumbprints, or filesystem locations.

## 1) Environment Summary (High-level)

### Operating system & shell reality

**OS family:** Windows (enterprise-managed workstation environment).
**Shell context:** Common automation happens through **PowerShell** and also
**Git-Bash-style bash** (mixed POSIX shell semantics on Windows).
**Key implication:** Scripts and tooling must tolerate **Windows path semantics**, but also be robust when invoked from a POSIX-like shell wrapper.

### Runtime/tool ecosystems available (design assumption level) Your software should assume it can rely on:

**PowerShell** execution for .psl automation (critical for reuse of the internal Exchange automation toolset). **Node/npm** ecosystem (but see mirrors below).
**Container tooling (Podman)** rather than Docker in many workflows. Enterprise desktop capabilities exist (notably **Outlook**), but Outlook-based automation should be treated as **optional** unless you require desktop-only behaviors.

## 2) Non-Negotiable Architectural Requirements (Not "just config")

These are the items that must be supported by the software's design *before* bringing it into the environment.

### 2.1 Dependency acquisition must support enterprise mirrors Your software must support:

**npm registry override / internal mirrors** (do not assume public npm registry). **container registry/mirror usage** (do not assume direct pull from Docker Hub without overrides).
Potential restrictions on direct outbound access; treat "public internet dependency resolution" as non-guaranteed.
**Design implications:**
Registry endpoints must be configurable (via env/config), not hardcoded. Build/install steps should be able to run with **no direct public internet**. Avoid tooling that *only* works with public registries unless you can redirect it.

### 2.2 Network access may require enterprise certificate-based authentication

Any component that fetches web resources (HTTP clients, SDKs, "web fetch"
subsystems, artifact downloaders, internal APIs) must support:
**certificate-based authentication / mutual TLS (MTLS) / PKI-based access patterns** as required by enterprise services.
A trust model that is not "public CA only."

**Design implications:**

Your HTTP layer must support:

client cert auth (pluggable)
custom trust bundles (pluggable)
Do **not** design around username/password-only auth for internal services. Do **not** assume you can embed certs in source code or ship them with the artifact.

### 2.3 Exchange integration must reuse the toolset OR match its patterns For Exchange/Outlook/Calendar/Tasks functionality, your system must either:

**Option A Reuse existing toolset `.psl tools**
Invoke the existing PowerShell tools as the integration layer (strongest compatibility path).
This minimizes the risk of missing enterprise edge cases (hybrid, delegate mailboxes, archives, etc.).
**Option B Implement an equivalent model**
If you do not reuse the toolset, you must implement the **same functional patterns**: backend abstraction (Graph vs EWS vs Outlook desktop fallback)
shared/delegate mailbox handling as a first-class concept
item ID translation/normalization strategies
archive-aware behavior (Graph limitations)
safe defaults (draft-first operations)

**Design implications:**
A Graph-only implementation is likely insufficient.
Authentication is likely non-trivial and may be embedded in how the toolset invokes Graph/EWS; plan for pluggable auth and multiple backends.

### 2.4 Outlook desktop automation is a capability boundary (optional adapter) Some workflows are fundamentally desktop-only:

PST export / PST reading/searching
Outlook template-based compose (.oft/.msg)
opening drafts/inspectors for user review
Design implications: 
Treat Outlook COM automation as an **optional adapter**:
enabled only when Outlook is present and an interactive desktop session exists not required for server-side automation flows unless your use-case demands it

## 3) Exchange / Outlook Capability Requirements (Toolset-Compatible)

The toolset demonstrates that enterprise Exchange reality requires more than "send email
via API." Your system should plan around these areas:

### 3.1 Required capability areas

Your integration must cover (at least conceptually): **Mail**
search / retrieve messages
create drafts (not auto-send)
reply/forward flows
attachments
move/delete/update properties (with safe defaults)
conversation/thread retrieval
**Calendar**
create appointments/meetings with draft-safe "don't send invites yet" mode update/send invites as a separate step
cancel/delete semantics that respect organizer vs attendee role
free/busy retrieval
meeting-time suggestion behavior
rooms/resources handling
**Tasks**
list/create/update/complete/delete tasks
handle differences between modern Graph task surfaces vs legacy behaviors
**Mailbox organization**
folder listing and folder CRUD
inbox rules CRUD (with awareness of Outlook UI drift)
categories (master category list semantics)
**Directory**
GAL search (enterprise directory distinct from personal contacts) contacts CRUD
distribution list membership/ownership patterns
room/resource lookup
MailTips and OOF behavior

### 3.2 Non-obvious semantics you must support

These are the patterns that commonly break naive integrations:
**Shared/delegate mailbox support**
target mailbox as a parameter ("operate on mailbox X as user Y") folder delegation vs full mailbox access are not the same
**Backend selection is capability-dependent**
some operations may require EWS even if Graph is available
some operations may require Outlook desktop fallback
**Graph vs EWS differences matter**
e.g., draft-safe meeting invite semantics differ; Graph can require "staging"
attendees for later send
**Item identity is messy**
item IDs differ across Graph, EWS, and Outlook/MAPI contexts translation/normalization is required if you mix sources/tools
**Archives are special**
Online Archive may not be accessible via Graph; must have fallback logic

### 3.3 Safety defaults (strongly recommended; toolset-aligned)

Email operations should default to **draft-first** rather than send.
Meeting creation should default to **create without sending invitations**, with an explicit "send" step.
Deletions should default to **recoverable/soft delete** unless explicitly overridden.
Free/busy should fail closed on "No Data" (do not treat "unknown" as "free").

## 4) Package & Container Ecosystem Requirements (Sanitized)

### npm ecosystem (design-time requirements)

Must support an **internal npm mirror/registry** (public npm cannot be assumed). Must support per-environment registry config (user-level or environment-level). Avoid designs that require downloading arbitrary packages at runtime from the
public internet.

### Container ecosystem (design-time requirements)

Must support **Podman-based workflows** (Docker may not be the standard baseline). Must support internal container registries/mirrors and image naming policies. Must not assume anonymous pulls from Docker Hub.

## 5) "Incorrect Assumptions" Checklist (What to avoid hardcoding)

Your external-development version must **not assume**:

### Networking / auth

direct internet egress is available
username/password auth works for internal services public CA trust is sufficient
no client certificates are ever needed

### Exchange

Graph-only is enough for all enterprise Exchange features
no shared mailboxes / no delegation
archives behave like primary mailboxes
item IDs are stable/consistent across toolchains

### OS/runtime/tooling

Linux-only behavior
POSIX-only paths
Docker-only container runtime
Outlook is never involved (if you need PST/templates/UI review flows)

## 6) What is expected to be configured later (Deployment-time knobs)

These items should be treated as configuration-only and supplied when software is brought into the environment:

npm registry/mirror URL(S)
container registry/mirror URL(s) and namespaces
any proxy/gateway endpoints (if applicable)
certificate selection details (thumbprints, store names, etc.)
trust bundle locations
Exchange tenant/host specifics
mailbox identity defaults and allowed delegate scopes
Design requirement:  all of the above must be injectable/configurable without code changes.

## 7) Toolset Reuse Guidance (Practical Recommendation)

### Recommended default: invoke the toolset for Exchange

If Exchange functionality is in scope, the lowest-risk approach is:
implement an adapter that calls the relevant 'Create-Exchange*`, `Get-Exchange*`,
Search-Exchange*, etc. PowerShell tools
parse their structured outputs
keep your app logic backend-agnostic

This avoids you having to re-implement:
Graph/EWS capability routing
delegate mailbox edge cases
ID translation
archive handling
Outlook UI/COM fallbacks

### If you reimplement: match the toolset's "broker" architecture

At minimum, design an internal interface like:
`ExchangeProvider with implementations: `GraphExchangeProvider` `EwsExchangeProvider` optional OutlookDesktopProvider`
plus a Router/Broker that chooses per capability and mailbox context
plus an AuthProvider that can support certificate-based authentication and
delegated OAuth where required


---

Jira information
Jira 10.x
Advanced Roadmaps for Jira 10.x

Currently configured issue types (more can be added if needed)
Bug
Epic
Feature
Risk
Story
Sub-Task
Task
Test

---

Confluence information
Confluence 9.x
Available macros

Available Confluence Macros
(Epic Sum Up) Filter Results Gadget URL
Shows the issues/results for a saved filter.
(Epic Sum Up) Filter results grid gadget
Gadget URL
(Epic Sum Up) Display filter or JQL results in a configurable grid view with editing, filtering and sorting functionality.
Activity Stream
Gadget URL
Lists recent activity in a single project, or in all projects.

Agile Wallboard Gadget
Gadget URL
Displays a board as a Wallboard gadget

Advanced Roadmaps for Jira in Confluence
Embed an Advanced Roadmaps for Jira plan in a page for others to view.

AI Table
Analyze and transform tables using AI: generate modified tables, summaries, charts, diagrams, or insights from source data with custom prompts powered by ChatGPT.
Perform smart reporting and interactive data exploration.
Alert
Create custom pop up alerts to flag important context.
Align
Align the content of the macro as you choose.
Anchor
Creates an anchor inside the page, which can be hyperlinked.
Assigned to Me
Gadget URL
Displays all unresolved issues assigned to me
Attachments
Creates a list of attachments belonging to this page.
Average Age Chart
Gadget URL
Displays the average number of days issues have been unresolved.
Background Color
Sets the background color for a block of content. Colour names or hex values can be
used.

BibTeX Display Table
Display BibTeX references in a searchable and sortable table on a page. Pairs with
BibTex Reference macros.
BibTeX Referencing
Insert BibTeX formatted references within your content. Pair with the BibTex Display Table macro to display the references.

Blog Posts
View, summarize or list the most recent blog posts in the space

Button Group
Group together multiple Button Hyperlinks.

Button Hyperlink
Highlight hyperlinks with a customisable button.
Center
Centers a block of content or text.

Change History
Shows the history of version comments for the current page or blog posts.
Chart
Display a chart

Chart from Table
Create bright and dynamically updated charts from table data.

Cheese
Inserts the text 'I like cheese!', used for testing macro functionality.
Children Display
List all the children of a page (and possibly their children)
Clickable
Makes the contained content clickable.
Code Block
Macro to format blocks of source-code or XML.
Column
Defines a column on the page. (Must be used within a Section macro.)
Confluence Page Viewer
Gadget URL
Embed a Confluence Data Center page in your Jira dashboard.

Content by Label
List pages and posts with the labels you enter.
Content by User
List all the content created by a particular user

Content Report Table
Provides a content report in table format, based on labels.

Contributors
Displays a list of contributors to a page, its hierarchy or selected spaces, or
watches of these pages.

Contributors Summary
Displays a summary of contributions to a page, its hierarchy or selected spaces, in tabular form.

Countdown
Display a countdown counter

Coverage Statistics for Jira
Gadget URL
This gadget shows the coverage statistics for the configured project and coverage filter.

Create from template
Embed a button in your content which enables users to create content from any pre-defined template.

Create Space Button
Displays a create space icon that links to the create space page.

Created vs. Resolved Chart
Gadget URL
Displays created issues vs. resolved issues for a project or saved filter.

Crucible Charts
Gadget URL
Chart Crucible review and comment data.

CSS Stylesheet
Insert a style sheet in to your content.
Days Remaining in Sprint Gadget
Gadget URL
Displays days remaining in a sprint (Wallboard capable)
Diagramly
DO NOT USE, this is a legacy macro. Please use drawio macro instead.
Dialog
Content that is opened in a dialog by a button or other link with a URL hash referring to the id of this dialog, eg: '#dialog-id'

Div
Wrap content in a div tag. This lets you apply optional CSS classes and Inline styles.
draw.io Board Diagram
draw.io is an online diagram drawing application for workflow, BPM, org charts, UML, ER, network diagrams.
draw.io Diagram
draw.io is an online diagram drawing application for workflow, BPM, org charts, UML, ER, network diagrams.
Embed draw.io Diagram
Embed an existing draw.io diagram

Excerpt
Mark a section of a page as an excerpt for page summaries
Excerpt Include
Include the excerpt from one page within another page

Expand
Embeds an expandable text box into your page.
Fancy Bullets
Easily make ordered or unordered lists with many styles of bullet and numbering.
Favorite Filters
Gadget URL
Lists favorite filters for the current user.
Favorite Pages
Lists your favorite pages.
Filter Results
Gadget URL
Shows the issues/results for a saved filter.

FishEye Charts
Gadget URL
Chart LOC data from a FishEye repository.
FishEye Recent Changesets
Gadget URL
Show recent changesets from a FishEye repository path.
Footnote
Add a footnote marker to a page, to be used alongside the Footnotes Display macro
Footnotes Display
Display footnotes on a page, to be used alongside the Footnotes macro

Forms
Defines a form. Add further macros inside to build up your form

Forms Additional Recipients
Adds an input field to the form that is used to send the form response to additional Confluence users and email addresses

Forms Attachment
Adds an attachment field to the Form

Forms Checkbox button group
Adds a Checkbox button group with multiple checkbox buttons

Forms
Checkbox matrix
Adds a Checkbox matrix with multiple checkboxes
Forms Conditional Fields
Defines the Conditional Fields in the form

Forms
Datepicker Field
Adds a datepicker in order to choose a date

Forms Destinations List
Encapsulates destination macros. Add destination macros inside.

Forms Dropdown list
Adds a dropdown list with multiple options

Forms Fieldset
Defines a fieldset in a form

Forms
Hidden Field
Hidden Field Macro
Forms
Input Field
Adds an input field to the form. Can be text and/or numeric and supports validation

Forms Mail body formatter
Set a template for form submissions that are sent to an email

Forms Page
Defines a page within a form
Forms
Radio button group
Adds a Radio button group with multiple radio buttons

Forms Radio matrix
Adds a Radio matrix with multiple radio buttons

Forms
Responses Table
View Submitted Responses of a Form
Forms
Star rating
Adds a star rating scale with choice of icon.
Forms Success message
Success message shown to users after they submit
Forms Textarea Field
Adds a configurable textarea to a form

Forms User Picker
User Picker

Gallery
Creates a thumbnail gallery from a page's attachments.

Global Reports
Displays a list of links to global reports.
Heat Map
Gadget URL
Displays the matching issues for a project or filter as a heat map.
Highlight
Apply a background color to a small section of your content.
Horizontal Navigation Bar
Add an styled Horizontal Navigation bar to the page.
Horizontal Navigation Bar Page
Pages to be used within the Horizontal Navigation Bar.
HTML Comment
Wraps content in an HTML comment
HTML Image
Creates an HTML image (img) tag.
HTML Table
Inserts an HTML table
iFrame
Use an iFrame to display a different web page, or element in your confluence page.

Include Page
Include the excerpt from one page within another page

Info
Highlights content as an informational note with a blue background.

Issue Statistics
Gadget URL
Issue Statistics

Issues in progress
Gadget URL
Issues in progress

Issues reported during testing (list)
Provides a list of issues reported during testing

Issues reported during testing by coverage
Provides a bar chart with the issues reported during testing grouped by coverage

Issues reported during testing by environment
Provides a bar chart with the issues reported during testing grouped by environment

Issues reported during testing by epic
Provides a bar chart with the issues reported during testing grouped by epic

Issues reported during testing by priority
Provides a bar chart with the issues reported during testing grouped by issue priority
Issues reported during testing by project
Provides a bar chart with the issues reported during testing grouped by project

Issues reported during testing by status
Provides a bar chart with the issues reported during testing grouped by issue status

Issues reported during testing by test cycle
Provides a bar chart with the issues reported during testing grouped by test cycle

Issues reported during testing by test plan
Provides a bar chart with the issues reported during testing grouped by test plan

Issues reported during testing by tester
Provides a bar chart with the issues reported during testing grouped by tester

Issues reported during testing by type
Provides a bar chart with the issues reported during testing grouped by issue type

Issues reported during testing over time
Provides a line and bar chart of the issues reported during testing over time

Jenkins Job Status
Visualize the latest completed build of a Jenkins job with a status light

Jira
Embed Jira issues or filters into your status reports, release notes, requirements or specifications.

Jira Charts
Display Jira information on your page as a chart.

JIRA Issues Calendar
Gadget URL
JIRA Issues Calendar

Jira Road Map
Gadget URL
Display upcoming versions for specified projects.

Labels Gadget
Gadget URL
Jira issue labels in a Gadget

Labels List
Renders the list of all labels or labels for a specific space sorted alphabetical.

LaTeX Formatting
Display mathematical and scientific equations on a page, using LaTeX.

List Item (li)
Creates an HTML list item (li) tag

Livesearch
Embeds a search box into your Confluence page to show search results as you type.

Loremipsum
Insert paragraphs of 'lorem ipsum' space-filler text

Lozenge
Insert a lozenge-shaped component with space for both image and text, which can link to other pages.
Markdown
This macro renders Markdown into HTML.
Markdown From URL
This macro dynamically imports Markdown from a URL and renders it into HTML.

Message Box
Inserts a styled message box.

Multiexcerpt
Multiexcerpt macro. The body of this macro can be displayed by Multiexcerpt-Include macros.
Multiexcerpt include
Multiexcerpt-Include macro. Used to display the body of a MultiExcerpt macro.

Multimedia
Insert audio and video files that are attached to any page.
Navigation Map
Creates a map of pages associated with a specified label.

No Format
Displays text in monospace font within a panel, with no other formatting applied.

Note
Highlights content as a note with a yellow background.

Numbered Headings
The Numbered Headings macro automatically numbers enclosed headings, any way you like.

Office Excel
Inserts Microsoft Excel content into the page

Office Powerpoint
Inserts an interactive Microsoft Powerpoint presentation into the page

Office Word
Inserts Microsoft Word content into the page

Ordered List (ol)
Creates an HTML ordered list (ol) tag

Page Index
Creates an index of all pages within the space.
Excerpts from each page are included when there are less than 200 pages.
Page properties
Enter a table of summary information in this macro and display it on another page using a Page Properties Report macro. You will need to add a label to this page and specify it in the report macro.
Page properties report
Display a table of pages that contain the Page Properties macro and a specific label. The table includes a link to each page and the summary information contained in the Page Properties macro(s) on that page.
Page Tree
Renders a page tree.
Page Tree Search
Provides a search box that searches a hierarchy of pages (Page Tree) from a specified root page.
page-title-plain-text
This macro will display the title of your Confluence page as plain text within the body of the page. Updates to the page title will automatically get updated in this
text as well.
Panel
Displays a block of text within a customizable panel.
Paragraph
Wraps text in p tags to define a paragraph.
PDF
Inserts a PDF document into the page as an interactive presentation

Pie Chart
Gadget URL
Displays the matching issues for a project or filter as a pie chart.

Pivot Table
Macro generates a pivot table.
Popular Labels
Generates a list or 'heatmap' of the most popular labels.
Pre
Wraps content in a div tag with optional class name and styles for the tag.
Privacy Mark
Display a privacy indicator with optional tooltip. When clicked, the page will be focused on a privacy-policy macro if present.
Privacy Policy
Display a privacy statement specific to a page. By default it will link to your full privacy policy on a page called "Privacy Policy"
Profile picture
Displays a users profile picture.
Progress Bar
Hyperlink Step
Add a hyperlinked progress bar step to a page. This macro should be placed inside
our Progress Bar Container macro.

Progress Bar Container
Use this macro to create a progress bar on your page. Add Progress Bar Hyperlink Step macros inside this container for each step of the progress bar.
Projects
Gadget URL
List multiple projects

Quick Links
Gadget URL
Displays links for common tasks
Recently Created Chart
Gadget URL
Displays recently created issues for a specified project as a bar chart
Recently Updated
Lists the most recently changed content within Confluence.
Recently Updated Dashboard
Displays recent updates with filtering options. Used on the dashboard.
Recently Used Labels
List the labels that have been used recently.
Related Labels
Lists labels used on other pages that have labels in common with the current page.
Resolution Time
Gadget URL
Displays a bar graph of elapsed time to resolve issues for a project or filter.
Restful Table
Creates a RESTful table

Rich Text
Gadget URL
Display formatted text with emojis, macros, links, and lists.

Risk Matrix
Gadget URL
Categorises risks according to impact and probability

Roadmap Planner
Create a simple, visual roadmap for planning projects, software releases and more.

Rollover
Combine with our CSS Macro to create visual effects when users roll their mouse over elements on your page.
Round Rectangle
Highlight important content with rounded rectangles. They are great for creating
content sections.
Scroll Bookmark
Adds a bookmark with a custom identifier.
Scroll Content Block
Content inside the macro will be kept on the same page in exported documents.
Scroll Export Button
Adds a button that triggers an export with a Scroll Exporter product.

Scroll Ignore
Excludes content from all Scroll exports.

Scroll Ignore (inline)
Excludes inline content from all Scroll exports.
Scroll Indexterm
Adds a term to indexes generated in Scroll exports. Not displayed in Confluence.
Scroll Landscape
Changes page orientation to landscape in Scroll exports. Doesnt affect Confluence page view.
Scroll Only
Hides content in Confluence but includes it in all Scroll exports.
Scroll Only (inline)
Hides inline content in Confluence but includes it in all Scroll exports.

Scroll Pagebreak
Inserts a page break when exporting with Scroll Exporters.
Scroll Pagetitle
Makes Scroll overwrite the page title on export.
Scroll Portrait
Changes page to portrait orientation in Scroll exports. Doesnt affect Confluence page view.
Scroll Table Layout
Specifies table column widths of the following table for export with Scroll.
Scroll Title
Adds a title to a block element like images and tables.
Search Box
Add a search box on the page that lets you fine-tune the search within your Confluence spaces.
Section
Defines a section on a page, which can contain one or more Column macros.

Shared Links Bookmarklet Button
Share a link from anywhere by dragging this button to your browser.
Sonar Test Status
Visualize the latest test results from a Sonar project with a status light

Space attachments
Displays a list of attachments in a space.

Spaces List
Displays a list of the spaces on this wiki.

Span
Wraps content in a span tag with optional class name and styles for the tag.

Spreadsheet from Table
Convert data from any table macro or native table into dynamic editable Excel-style spreadsheet (grid table). Perform advanced data manipulation directly within Confluence.
Sprint Burndown Gadget
Gadget URL
Sprint burndown chart to track remaining work (Wallboard capable)

Sprint Health Gadget
Gadget URL
Visual snapshot of the health of a sprint (Wallboard capable)

Static Status Light
Render a static status light with same look & feel and capabilities as Sonar Test Status or Jenkins Job Status

Status
Communicate the status of a project, task or milestone with visual indicators.

Strikethrough
Strike out text with a red strike-through.

Table Body (tbody)
Creates an HTML table body (tbody) tag

Table Cell (td)
Creates an HTML table cell (td) tag

Table Excerpt
Mirror and reuse the same table in multiple places (an advanced version of Excerpt) or build combined reports (an advanced version of Page Properties). Create dynamic reports and perform deep data analysis.
Table Excerpt Include
Table Excerpt Include macro allows you to use the mirrored copy of table for building multiple pivot tables and generating several charts on the basis of the
same table data.
Table Filter
Build dynamic, interactive tables with advanced features like filtering, sorting,
pagination, freezing rows/columns, calculated columns/rows (sum, count, average, and
more). Make large tables easier to navigate and understand.
Table from CSV
Output CSV or TSV data on Confluence pages and display it as a live, native-looking table. Create live reports, dashboards, and synced data views.

Table from JSON
Output JSON data on Confluence pages and display it as a live, native-looking table. Create live reports, dashboards, and synced data views.
Table Head (th)
Creates an HTML table heading (th) tag
Table Head (thead)
Creates an HTML table heading (thead) tag
Table of Content Zone
Creates a Table of Contents for headings within the body of the macro

Table of Contents
Creates a Table of Contents for the current page based on headings in the page.
Table Row (tr)
Creates an HTML table row (tr) tag
Table Spreadsheet
Create and edit Excel-style spreadsheets directly within Confluence pages. Use a powerful grid for managing and calculating data with familiar features like cell formulas, conditional formatting, filtering, sorting, pivots, and charts.
Table Spreadsheet Include
Mirror Excel data: use cell ranges from Table Spreadsheet as excerpts across multiple Confluence pages. Perform dynamic data sharing and consistent reporting.
Table Transformer
Join and merge tables, build table diffs.
Tabs Container
Add Tabs to your page to organize your content. Put your Tab Page Macros inside this container.
Tabs Page
Add Tabs to your page to organize your content. Put these Tab Page Macros inside the Tab Container macro.
Task report
Create a report of tasks from specific locations, people, status and more.
Team Calendars
Embeds any number of Events, People or Jira calendars into Confluence content.

Test cases by component
Provides a table report with an overview of the test case status grouped by component
Test cases by coverage
Provides a table report with an overview of the test case status grouped by coverage
Test cases by creator
Provides a table report with an overview of the test case status grouped by creator
Test cases by epic
Provides a table report with an overview of the test case status grouped by epic

Test cases by folder
Provides a table report with an overview of the test case status grouped by folder
Test cases by project
Provides a table report with an overview of the test case status grouped by project
Test cases by status
Provides a test case status donut chart

Test cases created over time
Provides a test case creation line and bar chart
Test execution assignment
Provides the test execution assignment based on the selected tester

Test execution burn down
Provides a test execution burn down chart

Test execution burn up
Provides a test execution burn up chart
Test execution effort (overall)
Provides a test execution effort bar chart (estimated x actual)

Test execution effort by tester
Provides a test execution effort by tester (estimated x actual)
Test execution effort over time
Provides a test execution effort line and bar chart
Test execution results ( list)
Test execution results (list)

Test execution results (overall)
Provides a test execution results gauge chart (overall)

Test execution results (progress)
Provides a test execution results donut chart
Test execution results by component
Provides a test execution results stacked bar chart grouped by component
Test execution results by coverage
Provides a test execution results stacked bar chart grouped by coverage
Test execution results by environment
Provides a test execution results stacked bar chart grouped by environment
Test execution results by epic
Provides a test execution results stacked bar chart grouped by epic
Test execution results by iteration
Provides a test execution results stacked bar chart grouped by iteration
Test execution results by label
Provides a test execution results stacked bar chart grouped by label
Test execution results by priority
Provides a test execution results stacked bar chart grouped by test case priority
Test execution results by project
Provides a test execution results stacked bar chart grouped by project

Test execution results by test cycle
Provides a test execution results stacked bar chart grouped by test cycle
Test execution results by test plan
Provides a test execution results stacked bar chart grouped by test plan
Test execution results by tester
Provides a test execution results stacked bar chart grouped by tester
Test execution results by type
Provides a test execution results stacked bar chart grouped by test execution type
Test execution results by version
Provides a test execution results stacked bar chart grouped by version
Test execution results over time
Provides a test execution results line and bar chart
Test execution results over time (accumulated)
Provides a test execution results line and bar chart (accumulated)

Test execution results over time (completed)
Provides a test execution results line and bar chart (completed)

Test execution scorecard by component
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by component

Test execution scorecard by coverage
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by coverage
Test execution scorecard by environment
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by environment
Test execution scorecard by epic
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by epic
Test execution scorecard by folder
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by folder
Test execution scorecard by iteration
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by iteration

Test execution scorecard by project
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by project
Test execution scorecard by test cycle
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by test cycle
Test execution scorecard by test plan
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by test plan
Test execution scorecard by tester
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by tester
Test execution scorecard by version
Provides a scorecard report with an overview of the test execution results, effort and defects grouped by version
Time Since Chart
Gadget URL
Displays the time since a chosen field for each issue for a project or saved filter.
Tip
Highlights content as a helpful tip with a green background.
Tooltip
Create tooltip for an element
Top 10 defects impacting testing
Provides a list with the top 10 defects that impacts the most number of test cases
Two Dimensional Filter Statistics
Gadget URL
Display statistics of issues returned from specified filter
Unordered List (ul)
Creates an HTML unordered list (ul) tag

User List
Displays a list of Confluence users based on group membership.
User Profile
Displays a user's profile details.
Version Report
Gadget URL
Track the projected release date for a version. This helps you monitor whether the version will release on time, so you can take action if work is falling behind.
Voted Issues
Gadget URL
Shows the issues voted by the current user.
Wallboard Spacer Gadget
Gadget URL
Allows for custom spacing between gadgets in a wallboard
Warning
Highlights content as a warning note with a red background.
Watched Issues
Gadget URL
Shows the issues watched by the current user.

Widget Connector
Embed videos, slideshows, posts, and more from the web.

©
Inserts a copyright symbol

®
Inserts a registered trade mark ®

Π
Inserts a service mark

TM
Inserts a trade mark TM

