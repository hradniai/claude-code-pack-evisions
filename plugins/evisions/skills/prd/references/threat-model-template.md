# Threat model template: STRIDE

Reference for phase 5 of the `prd` skill. Walk each main component through the six STRIDE questions,
keep only the threats that are real for this product, and give every kept threat a mitigation and a
residual risk.

## STRIDE

| Letter | Threat | Question |
|---|---|---|
| S | Spoofing | Can someone pretend to be a user, a system or a service? |
| T | Tampering | Can data be changed in transit or at rest by someone who should not? |
| R | Repudiation | Can someone deny an action, with no way to prove they did it? |
| I | Information disclosure | What data could leak, to whom, through which channel? |
| D | Denial of service | What could overload the system, crash it, or run up its bill? |
| E | Elevation of privilege | Can a user gain rights they should not have (admin, another tenant's data)? |

## Components to walk through

1. The browser or app the user runs.
2. The server side: API, server functions, background jobs.
3. The database and file storage.
4. Authentication and sessions.
5. Automations and webhooks (workflow tools, scheduled jobs, inbound webhooks).
6. External APIs: model providers, payments, email, analytics.
7. The network edge: reverse proxy, CDN, firewall.
8. Hosting and deployment: servers, containers, CI/CD, secrets.

Most components do not face all six threats. For each one that does not, write one line saying why.

## Common threats and mitigations

### Spoofing

| Threat | Mitigation |
|---|---|
| Logging in as another user | a maintained auth provider, email verification, MFA for sensitive roles |
| Session theft through injected script (XSS) | Content-Security-Policy, HttpOnly cookies, sanitising user-supplied rich text |
| An API key shipped to the browser | secrets only on the server; never in public build-time variables (for example `NEXT_PUBLIC_*`, `VITE_*`) |
| Forged login redirects (OAuth state, CSRF) | the provider's own OAuth flow, which checks the state parameter |
| Email pretending to come from us | SPF, DKIM and DMARC with a reject policy on the sending domain |

### Tampering

| Threat | Mitigation |
|---|---|
| Trusting roles or prices sent by the client | re-derive everything security-relevant on the server |
| Mass assignment (the request body written straight into the database) | an explicit allow-list schema of accepted fields |
| Path traversal in uploads | generate file names on the server; never use the client's file name as a path |
| An admin database key used in client code, bypassing row-level security | admin keys only in server-only modules, checked by a search in CI |
| SQL injection | parameterised queries only; never string-built SQL |

### Repudiation

| Threat | Mitigation |
|---|---|
| "I did not delete that" | an append-only audit log of destructive actions: who, when, what |
| "I never consented" | a consent log: timestamp, what was agreed, which version of the text |
| Disputed payments | verified payment webhooks, idempotency keys, a payment audit log |
| Disputed AI-assisted content | record where AI was involved, and say so to users |

### Information disclosure

| Threat | Mitigation |
|---|---|
| User A reads user B's data | row-level access control on every table, with tests that try to cross the boundary |
| Error messages leaking stack traces or connection strings | generic errors to the client, details only to the error tracker |
| Backups or dumps publicly reachable | private storage, encryption at rest, never inside the web root |
| Personal data in logs and error tracking | scrub before sending; never log request bodies wholesale |
| API responses with more fields than needed | select only the fields the client needs |
| Personal data sent to a model provider | send the minimum, strip identifiers where possible, a data processing agreement with the provider |

### Denial of service

| Threat | Mitigation |
|---|---|
| Brute force on login | rate limiting and lockout (often built into the auth provider) |
| An LLM cost explosion (abuse or prompt injection running up the bill) | `max_tokens` on every call, a per-user daily budget, a monthly spending cap that switches the feature off, an alert before the cap |
| Database connection exhaustion | a connection pooler |
| Flooding a public webhook | signature verification, rate limiting per source |
| Any public endpoint without limits | rate limiting at the edge |
| Expensive inputs (huge JSON, regular expressions from users) | input size limits, parse depth limits, timeouts |

### Elevation of privilege

| Threat | Mitigation |
|---|---|
| A logged-in user calls an admin-only function | a server-side role check on every privileged operation |
| A new table without row-level access control | deny-by-default for new tables, tests in CI |
| Forged token claims | verify tokens with the provider's library on the server; never trust a token decoded in the browser |
| A webhook that triggers privileged actions | signature verification plus a permission check before acting |
| An AI agent with private data, untrusted input and the ability to act (the "lethal trifecta") | remove one of the three: read-only agent, human approval of every action, or no untrusted input |
| Container escape | run as a non-root user, read-only filesystem where possible, never privileged mode |

## Risk matrix

| | Impact low | Impact medium | Impact high |
|---|---|---|---|
| **Likelihood high** | medium | high | critical |
| **Likelihood medium** | low | medium | high |
| **Likelihood low** | low | low | medium |

- **Critical**: mitigated before launch.
- **High**: mitigated before launch, or accepted by the user in writing with the reason.
- **Medium**: mitigated when the budget allows, otherwise planned after launch.
- **Low**: documented and watched.

## Output template (PRD section 20)

```markdown
## 20. Threat model (STRIDE)

### Trust boundaries
One paragraph: browser, server, database, third-party services, and what crosses between them.

### Threats
| ID | Threat | STRIDE | Likelihood | Impact | Risk | Mitigation | Residual risk |
|---|---|---|---|---|---|---|---|
| T1 | <threat> | I | high | high | critical | <control> | <what remains> |

### Must be mitigated before launch
1. T1 - <plan>

### Accepted risks
- T7 - accepted by <who> because <reason>; revisit at <milestone>

### Out of scope
- <threat> - <why: someone else's responsibility, or beyond our control>
```

## When to bring in a security firm

- Regulated data (health, payments, public sector) where a sector framework prescribes threat modelling.
- A high-risk AI classification under the EU AI Act.
- A client audit (SOC 2, ISO 27001) that requires a threat model as evidence.
- A high-value target: payments, sensitive personal data at scale.
- Low confidence in the team's own model.
