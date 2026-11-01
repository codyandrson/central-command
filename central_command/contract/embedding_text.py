"""The one text recipe: what a graph element's vector is the embedding OF.

Lives in `contract/` because every tier that embeds or checks a vector needs
the same answer and `contract/` imports nothing. `TEXT_RECIPE` names the recipe
on the embedder record (`integrations/embedder_record.py`); changing what
`embedding_text` returns means a new recipe version and a re-embed of the
graph, because vectors from different recipes do not compare.

`CANARY_TEXTS` are the fixed sentences the record keeps with their vectors: the
same text through the same model must give the same vector back, so a cosine
below the threshold means the model behind the alias changed. They are plain
sentences, not graph content, and changing one changes nothing already recorded
(the record stores the texts it embedded).
"""

from __future__ import annotations

TEXT_RECIPE = "entity:name+nl+summary;edge:fact;community:name;v1"

CANARY_TEXTS = (
    "The quarterly maintenance window for the example.com gateway moved to Thursday evening.",
    "Jane Doe approved the renewal of the storage contract after reviewing the cost report.",
    "A fact about a retired service stays in the knowledge graph with the date it stopped being true.",
)


def embedding_text(
    kind: str, name: str | None = None, summary: str | None = None, fact: str | None = None,
) -> str:
    """The text to embed for one element. `kind` is `entity`, `edge` or
    `community`. An entity is its name, a newline and its summary, verbatim and
    stripped; an edge is its fact, verbatim; a community is its name. An
    unknown kind raises: a guess here would be a vector nothing can compare."""
    if kind == "entity":
        return f"{name or ''}\n{summary or ''}".strip()
    if kind == "edge":
        return fact or ""
    if kind == "community":
        return name or ""
    raise ValueError(f"unknown embedding kind {kind!r}; valid: entity, edge, community")
