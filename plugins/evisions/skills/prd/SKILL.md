---
name: prd
description: Interview the user, one block of questions at a time, to a PRD (product requirements document) that covers the business side (problem, goals, users, scope, acceptance criteria) AND the technical dimensions a non-developer does not know to ask about - GDPR and EU AI Act triage, data classification, authentication, backup and disaster recovery, and a STRIDE threat model. The interview runs in the user's language; the finished PRD is written in English to docs/prd/<slug>-prd.md. Use when the user wants a PRD, requirements or a product spec for something that will be built ("napiš PRD", "sepiš zadání", "potřebuju požadavky na appku", "write a PRD", "product requirements", "spec this app"). NOT for HOW to build it (architecture, implementation plan) and NOT for developing an idea that is not decided yet (use evisions:socratic-brainstormer).
---

# PRD - requirements interview

<purpose>
Lead the user to an approved PRD: what will be built, for whom, why, how success is measured, and the
technical and legal dimensions a person who is not a developer would not think to raise. The PRD states
WHAT and WHY; HOW (architecture, code, tasks) comes after it and is not this skill's job.
</purpose>

<hard_rules>
- **WHAT and WHY before HOW.** Never design a solution until the problem, goals and scope are confirmed.
  Why: a solution chosen before the problem is clear gets defended instead of questioned.
- **One block at a time.** Ask 3 to 7 questions, then stop and wait for the answers. Why: a wall of
  questions gets skimmed and half-answered.
- **Only the user's answers are facts.** Never invent a goal, persona, metric, number, deadline or
  budget. Your inference is written as an Assumption and labelled so. Why: the PRD is later read as
  agreed.
- **Interview in the user's language; the PRD file is English.** Translate their words faithfully,
  never embellish. Why: the team shares the file, the user thinks in their own language.
- **Compliance is triage, not legal advice.** Flag what applies and why; recommend a lawyer or the data
  protection officer where the references say so. Why: a confident wrong legal answer costs more than
  an honest "check this".
- **Dated facts get checked.** Deadlines, vendor features and prices in the references were current when
  written; before the PRD states one as fact, check the official source or mark it "verify". Why: rules
  and products change faster than this skill.
- **No secret values** in the PRD or anywhere you write; name a credential by its variable NAME only.
- **Journals and file locations** follow the documentation standard (journals before the first edit;
  environment rules on where files may be written win).
</hard_rules>

## Start

1. **Existing PRD?** Look in `docs/prd/`. If a PRD for this product exists, ask whether to revise it
   rather than start a new one.
2. **Mode.** Ask whether the user wants to go step by step, or get a quick draft first and iterate on it.
3. **Calibration.** Ask, in the user's language: who is this for - (a) internal use in our own team,
   (b) one client, (c) the public or many clients - and will it include an AI feature (chatbot, text or
   image generation, classification, prediction)? Calibrate the technical phases:

| Answer | Emphasis |
|---|---|
| (a), no AI | Light compliance triage; DPIA and AI Act transparency usually skipped; basic backup tier |
| (a), with AI | AI Act transparency usually not needed (no outside users), but check personal data sent to the model provider |
| (b) one client | Full compliance triage; the client is usually the data controller and we the processor; DPIA likely if personal data is sensitive or large-scale; AI Act if AI |
| (c) public or many clients | Full compliance baseline; EU Accessibility Act likely if consumer-facing; AI Act transparency if AI; full GDPR data subject rights |

## Living state

End every turn with a short status block, so the user always sees where the PRD stands:

- **Facts** - what the user stated
- **Assumptions** - your inferences, each marked for confirmation
- **Decisions** - what the user decided
- **Open questions**
- **Risks and dependencies**

Keep it to the items that changed or matter now; the full list goes into the PRD.

## Phases

Close each phase with an explicit approval question ("Approved? yes / no / changes") before moving on.
Flag ambiguities, scope conflicts, missing metrics and privacy risks as you meet them.

0. **Kickoff** - context, the problem, why now, who is affected, who decides.
1. **Goals and KPIs** - business objectives, how success is measured, guardrail metrics (what must not
   get worse). Insist on something measurable.
