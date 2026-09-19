"""Mail body → the text an agent reads. ONE converter for every path that
hands an email to a model.

Why this exists (2026-09-19, ten dead triage runs on two airline
confirmations): the old path was `re.sub(r"<[^>]+>", " ", html)`. A tag regex
keeps everything that is not a tag — the whole `<style>` sheet, undecoded
`&zwnj;&nbsp;` preheader padding, and the indentation skeleton of a
twenty-deep layout table. One itinerary became 313,000 characters, 96% of
them whitespace, around ~2,400 tokens of actual content.

Choices, each measured on real mail (39 HTML messages, same day):

- **inscriptis, not markdown.** Email tables are layout, not data: markdownify
  and markitdown spent 5-8x the tokens on pipes and tracking URLs. inscriptis
  keeps a table row on one line ("Depart: … | Arrive: …") and drops nothing.
- **Never an article extractor.** trafilatura / readability-style tools delete
  "boilerplate", and on a receipt the boilerplate is the receipt (fact recall
  fell to 0% on some messages).
- **HTML before text/plain.** Marketing mail's plain part carries every raw
  tracking URL (up to 94% of it); over the mails that had both parts the plain
  one cost 6x the tokens of the converted HTML.
- **Links are dropped, hidden text is removed.** Unsubscribe comes from the
  message HEADERS (`contract/mail.py`), never the body. Text a human reader
  cannot see (`display:none`, `visibility:hidden`, `font-size:0` …) is the
  documented injection vector against mail summarisers, and no converter
  removes all of it — hence `_drop_hidden`. Colour tricks (white on white) are
  not detected: that needs the cascade, and email content is data, never
  instructions, either way.
"""

from __future__ import annotations

import html as _html
import logging
import re

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
# Preheader padding and friends: zero-width (non-)joiners, word joiner, BOM,
# combining grapheme joiner, soft hyphen.
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u034f\u00ad]")
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|(?:font-size|max-height|opacity)\s*:\s*0(?![.\d])",
    re.I,
)
_URL = re.compile(r"(https?://[^/\s<>()\[\]\"']+)[^\s<>()\[\]\"']*")
_LONG_URL = 80
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)


def normalize(text: str) -> str:
    """Entities decoded, invisible padding gone, runs of blanks collapsed —
    at most one empty line between paragraphs."""
    text = _INVISIBLE.sub("", _html.unescape(text or "")).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _drop_hidden(tree) -> None:
    for el in tree.xpath("//*[@style]"):
        if el.getparent() is not None and _HIDDEN_STYLE.search(el.get("style") or ""):
            el.drop_tree()  # keeps the tail text, which IS visible


def html_to_text(html: str) -> str:
    """Visible text of an HTML mail body. Never raises: mail is hostile input
    and ingestion must not die on one malformed message — a parse failure
    degrades to the tag strip plus `normalize`, which is still bounded."""
    if not (html or "").strip():
        return ""
    try:
        import lxml.html
        from inscriptis import Inscriptis
        from inscriptis.model.config import ParserConfig

        # lxml refuses a str that still carries `<?xml … encoding=…?>` (real
        # mail does: two of the first forty messages tried).
        tree = lxml.html.fromstring(_XML_DECL.sub("", html, count=1))
        _drop_hidden(tree)
        text = Inscriptis(
            tree, ParserConfig(display_links=False, display_images=False)
        ).get_text()
    except Exception:  # noqa: BLE001 — degrade, never a dead ingest
        log.exception("mailtext: html conversion failed; falling back to tag strip")
        text = _TAG_RE.sub(" ", re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", html))
    return normalize(text)


def plain_to_text(text: str) -> str:
    """A text/plain body: normalised, with tracking-length URLs cut to their
    host — the path is a redirect token, never something triage reads."""
    def short(m: re.Match) -> str:
        return m.group(0) if len(m.group(0)) <= _LONG_URL else m.group(1) + "/…"

    return normalize(_URL.sub(short, text or ""))


def body_text(html: str | None, plain: str | None, snippet: str | None = None) -> str:
    """Best text for one message: converted HTML, else the plain part, else
    the provider snippet."""
    return html_to_text(html or "") or plain_to_text(plain or "") or normalize(snippet or "")


def _demo() -> None:
    page = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<html><head><style>td{color:red}</style></head><body>"
        '<div style="display:none">PREHEADER&zwnj;&nbsp;&zwnj;&nbsp;</div>'
        '<span style="font-size:0px">ZEROFONT</span>'
        '<p style="font-size:0.9em">small print</p>'
        "<table><tr><td>\n\n      Depart:</td><td>5/23&nbsp;11:59 PM</td></tr></table>"
        '<a href="https://t.example.com/x">Manage booking</a></body></html>'
    )
    out = html_to_text(page)
    assert "Depart: 5/23 11:59 PM" in out, out
    assert "small print" in out and "Manage booking" in out, out
    for gone in ("PREHEADER", "ZEROFONT", "color:red", "example.com", "\xa0", "&nbsp"):
        assert gone not in out, (gone, out)
    long = "https://click.example.com/" + "a" * 200
    assert plain_to_text(f"see {long} now") == "see https://click.example.com/… now"
    assert body_text("", "plain", "snip") == "plain" and body_text(None, None, "snip") == "snip"


if __name__ == "__main__":
    _demo()
    print("ok")
