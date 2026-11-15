"""A skill correction: one exact span of a skill document and its replacement.

The skills library learns from use (DL-215): the agent that used a skill, or
session-close reflection on its behalf, proposes a correction when the guidance
was wrong or missing a step. Two things make that reviewable rather than a
re-read of a whole document:

  * The correction is EXACT-SPAN. The drafter quotes a span of the document's
    CURRENT text and gives its replacement; the span must occur exactly once.
    That is the read-before-write rule made mechanical: a drafter that has not
    read the current text cannot quote it, and a span that matches nowhere (or
    twice) is refused with a message the drafter can act on, never guessed at.
  * The proposal is built HERE, in code, at propose time: the full resulting
    document is captured into an ordinary `skill.doc_add` action (the Executor
    writes exactly those bytes), the unified diff rides in `expected_effect`,
    and the version the span was applied to is stamped as `base_version`. The
    Executor re-checks that version (`executor._check_skill_doc_add`), so a
    correction approved after the document moved is refused as world-moved
    instead of silently undoing the newer version.

Runtime tier: reads the library through `repo`, imports nothing from the
gateway.
"""

from __future__ import annotations

import difflib

from central_command.contract import Action, Evidence, Proposal, Reversibility
from central_command.db import repo


class SpanMismatch(ValueError):
    """The quoted span does not occur exactly once in the current text."""

    def __init__(self, count: int):
        self.count = count
        super().__init__(
            "the span was not found in the document's current text"
            if count == 0 else
            f"the span occurs {count} times in the document's current text"
        )


def apply_exact_span(text: str, find: str, replace: str) -> str:
    """`text` with the ONE occurrence of `find` replaced by `replace`.

    Exact: no normalisation of whitespace or case, because the result is the
    document the library will hold and the drafter must be quoting what is
    there. Raises `SpanMismatch` unless `find` occurs exactly once."""
    if not find:
        raise SpanMismatch(0)
    count = text.count(find)
    if count != 1:
        raise SpanMismatch(count)
    return text.replace(find, replace, 1)


def unified_diff(old: str, new: str, label: str) -> str:
    return "\n".join(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{label} (current)", tofile=f"{label} (corrected)", lineterm="",
    ))


async def current_doc(skill_id: str, doc_key: str) -> tuple[dict | None, dict | None]:
    """The skill row and its CURRENT version of `doc_key` (either may be None)."""
    skill = await repo.get_skill(skill_id)
    if skill is None:
        return None, None
    docs = await repo.list_skill_docs(skill_id)
    return skill, next((d for d in docs if d["doc_key"] == doc_key and d.get("is_current")), None)


def _retry_text(skill_id: str, doc_key: str, detail: str) -> str:
    return (
        f"refused: {detail} of '{doc_key}' on skill '{skill_id}'. Nothing was "
        "proposed. `find` must be copied character for character from the "
        "document's CURRENT text and occur exactly once — load the skill "
        "again (or search its reference) and quote a longer, unique span."
    )


async def build_correction(
    *, agent_id: str, skill_id: str, doc_key: str, find: str, replace: str, why: str,
) -> Proposal | str:
    """The correction proposal, or the refusal text to hand back to the drafter.

    Everything the operator reviews is computed here from the CURRENT document:
    the full corrected content (what the Executor writes), the diff (in
    `expected_effect`, beside the Inbox's own diff against the current version)
    and `base_version` (what the Executor re-checks)."""
    skill_id, doc_key = (skill_id or "").strip(), (doc_key or "guidance").strip()
    if not skill_id:
        return "refused: skill_id is required"
    if not (why or "").strip():
        return ("refused: `why` is required — state the rule or pitfall the "
                "correction teaches, in a sentence or two")
    if find == replace:
        return "refused: `replace` is identical to `find`; there is nothing to correct"
    skill, doc = await current_doc(skill_id, doc_key)
    if skill is None:
        return f"refused: there is no skill '{skill_id}' in the library"
    if skill.get("status") == "RETIRED":
        return f"refused: the skill '{skill_id}' is retired"
    if doc is None:
        return (f"refused: skill '{skill_id}' has no current document '{doc_key}'. "
                "A correction changes a document you have read; to add a new "
                "reference document use propose_skill_doc instead.")
    try:
        content = apply_exact_span(doc["content"], find, replace)
    except SpanMismatch as e:
        return _retry_text(skill_id, doc_key, str(e))
    if not content.strip():
        return "refused: the correction would leave the document empty"

    label = f"{skill_id}/{doc_key} v{doc['version']}"
    diff = unified_diff(doc["content"], content, label)
    arguments = {
        "skill_id": skill_id, "doc_key": doc_key, "title": doc["title"],
        "content": content,
        # The version the span was applied to: stamped by THIS code, never the
        # model (`park_proposal` strips one an agent wrote itself).
        "base_version": doc["version"],
        "correction": {"find": find, "replace": replace},
        **({"describes": doc["describes"]} if doc.get("describes") else {}),
    }
    return Proposal(
        intent=f"Correct the '{doc_key}' document of the skill '{skill_id}': {why.strip()}",
        actions=[Action(
            capability="skill.doc_add@v1",
            arguments=arguments,
            target_ref={"system": "central_command", "id": skill_id},
            reversibility=Reversibility.reversible,
        )],
        evidence=[Evidence(
            kind="authored",
            source_ref=f"agent:{agent_id}",
            locator=f"skill_doc {skill_id}/{doc_key} v{doc['version']} (the span quoted in `correction.find`)",
            claim=why.strip(),
        )],
        expected_effect=(
            f"skill {skill_id} carries '{doc_key}' v{doc['version'] + 1}: v{doc['version']} "
            "with exactly this change, refused at execution if the document has "
            f"moved past v{doc['version']}:\n{diff}"
        ),
    )
