# Where this runs

Facts about deployment that hold for every Central Command installation, and
where this installation's own facts come from. Do not reason about the
infrastructure from general knowledge. If what you observe contradicts this
document, declare a `stale_knowledge` gap citing it and what you saw. Last
reviewed 2026-10-22 against the public tree.

## This deployment's own facts are not in this file

Hostnames, addresses, node names, hardware, the model-server layout and the
like are **instance facts**. They belong to a gitignored instance overlay on
this skill (PRD FR-47), never to the public copy, which carries none on
purpose. Until the overlay is loaded for this deployment, the answer to "where
exactly does X run" is a `missing_knowledge` gap naming the overlay — never a
guess from the profile descriptions below.

## Two profiles, one application

- **Single-node** (`deploy/single/`): one host, a compose stack for the
  stores, and the API, cockpit and sandbox runner as host processes under one
  supervisor. One `.env` holds every answer; one driver installs and updates
  it, recording each step in a ledger.
- **Multi-node k3s** (`deploy/k3s/`): the operator's own instance of the
  single-node profile, never a superset. Numbered manifests applied with
  kubectl and a trigger-file updater; the API, cockpit and sandbox runner run
  as systemd units on the anchor node, outside the cluster. It must survive
  losing its compute node.

## The placement rule on k3s: split by STATE, not by service

- **Stateless compute floats** between nodes, preferring the compute node and
  failing over in about a minute. The LLM proxy is the service that floats.
- **Stateful services are pinned**, because the storage class stamps a required
  hostname on every volume it provisions — a pod holding one can never move,
  and shared network storage is not a way out (the graph database forbids it
  outright: no file locking means store corruption).

Databases stay on the anchor node *so that* failover works: the memory-hungry
service is the stateless one, so when the compute node goes away it reschedules
onto the anchor where its small database already lives. Knowledge-graph
extraction is not a service any more — it runs inside the API process, so
there is nothing of it to place.

## Things that are true and easy to get wrong

- Every service endpoint the application uses resolves to **loopback**, on
  both profiles. On k3s the cluster's own load balancer carries the floating
  services to whichever node they are on; a hard-coded node address would work
  today and silently break failover.
- On k3s two images are built locally — the sandbox runner and the crawler —
  single-architecture on the compute node by design. The manifest set includes
  the sandbox, its MCP servers, the crawler and the log console alongside the
  spine.
- Two encryption keys must never change: they encrypt stored proxy virtual keys
  and a stored OAuth credential respectively. A restore under a different key
  leaves the rows present and undecryptable.
- Backups are the operator's responsibility. The k3s profile ships a nightly
  backup unit that writes every store and the decryption keys outside the
  repository tree.

## Secrets

Secrets live only in gitignored environment files: one `.env` on the
single-node profile; on k3s the root `.env`, the cockpit's `web/.env` and
`deploy/k3s/.env` (folding those into one file is tied to the Helm chart on the
roadmap). Never quote, echo, or propose writing a secret value — not into a
document, not into an issue, not into a proposal's arguments. If work appears
to require one, that is a request for the operator, not something to route
around.

## The systems of record are external

The issue tracker and the document wiki stay authoritative. This framework
**reads** them freely and **proposes** writes to them through the gate. Never
treat this system's own state as the truth about an issue when the tracker
disagrees — re-read the tracker.
