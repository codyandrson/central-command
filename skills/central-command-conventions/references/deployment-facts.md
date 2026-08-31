# Where this runs (as of 2026-08-31)

Facts about the deployment, so you do not reason about it from general
knowledge. If what you observe contradicts this document, declare a
`stale_knowledge` gap citing it and what you saw — this file is a snapshot and
snapshots age.

## Shape

A **two-node k3s cluster** in a home lab:

- `raspberrypi` — arm64, the k3s control plane.
- `chromebox` — amd64, 8 cores / 15 GB, the agent node.

The control-plane API and the cockpit UI run as systemd units on the Pi, outside
the cluster. A Docker Compose stack still exists in the repo as the rollback
path; it is **not** what runs.

## The placement rule: split by STATE, not by service

- **Stateless compute floats** (prefers the chromebox, fails over in about a
  minute): the LLM proxy and the graph-extraction service.
- **Stateful services are pinned**, because the storage class stamps a required
  hostname on every volume — a pod holding one can never move, and shared
  network storage is not a way out (the graph database forbids it outright: no
  file locking means store corruption).

Databases stay on the Pi *so that* failover works: the memory-hungry service is
the stateless one, so when the chromebox goes away it reschedules onto the Pi
where its small database already lives. Verified by drill.

## Why the split exists

The LLM proxy's bundled query engine leaks memory at gigabytes per minute **on
arm64**, and took the host down three times. Running it on the x86_64 node takes
it off the architecture where the leak was measured. The memory cap that
preceded this was a blast-radius guard, never a fix.

## Things that are true and easy to get wrong

- Every service endpoint the application uses resolves to **loopback**. The
  cluster's own load balancer carries the floating services to whichever node
  they are on. A hard-coded node address would work today and silently break
  failover.
- Three locally-built images exist: the graph-extraction service (one per
  architecture — a floating pod needs it present on **both** nodes, or the
  failure appears only on the day it actually moves), and the sandbox runner
  and crawl service (chromebox-only, single-arch by design). The manifest set
  includes the sandbox, its MCP servers, the crawler, and the log console
  alongside the original spine.
- Two encryption keys must never change: they encrypt stored proxy virtual keys
  and a stored OAuth credential respectively. A restore under a different key
  leaves the rows present and undecryptable.
- Backups run nightly, outside the repository tree, including the decryption
  keys.

## Secrets

Secrets live in one gitignored environment file and nowhere else. Never quote,
echo, or propose writing a secret value — not into a document, not into an
issue, not into a proposal's arguments. If work appears to require one, that is
a request for the operator, not something to route around.

## The systems of record are external

The issue tracker and the document wiki stay authoritative. This framework
**reads** them freely and **proposes** writes to them through the gate. Never
treat this system's own state as the truth about an issue when the tracker
disagrees — re-read the tracker.
