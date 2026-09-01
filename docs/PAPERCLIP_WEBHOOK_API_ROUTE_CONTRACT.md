# Verified webhook-scoped plugin API routes

## Problem

Paperclip manifests accept `auth: "webhook"` for scoped plugin API routes, but
the stable plugin SDK exposes no API-route signature-verifier declaration or
callback. The host therefore rejects these routes before `onApiRequest` with
HTTP 422. A handler cannot make the route safe (or usable) by verifying the
request itself because it is never invoked.

Papervoice needs this mode for:

```text
GET /api/plugins/papervoice/api/boardroom-config?companyId=<uuid>
Authorization: Bearer <short-lived HMAC token>
```

The caller has no Paperclip actor identity. The token is minted by the plugin,
stored in LiveKit room metadata, scoped to a company and room, and expires.

## Required host/SDK contract

Webhook-authenticated API routes must use a separate verification phase before
normal route dispatch:

1. A plugin declaring any API route with `auth: "webhook"` must also implement
   `onVerifyApiRequest`. Plugin installation or enablement should fail clearly
   when the callback is absent.
2. The host resolves the route and declared `companyResolution`, but treats the
   query-derived company ID as untrusted until verification succeeds.
3. The host invokes `onVerifyApiRequest` with the route key, method, normalized
   path/query, headers, raw body bytes, and resolved company ID. It must not log
   authorization or signature values.
4. The callback returns a discriminated result:

   ```ts
   type PluginApiVerificationResult =
     | { verified: true }
     | { verified: false; status?: 401 | 403; code?: string };
   ```

   Exceptions, timeouts, malformed results, and missing callbacks fail closed.
5. Only after `{ verified: true }` may the host dispatch the unchanged request
   to `onApiRequest`. A successful verification grants access only to that one
   request; it must not create an actor session or authorize other routes.
6. The host should rate-limit verification attempts before worker dispatch and
   preserve the plugin handler's per-company rate limit after verification.

The verifier runs in the plugin worker so it can read company-scoped config and
resolve plugin-owned secret references without exposing secret material to the
host route layer. A manifest-only algorithm declaration is insufficient for
Papervoice because its signing key is company-scoped plugin configuration.

## Papervoice verifier semantics

Papervoice's verifier must reuse the existing boardroom token validation logic
and require all of the following before returning `verified: true`:

- `routeKey === "boardroom-config"`;
- an exact `Bearer` authorization scheme;
- a configured HMAC secret for the query-resolved company;
- a valid constant-time HMAC-SHA256 signature;
- a token company ID equal to the query company ID;
- a non-empty room name and an unexpired expiration timestamp.

The `boardroom-config` handler should retain its validation as defense in depth.
No compatibility mode should dispatch webhook-authenticated routes when the
verifier callback is missing.

## Acceptance test

An integration test in Paperclip core should register a fixture plugin with a
webhook API route and verifier, then prove:

- absent, malformed, expired, wrongly signed, and cross-company tokens are
  rejected before `onApiRequest`;
- a valid token reaches `onApiRequest` with the original query and headers;
- a webhook route without a verifier remains fail-closed;
- non-webhook route authentication is unchanged.

After a host release containing this contract is installed, Papervoice can add
the callback against that SDK, rebuild/reinstall the plugin, and rerun
`scripts/healthcheck` plus a live join test.
