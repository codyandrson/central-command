> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Enterprise SSO

Enterprise SSO is available on managed cloud. Any Account Owner can submit credentials and request domains; a Zep operator then approves the domains and enables the connection, which is where eligibility is decided. See [pricing](https://www.getzep.com/pricing). BYOC deployments authenticate through deployment configuration instead, so the settings panel described here does not appear in a BYOC dashboard.

Enterprise SSO makes your identity provider the source of truth for who can reach your Zep account. Members sign in with your IdP rather than a Zep-held credential, so suspending someone in your directory removes their route into Zep.

You configure your own IdP credentials. Zep configures which email domains route to you, because a domain is a claim on a namespace shared by every Zep customer — the same domain cannot point at two accounts. That split is why setup has two halves: you submit credentials and request domains, and a Zep operator approves the domains and turns the connection on.

## Before you start

* You need the `sso.manage` capability, which is granted to the Account Owner only. See [Managing team access](/role-based-access-control).
* Register a confidential OIDC application in your identity provider for the Zep dashboard. You need its issuer URL, client ID, and client secret.
* Have your email domains ready. You can request up to 20 domains per submission.

Enterprise SSO covers dashboard sign-in for your members. Signing end users in to a project's Memory MCP Server is a separate connection with its own OIDC application — see [Configuring authentication](/memory-mcp-server/authentication).

## Set up enterprise SSO

Configure the connection in the Zep Dashboard under **Account ▸ Settings ▸ Enterprise SSO**.

#### Create an OIDC application in your IdP

Register a confidential OIDC client for the Zep dashboard. Note the issuer URL, client ID, and client secret. Zep derives the signing keys from the issuer's OIDC discovery document, so you do not enter a JWKS URL.

#### Request your email domains

Enter the domains your members sign in with. Each is recorded as a request that is **pending** until a Zep operator approves it. A pending domain routes nothing — it is inert until approval. At least one domain is required the first time you submit.

#### Submit your credentials

Enter the issuer URL, client ID, and client secret, then save. Zep fetches the issuer's discovery document to confirm it is reachable and rejects the submission if it is not. The secret is stored encrypted and is never displayed again.

Saving does not turn SSO on. The first submission creates your connection in a pending state.

#### Wait for Zep to approve and enable

A Zep operator approves your domain requests, validates the issuer and its signing keys, then enables the connection. Approval fails if another account already holds a domain you requested; Zep contacts you when that happens.

The panel shows each domain as either approved and routing sign-ins, or pending approval.

## Optional settings

Leave these blank unless your IdP requires them.

| Setting        | Description                                                                                                                                                                                             |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Groups claim   | The claim carrying the user's group membership. Required if you restrict sign-in by group.                                                                                                              |
| Allowed groups | Restricts sign-in to members of these groups. Group membership is read from the token at sign-in, so it is only as fresh as the user's last login.                                                      |
| Scopes         | Extra scopes to request from your IdP beyond the defaults.                                                                                                                                              |
| Prompt         | The OIDC `prompt` value to send, such as `login` to force reauthentication.                                                                                                                             |
| Email claim    | The claim Zep reads the user's email address from. When set, that claim is the only accepted source — Zep does not fall back to another claim if it is missing. Leave blank to use the standard claims. |

## Rotating credentials

Submit the form again with the new values. The new secret is written before the change takes effect, so a rotation never leaves the connection pointing at a secret that no longer exists.

Changing the issuer URL or client ID changes what Zep validates sign-ins against, so it disables the connection until a Zep operator revalidates it. Rotating only the client secret leaves the connection enabled.

## Adding and removing domains

Request additional domains at any time through the same panel; each new domain is pending until Zep approves it. Approved domains are read-only — to retire one, contact Zep. Existing sign-ins for your other approved domains are unaffected while a new request is pending.

## What members see

Nothing changes for members until Zep enables the connection. After that, members whose email address is at an approved domain are sent to your identity provider to sign in.