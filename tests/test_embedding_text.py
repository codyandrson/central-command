"""One text recipe: `contract.embedding_text` says what a vector is the embedding
of, and the embedder record names it (epic-embedder-integrity R7). The curation
writer, the module and the extractor's patch adopt it in their own stories."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from central_command.contract import embedding_text as module
from central_command.contract.embedding_text import CANARY_TEXTS, TEXT_RECIPE, embedding_text


def test_an_entity_is_its_name_a_newline_and_its_summary_verbatim_and_stripped():
    assert embedding_text("entity", name="Jane Doe", summary="Runs  the\nrenewal.") == (
        "Jane Doe\nRuns  the\nrenewal."
    )


@pytest.mark.parametrize("name,summary,expected", [
    ("Jane Doe", "", "Jane Doe"),
    ("Jane Doe", None, "Jane Doe"),
    ("  Jane Doe", "Runs it. \n", "Jane Doe\nRuns it."),
    ("", "", ""),
])
def test_an_entity_is_stripped_at_both_ends(name, summary, expected):
    assert embedding_text("entity", name=name, summary=summary) == expected


def test_the_entity_recipe_is_the_one_the_curation_writer_already_uses():
    name, summary = "Jane Doe", " Runs the renewal.\n"
    assert embedding_text("entity", name=name, summary=summary) == f"{name}\n{summary}".strip()


def test_an_edge_is_its_fact_verbatim():
    fact = "  Jane Doe manages the renewal \n"
    assert embedding_text("edge", fact=fact) == fact


def test_a_community_is_its_name():
    assert embedding_text("community", name="Renewals", summary="ignored") == "Renewals"


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="episode"):
        embedding_text("episode", name="x")


def test_the_recipe_constant_names_all_three_recipes_and_a_version():
    assert TEXT_RECIPE == "entity:name+nl+summary;edge:fact;community:name;v1"


def test_there_are_three_different_fixed_canaries():
    assert isinstance(CANARY_TEXTS, tuple)
    assert len(set(CANARY_TEXTS)) == 3 and all(t.strip() for t in CANARY_TEXTS)


def test_the_module_imports_nothing_from_the_project():
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert imported == ["__future__"]
