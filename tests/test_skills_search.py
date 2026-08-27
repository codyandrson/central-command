"""Chunking and reference search.

Chunking is pure and always runs. Search needs a real Postgres — the ranking is
`ts_rank` over a generated tsvector, so a mock would be testing a fake.
"""

from __future__ import annotations

import pytest

from central_command.db import repo
from central_command.skills.chunking import split_markdown
from tests.conftest import needs_pg

pytestmark_db = needs_pg


# --- chunking (pure) ---------------------------------------------------------


def test_splits_on_headings():
    content = "# One\nalpha\n\n# Two\nbeta\n\n# Three\ngamma"
    chunks = split_markdown(content)
    assert len(chunks) == 3
    assert chunks[0].startswith("# One")
    assert "alpha" in chunks[0]
    assert "beta" in chunks[1]


def test_preamble_before_the_first_heading_is_kept():
    """Front matter and intros carry real information — dropping anything above
    the first heading would silently lose it."""
    content = "intro text\n\n# One\nalpha"
    chunks = split_markdown(content)
    assert "intro text" in chunks[0]


def test_oversized_section_splits_on_paragraphs():
    body = "\n\n".join(["word " * 100] * 10)   # ~5000 chars in one section
    chunks = split_markdown("# Big\n" + body, max_chars=2000)
    assert len(chunks) > 1
    assert all(len(c) <= 2000 for c in chunks)


def test_empty_content_yields_no_chunks():
    assert split_markdown("") == []
    assert split_markdown("   \n\n  ") == []


# --- search (needs Postgres) -------------------------------------------------


@pytest.fixture()
async def clean_skills():
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-%'")
    finally:
        await conn.close()
    yield
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-%'")
    finally:
        await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_search_finds_the_matching_chunk_with_its_citation(clean_skills):
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "keys", "reference", "Key management",
        "# Generate a key\nPOST /key/generate accepts budget_duration and max_budget.\n\n"
        "# Delete a key\nPOST /key/delete takes a list of keys.",
        source_url="https://docs.litellm.ai/keys",
        describes="LiteLLM proxy v1.55",
    )

    hits = await repo.search_skill_chunks("cc-test-lite", "budget duration")
    assert hits, "expected the generate-key chunk to match"
    assert "budget_duration" in hits[0]["content"]
    assert hits[0]["title"] == "Key management"
    assert hits[0]["source_url"] == "https://docs.litellm.ai/keys"
    assert hits[0]["describes"] == "LiteLLM proxy v1.55"


@pytestmark_db
@pytest.mark.asyncio
async def test_superseded_versions_are_not_searchable(clean_skills):
    """A superseded doc stays readable in history but must never come back to an
    agent as fact — that is the whole point of versioning it."""
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "keys", "reference", "Key management",
        "# Keys\nThe zzzuniquetoken endpoint is current.",
    )
    assert await repo.search_skill_chunks("cc-test-lite", "zzzuniquetoken")

    await repo.add_skill_doc(
        "cc-test-lite", "keys", "reference", "Key management",
        "# Keys\nThat endpoint was removed.",
    )
    assert await repo.search_skill_chunks("cc-test-lite", "zzzuniquetoken") == []


@pytestmark_db
@pytest.mark.asyncio
async def test_search_is_scoped_to_one_skill(clean_skills):
    """A skill's search tool must never leak another skill's material — the
    agent asked LiteLLM a question, not the whole library."""
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.create_skill("cc-test-jira", "Jira", "Issue tracking")
    await repo.add_skill_doc(
        "cc-test-jira", "api", "reference", "Jira API",
        "# Transitions\nzzzuniquetoken lists legal transitions.",
    )
    assert await repo.search_skill_chunks("cc-test-lite", "zzzuniquetoken") == []
    assert await repo.search_skill_chunks("cc-test-jira", "zzzuniquetoken")


@pytestmark_db
@pytest.mark.asyncio
async def test_limit_is_honoured_below_the_raised_cap(clean_skills):
    # The hard clamp was raised from 10 to 500 (sized to the deployment
    # ceiling, n_ctx=262144 — see central_command/db/repo.py:search_skill_chunks).
    # A request for fewer than 500 hits should get every match, not a
    # leftover small-context cap.
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    body = "\n\n".join(f"# Section {i}\nzzzuniquetoken appears here." for i in range(30))
    await repo.add_skill_doc("cc-test-lite", "big", "reference", "Big", body)

    hits = await repo.search_skill_chunks("cc-test-lite", "zzzuniquetoken", limit=99)
    assert len(hits) == 30


