"""Mail rules (2026-09-22, v2.40.0) — standing inbox rules, the shape Gmail
filters and Outlook rules take, applied by the dispatcher BEFORE any model
call.

Why this exists: the triage agent had no home for "mail from this sender
never needs action" except the shared knowledge graph, so it wrote one
episode per sender (~266 in a fortnight), each costing the operator an
approval, and nothing ever read them back when the next copy was claimed. A
rule is POLICY, not knowledge. It lives in `mail_rule`, is proposed by an
agent (`propose_mail_rule`, gated `mail.create_rule`) or created by the
operator (ungated, the bulk-dismissal precedent), and a matching row folds at
claim time at zero model cost — the one place a memory of this kind actually
saves work.

Semantics, borrowed on purpose (docs fetched 2026-09-22):
- Criteria are AND-combined, all case-insensitive: `from_address` is the
  sender's address exactly; `from_domain` is the address's domain or any
  subdomain of it (Gmail's `from:@example.com` idiom); `subject_contains` and
  `body_contains` are substring matches (Outlook's "contains"). A rule must
  identify the SENDER (address or domain; a phrase alone would take mail
  from anyone who uses it), and a public mailbox domain alone (gmail.com …)
  is refused — that is not a sender, it is everyone.
- `exceptions` carry the same fields; ANY matching exception exempts the row
  (Outlook's "except if").
- Rules apply in `position` order and the first match wins (Outlook's "stop
  processing more rules", on by default), so a narrow exception rule can be
  placed above a broad one later without special cases.
- A rule is previewed against the QUEUE before it is proposed or saved
  (Gmail's search-first flow): the proposal carries the description in
  words, the current match count and sample rows, so what the operator
  approves is what they saw. Whether the queued matches are dismissed on
  approval is an explicit flag (`apply_to_queued`), both products' opt-in.
- An item the operator REOPENS after a rule folded it carries
  `rule_exempt` in its payload and is never matched by a rule again: the
  reopen note means "I want eyes on this", and a rule that keeps taking it
  back would be a loop the operator cannot escape.

Pure matching lives here (`matches`, `describe`) so it is testable without a
database; the SQL twin for previews and sweeps is `repo.mail_rule_matches`
and `tests/test_mail_rules.py` pins the two to each other.
"""

from __future__ import annotations

import re

CRITERIA_FIELDS = ("from_address", "from_domain", "subject_contains", "body_contains")

# A domain that identifies a PROVIDER, not a sender — a rule on it alone would
# cover every stranger who uses that provider. Mirrors the generic-provider
# list `work_item.sender_key` uses (schema.sql).
PUBLIC_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "pm.me", "comcast.net", "att.net", "verizon.net",
    "sbcglobal.net",
})

_MIN_CONTAINS = 3
_ADDRESS_RE = re.compile(r"<([^>]+)>")


def normalize(criteria: dict | None) -> dict:
    """Lower-cased, stripped, empty fields dropped, unknown fields refused."""
    out: dict[str, str] = {}
    for key, value in (criteria or {}).items():
        if key not in CRITERIA_FIELDS:
            raise ValueError(f"unknown rule field {key!r} (known: {', '.join(CRITERIA_FIELDS)})")
        if value is None:
            continue
        text = str(value).strip().lower()
        if not text:
            continue
        if key == "from_domain":
            text = text.lstrip("@")
        out[key] = text
    return out


def validate(criteria: dict, exceptions: dict | None = None) -> tuple[dict, dict]:
    """The rule's shape rules, raised as ValueError with the operator-facing
    reason. Returns the normalized (criteria, exceptions)."""
    crit = normalize(criteria)
    exc = normalize(exceptions)
    if not crit:
        raise ValueError(
            "a rule needs at least one criterion: from_address, from_domain, "
            "subject_contains or body_contains"
        )
    if not (crit.keys() & {"from_address", "from_domain"}):
        raise ValueError(
            "a rule must identify the sender (from_address or from_domain) — a "
            "subject or body phrase alone would take mail from anyone who uses it"
        )
    for key in ("subject_contains", "body_contains"):
        for bag, label in ((crit, "criteria"), (exc, "exceptions")):
            if key in bag and len(bag[key]) < _MIN_CONTAINS:
                raise ValueError(
                    f"{label}.{key} must be at least {_MIN_CONTAINS} characters — "
                    f"{bag[key]!r} would match almost everything"
                )
    if "from_address" in crit and "@" not in crit["from_address"]:
        raise ValueError(f"from_address {crit['from_address']!r} is not an email address")
    if "from_domain" in crit and ("@" in crit["from_domain"] or "." not in crit["from_domain"]):
        raise ValueError(f"from_domain {crit['from_domain']!r} is not a domain")
    if (
        "from_domain" in crit
        and crit["from_domain"] in PUBLIC_MAIL_DOMAINS
        and not (crit.keys() - {"from_domain"})
    ):
        raise ValueError(
            f"from_domain {crit['from_domain']!r} alone names a mailbox provider, "
            "not a sender — every stranger on it would match; add from_address "
            "or a subject/body criterion"
        )
    return crit, exc


