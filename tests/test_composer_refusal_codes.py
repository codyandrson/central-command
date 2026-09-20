"""A new refusal code has to be decided about, not defaulted about.

`why_not_sendable` is THE statement of when the operator may type into a
session. `nerve_gateway._COMPOSER_DISABLING` is the cockpit's half of the same
rule: a code in that tuple replaces the message box with an explanation, and a
code absent from it leaves an enabled box.

The tuple is an ALLOWLIST, not a check — it names the codes for which the
composer goes away. That asymmetry is the whole hazard. Omit a code and nothing
raises, nothing logs, and the cockpit renders a perfectly normal message box
over a session that `_chat_send` will 409. The operator types, hits enter, and
is told no by a surface that invited them. That is the 2026-07-25 bug the pair
was built to end, and `why_not_sendable`'s own docstring warns about repeating
it: "Adding a code means adding it to `nerve_gateway._COMPOSER_DISABLING` too
if the operator should not type."

So this collects every code the gateway can put in front of the composer —
from `why_not_sendable`'s returns and from `_send_target`'s own blocks — and
requires each one to be classified. Either it disables the composer (it is in
the tuple), or it is named below with the reason it deliberately does not. A
code that is neither fails here, in the commit that adds it, which is the only
moment anyone is in a position to decide.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from central_command.api.nerve_gateway import _COMPOSER_DISABLING

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"
CONVERSE = SRC / "runtime" / "converse.py"
NERVE = SRC / "api" / "nerve_gateway.py"

# Codes that deliberately leave the composer ENABLED, each with the reason.
# This is the other half of the decision — not an escape hatch, a record.
DELIBERATELY_NOT_DISABLING = {
    "turn_in_progress": (
        "transient: nothing is wrong and nothing needs deciding. The cockpit "
        "already shows the turn running, and the state clears itself when the "
        "turn lands — replacing the composer would make the operator re-find "
        "the lane a few seconds later"
    ),
}


def _codes_from_why_not_sendable() -> set[str]:
    """Every code `why_not_sendable` can return — read off the `return (code,
    message)` tuples rather than the docstring's list, which is prose and can
    drift from the returns beneath it."""
    tree = ast.parse(CONVERSE.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "why_not_sendable"
    )
    codes = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple)
                and node.value.elts
                and isinstance(node.value.elts[0], ast.Constant)
                and isinstance(node.value.elts[0].value, str)):
            codes.add(node.value.elts[0].value)
    return codes


def _codes_from_send_target() -> set[str]:
    """`_send_target` blocks on its own two codes before it ever consults
    `why_not_sendable` — a key that names no session, and a key that names no
    lane. Both reach `_composer_state` by the same path."""
    tree = ast.parse(NERVE.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_send_target"
    )
    codes = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Dict) and node.keys):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "blocked"):
                continue
            if not isinstance(value, ast.Tuple) or len(value.elts) < 2:
                continue
            code = value.elts[1]
            if isinstance(code, ast.Constant) and isinstance(code.value, str):
                codes.add(code.value)
    return codes


ALL_CODES = sorted(_codes_from_why_not_sendable() | _codes_from_send_target())


def test_the_walks_see_the_codes_they_are_guarding():
    """Both readers must actually find codes: a parser that returned an empty
    set would make every assertion below vacuously true forever."""
    from_rule = _codes_from_why_not_sendable()
    from_gateway = _codes_from_send_target()
    assert {"not_a_conversation", "pending_proposal", "turn_in_progress"} <= from_rule, (
        f"the reader of runtime/converse.py:why_not_sendable found {sorted(from_rule)} "
        "— it has stopped seeing the `return (code, message)` tuples it parses"
    )
    assert from_gateway == {"no_session", "no_lane"}, (
        f"_send_target's own block codes read as {sorted(from_gateway)}; it "
        "used to block on exactly no_session and no_lane. If it gained one, "
        "that code needs classifying below like any other"
    )
    assert len(ALL_CODES) >= 10


@pytest.mark.parametrize("code", ALL_CODES)
def test_every_refusal_code_is_classified(code):
    disabling = code in _COMPOSER_DISABLING
    deliberate = code in DELIBERATELY_NOT_DISABLING
    assert disabling != deliberate, (
        f"the refusal code {code!r} has not been decided about "
        f"(in _COMPOSER_DISABLING: {disabling}; listed as deliberately "
        f"non-disabling here: {deliberate}).\n"
        "`_COMPOSER_DISABLING` is an ALLOWLIST, not a check: a code missing "
        "from it renders an ENABLED message box over a session `_chat_send` "
        "will 409 — the operator types, hits enter, and is refused by a "
        "surface that invited them.\n"
        "Pick one: add it to `_COMPOSER_DISABLING` in "
        "central_command/api/nerve_gateway.py if the operator should not type, "
        "or add it to DELIBERATELY_NOT_DISABLING in this file with the reason "
        "it stays enabled (the only entry today is `turn_in_progress`, which "
        "clears itself)."
    )


def test_the_allowlist_names_no_code_the_gateway_cannot_emit():
    """The reverse drift: a code that was removed from `why_not_sendable` but
    left in the tuple reads as a live rule and is dead text."""
    stale = sorted(set(_COMPOSER_DISABLING) - set(ALL_CODES))
    assert stale == [], (
        f"_COMPOSER_DISABLING names {stale}, which nothing can emit any more. "
        "Drop the entry — a rule nobody can trigger is how the next reader "
        "learns to skim the tuple instead of trusting it"
    )


def test_the_classifications_do_not_overlap():
    overlap = sorted(set(_COMPOSER_DISABLING) & set(DELIBERATELY_NOT_DISABLING))
    assert overlap == [], (
        f"{overlap} are listed both as composer-disabling and as deliberately "
        "not disabling — one of the two records is wrong, and the code's "
        "behaviour follows the tuple"
    )
