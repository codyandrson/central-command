"""Mail body → agent text (`ingest/mailtext.py`), and the two guards behind it.

2026-09-19: two airline confirmations became 313k-character prompts — 96%
whitespace left by a tag regex — and killed ten triage runs, five attempts
each. The converter makes mail small; the prompt bound and the first-attempt
park are for the mail it cannot make small.
"""

from email.message import EmailMessage

from central_command.contract import is_context_overflow
from central_command.ingest import dispatcher, ledger, mailtext


def _layout_mail(rows: int = 400) -> str:
    cell = "<tr><td>\n" + " " * 60 + "<table><tr><td>\n" + " " * 60 + "&nbsp;</td></tr></table></td></tr>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?><html><head><style>'
        + "td{font-family:Arial;color:#111}" * 200
        + '</style></head><body><div style="display:none">Preview&zwnj;&nbsp;</div><table>'
        + cell * rows
        + "<tr><td>Depart:</td><td>5/23/2026 11:59 PM</td></tr>"
        + "<tr><td>AMOUNT PAID:</td><td>$582.96</td></tr>"
        + cell * rows
        + '</table><a href="https://click.example.com/' + "t" * 300 + '">Manage booking</a>'
        + "</body></html>"
    )


def test_a_layout_table_mail_comes_out_small_and_whole():
    html = _layout_mail()
    assert len(html) > 100_000
    out = mailtext.html_to_text(html)
    assert len(out) < 300, len(out)
    # A row stays a row, and the facts survive.
    assert "Depart: 5/23/2026 11:59 PM" in out
    assert "AMOUNT PAID: $582.96" in out
    assert "Manage booking" in out
    for leaked in ("font-family", "click.example.com", "Preview", "&nbsp", "\xa0", "‌"):
        assert leaked not in out, leaked


def test_text_nobody_can_see_is_not_text():
    out = mailtext.html_to_text(
        '<p>visible</p><span style="font-size:0">a</span><div style="visibility:hidden">b</div>'
        '<div style="max-height:0;overflow:hidden">c</div><span style="opacity:0">d</span>'
        '<p style="font-size:0.9em;opacity:0.8">fine print</p>'
    )
    assert out.split() == ["visible", "fine", "print"]


def test_unparseable_html_degrades_and_never_raises(monkeypatch):
    import lxml.html

    monkeypatch.setattr(lxml.html, "fromstring", lambda *_: 1 / 0)
    out = mailtext.html_to_text("<style>p{color:red}</style><p>still   here</p>")
    assert out == "still here"


def test_plain_part_keeps_short_links_and_cuts_tracking_ones():
    long = "https://links.example.com/ls/click?upn=" + "u" * 200
    out = mailtext.plain_to_text(f"Docs: https://example.com/docs\nTrack: {long}\n\n\n\nbye")
    assert out == "Docs: https://example.com/docs\nTrack: https://links.example.com/…\n\nbye"


def test_raw_rfc822_html_is_converted_not_handed_over_as_markup():
    single = EmailMessage()
    single["From"], single["Subject"] = "Jane Doe <jane@example.com>", "hi"
    single.set_content("<html><body><p>only <b>html</b></p></body></html>", subtype="html")
    assert ledger.parse_email(single.as_string())["body"] == "only html"

    both = EmailMessage()
    both["From"], both["Subject"] = "Jane Doe <jane@example.com>", "hi"
    both.set_content("the plain part")
    both.add_alternative("<p>the html part</p>", subtype="html")
    assert ledger.parse_email(both.as_string())["body"] == "the html part"


def test_the_first_prompt_is_bounded_and_says_so(monkeypatch):
    monkeypatch.setattr(dispatcher.context, "tool_result_ceiling", lambda: 1000)
    assert dispatcher._bound_prompt("x" * 3000) == "x" * 3000
    cut = dispatcher._bound_prompt("x" * 3001)
    assert cut.startswith("x" * 3000) and "CLIPPED at 3000" in cut


def test_an_overflow_is_recognised_by_the_proxys_words_only():
    assert is_context_overflow(RuntimeError(
        "status_code: 400, body: litellm.ContextWindowExceededError: request (84314 tokens) "
        "exceeds the available context size (81920 tokens)"))
    assert not is_context_overflow(RuntimeError("status_code: 400, invalid tool schema"))
    # A reasoning model spending its OUTPUT cap can differ on a retry.
    assert not is_context_overflow(RuntimeError(
        "Model token limit (262144) exceeded before any response was generated"))
    assert not is_context_overflow(None)


def test_nothing_reads_a_mail_body_except_through_the_converter():
    """The tripwire. `body_html` / `body_text` are the façade's raw fields; a
    new reader that picks one up directly brings back the tag-strip bug (or
    the tracking-URL one) on its own path. Read `ledger.provider_body`."""
    import pathlib
    import re

    root = pathlib.Path(dispatcher.__file__).resolve().parents[1]
    allowed = {"ingest/ledger.py", "ingest/mailtext.py", "integrations/email_facade.py"}
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if str(p.relative_to(root)) not in allowed
        and re.search(r"""["'](body_html|body_text)["']""", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders
