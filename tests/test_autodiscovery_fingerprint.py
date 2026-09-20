"""What "changed" means to autodiscovery's memory — the one part of it nothing
was pinning.

DL-066 (autodiscovery has memory; only new/changed/failed ids reach the agent)
is otherwise well guarded already, and those tests are not duplicated here:

- `tests/test_heartbeat.py::test_reconcile_only_new_changed_or_failed_reach_the_agent`
  is the rule itself, over every disposition at once;
- `...::test_reconcile_reoffers_a_registered_model_that_left_the_proxy`,
  `...::test_reconcile_adopts_a_bootstrapped_fingerprint_without_retasking` and
  `...::test_bootstrap_snapshot_reads_prior_add_tasks` cover the edges;
- `...::test_a_second_pass_does_not_retask_examined_models` drives the whole
  loop through `ACTIONS["litellm.discovery"]` across four passes, including the
  snapshot being written back to `app_setting`.

What none of them touch is `FINGERPRINT_FIELDS` — the tuple that decides what
"changed" IS. Every one of those tests moves `shutdown_date` or `display_name`,
so the tuple could lose `id` or `pricing` and they would all still pass: a
model whose price doubled overnight would simply never come back to the
operator. The failure in the other direction is worse and just as quiet: add a
field the provider rewrites on every fetch (a timestamp, a rate-limit counter,
a `created` epoch) and every id's fingerprint changes every pass, which
restores the daily re-examination of the same 60 dated snapshots that the
memory was built to end — as a flood of tasks nobody asked for, not as an error.

So this pins the tuple's contents and its two properties: each listed field
moves the fingerprint, and nothing else does.
"""

from __future__ import annotations

from central_command.heartbeat import actions as hb_actions

# The fields a catalog entry is judged by, and why each one is worth re-asking
# the operator about. Anything not here is deliberately invisible to the memory.
EXPECTED_FIELDS = {
    "id": "the model's identity; a different id is a different model entirely",
    "shutdown_date": "the vendor announced a retirement — the operator needs to know",
    "display_name": "a rename usually signals a version or tier change behind it",
    "pricing": "cost is the whole reason a model choice gets revisited",
}


def test_the_fingerprint_fields_are_the_agreed_four():
    assert set(hb_actions.FINGERPRINT_FIELDS) == set(EXPECTED_FIELDS), (
        "FINGERPRINT_FIELDS changed. It decides what autodiscovery calls a "
        "CHANGE, and both directions fail silently:\n"
        "  - dropping a field means a real change (a doubled price, an "
        "announced shutdown) never comes back to the operator at all;\n"
        "  - adding a field the provider rewrites on every fetch — a "
        "timestamp, a counter, a `created` epoch — makes every id look changed "
        "every pass, which is the daily re-examination of the same catalog "
        "this memory exists to end.\n"
        f"Expected {sorted(EXPECTED_FIELDS)}, got "
        f"{sorted(hb_actions.FINGERPRINT_FIELDS)}. If the change is "
        "deliberate, say so here with the reason, the way the others are."
    )


def test_every_fingerprint_field_actually_moves_the_fingerprint():
    """A field listed but not read would be a rule nobody enforces — the
    comprehension in `catalog_fingerprint` drops keys absent from the entry, so
    a typo'd field name is invisible rather than loud."""
    base = {"id": "vendor/model-a", "shutdown_date": None,
            "display_name": "Model A", "pricing": {"input": 1.0}}
    baseline = hb_actions.catalog_fingerprint(base)
    for field in hb_actions.FINGERPRINT_FIELDS:
        moved = dict(base, **{field: "something-else"})
        assert hb_actions.catalog_fingerprint(moved) != baseline, (
            f"{field!r} is in FINGERPRINT_FIELDS but changing it does not "
            "change the fingerprint, so a catalog entry that moved on that "
            "field never reaches the agent — the field is decorative"
        )


def test_a_field_outside_the_tuple_never_looks_like_a_change():
    """The volatile-field flood, stated as a test: everything the provider
    sends that is NOT in the tuple must be invisible to the memory."""
    base = {"id": "vendor/model-a", "display_name": "Model A"}
    baseline = hb_actions.catalog_fingerprint(base)
    for noisy, value in (
        ("created", 1758300000),
        ("fetched_at", "2026-09-20T04:00:00Z"),
        ("rate_limit_remaining", 4711),
        ("description", "a long blurb the vendor rewrites"),
        ("context_length", 200000),
    ):
        assert hb_actions.catalog_fingerprint(dict(base, **{noisy: value})) == baseline, (
            f"a catalog entry carrying {noisy!r} fingerprints differently from "
            "one without it. Every id would then look CHANGED on the first "
            "pass after the provider started sending that key, and the "
            "operator would get a review task for the whole catalog"
        )


def test_the_fingerprint_is_order_and_absence_stable():
    """Two entries that say the same thing must hash the same, whatever order
    the provider's JSON arrived in — otherwise a re-serialised catalog reads as
    a catalog that moved."""
    a = {"id": "m", "display_name": "M", "pricing": {"input": 1, "output": 2}}
    b = {"pricing": {"output": 2, "input": 1}, "display_name": "M", "id": "m"}
    assert hb_actions.catalog_fingerprint(a) == hb_actions.catalog_fingerprint(b)
    # An absent field and an explicitly-null one are different answers on
    # purpose: "the vendor said nothing" is not "the vendor said no shutdown".
    assert (hb_actions.catalog_fingerprint({"id": "m"})
            != hb_actions.catalog_fingerprint({"id": "m", "shutdown_date": None}))


def test_the_snapshot_setting_key_is_the_one_the_action_reads_back():
    """The memory only persists if the reader and the writer agree on the
    `app_setting` key; a rename on one side loses every disposition and
    re-offers the whole catalog once, silently."""
    assert hb_actions.SNAPSHOT_SETTING == "autodiscovery_snapshot"
    source = (__import__("pathlib").Path(hb_actions.__file__)).read_text(encoding="utf-8")
    assert source.count("SNAPSHOT_SETTING") >= 2, (
        "the snapshot setting name is referenced fewer than twice in "
        "heartbeat/actions.py — a literal key string has probably crept in "
        "beside the constant, which is how the two halves drift apart"
    )
