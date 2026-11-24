"""One-time Google consent for the native Google client (DL-234).

    python -m central_command.google_consent [--env-file .env] [--port 8765]

Runs Google's OAuth 2.0 flow for installed applications through
google-auth-oauthlib's `InstalledAppFlow.run_local_server`: authorization code
with PKCE, the loopback redirect to ``http://127.0.0.1:<port>/``, the browser
NOT opened here (``open_browser=False``: the server is headless), the listener
bound to 127.0.0.1 only. The client id and secret are read from the env file
(``CC_GOOGLE_CLIENT_ID``, ``CC_GOOGLE_CLIENT_SECRET``; a *Desktop app* client
the operator creates), and the refresh token Google returns is written into the
same file as ``CC_GOOGLE_REFRESH_TOKEN`` with python-dotenv's ``set_key``
(every other line kept, the file's mode kept, a re-run replaces the key).

The out-of-band copy/paste flow is gone (Google shut it down), so the browser
must reach the loopback listener: on a headless server reached over ssh the
operator forwards the port from the machine with the browser, and Google's
redirect travels through the tunnel to this command.

Never printed or logged: the refresh token, the access token, the client
secret and the authorization code. The only thing printed is the consent URL
(client id, scopes, redirect, state and PKCE challenge: nothing secret). The
library logs the redirect's request line, which carries the code, at INFO on
`google_auth_oauthlib.flow`; that logger, and the two oauthlib loggers under
it, are held at WARNING for the run. An error is reported by its type and
OAuth error code only.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

from central_command.integrations import google

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = google.TOKEN_URI
DEFAULT_PORT = 8765
KEY = "CC_GOOGLE_REFRESH_TOKEN"
# The loggers that would see the authorization code or the token.
_QUIET = ("google_auth_oauthlib.flow", "requests_oauthlib", "oauthlib")

EPILOG = f"""\
Before you start (once, in the Google Cloud console, as the Google account
whose mail and calendar Central Command will read):
  1. Create (or pick) a project and enable the Gmail API and the Google
     Calendar API.
  2. Configure the OAuth consent screen. Choose the Personal user type if
     the console offers it (only your own account, no verification, no
     7-day token expiry). Otherwise choose External and PUBLISH it ("In
     production"): in "Testing" status Google expires the refresh token
     7 days after consent, and mail and calendar would stop.
  3. Create an OAuth client of type "Desktop app". Put its id and secret in
     the env file as CC_GOOGLE_CLIENT_ID=... and CC_GOOGLE_CLIENT_SECRET=...

Then, with the browser on another machine (a headless server over ssh):
  4. On the machine with the browser, forward the port:
       ssh -L {DEFAULT_PORT}:127.0.0.1:{DEFAULT_PORT} <user>@<server>
  5. In that ssh session, from the checkout:
       .venv/bin/python -m central_command.google_consent --port {DEFAULT_PORT}
  6. Open the URL it prints in the browser, sign in as that account and
     allow the requested access (an "unverified app" warning is expected
     for an External app you have not had verified: continue).
  7. Google sends the browser to http://127.0.0.1:{DEFAULT_PORT}/ , the tunnel
     carries it here, and the command writes CC_GOOGLE_REFRESH_TOKEN into
     the env file and exits. Nothing secret is printed.
The listener answers ONE redirect: open the URL from this run only. Running it
again asks again and replaces that one key.

This does NOT switch any traffic: CC_GOOGLE_PROVIDER=native does, at the
cutover, followed by the release's usual restart.

Scopes requested: {" ".join(google.SCOPES)}
"""

PROMPT = ("Listening on 127.0.0.1 for Google's redirect. Open this URL in a browser that "
          "reaches that address (over the ssh tunnel), sign in and allow access:\n\n{url}\n")
SUCCESS = "Consent received. You can close this tab."


def _granted(creds) -> set[str]:
    scopes = getattr(creds, "granted_scopes", None) or []
    return set(scopes.split() if isinstance(scopes, str) else scopes)


def _describe(e: BaseException) -> str:
    """Type and OAuth error code only: an oauthlib error's `error`, never its
    request or the URL it was parsing."""
    code = getattr(e, "error", None)
    return f"{type(e).__name__}" + (f" ({str(code)[:80]})" if isinstance(code, str) and code else "")


def run(env_file: Path, port: int, *, out=None, err=None,
        auth_uri: str | None = None, token_uri: str | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    if not env_file.is_file():
        print(f"error: {env_file} does not exist — run this from the checkout, or pass --env-file",
              file=err)
        return 2
    values = dotenv_values(env_file)
    client_id = (values.get("CC_GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (values.get("CC_GOOGLE_CLIENT_SECRET") or "").strip()
    missing = [k for k, v in (("CC_GOOGLE_CLIENT_ID", client_id),
                              ("CC_GOOGLE_CLIENT_SECRET", client_secret)) if not v]
    if missing:
        print(f"error: {', '.join(missing)} not set in {env_file} — create a Desktop-app OAuth "
              "client in Google Cloud and put its id and secret there (see --help)", file=err)
        return 2

    from central_command.integrations import provider_extras

    refusal = provider_extras.refusal("google")
    if refusal:
        print(f"error: {refusal}", file=err)
        return 2
    from google_auth_oauthlib.flow import InstalledAppFlow

    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    # Let a partial grant through oauthlib so the check below can name what is
    # missing, instead of oauthlib raising "Scope has changed".
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    flow = InstalledAppFlow.from_client_config({"installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": auth_uri or AUTH_URI,
        "token_uri": token_uri or TOKEN_URI,
        "redirect_uris": ["http://127.0.0.1"],
    }}, scopes=list(google.SCOPES))
    try:
        # The library prints the prompt with `print`; send it to `out`.
        with contextlib.redirect_stdout(out):
            creds = flow.run_local_server(
                host="127.0.0.1", bind_addr="127.0.0.1", port=port, open_browser=False,
                authorization_prompt_message=PROMPT, success_message=SUCCESS,
                # authorization_url(**kwargs): a refresh token, and one again on a re-run.
                access_type="offline", prompt="consent",
            )
    except KeyboardInterrupt:
        print("cancelled; nothing was written", file=err)
        return 130
    except OSError as e:
        print(f"error: cannot listen on 127.0.0.1:{port} ({e.strerror or type(e).__name__}) — pick "
              "another --port and forward that one", file=err)
        return 2
    except Exception as e:  # noqa: BLE001 — a denial, a state mismatch, a token endpoint refusal
        print(f"error: the consent did not complete: {_describe(e)}; nothing was written", file=err)
        return 1
    if not creds.refresh_token:
        print("error: Google answered without a refresh token; nothing was written. Remove the "
              "app's access at https://myaccount.google.com/permissions and run this again",
              file=err)
        return 1
    lacking = [s for s in google.SCOPES if s not in _granted(creds)]
    if lacking:
        print(f"error: the consent did not grant {', '.join(lacking)}; nothing was written. "
              "Run this again and leave every requested access ticked", file=err)
        return 1
    replaced = bool(values.get(KEY))
    set_key(str(env_file), KEY, creds.refresh_token, quote_mode="never")
    print(f"{'Replaced' if replaced else 'Wrote'} {KEY} in {env_file} "
          f"(scopes granted: {' '.join(google.SCOPES)}).", file=out)
    print("Nothing is switched yet: CC_GOOGLE_PROVIDER=native does that at the cutover.", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m central_command.google_consent",
        description="Mint the refresh token for the native Google client (DL-234): Google's "
                    "installed-app OAuth flow (google-auth-oauthlib) with a loopback redirect, the "
                    "token written into the env file as CC_GOOGLE_REFRESH_TOKEN and never printed.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env-file", default=".env", type=Path,
                        help="the env file holding CC_GOOGLE_CLIENT_ID/_SECRET and receiving the "
                             "token (default: .env in the current directory)")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int,
                        help=f"the loopback port Google redirects to; forward this one over ssh "
                             f"(default {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("error: --port must be 0..65535", file=sys.stderr)
        return 2
    return run(args.env_file, args.port)


if __name__ == "__main__":
    sys.exit(main())
