# PRD template

The structure of the file the `prd` skill writes to `docs/prd/<slug>-prd.md`. English. Every section is
present; one that does not apply says so in one line with the reason. Facts, assumptions and decisions
are kept apart: an assumption is marked `(assumption)` until the user confirms it.

```markdown
# PRD: <product name>

Status: draft            <- draft | approved (only the user approves)
Last updated: YYYY-MM-DD
Owner: <the person the user named, or "not stated">

## 1. Executive summary
One paragraph: the problem, who has it, what we build, how we know it worked.

## 2. Problem and context
What happens today, why it hurts, why now. Evidence the user gave.

## 3. Goals and KPIs
| Goal | KPI | Target | Guardrail (must not get worse) |

## 4. Stakeholders and RACI
| Activity | Responsible | Accountable | Consulted | Informed |

## 5. Users, personas and jobs to be done
Per persona: who they are, the job they hire the product for, their current workaround.

## 6. Use cases and main user journeys
Main flow per use case, then edge cases and error paths.

## 7. Scope
| Requirement | MoSCoW | Goal or KPI it serves |
Out of scope: an explicit list.

## 8. Functional requirements
Numbered (FR1, FR2, ...), each testable, each traced to a goal.

## 9. Non-functional requirements
Performance, availability, supported devices and languages, accessibility, security.

## 10. Metrics, tracking plan and experiments
| Event | Trigger | Attributes | Personal data? | Purpose |

## 11. Dependencies, risks and mitigation
| Risk | Likelihood | Impact | Mitigation | Owner |

## 12. Alternatives considered and why not
| Alternative | Why not (the user's reason, or "not stated") |

## 13. Milestones, roadmap and budget
Milestones with dates and budget only as the user stated them.

## 14. Acceptance criteria and definition of done
Functional scenarios in Given/When/Then form, then the security scenarios, then the security gates
agreed for this product (see the list below).

## 15. Open questions
| Question | Who answers | By when |

## 16. Compliance profile
(template in eu-compliance-checklist.md)

## 17. Data inventory
| Field | Tier | Retention | Who may access | Notes |

## 18. Authentication and authorization
(template in auth-strategy-decision-tree.md)

## 19. Backup and disaster recovery
RTO, RPO, mechanism, restore drill cadence, off-site copy location.

## 20. Threat model (STRIDE)
(template in threat-model-template.md)

## Appendix
Schemas, API contracts, mockups - only when the user asks for them.
```

(`<-` marks an explanation, not file content.)

## Security acceptance scenarios (examples)

```gherkin
Feature: Authorization boundary
  Scenario: User A cannot read User B's invoices
    Given User A is logged in
    And User B has invoices
    When User A requests the invoice list
    Then User A sees only User A's invoices
    And no data of User B appears in the response

  Scenario: A request without login is rejected
    Given no login token is sent
    When the invoice list is requested
    Then the response status is 401
    And no invoice data is returned
```

## Security gates to choose from

Agree with the user which apply; each chosen gate becomes a checkbox in section 14.

- [ ] Every table holding user data has row-level access control (row-level security or an equivalent
      check in every query), proven by automated tests that try to read another user's or tenant's data.
- [ ] Security headers configured (Content-Security-Policy, Strict-Transport-Security,
      X-Content-Type-Options); a public checker such as Mozilla Observatory shows no failing header.
- [ ] Cookie consent: opt-in, "Reject all" as prominent as "Accept all", in the site's language.
- [ ] AI disclosure shown to users where the AI Act transparency duty applies.
- [ ] Accessibility statement published where the EU Accessibility Act applies.
- [ ] Data subject requests (access, deletion, export) work end to end.
- [ ] Error tracking and logs scrub personal data before it leaves the application.
- [ ] No secrets in the repository: a secret scanner runs before commit and on push.
- [ ] Third-party CI actions and build dependencies pinned to exact versions or commit hashes.
- [ ] Restore from backup tested at the agreed cadence, with the date of the last successful drill.