@pytestmark_db
@pytest.mark.asyncio
async def test_limit_is_still_clamped_at_the_raised_cap(clean_skills):
    # The clamp is not removed outright — it protects an FTS index scan, not
    # a text-volume budget — just raised. Requesting far more than the cap
    # still tops out at 500.
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    body = "\n\n".join(f"# Section {i}\nzzzuniquetoken appears here." for i in range(30))
    await repo.add_skill_doc("cc-test-lite", "big", "reference", "Big", body)

    hits = await repo.search_skill_chunks("cc-test-lite", "zzzuniquetoken", limit=100_000)
    assert len(hits) <= 500


# --- final-review I4: AND-over-a-long-query ----------------------------------


@pytestmark_db
@pytest.mark.asyncio
async def test_a_long_natural_language_query_falls_back_to_any_term(clean_skills):
    """`websearch_to_tsquery` joins bare terms with `&`, so a six-word question
    needs all six lexemes in ONE ~2000-char chunk. Measured on the live index
    during the first real outing:

        0 hits | defer_loading=True example code capability construction
        0 hits | defer_loading capability id description example
        1 hits | load_capability tool framework-managed catalog
       14 hits | defer_loading

    Three of five conceptual searches came back "no matching passage" for
    material the library plainly held. The exact failing queries are used here,
    against seeded content, because a test that only exercises the AND path
    proves nothing about the fix.
    """
    await repo.create_skill("cc-test-pai", "Pydantic AI", "Agent framework")
    await repo.add_skill_doc(
        "cc-test-pai", "capabilities", "reference", "Capabilities",
        "# Deferred capabilities\n"
        "A Capability with defer_loading=True is announced to the model as a "
        "single catalog line. The model calls load_capability to pull its "
        "instructions into context.\n\n"
        "# Toolsets\n"
        "Each capability owns a FunctionToolset whose tools become callable "
        "once the capability is loaded.",
    )

    # The strict AND path still works and is still preferred.
    strict = await repo.search_skill_chunks("cc-test-pai", "defer_loading")
    assert strict, "the single-term query must match, as it did live"

    for query in (
        "defer_loading=True example code capability construction",
        "defer_loading capability id description example",
        "load_capability tool framework-managed catalog",
    ):
        hits = await repo.search_skill_chunks("cc-test-pai", query)
        assert hits, f"query returned nothing: {query!r}"
        # Best partial match first — ts_rank still orders the fallback.
        assert "defer_loading" in hits[0]["content"] or "load_capability" in hits[0]["content"]


@pytestmark_db
@pytest.mark.asyncio
async def test_a_genuine_absence_still_returns_nothing(clean_skills):
    """The fallback must widen the query, not abolish the honest empty result —
    otherwise `format_hits`' "declare a gap rather than guessing" instruction
    would never fire and every subject would look documented."""
    await repo.create_skill("cc-test-pai", "Pydantic AI", "Agent framework")
    await repo.add_skill_doc(
        "cc-test-pai", "capabilities", "reference", "Capabilities",
        "# Deferred capabilities\nload_capability pulls instructions into context.",
    )
    assert await repo.search_skill_chunks(
        "cc-test-pai", "kubernetes ingress certificate rotation"
    ) == []


@pytestmark_db
@pytest.mark.asyncio
async def test_a_negated_term_is_not_widened_into_noise(clean_skills):
    """`-unwanted` renders as `!'unwant'`, and OR-ing that in would match every
    chunk merely lacking the word — the fallback would return noise ranked
    above nothing. A query carrying a negation keeps its AND form and still
    returns the honest empty result."""
    await repo.create_skill("cc-test-pai", "Pydantic AI", "Agent framework")
    await repo.add_skill_doc(
        "cc-test-pai", "capabilities", "reference", "Capabilities",
        "# Deferred capabilities\nload_capability pulls instructions into context.",
    )
    assert await repo.search_skill_chunks(
        "cc-test-pai", "zzzabsenttoken -context"
    ) == []
