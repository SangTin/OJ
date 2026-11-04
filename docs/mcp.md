# MCP: problem and contest management

VNOJ provides an opt-in, stateless Streamable HTTP MCP endpoint at `/mcp`.
It exposes exactly six tools: `create_problem`, `edit_problem`, `delete_problem`,
`create_contest`, `edit_contest`, `delete_contest`. No user management tools or
user-related fields are exposed. The logged-in creator becomes the author of
new objects; existing authors, curators, contestants and banned-user lists are
preserved. Uploading problem test data, grading files, editorial changes and
contest rating are outside this endpoint's scope.

## Deployment

Install `requirements.txt` (pins django-oauth-toolkit 3.4.1), run
`python manage.py migrate`, then configure `dmoj/local_settings.py`:

```python
MCP_ENABLED = True
MCP_RESOURCE_URL = 'https://your-oj.example/mcp'
# Exact callback URLs, never wildcard domains.
MCP_ALLOWED_REDIRECT_URIS = ('https://claude.ai/api/mcp/auth_callback',)
MCP_REGISTRATION_LIMIT = 10  # registrations per IP/hour
```

Restart web workers. Keep the existing Redis/memcached cache shared between
workers for registration rate limiting. Serve this endpoint exclusively over
HTTPS, configure `ALLOWED_HOSTS`, and only trust proxy forwarding headers from
your reverse proxy. The server URL must match the public canonical URL, including
the path and absence of trailing slash. Reverse proxies must preserve the
Authorization header and must not cache `/mcp`, registration or token responses.
No special streaming proxy configuration is needed: this server responds with
JSON rather than an SSE stream and stores no MCP session state.

Authorization uses the existing Django OAuth Toolkit application and token tables;
DOT 3.4.1 includes migrations beyond older versions. Do not fake these migrations.
The unrelated legacy site API token mechanism and existing OAuth clients are
preserved; legacy tokens and web session cookies cannot authorize MCP calls.

## Connection and authorization

Add `https://your-oj.example/mcp` as a remote connector URL. Discovery is available at
`/.well-known/oauth-protected-resource/mcp` and
`/.well-known/oauth-authorization-server`. Dynamic client registration at
`/mcp/register` creates public authorization-code clients with exact allowlisted
redirect URIs and mandatory S256 PKCE. Users authenticate through the existing
site login and OAuth consent screens. Tokens require the `mcp` scope and must be
bound to the canonical MCP resource URL. Token validity and the user's active,
approved and password-change state are checked on every request. MCP has no
independent administrator identity; OAuth consent never grants site permissions.

Claude Code is a native client and sends `application_type: "native"` with a
loopback callback using a random local port. To support it, explicitly configure
`MCP_ALLOW_LOOPBACK_REDIRECTS = True`. This accepts only HTTP callbacks on
`127.0.0.1`, `localhost` or `[::1]`, with an explicit nonzero port and path
`/callback` or `/oauth/callback`; credentials, query strings and fragments are
rejected. Each client stores the exact submitted URI, including the port, and
still requires S256 PKCE and user consent. The setting defaults to `False`;
HTTPS web callbacks remain subject to `MCP_ALLOWED_REDIRECT_URIS`.

Optional `MCP_ALLOWED_ORIGINS` lists extra exact browser origins allowed to make
requests. With an empty tuple only the server origin is accepted; requests without
an Origin header, as used by server-to-server clients, remain supported. This
setting does not replace OAuth authorization.

## Calling tools

Send JSON-RPC 2.0 POST requests with `Content-Type: application/json`,
`Accept: application/json, text/event-stream`, `Authorization: Bearer <OAuth token>`,
and `MCP-Protocol-Version: 2025-06-18` (also supports 2025-03-26 and 2025-11-25).
Call `initialize`, then `notifications/initialized`; discover exact field schemas
with `tools/list` and call tools using `tools/call`. Notifications cannot mutate
resources. GET/SSE, session resumption and batch JSON-RPC are not supported.

Example create parameters (IDs must refer to existing site records):

```json
{
  "jsonrpc": "2.0", "id": 2, "method": "tools/call",
  "params": {
    "name": "create_problem",
    "arguments": {
      "fields": {
        "code": "sample", "name": "Sample problem", "description": "Statement",
        "group": 1, "types": [1], "allowed_languages": [1],
        "time_limit": 1, "memory_limit": 65536, "points": 100
      }
    }
  }
}
```

Create a contest with `fields` containing `key`, `name`, `start_time` and `end_time`
(ISO 8601 timestamps with timezone). Optional `problems` is the complete ordered
list of `{ "code": "sample", "points": 100, "partial": true }` entries.
`max_submissions` and `is_pretested` may also be supplied. Omit `problems` on edit
to preserve the list; an empty list removes all entries that have no submissions.
Unchanged entries preserve their IDs and submissions; removing an entry with
submissions is rejected. Related scoring changes enqueue the normal Celery
rescore tasks, so the existing Celery broker/workers must be operating.

Edits use `{ "code": "sample", "fields": { "name": "Updated title" } }`
for a problem or `{ "key": "contestkey", "fields": { ... } }` for a contest.
Only supplied fields change. Identifiers cannot be renamed through MCP. Unknown
fields and invalid JSON types are rejected rather than ignored or coerced.
Arrays are limited to 200 entries and request bodies to 1 MiB. Use `tools/list`
for the full supported field list; it intentionally excludes executable contest
label scripts, rating controls and user relations.

