# Threat model

## Assets

- Desktop input authority and screen/accessibility content.
- User files, media, credentials, application data, and action history.
- Approval tokens, session grants, adapter permissions, and local transport secrets.

## Trust boundaries

- Model output and MCP callers are untrusted.
- Application adapters are isolated and receive explicit capabilities.
- The desktop daemon is trusted to enforce policy and OS boundaries.
- Remote analysis providers receive only data allowed by the active session.

## Required controls

- Bind transports to loopback and authenticate every caller.
- Validate every request against strict schemas.
- Require an active, unexpired session for observations and actions.
- Revalidate observation, application, window, and target before input.
- Bind approvals to the exact action and expire them quickly.
- Apply path grants, action/time budgets, rate limits, and deadlines.
- Redact protected fields, secrets, and configured screen regions.
- Keep input cancellation independent from the model connection.
- Write append-only, redacted audit events for every attempted action.
- Never expose a generic shell through the desktop protocol.
