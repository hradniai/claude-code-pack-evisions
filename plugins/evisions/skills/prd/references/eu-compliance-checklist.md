# EU compliance checklist: GDPR, AI Act, Accessibility Act, ePrivacy

Reference for phase 4a (compliance triage) and 4b (data classification) of the `prd` skill.

<verify_first>
Written in 2026. Regulations get amended and guidance changes: in 2025 and 2026 the EU proposed and
negotiated "digital omnibus" changes that move some AI Act dates, especially for high-risk systems and
the marking of AI-generated content. Before a PRD states a deadline, a threshold or a vendor fact as
current, check the official source (EUR-Lex for the legal text, the European Commission's AI Act pages,
the EDPB, the national data protection authority) or write "verify" next to it. This is triage to find
what applies, not legal advice.
</verify_first>

## GDPR (General Data Protection Regulation)

Applies since 25 May 2018 to any processing of personal data of people in the EU, wherever the
processing happens.

### Controller or processor

- **Controller**: decides why and how personal data is processed. On client work this is usually the
  client.
- **Processor**: processes personal data on the controller's behalf. An agency building or running a
  product for a client is usually a processor, and each hosting or API provider it uses is a
  sub-processor. A processor needs a data processing agreement with the controller.

Record the role in the PRD; the obligations differ.

### DPIA (data protection impact assessment), Article 35

Required when processing is likely to result in a high risk to people's rights and freedoms. The EDPB
guidelines list criteria; meeting two or more usually means a DPIA is needed:

- evaluation or scoring, including profiling;
- automated decisions with legal or similarly significant effect;
- systematic monitoring;
- sensitive data or data of a highly personal nature;
- large scale;
- matching or combining datasets;
- vulnerable data subjects (children, employees, patients);
- innovative use of technology (AI features count);
- processing that prevents people from exercising a right or using a service.

National authorities publish lists of operations that always or never need a DPIA; check the one for the
country of the controller. Check whether the EDPB has published a harmonised DPIA template and use it if so.

### Data subject rights, Articles 12 to 23

The product must let people exercise these, by an in-product function or a documented manual procedure.
Answer without undue delay and within one month (extendable in complex cases):

- Article 15, access: a copy of their personal data.
- Article 16, rectification: correct inaccurate data.
- Article 17, erasure: delete their data (with legal exceptions). Deletion must reach every table,
  backup policy and third-party copy the product controls.
- Article 18, restriction: pause processing.
- Article 20, portability: a structured, machine-readable export (JSON or CSV).
- Article 21, objection: opt out, for example of direct marketing.
- Article 22, automated decisions: the right to a human review of a decision with significant effect.

Keep a log of every request: date, who, what was provided or deleted.

### Breach notification, Articles 33 and 34

- Notify the supervisory authority within 72 hours of becoming aware of a personal data breach (a
  processor notifies the controller without undue delay).
- Notify affected people when the breach is likely to result in a high risk to them.
- Keep an internal breach log, including breaches that did not need notification.

### Records of processing activities, Article 30

Organisations under 250 employees are exempt only when the processing is occasional, unlikely to risk
people's rights, and involves no sensitive or criminal-record data. Most products that run continuously
are not occasional, so keep the record: purposes, categories of people and data, recipients (including
outside the EU), retention periods, and the security measures in general terms.

## Data classification (phase 4b)

| Tier | Examples | Handling |
|---|---|---|
| Public | marketing copy, product catalogue, company name | no restriction; may ship to the browser |
| Internal | internal IDs, aggregated statistics, order counts | server side only; access-controlled |
| Personal data | names, emails, phone numbers, addresses, IP addresses | per-user or per-tenant access control, encryption at rest, audit log of access to it, data subject rights supported, retention period set |
| Sensitive personal data | health, financial data, government IDs, biometrics, political views, sexual orientation | everything above plus field-level encryption with keys outside the database, strictly limited access, a separate audit log, a DPIA, and usually explicit consent |

## EU AI Act (Regulation (EU) 2024/1689)

In force since 1 August 2024, applied in phases under its original timeline (check amendments, see the
note at the top):

| From | What applies |
|---|---|
| 2 February 2025 | prohibited practices (for example social scoring, manipulative techniques) and the AI literacy duty (Article 4) |
| 2 August 2025 | obligations for general-purpose AI model providers, governance and penalties |
| 2 August 2026 | most of the rest, including the transparency duties of Article 50 and the high-risk rules for the areas in Annex III |
| 2 August 2027 | high-risk rules for AI that is a safety component of products under the EU product legislation in Annex I |

### Classification of an AI feature

- **Prohibited**: the practices in Article 5. Stop and escalate.
- **High-risk**: Annex III areas such as biometric identification or categorisation, critical
  infrastructure, education and vocational training (admission, assessment), employment (recruiting,
  promotion, task allocation, termination), access to essential services (credit scoring, public
  benefits), law enforcement, migration and border control, justice and democratic processes. Requires
  a risk management system, data governance, technical documentation, logging, human oversight, and
  registration. Get a lawyer before building.