def describe(criteria: dict, exceptions: dict | None = None) -> str:
    """The rule in plain words — what the operator reads on the proposal and
    in the rules list. Generated, never agent-written, so it cannot drift from
    what the rule actually matches."""
    crit, exc = normalize(criteria), normalize(exceptions)
    parts = []
    if "from_address" in crit:
        parts.append(f"from {crit['from_address']}")
    if "from_domain" in crit:
        parts.append(f"from anyone at {crit['from_domain']} (or a subdomain)")
    if "subject_contains" in crit:
        parts.append(f"whose subject contains \"{crit['subject_contains']}\"")
    if "body_contains" in crit:
        parts.append(f"whose body contains \"{crit['body_contains']}\"")
    text = "Auto-dismiss mail " + " and ".join(parts)
    ex = []
    if "from_address" in exc:
        ex.append(f"it is from {exc['from_address']}")
    if "from_domain" in exc:
        ex.append(f"it is from anyone at {exc['from_domain']}")
    if "subject_contains" in exc:
        ex.append(f"the subject contains \"{exc['subject_contains']}\"")
    if "body_contains" in exc:
        ex.append(f"the body contains \"{exc['body_contains']}\"")
    if ex:
        text += ", except when " + " or ".join(ex)
    return text + "."


def sender_address(from_header: str | None) -> str:
    """The bare address in a From header, lower-cased: `Name <a@b>` → `a@b`."""
    raw = (from_header or "").strip().lower()
    m = _ADDRESS_RE.search(raw)
    return (m.group(1) if m else raw).strip()


def item_fields(item: dict) -> dict:
    """The matchable view of a work item: sender address, subject, and the
    body BELOW the header block `ledger.agent_input` prepends (a body match on
    "From:" would otherwise hit every row)."""
    payload = item.get("payload") or {}
    text = payload.get("text") or ""
    head, sep, body = text.partition("\n\n")
    body = body if sep else text
    return {
        "address": sender_address(payload.get("from")),
        "subject": (item.get("subject") or "").lower(),
        "body": body.lower(),
    }


def _fields_match(spec: dict, fields: dict) -> bool:
    """AND over every present field of one spec (criteria or exceptions)."""
    if "from_address" in spec and fields["address"] != spec["from_address"]:
        return False
    if "from_domain" in spec:
        domain = fields["address"].rpartition("@")[2]
        d = spec["from_domain"]
        if not (domain == d or domain.endswith("." + d)):
            return False
    if "subject_contains" in spec and spec["subject_contains"] not in fields["subject"]:
        return False
    if "body_contains" in spec and spec["body_contains"] not in fields["body"]:
        return False
    return True


def matches(rule: dict, item: dict) -> bool:
    """Does this ACTIVE rule take this item? Criteria all hold, and no
    exception does. An operator-reopened item (`rule_exempt`) never matches."""
    if rule.get("revoked_at"):
        return False
    if (item.get("payload") or {}).get("rule_exempt"):
        return False
    fields = item_fields(item)
    crit = normalize(rule.get("criteria"))
    if not crit or not _fields_match(crit, fields):
        return False
    exc = normalize(rule.get("exceptions"))
    return not (exc and _fields_match(exc, fields))


def first_match(rules: list[dict], item: dict) -> dict | None:
    """First active rule, in position order, that takes the item — or None."""
    for rule in sorted(rules, key=lambda r: (r.get("position") or 0, r.get("created_at") or "")):
        if matches(rule, item):
            return rule
    return None


def dismissal_rationale(rule: dict) -> str:
    return f"mail rule {rule['id']}: {rule['description']} ({rule['reason']})"
