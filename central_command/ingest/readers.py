"""Purpose-built file readers behind the extraction seam (`ingest/extract.py`).

Each takes the file's bytes and returns the text an agent reads, or raises.
They replaced markitdown for the types where the 2026-11-14 bake-off measured
it losing or corrupting content (DL-216): PDF (interleaved columns, prices
split from their items, watermark letters inside amounts), spreadsheets (no
size limit: one 84,000-row sheet became 2.5 million tokens), calendar files,
forwarded mail and RTF (the raw file passed through). markitdown keeps Word,
PowerPoint, HTML, CSV and the rest.

Every reader says what it left out. A cut the reader cannot see is the same
silent failure as a dropped file (`api/attachments.py`).

The libraries are imported inside the functions: this module is imported at
application start through `extract.py`, and none of them should load until a
file of its type arrives.
"""

from __future__ import annotations

import datetime as dt
import io

# A spreadsheet is cut at these sizes and the cut is stated in the sheet's
# heading. The row limit is a parameter (mail passes a smaller one); columns
# and cell text are bounded here because one pathological cell or a
# thousand-column export should not crowd out the rows.
SHEET_MAX_ROWS = 1000
SHEET_MAX_COLS = 50
SHEET_MAX_CELL = 500

# One calendar file can be a whole published calendar; an invite is a handful
# of events. Past this many the rest are counted, not rendered.
CALENDAR_MAX_EVENTS = 50
CALENDAR_MAX_DESCRIPTION = 4000