Deletion uses `{ "code": "sample", "confirm": true }` or
`{ "key": "contestkey", "confirm": true }`. Problem deletion uses the site's
soft-delete workflow. Contest deletion removes the contest and its related
associated configuration only when there are no participants, submissions or
ratings. Contests containing any of these cannot be deleted through MCP; their
results remain intact. Successful contest deletion writes a Django admin audit
entry with the actor and a snapshot of scalar contest configuration (excluding
access codes). This is an audit record, not a full restore/undelete mechanism. Clients should ask the human to confirm deletion before setting this
flag. MCP requires both object edit rights and Django `delete_problem` or
`delete_contest`, which is stricter than the site's general edit/delete flow.

## Permission boundaries

Creation requires `judge.add_problem` or `judge.add_contest`. Editing requires
the model's `is_editable_by` check, including the site's own/all/public edit
permissions and author/curator membership. Sensitive problem fields require
`change_public_visibility`, `change_manually_managed` or `problem_full_markup`;
editing the description of an existing full-markup problem also needs the latter.
Organization changes require `create_organization_problem` for problems and
`create_private_contest` for contests, plus administration of the selected
organization (or `edit_all_organization`). Organization-private objects require
an organization. Contest access codes need `contest_access_code`. Global contest
visibility requires `change_contest_visibility`; `create_private_contest` can
publish only contests that remain private. Removing private restrictions from a
visible contest cannot bypass this global visibility check.

A contest can reference public accessible problems or private problems that the
caller can edit. It cannot take another author's inaccessible/private problem.
Writes validate Django forms/models and run atomically. Creation, edits and
problem soft deletion record revisions; empty-contest deletion records the audit
entry described above. Normal model signals invalidate caches. Tool failures return `isError: true`.
Errors after a successful commit (for example a broker failure in an existing
post-commit hook) require checking the resource before retrying.

## Validation

Run targeted tests in the project's normal MySQL/Python 3.12 environment:

```bash
python manage.py test judge.tests.test_mcp judge.tests.test_mcp_auth --noinput
flake8 judge/mcp.py judge/mcp_auth.py judge/tests/test_mcp.py judge/tests/test_mcp_auth.py
```

Tests cover OAuth boundaries and registration, protocol handling, six-tool scope,
object authorization, sensitive fields, strict input types and transactional CRUD.

Problem statement syntax is documented in [Problem statement authoring guide](problem-statement-format.md).
MCP sends these rules in its initialization instructions and the problem description field schema.

## Creation response to the user

After `create_problem` or `create_contest` succeeds, the AI must include a clickable
Markdown link to the created object in its reply. Use the tool result's `url`;
resolve relative paths against the MCP server origin. Use a descriptive label in
the user's language, such as `[View problem](https://your-oj.example/problem/sample)`
or `[View contest](https://your-oj.example/contest/contestkey)`. Do not construct
paths from assumed routing rules, and do not claim successful creation or provide
a newly created object link when the tool reports an error.

## OAuth lifecycle and reverse proxies

MCP accepts only public authorization-code clients created through the restricted
DCR endpoint and still compliant with the current redirect policy. Applications
registered through `/o/applications/register/` cannot request or use the `mcp`
scope, even if a token was issued before this restriction. Existing non-MCP OAuth
clients keep their original behavior.

`MCP_ACCESS_TOKEN_EXPIRE_SECONDS` defaults to 3600 (one hour).
`MCP_REFRESH_TOKEN_EXPIRE_SECONDS` defaults to 604800 (seven days for each refresh
token); rotation issues a new refresh token. Password changes revoke the user's
MCP access tokens, refresh tokens and pending authorization grants. Applications
must reconnect after revocation. Non-MCP OAuth tokens are preserved.

Registration ignores unknown metadata (such as `client_uri`, `logo_uri`,
`software_id`) without fetching URLs or applying extra application privileges.
MCP discovery advertises only `token_endpoint_auth_method: none` for its public
clients. Malformed/deeply nested JSON is rejected.

When TLS terminates at a trusted reverse proxy, configure Django to recognize the
original HTTPS scheme; setting the canonical resource URL alone is not enough
for DOT's audience verification. For example, **only when the proxy strips any
client-supplied value and sets `X-Forwarded-Proto` itself**:

```python
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
MCP_RESOURCE_URL = 'https://your-oj.example/mcp'
```

Preserve the public Host/Authorization headers and configure `ALLOWED_HOSTS`.
Do not trust forwarded headers from arbitrary callers. Discovery challenges use
the configured canonical resource origin. Clients must use that same origin.

## Website authoring limits

Website forms and MCP share problem time-limit, contest-duration and organization
code-prefix validators. Higher problem time limits need
`judge.high_problem_timelimit`; longer contests need `judge.long_contest_duration`.
Organization problems/contests must have the organization's normalized code
prefix. With `VNOJ_QUOTA_ENFORCEMENT_ENABLED`, problem creation or transfer into an
organization checks its problem/storage quota under a database row lock.
`disallow_virtual` is restricted to superusers, matching the website form.
Creation sets a problem's date and schedules the existing `on_new_contest` hook
for contests after commit. Official contest mode keeps its required defaults.
