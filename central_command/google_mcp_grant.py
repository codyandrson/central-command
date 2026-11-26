"""One-time approvals for the Google MCP client (DL-237).

    python -m central_command.google_mcp_grant [--tool NAME ...]

Google's MCP servers may ask a human to approve a tool before an agent's client
may use it (MCP form elicitation: `resultType: "input_required"`, with choices
such as ALLOW_ONCE, ALLOW_TOOL_ALWAYS and DENY_ONCE). The request path
(`integrations/google_mcp.py`) never answers such a question: it refuses the
call and names this command. This command is where the OPERATOR answers.

With no `--tool`, it makes one harmless real call to each READ tool the
provider uses (Gmail `search_threads`, `get_thread`, `get_message`; Calendar
`list_events`, `get_event`), and when Google asks, it shows Google's question
and Google's own choices on the terminal and sends the choice the operator
types. It never chooses: an empty answer asks again, `c` or end of input sends
nothing. It refuses to run without a terminal on both stdin and stdout.

Nothing read is printed: not a subject, address, snippet or body, and not
Google's preview of the action (`params.message`, which can quote mail); only
the tool, Google's question (`approval_choice.title`), the choices, and what
kind of answer came back. No token or secret is printed.

The WRITE tools (`mark_message_spam`, `create_event`, `update_event`,
`delete_event`) are listed, not called: Google asks for them at first use, and
that first write fails with the refusal. `--tool <name>` then asks Google about
that one tool WITHOUT a write: the call is aimed at a target that does not
exist (a message id or event id no mailbox holds, a calendar id under the
reserved `example.invalid` domain), so if Google asks, the operator answers,
and whatever is chosen there is nothing for Google to change. Whether Google
asks before it looks the target up is not documented; if it answers "not
found" without asking, it will ask at the next real write instead.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

from central_command.config import settings
from central_command.integrations import google, google_mcp

GMAIL = "Gmail"
CALENDAR = "Google Calendar"
TIMEOUT_SECONDS = 60.0
MAX_ROUNDS = 3

# Targets that cannot exist, for the no-write probe of a write tool.
NO_MESSAGE = "0000000000000000"
NO_EVENT = "ccgrantprobenonexistent0"
NO_CALENDAR = "cc-grant-probe@example.invalid"

READS = (*google_mcp.GMAIL_READS, *google_mcp.CALENDAR_READS)
WRITES = (*google_mcp.GMAIL_WRITES, *google_mcp.CALENDAR_WRITES)


def _url(server: str) -> str:
    return settings.google_mcp_gmail_url if server == GMAIL else settings.google_mcp_calendar_url


def _server(tool: str) -> str:
    return GMAIL if tool in (*google_mcp.GMAIL_READS, *google_mcp.GMAIL_WRITES) else CALENDAR


def _write_probe(tool: str) -> dict:
    """The arguments of a write aimed at nothing (see the module docstring)."""
    now = datetime.now(UTC).replace(microsecond=0)
    return {
        "mark_message_spam": {"messageId": NO_MESSAGE},
        "create_event": {
            "calendarId": NO_CALENDAR,
            "summary": "Central Command approval probe",
            "startTime": (now + timedelta(days=1)).isoformat(),
            "endTime": (now + timedelta(days=1, minutes=30)).isoformat(),
            "notificationLevel": "NONE",
        },
        "update_event": {
            "calendarId": NO_CALENDAR,
            "eventId": NO_EVENT,
            "summary": "Central Command approval probe",
            "notificationLevel": "NONE",
        },
        "delete_event": {
            "calendarId": NO_CALENDAR,
            "eventId": NO_EVENT,
            "notificationLevel": "NONE",
        },
    }[tool]


class _Session:
    def __init__(self, out: TextIO, ask: Callable[[str], str]):
        self.out = out
        self.ask = ask
        self.failed = False

    def say(self, text: str) -> None:
        print(text, file=self.out)

    def _choose(self, values: list[str], labels: list[str]) -> str | None:
        """The operator's choice, by number or by Google's identifier; None
        for `c` or end of input. Never a default."""
        prompt = f"Enter choice (1-{len(values)}, or c to send nothing): "
        while True:
            try:
                raw = self.ask(prompt).strip()
            except EOFError:
                return None
            if raw.lower() == "c":
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(values):
                return values[int(raw) - 1]
            if raw in values:
                return raw
            self.say("  not one of the choices above; type its number, or c")

    async def probe(self, tool: str, arguments: dict) -> dict | None:
        """One call; every question Google asks is put to the operator. The
        tool's data on a completed call, else None."""
        server = _server(tool)
        url = _url(server)
        try:
            result = await google_mcp.post(url, tool, arguments, TIMEOUT_SECONDS)
            for _ in range(MAX_ROUNDS):
                if result.get("resultType") != "input_required":
                    break
                extra = self._question(server, tool, result)
                if extra is None:
                    self.say(f"{tool}: not answered; nothing was sent to Google")
                    return None
                result = await google_mcp.post(url, tool, arguments, TIMEOUT_SECONDS, extra)
            else:
                if result.get("resultType") == "input_required":
                    self.say(f"{tool}: Google asked {MAX_ROUNDS} times; stopped")
                    self.failed = True
                    return None
        except google._TokenUnavailable as e:
            self.say(f"{tool}: no access token ({e}) - re-run {google_mcp.CONSENT_COMMAND}")
            self.failed = True
            return None
        except google_mcp._Fail as e:
            self.say(f"{tool}: {e}")
            self.failed = True
            return None
        if result.get("isError"):
            err = google_mcp._tool_error(tool, result)
            kind = ", ".join(sorted(err.reasons)) or (f"code {err.code}" if err.code else "")
            self.say(f"{tool}: Google answered an error ({kind or 'no reason given'})")
            return None
        self.say(f"{tool}: Google answered (nothing it returned is shown)")
        return google_mcp.structured(result)

    def _question(self, server: str, tool: str, result: dict) -> dict | None:
        requests = result.get("inputRequests")
        entries = requests.items() if isinstance(requests, dict) else []
        for key, req in entries:
            params = req.get("params") if isinstance(req, dict) else None
            schema = params.get("requestedSchema") if isinstance(params, dict) else None
            props = schema.get("properties") if isinstance(schema, dict) else None
            if not isinstance(props, dict) or not props:
                continue
            field, spec = next(iter(props.items()))
            spec = spec if isinstance(spec, dict) else {}
            values, labels = _options(spec)
            if not values:
                continue
            self.say(f"\n=== {server} ({tool}) ===")
            self.say(str(spec.get("title") or "Google asks for an approval of this call."))
            self.say("(Google's preview of the action is not shown here: it can quote mail.)")
            for i, (value, label) in enumerate(zip(values, labels, strict=True), 1):
                self.say(f"  [{i}] {label} ({value})")
            choice = self._choose(values, labels)
            if choice is None:
                return None
            return {
                "requestState": result.get("requestState"),
                "inputResponses": {key: {"action": "accept", "content": {field: choice}}},
            }
        self.say(f"{tool}: Google asked in a form this command cannot show; nothing was sent")
        self.failed = True
        return None