2. **Users, jobs and use cases** - personas, jobs to be done, main flows and edge cases.
3. **Scope** - MoSCoW priorities (Must, Should, Could, Won't), what is explicitly out of scope,
   alternatives considered and why not.
4. **Non-functional requirements** - performance, availability, languages, devices, accessibility,
   security, compliance. Then the four technical sub-phases below, as calibrated.
5. **Dependencies, risks and mitigation** - technical and organizational, plus the STRIDE threat model.
6. **Analytics and tracking plan** - events, attributes, funnels, and what the tracking means for privacy.
7. **Milestones, roadmap, budget, responsibilities** - a RACI (who is Responsible, Accountable,
   Consulted, Informed) and a timeline; numbers only from the user.
8. **Acceptance criteria and definition of done** - Given/When/Then scenarios, including the security
   scenarios below.
9. **Review and sign-off** - summary, open items, decisions still to close.

Every requirement maps to a goal or metric and carries a MoSCoW priority, so nothing floats without a
reason to exist.

### 4a. Compliance triage

Ask these yes/no questions in one block:

1. Does it process personal data (names, emails, addresses, IP addresses, behaviour tracking)?
2. Does it process special categories or other sensitive data (health, biometrics, finances,
   political views, sexual orientation, criminal records)?
3. Does it profile people or make automated decisions about them (scoring, hiring, recommendations
   with real effects)?
4. Is the scale large (many thousands of EU users, or sensitive data at any real scale)?
5. Does it include an AI feature?
6. Does data leave the EU (a US cloud, a model API, a third-party service)?
7. Does it involve vulnerable people (children, employees, patients) or combine datasets from different
   sources?

Routing:
- Question 1 no: GDPR mostly does not apply; say so and move on.
- Two or more yes among 2, 3, 4, 5 and 7: a DPIA (data protection impact assessment, GDPR Article 35) is
  likely required.
- Question 5 yes: classify the AI feature under the EU AI Act (prohibited, high-risk, limited-risk with
  transparency duties, minimal-risk).
- Question 6 yes: a transfer mechanism per recipient (adequacy decision, standard contractual clauses).

Output: the PRD section "Compliance profile". Details, dates and the output template:
`references/eu-compliance-checklist.md`.

### 4b. Data classification

For every data field the product will store, agree a tier with the user: Public, Internal, Personal
data, Sensitive personal data. Each tier implies handling (where it may appear, access control,
encryption, audit log, retention, data subject rights). Output: the PRD section "Data inventory", a table
of fields with tier, retention period and who may access it. The tier table is in
`references/eu-compliance-checklist.md` under "Data classification".

### 4c. Authentication and authorization

Walk the decision tree with the user: will users log in at all, the primary login method, multi-factor
authentication policy, the authorization model (roles, tenants), sessions, and account deletion.
Record each choice with the user's reason. Output: the PRD section "Authentication and authorization".
Decision tree and defaults: `references/auth-strategy-decision-tree.md`.

### 4d. Backup and disaster recovery

Three questions, each answered by the user in business terms:
1. How long may the product be down after a disaster? (RTO, recovery time objective)
2. How much recent data may be lost? (RPO, recovery point objective)
3. How often do we prove a restore actually works?

Offer tiers so the user can pick without technical knowledge:

| Tier | RTO | RPO | Restore drill | Typical mechanism |
|---|---|---|---|---|
| Production | under 1 hour | under 1 minute | quarterly | managed database with point-in-time recovery plus replication to a second region |
| Standard | under 4 hours | under 1 hour | yearly | managed database with point-in-time recovery |
| Basic | under 24 hours | under 24 hours | on each schema migration | daily snapshot or dump, manual restore |
| Best effort | none promised | up to a week | never (risk accepted in writing) | weekly dump |

Output: the PRD section "Backup and disaster recovery" with RTO, RPO, mechanism, restore drill cadence
and where the off-site copy lives (another region or another provider).

### 5. Threat model (STRIDE)

After the ordinary risks, walk the main components (browser, server or API, database, auth, automations
and webhooks, external APIs such as model providers, network edge, hosting) through STRIDE: Spoofing,
Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege. For each real
threat: likelihood, impact, mitigation, residual risk. A component without a given threat gets one line
saying why. Output: the PRD section "Threat model". Checklist, risk matrix and template:
`references/threat-model-template.md`.

### 8. Security acceptance criteria

Beyond functional scenarios, write security scenarios in Given/When/Then form, for example that user A
cannot read user B's records and that a request without login is rejected. Then agree the security gates
that apply to this product (the list is in `references/prd-template.md`, section 14).

## Writing the PRD

- Path: `docs/prd/<kebab-slug>-prd.md` at the project root (create `docs/prd/` when needed); the slug is
  the product name, lowercase, hyphens, no diacritics.
- Structure: `references/prd-template.md`. Sections that do not apply stay with one line saying why.
- Write it when every phase is approved, or earlier when the user asks for a draft; a draft carries
  `Status: draft` and lists the open questions. Only the user sets `Status: approved`.
- Decisions the user makes during the interview (for example the login method or the backup tier) are
  decisions under the documentation standard as well: add the decision-log row or the capture line.
- After writing, tell the user in their language where the file is, what is still open, and which
  sections need a lawyer or a developer to confirm.

## Shortcuts the user can ask for

Status of the PRD; the next question block; a one-paragraph summary; a single section on its own (the
compliance profile, the data inventory, the auth decision, the backup strategy, the threat model, the
risk matrix, the tracking plan, the roadmap); the functional requirements as a backlog of user stories
ready to paste into an issue tracker.

<hard_rules_repeat>
WHAT and WHY before HOW. One question block at a time, each phase approved before the next. Only the
user's answers are facts; inferences are labelled assumptions. Interview in the user's language, PRD in
English at `docs/prd/<slug>-prd.md`. Compliance is triage with a pointer to counsel, and dated facts are
checked before they are stated. No secret values.
</hard_rules_repeat>
