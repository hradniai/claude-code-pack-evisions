# Authentication and authorization decision tree

Reference for phase 4c of the `prd` skill. Ask the user each step in plain words, record their choice
and their reason. Provider names are examples, not endorsements; check a provider's current feature and
pricing pages before the PRD relies on them.

## Step 1: will users log in at all?

No login is a valid answer for:
- a marketing site with public content only;
- a read-only public dashboard;
- an internal tool reachable only through the company network or a VPN;
- a client-side-only tool that stores nothing on a server.

Then the PRD records why there is no login, and the rest of this tree is skipped.

## Step 2: primary login method

| Method | Fits | Notes |
|---|---|---|
| Magic link by email | low-friction sign-up, content sites, low-stakes tools | security equals the security of the mailbox |
| Email and password | B2B tools, where users expect it | passwords hashed with Argon2id (the OWASP recommendation) or bcrypt; never MD5 or SHA-1 |
| Social or work account (Google, Microsoft, GitHub) | users who already have such an account | match the audience: companies mostly have Microsoft or Google accounts, developers GitHub |
| Passkeys (WebAuthn) | the strongest protection against phishing | use a provider or a maintained library; never implement WebAuthn yourself |
| Enterprise single sign-on (SAML or OIDC) | enterprise clients who require their own identity provider | often a paid tier of the auth provider |

Defaults to propose:
- **Client website with accounts:** magic link plus one social login, passkeys when the provider supports
  them.
- **B2B application:** email and password plus multi-factor authentication, enterprise single sign-on as
  an option for large clients.

Do not:
- write your own authentication (token signing, password storage, session handling): use a maintained
  auth provider or framework (for example the auth service of the chosen backend platform, or a
  maintained auth library for the chosen framework);
- implement WebAuthn by hand;
- build on an abandoned library (for example Lucia, deprecated by its author in 2025 and turned into a
  learning resource).

## Step 3: multi-factor authentication (MFA)

| Who | MFA |
|---|---|
| Administrators and other privileged roles | mandatory |
| Access to financial or health data | mandatory |
| Ordinary users | optional, offered as opt-in |
| Public read-only access | none (no accounts) |

- Use an authenticator app (TOTP, time-based one-time codes) or passkeys; not SMS codes, which SIM-swap
  attacks defeat.
- Recovery codes: 8 to 10, shown once, stored hashed.
- Check the provider's MFA support is generally available, not beta, before a production launch.

## Step 4: authorization model

| Model | Fits | Typical implementation |
|---|---|---|
| One tenant, one role | personal or internal tool, simple client website | every query filtered by the logged-in user's ID |
| One tenant, several roles (admin, user, viewer) | admin panels, dashboards | role stored server side, checked on every request |
| Multi-tenant (workspaces, organizations) | SaaS, several clients in one system | every row carries a tenant ID; database-level row security (for example PostgreSQL row-level security) or a mandatory filter in every query |
| Attribute-based (ABAC) | complex rules: time, location, data classification | a policy engine (for example Open Policy Agent or Casbin) on top of the basic checks |
| Relationship-based (ReBAC) | sharing, hierarchies, social features | a relationship-based authorization service on top of the basic checks |

Rules that hold for every model:
- Authorization is checked on the server for every request. Hiding a button is not access control.
- The database-level check is the floor; a role system is a layer above it, never instead of it.
- Tenant isolation is proven by automated tests: a user of tenant A must not see anything of tenant B.

## Step 5: sessions and account lifecycle

| Decision | Default |
|---|---|
| Session storage | an HttpOnly, Secure cookie (ideally with the `__Host-` prefix); never a token in localStorage, where any injected script can read it |
| Session length | sliding one week for ordinary users, one day for admin or financial access |
| Logout | this device by default, "log out everywhere" in account settings |
| Account deletion (GDPR Article 17) | required wherever personal data is stored: real deletion across all related data plus an audit entry; a soft delete that keeps the data is not erasure |
| Inactive accounts | a stated policy (for example delete after 12 months without login, with a warning email) |

## Output template (PRD section 18)

```markdown
## 18. Authentication and authorization

### Login
- Method: magic link | email and password | social login (<providers>) | passkeys | SAML/OIDC SSO
- Provider or library: <name>
- Reason: <the user's reason>

### Multi-factor authentication
- Policy: all users | privileged roles | optional | none
- Method: authenticator app | passkeys | recovery codes

### Authorization
- Model: one role | several roles | multi-tenant | attribute-based | relationship-based
- Enforcement: <where the check happens>
- Tenant isolation: <how, and which tests prove it>

### Sessions
- Storage, length, logout behaviour

### Account lifecycle
- Sign-up: <self-service with verification | invitation only>
- Password reset: <provider-managed | custom>
- Deletion: <self-service | on request>, <what is deleted, in which systems>
- Inactive accounts: <policy>

### Audit
- Logged events: <logins, failed logins, password and MFA changes, role changes>
```

## Common mistakes

- Offering a social login the audience does not have.
- Forcing passwords where a magic link is the better experience.
- Skipping MFA for administrators because "it is only us": admin accounts are the most valuable target.
- Multi-tenancy without tests that prove the isolation.
- A custom role table with no database-level check underneath.

## When to get a specialist

- Enterprise contracts that prescribe an identity provider, audit log retention or a certification
  (SOC 2, ISO 27001).
- Regulated data (medical, financial, public sector).
- Users in several jurisdictions with data residency requirements.
- Users under 16: parental consent rules differ by country.
