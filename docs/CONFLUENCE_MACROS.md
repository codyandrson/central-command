# CONFLUENCE_MACROS.md — inventorying the Confluence instance's macros

The operator's guide to telling agents which macros the Confluence site really
has. Confluence has no universal macro set: what renders on one site is an
"unknown macro" placeholder on another, and a wrong macro name or parameter
key does not error, it sits broken on a page someone may already be reading.
The Confluence skill therefore composes only from an allowlist of
instance-verified recipes, and this guide is how the operator produces that
allowlist and gets it in front of agents. For the rule agents follow see
`skills/confluence/references/macro-discovery.md` and `instance-profiles.md`;
for the tool's output and exit codes see "Run it" below.

Nothing in this guide is about one particular site. Examples use role names
and `example.com`; the inventory itself is an instance fact and never enters
the checkout (see "Where the file goes").

## When to run it

Run the inventory at three moments, all manual — nothing detects them for you
(a marketplace app's version needs Confluence admin rights to read, so no
automatic trigger is possible from an agent's account):

1. **Onboarding.** A separate, optional session after the install, not a step
   of it. The setup skill offers it.
2. **After a marketplace app is installed or upgraded** on the Confluence
   site. An upgrade can change a macro's parameter surface, so the recipe
   harvested before it may be wrong now.
3. **When the Confluence version moved.** The probe prints the instance
   version, and the inventory carries the version it was harvested from as
   `instance_version:`. Compare them by eye:

   ```bash
   python scripts/atlassian_probe.py --confluence-only
   # ... INFO confluence version=cloud (<build>) — compare with `instance_version:` ...
   ```

   Cloud reports `cloud` plus a build; Server/Data Center reports its product
   version (the Server/DC path is VERIFY-ON-SITE: coded to Atlassian's
   published reference, not proven against a live instance). A different value
   than the file's stamp means the file predates the site.

## Build the sampler page

The Confluence macro browser generates correct-by-construction storage
markup: the real parameter names, the real `ac:schema-version`, the installed
app's own defaults. An agent inventing the same markup from guesses is exactly
the failure this guide exists to prevent, so the operator builds the page by
hand:

1. Create a page titled **Central Command Macro Sampler** (the default title;
   `--sampler-title` names another) in a space the Confluence account in
   `.env` can read.
2. Insert **one instance of every macro you want agents to compose**, through
   the macro browser, with simple defaults. Built-ins and marketplace apps
   alike.
3. Publish the page. Keep real content out of the macro bodies: the block is
   copied into the inventory whole, and agents read the inventory.

Only the sampler page yields a recipe. For a macro that exists only on
ordinary pages, the inventory records how many pages use it and the first
page's id, space key and a reduced title, and no block: page text is untrusted
and never flows into the file agents read. To get a recipe for such a macro,
add it to the sampler page and run the inventory again.

## Run it

From the checkout, on the API host, with the checkout's `.env` (the same
credentials `scripts/atlassian_probe.py` uses). **Dry run first**: it does
every read and prints the inventory, and writes nothing.

```bash
python scripts/confluence_macro_inventory.py --dry-run
```

Read it, then run it for real:

```bash
python scripts/confluence_macro_inventory.py
```

Progress and errors go to stderr; with `--dry-run` stdout carries only the
inventory, so `> inventory.md` captures it.

| Flag | Meaning |
|---|---|
| `--dry-run` | Print the inventory; write nothing. |
| `--out PATH` | Write here instead of the default. A path inside the checkout is refused. |
| `--sampler-title TITLE` | The sampler page's title (default: `Central Command Macro Sampler`). |
| `--sampler-id ID` | Pin the sampler by its numeric page id and skip the title search. Use it once the page exists. |
| `--sampler-space KEY` | Narrow the title search to one space key. |
| `--macro NAME` | An extra macro to inventory, by its storage `ac:name` (not its display name); repeatable. |

**Exit codes.** `0` written or printed. `1` no page with that title, or
several: the run refuses to guess which one is yours; pin it with
`--sampler-id`. `2` Confluence is not configured, the connection failed, the
credentials were refused, or an argument was refused (an `--out` inside the
checkout, a malformed `--sampler-id`, a `--macro` that is not a plain name).

**What the file says.** A header, then one section per macro:

```
# Confluence macro inventory

harvested: 2026-11-01
instance_version: cloud (<build>)
flavor: cloud
sampler_page: Central Command Macro Sampler (id <page id>)
sampler_identified_by: page id

## acme-grid

- ac:name: acme-grid
- schema version: 3
- pages using it: 12
- on the sampler page: yes
- first page found: id <page id>, space <KEY>, title (quoted data): "Quarterly numbers"
- example, from the sampler page:

  <the block, exactly as the macro browser wrote it>
```

- `harvested` and `instance_version` are the stamp. A Server/DC version is
  marked VERIFY-ON-SITE; a failed version read stamps `unknown`.
- `sampler_identified_by` is `page id`, `space and title` or `title only`. A
  title-only sampler is unproven: the title does not say who built the page.
  Pin it with `--sampler-id` and run again.
- `pages using it` is capped at the client's bounded crawl and reads `500+`
  when truncated, never a count that looks exact.
- The candidates are the sampler's blocks, the macro names the skill's
  intent-to-macro table gives in backticks (the built-ins), and any `--macro`
  names.

## Where the file goes

By default to the active profile's overlay field file in the skill overlay
directory, **outside the checkout**: `confluence_profile_cloud.md` or
`confluence_profile_server.md`, by `CC_CONFLUENCE_API_FLAVOR`, under
`CC_SKILL_OVERLAY_DIR` (default `<state dir>/skill-overlay`). The inventory
is an instance fact: a path inside the checkout is refused before any
request, and the updater's `reset --hard` never touches the overlay. The file
is written owner-only. `--out` names another place outside the checkout.

## How the result reaches agents

Agents read stored text, not files. The overlay field is rendered into the
skill library when the skill is **imported**, so writing the file changes
nothing an agent sees. Re-import the Confluence skill:

1. In the cockpit, open the Skills page and choose **Import a skill folder**.
2. Folder path (on the API host): the checkout's `skills/confluence`. Skill
   id: `confluence`, to say this refreshes the existing skill. The import adds
   a new version of each document; nothing is overwritten and earlier versions
   stay readable.

To check which overlay fields resolve before importing (read-only, prints no
value):

```bash
python -m central_command.skills.overlay check
```

The skill's `instance-profiles` reference carries the markers
`<!-- overlay: confluence_profile_cloud -->` and
`<!-- overlay: confluence_profile_server -->` where the file's text lands.

## After onboarding: refreshing

Agents never run this script. After onboarding, an agent that refreshes the
sampler page does it through the `confluence-propose` pack, like any other
Confluence write: the operator approves each page create or update in the
inbox. The inventory is then re-run by the operator (pinned with
`--sampler-id`) and the skill re-imported, exactly as above.

## Related

- `skills/confluence/references/macro-discovery.md` — the per-use procedure and
  the sampler-page harvest this guide automates.
- `skills/confluence/references/instance-profiles.md` — the profile concept
  and where the overlay field lands.
- `scripts/atlassian_probe.py` — connectivity and the version line.
- `docs/ARCHITECTURE.md` — the skill overlay and why an edit needs a re-import.