- **Limited-risk (transparency duties, Article 50)**: most chatbots and content generation. People must
  be told they are interacting with AI unless it is obvious to a reasonably well-informed person; a
  chatbot that looks like a support agent is not obvious. AI-generated or manipulated content (images,
  audio, video, deepfakes) must be marked, and published AI-generated text on matters of public interest
  labelled, with exceptions.
- **Minimal-risk**: no specific duties beyond AI literacy.

Most agency AI features (FAQ chatbots, drafting assistants, lead qualification, internal tools) are
limited-risk or minimal-risk, but classify each one explicitly in the PRD.

Penalties for breaching the transparency duties reach EUR 15 million or 3% of worldwide annual turnover,
whichever is higher.

**Disclosure pattern**, in the product's language, as the first message of every conversation:

```text
I am an AI assistant of <company>. If you would like to talk to a person, write "human" at any time or
contact <human contact>.
```

### General-purpose AI models

The obligations for general-purpose AI models (Articles 53 to 55) fall on whoever provides the model.
A product that calls a model through an API is not a model provider; it is the provider or deployer of
an AI system and is classified as above.

### AI literacy, Article 4

Providers and deployers must take measures so that their staff and others operating the AI system on
their behalf have sufficient AI literacy. When a client's staff will run the AI feature, record in the
PRD what training or guidance they get.

## EU Accessibility Act (Directive (EU) 2019/882)

Applies since 28 June 2025, through each member state's national law, to listed products and services
sold to consumers: e-commerce, banking, electronic communications, e-books, passenger transport services,
access to audiovisual media, and others. Microenterprises providing services (fewer than 10 employees and
annual turnover or balance sheet total of at most EUR 2 million) are exempt.

In scope means:
- conformity with EN 301 549 (version 3.2.1 is harmonised and references WCAG 2.1 level AA; targeting
  WCAG 2.2 AA covers it and is a sensible contractual target);
- an accessibility statement published with the service;
- regular accessibility review.

Penalties are set per member state.

## ePrivacy Directive (2002/58/EC): cookies and similar storage

- Strictly necessary storage (session, security, load balancing) needs no consent.
- Everything else needs prior opt-in consent: no pre-ticked boxes (Court of Justice of the EU,
  Planet49, 2019); "Reject all" as easy and prominent as "Accept all" (EDPB guidance); categories such
  as necessary, statistics, marketing, preferences; a record of who consented to what and when; a way to
  withdraw at any time (for example a "Cookie settings" link in the footer); the banner in the site's
  language.
- Google Consent Mode v2 when Google Analytics or Google Ads run on EEA traffic.
- Self-hostable open-source consent managers exist (Klaro is one example).
- Analytics that set no cookies and process no personal data (Plausible and Umami are examples) may not
  need consent at all; confirm per product, because national authorities differ in detail.

## Transfers of personal data outside the EU

Needed whenever a recipient sits outside the EEA (for example a US cloud, model API or email service).
One mechanism per recipient:

- **Adequacy decision**: the country (or, for the US, a company certified under the EU-US Data Privacy
  Framework, adequacy decision of July 2023) is treated like the EU. Check the Commission's current list
  and the recipient's certification.
- **Standard contractual clauses** (the 2021 version), usually part of the vendor's data processing
  agreement, plus a transfer impact assessment and supplementary measures where needed (after the
  Schrems II judgment of 2020).
- **Binding corporate rules**: for multinational groups; rare for small organisations.

Record in the PRD: every third-party processor that receives EU personal data, its mechanism, and any
supplementary measures.

## Compliance profile template (PRD section 16)

```markdown
## 16. Compliance profile

### GDPR
- Role: controller | processor | sub-processor
- Personal data processed: <categories>
- Sensitive data processed: yes/no, <categories>
- DPIA required: yes/no, <reasoning against the EDPB criteria>
- Lawful basis: consent | contract | legal obligation | vital interest | public task | legitimate interest
- Data subject rights: <how each is served: function or manual procedure>
- Breach procedure: <who notices, who decides, who notifies whom within 72 hours>
- Records of processing kept: yes/no, <where>
- Data protection officer: <name and contact, or "not required, because ...">

### EU AI Act
- AI feature: yes/no
- Classification: prohibited | high-risk (Annex III area) | limited-risk (Article 50) | minimal-risk
- Transparency measure: <disclosure text and where it appears, content marking>
- AI literacy: <training or guidance for the people who operate it>

### EU Accessibility Act
- In scope: yes/no, <reasoning: sector, consumers, company size>
- Target: <WCAG level and date>
- Accessibility statement: <where it will be published>

### Cookies and similar storage
- Storage used: <categories>
- Consent mechanism: <tool, or "none needed, because ...">

### Transfers outside the EU
| Recipient | What data | Country | Mechanism | Supplementary measures |
```

## When to get help

- A lawyer or the data protection officer for: any high-risk AI classification, sensitive data, large-scale
  processing, children's data, or processing in several countries. A one-time review of the privacy
  notice and the data processing agreement is cheap compared with a fine or a rebuild.
- EU-wide guidance: the EDPB website (guidelines on DPIAs, consent, transfers).
- AI Act questions: the European Commission's AI Office pages.
- National specifics: the data protection authority of the controller's country.