def md_table(rows: list[list[str]]) -> str:
    """Rows of cell text as a markdown table; the first row is the header row
    only in the sense that markdown needs one. Empty rows are dropped."""
    clean = [
        [(c or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip() for c in r]
        for r in rows
    ]
    clean = [r for r in clean if any(r)]
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    out = ["| " + " | ".join(clean[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(out)


# --- PDF ------------------------------------------------------------------------


def pdf_text(data: bytes) -> str:
    """The text layer, page by page, in the order PDFium reads it. Returns ''
    for an image-only PDF (a scan) — the caller decides what an empty result
    means, exactly as it did for markitdown. Tables come out as plain lines:
    a row's cells stay together, an empty cell leaves no gap."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(data)
    try:
        pages = []
        for i in range(len(pdf)):
            textpage = pdf[i].get_textpage()
            try:
                pages.append(textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n").strip())
            finally:
                textpage.close()
    finally:
        pdf.close()
    if not any(pages):
        return ""
    if len(pages) == 1:
        return pages[0]
    return "\n\n".join(f"--- page {n} of {len(pages)} ---\n{p}" for n, p in enumerate(pages, 1))


# --- spreadsheets -----------------------------------------------------------------


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)[:SHEET_MAX_CELL]


def spreadsheet_text(data: bytes, max_rows: int = SHEET_MAX_ROWS) -> str:
    """Every sheet of an xlsx/xlsm/xlsb/xls/ods workbook, named, with its size
    and visibility, merged cells filled, and any cut stated in the heading.
    Formula cells show the value the file stored, never the formula."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_filelike(io.BytesIO(data))
    out = []
    for meta in wb.sheets_metadata:
        visibility = str(meta.visible).rsplit(".", 1)[-1]
        if visibility == "VeryHidden":
            out.append(f"## Sheet '{meta.name}' (very hidden) — not shown")
            continue
        try:
            sheet = wb.get_sheet_by_name(meta.name)
            rows = sheet.to_python(skip_empty_area=True)
        except Exception as exc:  # a chart sheet, or one sheet the reader cannot open
            out.append(f"## Sheet '{meta.name}' — could not be read ({type(exc).__name__})")
            continue
        _fill_merged(sheet, rows)
        total_rows = len(rows)
        total_cols = max((len(r) for r in rows), default=0)
        head = f"## Sheet '{meta.name}'" + (" (hidden sheet)" if visibility == "Hidden" else "")
        head += f" — {total_rows} rows x {total_cols} columns"
        if total_rows > max_rows or total_cols > SHEET_MAX_COLS:
            head += (
                f"; showing rows 1-{min(total_rows, max_rows)} and columns "
                f"1-{min(total_cols, SHEET_MAX_COLS)}, the rest is NOT shown"
            )
        shown = [[_cell(v) for v in r[:SHEET_MAX_COLS]] for r in rows[:max_rows]]
        out.append(head)
        out.append(md_table(shown) or "(empty sheet)")
    return "\n\n".join(out).strip()


def _fill_merged(sheet, rows: list[list]) -> None:
    """Copy a merged range's value into every cell it spans, so a merged
    header reads on each column it covers. xlsx/xls only; other formats
    report no ranges."""
    try:
        ranges = sheet.merged_cell_ranges or []
        r0, c0 = sheet.start or (0, 0)
    except Exception:
        return
    for (ra, ca), (rb, cb) in ranges:
        top, left = ra - r0, ca - c0
        # A range that starts above or left of the first data cell (an empty
        # merged banner) has no value in `rows`; a negative index would read
        # and write cells counted from the END.
        if top < 0 or left < 0:
            continue
        try:
            value = rows[top][left]
            for r in range(top, rb - r0 + 1):
                for c in range(left, cb - c0 + 1):
                    rows[r][c] = value
        except IndexError:
            continue


# --- calendar -----------------------------------------------------------------------

_METHODS = {
    "REQUEST": "a meeting invitation",
    "CANCEL": "the meeting is CANCELLED",
    "REPLY": "a reply to an invitation",
    "PUBLISH": "a published calendar entry, not an invitation",
    "COUNTER": "a counter-proposal",
    "ADD": "occurrences added to a series",
    "REFRESH": "a request for the latest version",
    "DECLINECOUNTER": "a counter-proposal declined",
}


def calendar_text(data: bytes) -> str:
    """An iCalendar file (an invite, a cancellation, a calendar export) as the
    lines a reader needs: what it is, title, when (in its own zone and UTC),
    recurrence, where, who, and the description.

    Recurrences are stated, never expanded. A time with no zone is shown as
    written and labelled floating; a zone name nothing resolves is shown as
    written with a warning, never guessed. The same event sent twice (Gmail
    delivers an invite as an inline part AND as invite.ics) is rendered once —
    de-duplicated on its id fields, never by comparing components, which is
    exponential in nesting depth below icalendar 7.1.3 (CVE-2026-55099)."""
    from icalendar import Calendar

    # A file may hold several VCALENDAR blocks; the first one's METHOD names it.
    cals = Calendar.from_ical(data, multiple=True)
    cal = cals[0] if cals else Calendar()
    method = str(cal.get("METHOD", "") or "").strip().upper()
    head = "CALENDAR FILE"
    if method:
        head += f" — METHOD: {method}" + (f" ({_METHODS[method]})" if method in _METHODS else "")
    out = [head]
    warnings: list[str] = []

    def who(value) -> str:
        address = str(value)
        if address.lower().startswith("mailto:"):
            address = address[7:]
        params = getattr(value, "params", {})
        name = params.get("CN")
        notes = [str(p).lower() for p in (params.get("ROLE"), params.get("PARTSTAT")) if p]
        text = f"{name} <{address}>" if name else address
        return text + (f" ({', '.join(notes)})" if notes else "")

    def value_of(prop):
        """`prop.dt`, or None: icalendar hands back a broken property for a
        malformed value and raises only when the value is read."""
        try:
            return prop.dt
        except Exception:
            return None

    def when(prop) -> str | None:
        if prop is None:
            return None
        value = value_of(prop)
        if value is None:
            warnings.append("a date could not be parsed and is shown as written")
            try:
                return f"{prop.to_ical().decode(errors='replace')} (as written)"
            except Exception:
                return "(unreadable date)"
        tzid = prop.params.get("TZID")
        if not isinstance(value, dt.datetime):
            return f"{value.isoformat()} (all day)"
        if value.tzinfo is None:
            if tzid:
                warnings.append(f"time zone {tzid!r} could not be resolved; the time is shown as written")
                return f"{value.strftime('%a %Y-%m-%d %H:%M')} [{tzid}, unresolved]"
            return f"{value.strftime('%a %Y-%m-%d %H:%M')} (floating: no time zone given)"
        if value.utcoffset() == dt.timedelta(0) and not tzid:
            return f"{value.strftime('%a %Y-%m-%d %H:%M')} UTC"
        utc = value.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
        return f"{value.strftime('%a %Y-%m-%d %H:%M')} {tzid or value.tzname() or ''} ({utc})".replace("  ", " ")

    def safe(event, key):
        """One property, or None when icalendar rejects its value: a malformed
        line from a real client must cost that line, not the whole invite."""
        try:
            return event.get(key)
        except Exception:
            warnings.append(f"{key} could not be parsed and is left out")
            return None

    seen: set[tuple] = set()
    events = [e for c in cals for e in c.walk("VEVENT")]
    shown = 0
    for position, event in enumerate(events):
        recurrence_id = safe(event, "RECURRENCE-ID")
        uid = event.get("UID")
        try:
            recurrence_key = recurrence_id.to_ical() if recurrence_id is not None else None
        except Exception:
            recurrence_key = position
        key = (
            # An event with no UID cannot be the same event as another one.
            str(uid) if uid else f"no-uid-{position}",
            recurrence_key,
            str(event.get("SEQUENCE")),
        )
        if key in seen:
            continue
        seen.add(key)
        if shown == CALENDAR_MAX_EVENTS:
            continue
        shown += 1
        out.append("")
        if recurrence_id is not None:
            out.append(f"Changed occurrence of a series (originally {when(recurrence_id)}):")
        out.append(f"Title: {event.get('SUMMARY') or '(no title)'}")
        start, end = safe(event, "DTSTART"), safe(event, "DTEND")
        line = f"When: {when(start) or '(no start given)'}"
        if end is not None:
            end_value = value_of(end)
            if end_value is None or isinstance(end_value, dt.datetime):
                line += f" to {when(end)}"
            else:
                # An all-day DTEND is exclusive: a one-day event ends "tomorrow".
                last = end_value - dt.timedelta(days=1)
                if start is None or last != value_of(start):
                    line += f" through {last.isoformat()}"
        duration = safe(event, "DURATION")
        if duration is not None and value_of(duration) is not None:
            line += f" (duration {value_of(duration)})"
        out.append(line)
        rrule = safe(event, "RRULE")
        if rrule:
            out.append(f"Repeats: {rrule.to_ical().decode(errors='replace')}")
        exdate = safe(event, "EXDATE")
        if exdate:
            groups = exdate if isinstance(exdate, list) else [exdate]
            try:
                out.append("Skipped dates: " + ", ".join(d.dt.isoformat() for g in groups for d in g.dts))
            except Exception:
                warnings.append("EXDATE could not be parsed and is left out")
        for label, prop in (
            ("Where", "LOCATION"),
            ("Status", "STATUS"),
            ("Update number (SEQUENCE)", "SEQUENCE"),
            ("Busy status", "X-MICROSOFT-CDO-BUSYSTATUS"),
        ):
            value = event.get(prop)
            if value not in (None, ""):
                out.append(f"{label}: {value}")
        organizer = event.get("ORGANIZER")
        if organizer:
            out.append(f"Organizer: {who(organizer)}")
        attendees = event.get("ATTENDEE")
        if attendees:
            attendees = attendees if isinstance(attendees, list) else [attendees]
            out.append("Attendees: " + "; ".join(who(a) for a in attendees))
        description = str(event.get("DESCRIPTION") or "").strip()
        if description:
            if len(description) > CALENDAR_MAX_DESCRIPTION:
                description = (
                    description[:CALENDAR_MAX_DESCRIPTION]
                    + f" … [description cut at {CALENDAR_MAX_DESCRIPTION} characters]"
                )
            out.append(f"Description: {description}")
        out.append(f"UID: {event.get('UID')}")
    # What else the file holds is said, never dropped: a task list or a
    # free/busy reply is not an event and is not rendered here.
    others: dict[str, int] = {}
    for c in cals:
        for comp in c.walk():
            kind = str(getattr(comp, "name", "") or "")
            if kind in ("VTODO", "VJOURNAL", "VFREEBUSY"):
                others[kind] = others.get(kind, 0) + 1
    if not events:
        out += ["", "(the file holds no events)"]
    elif len(seen) > shown:
        out += ["", f"[{len(seen) - shown} more events in this file are NOT shown]"]
    if others:
        out += ["", "Also in this file and NOT shown: "
                + ", ".join(f"{n} {k}" for k, n in sorted(others.items()))]
    if warnings:
        out += ["", "Warnings: " + "; ".join(dict.fromkeys(warnings))]
    return "\n".join(out).strip()


# --- mail inside mail, RTF ------------------------------------------------------------


def eml_text(data: bytes) -> str:
    """An attached message (.eml): its headers, its body through the one mail
    converter (`ingest/mailtext.py`), and its own attachments by name — their
    content is not read here."""
    import email
    from email import policy

    from central_command.ingest import mailtext

    msg = email.message_from_bytes(data, policy=policy.default)
    out = [f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]

    def part(subtype: str) -> str | None:
        found = msg.get_body(preferencelist=(subtype,))
        if found is None:
            return None
        try:
            return found.get_content()
        except Exception:
            return None

    out += ["", mailtext.body_text(part("html"), part("plain")) or "(no readable body)"]
    for att in msg.iter_attachments():
        size = len(att.get_payload(decode=True) or b"")
        out.append(
            f"[attached to that message, not read: {att.get_filename() or '(unnamed)'} "
            f"({att.get_content_type()}, {size:,} bytes)]"
        )
    return "\n".join(out).strip()


def rtf_text(data: bytes) -> str:
    from striprtf.striprtf import rtf_to_text

    return rtf_to_text(data.decode("latin-1"), errors="ignore").strip()
