# How Central Command works, in diagrams

A short set of pages for seeing the system before reading about it. Each page
is one topic: a diagram, a table that maps it to the code, and a list of what
it leaves out. The reference for everything here is
[ARCHITECTURE.md](../ARCHITECTURE.md).

| Page | What it shows |
|---|---|
| [System overview](system-overview.md) | The four tiers and the trust boundary |
| [Life of a mail](life-of-a-mail.md) | One mail, from arrival to an executed write |
| [The three verdicts](three-verdicts.md) | What Approve, Reject and Dismiss each set in motion |
| [Who approves](who-approves.md) | The operator, the auditor, and a standing gate override |
| [Outage handling](outage-handling.md) | What happens when a dependency is down |
| [Data model](data-model.md) | The Postgres tables, by area (generated) |
| [Deployment](deployment.md) | The single-node and multi-node layouts, and what an update does |

## Keeping these true

- **The data model page is generated.** Run
  `python scripts/gen_data_model_diagram.py` after changing `schema.sql`; never
  edit the page by hand.
- **The other pages are hand-drawn.** A change to a flow shown here updates its
  page in the same release.
- **These pages are for people.** They describe what the code does; the code
  and its tests remain the authority.
