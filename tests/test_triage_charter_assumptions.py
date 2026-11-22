"""The built-in inbox-triage charter tells the agent to confirm its assumptions (DL-212).

Two things are pinned, both by reading the text an agent is actually given.

The rule: a conclusion about the operator's world that no source states is an
assumption, and the agent confirms it from a stating source or asks. The earlier
wording asked only "if you cannot place the sender … and it would change your
handling", which a confident agent never met.

The text's own shape. A charter is prose with placeholders in angle brackets and
numbered steps the later sections refer to. One deployment lost both when its
charter was copied out of a rendered markdown view: `<…>` placeholders parse as
tags and vanish, list numbers are not part of the copied text. The stored text
looked plausible and ran for days. These assertions fail on a charter in that
state, so the built-in at least can never ship that way.
"""

from __future__ import annotations

import re

from central_command.runtime.agent import builtin_charter

CHARTER = builtin_charter("inbox-triage")


def test_an_unstated_conclusion_is_an_assumption_to_confirm_or_ask_about():
    assert "that no source states in words is an ASSUMPTION" in CHARTER
    assert "You never act on an assumption, however plausible it looks; you confirm it first." in CHARTER
    assert "When none states it, ask the operator with ask_operator" in CHARTER


def test_asking_is_keyed_on_what_was_stated_not_on_feeling_unsure():
    assert (
        "Ask whenever you are about to rely on something nobody stated, not only when you feel unsure"
        in CHARTER
    )
    # the trigger this replaced
    assert "If you cannot place the sender or the relationship and it would change your handling" not in CHARTER


def test_an_answer_with_no_facts_records_nothing():
    assert 'An answer that gives no facts ("dismiss", "no", "not needed") means no action and no record' in CHARTER


def test_the_examples_name_no_real_person_or_company():
    assert "Jane Rivers" in CHARTER and "Example Mutual" in CHARTER


def test_the_charter_keeps_its_placeholders_and_step_numbers():
    for placeholder in (
        "<what this email is>",
        "source_ref=<message id or sender>",
        "claim=<ONE passage copied exactly from the email",
        "claim=<their words, QUOTED VERBATIM>",
    ):
        assert placeholder in CHARTER, placeholder
    steps = re.findall(r"^([1-4])\. [A-Z]+", CHARTER, flags=re.MULTILINE)
    assert steps == ["1", "2", "3", "4"], steps