def _options(spec: dict) -> tuple[list[str], list[str]]:
    """Google's `enum` with its parallel `enumNames`, or a JSON Schema
    `oneOf` of `{const, title}` (the elicitation guide names both)."""
    if isinstance(spec.get("enum"), list):
        values = [str(v) for v in spec["enum"]]
        raw_names = spec.get("enumNames")
        names: list = raw_names if isinstance(raw_names, list) else []
        labels = [str(names[i]) if i < len(names) else v for i, v in enumerate(values)]
        return values, labels
    if isinstance(spec.get("oneOf"), list):
        opts = [o for o in spec["oneOf"] if isinstance(o, dict) and "const" in o]
        return [str(o["const"]) for o in opts], [str(o.get("title") or o["const"]) for o in opts]
    return [], []


def _first(items: Any, key: str = "id") -> str | None:
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get(key):
            return str(item[key])
    return None


async def _grant(tools: list[str], s: _Session) -> None:
    wanted = tools or list(READS)
    thread_id = message_id = event_id = None
    if any(t in wanted for t in google_mcp.GMAIL_READS):
        # get_thread and get_message need an id, which only a search can give.
        data = await s.probe("search_threads", {"query": "in:inbox", "pageSize": 1})
        threads = (data or {}).get("threads")
        thread_id = _first(threads)
        first = (
            next((t for t in threads if isinstance(t, dict)), {})
            if isinstance(threads, list)
            else {}
        )
        message_id = _first(first.get("messages"))
    if "get_thread" in wanted:
        if thread_id:
            data = await s.probe(
                "get_thread", {"threadId": thread_id, "messageFormat": "METADATA_ONLY"}
            )
            message_id = message_id or _first((data or {}).get("messages"))
        else:
            s.say("get_thread: no inbox thread to read; Google will ask at first use")
    if "get_message" in wanted:
        if message_id:
            await s.probe("get_message", {"messageId": message_id, "messageFormat": "RAW"})
        else:
            s.say("get_message: no inbox message to read; Google will ask at first use")
    if "list_events" in wanted or "get_event" in wanted:
        now = datetime.now(UTC).replace(microsecond=0)
        data = await s.probe(
            "list_events",
            {
                "calendarId": "primary",
                "startTime": now.isoformat(),
                "endTime": (now + timedelta(days=7)).isoformat(),
                "pageSize": 1,
                "orderBy": "startTime",
                "eventType": list(google_mcp.EVENT_TYPES),
            },
        )
        event_id = _first((data or {}).get("events"))
    if "get_event" in wanted:
        if event_id:
            await s.probe("get_event", {"calendarId": "primary", "eventId": event_id})
        else:
            s.say("get_event: no event in the next 7 days to read; Google will ask at first use")
    for tool in (t for t in tools if t in WRITES):
        s.say(f"{tool}: asking Google about a call aimed at a target that does not exist")
        await s.probe(tool, _write_probe(tool))
    if not tools:
        s.say(
            "\nThe write tools are not called here: "
            + ", ".join(WRITES)
            + ". Google asks for each at its first use, and that first write fails with the "
            "refusal. Then run this command again with --tool <name> for that tool."
        )


