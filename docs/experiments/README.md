# Experiments

Scripts that produced findings recorded in `docs/superpowers/specs/`. Kept so a
result can be re-derived rather than trusted.

## 2026-07-26 — LiteLLM complexity-classifier characterization

Ran LiteLLM's `ComplexityRouter.classify` over 283 real emails from
`work_item.payload->>'text'` with the live inbox-triage charter as
`system_prompt`. No network, no cost — the classifier is local.

    # export the corpus + charter from the Central Command DB, then:
    docker cp corpus_raw.json cc-litellm:/tmp/ ; docker cp charter.txt cc-litellm:/tmp/
    docker cp 2026-07-26-classifier-characterization.py cc-litellm:/tmp/classify.py
    docker exec cc-litellm python /tmp/classify.py

Findings (see the quality-router migration spec for the full write-up):
75.3% COMPLEX out of the box; `multi-step` fires on 100% of the corpus and
`code` on 83% because the default code keywords include business English; and
the tier correlates INVERSELY with real difficulty — dismissed mail scores
higher than mail that produced a proposal.
