"""mint-keys.sh never writes an empty key, and a stale alias is revoked.

2026-09-26, re-deploy with the LiteLLM database kept (README §8 item 4): the
fresh .env had an empty CC_LLM_API_KEY, LiteLLM refused `/key/generate` for
the `cc-spine` alias it already held, the `exit 1` inside a command
substitution killed only the subshell, an EMPTY value was written and setup
reported the mint step green. The API then could not start. These read the
script — the one thing a test can hold still — and pin the two fixes."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINT = (ROOT / "deploy" / "k3s" / "mint-keys.sh").read_text(encoding="utf-8")
GVISOR = (ROOT / "deploy" / "k3s" / "install-gvisor.sh").read_text(encoding="utf-8")


def test_a_failed_mint_is_captured_before_anything_is_written():
    ensure = MINT[MINT.index("ensure_key() {"):]
    ensure = ensure[:ensure.index("\n}\n")]
    assert re.search(r'key="\$\(mint "\$alias" "\$models"\)" \|\| exit 1', ensure)
    assert 'set_var "$f" "$var" "$key"' in ensure
    assert 'set_var "$f" "$var" "$(mint' not in MINT, "the swallowed-failure shape is back"
    assert '"$key" == sk-*' in ensure, "an empty or non-key value must never be written"


def test_a_colliding_alias_is_revoked_by_alias_and_minted_again():
    mint = MINT[MINT.index("mint() {"):MINT.index("ensure_key() {")]
    assert "already exists" in mint
    assert '/key/delete' in mint and 'key_aliases' in mint
    assert "return 1" in mint and "exit 1" not in mint, "mint reports failure to its caller, it never exits a subshell"


def test_the_gvisor_proof_waits_long_enough_for_a_cold_image_pull():
    m = re.search(r"pod/gvisor-proof --timeout=(\d+)s", GVISOR)
    assert m and int(m.group(1)) >= 300
    assert "get events" in GVISOR, "a timeout must print the pod's events"