def run(
    tools: list[str],
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    ask: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        print(
            "error: this command asks the operator to answer Google's approval questions and "
            "runs only at a terminal (stdin and stdout); nothing was sent",
            file=err,
        )
        return 2
    unknown = [t for t in tools if t not in (*READS, *WRITES)]
    if unknown:
        print(
            f"error: not a tool this provider calls: {', '.join(unknown)} "
            f"(reads: {', '.join(READS)}; writes: {', '.join(WRITES)})",
            file=err,
        )
        return 2
    missing = google.missing_settings()
    if missing:
        print(
            f"error: {', '.join(missing)} not set — python -m central_command.google_consent "
            "writes the refresh token (see its --help)",
            file=err,
        )
        return 2
    from central_command.integrations import provider_extras

    refusal = provider_extras.refusal("google")
    if refusal:
        print(f"error: {refusal}", file=err)
        return 2
    session = _Session(out, ask)
    try:
        asyncio.run(_grant(tools, session))
    except KeyboardInterrupt:
        print("cancelled", file=err)
        return 130
    return 1 if session.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m central_command.google_mcp_grant",
        description="Answer Google's approval questions for the Google MCP client "
        "(CC_GOOGLE_PROVIDER=mcp, DL-237): one harmless call per read tool, Google's "
        "question and choices shown, the operator's typed choice sent. Never chooses.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        choices=[*READS, *WRITES],
        help="ask about this tool only (repeatable); a write tool is asked about with a "
        "call aimed at a target that does not exist",
    )
    args = parser.parse_args(argv)
    return run(args.tool)


if __name__ == "__main__":
    sys.exit(main())
