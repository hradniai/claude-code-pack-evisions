---
name: research
description: Research into how practitioners actually do something, or into whether a claim is true, delivered with sources the user can click. Owns the tier choice (`evisions:research-analyst` for one angle, `evisions:research-lead` for several), the honesty standard every finding is reported under, source verification, and where the research file is saved. Use when the user wants something outside the current project researched, investigated, compared or fact-checked - "udělej research na X", "prozkoumej", "zjisti, jak to dělají ostatní", "co lidi používají na X", "je pravda, že...", "porovnej A a B", "research this". NOT for finding things in the project's own files (search them directly or use the Explore agent), and NOT for a question the conversation or the code at hand already answers.
---

<hard_rules>
1. **Report what the research SHOWS, never a deduction dressed as a finding**, because the user asked what exists and what there is evidence for, and a confident inference in its place destroys the point of asking.
2. **Never invent, guess or reconstruct a URL**, because a plausible link gets clicked, and one wrong link discredits every right one.
3. **Research never runs in the main context**, because raw search results and fetched pages would flood the conversation the user is working in. Dispatch an agent.
4. **Every research task writes a file, and the chat answer is still complete on its own**, because the file outlives the conversation while the chat message is what the user actually reads.
</hard_rules>

# Research

## Rule 0: report what the research shows

When the user asks what people do, what exists, or what the evidence says, they are asking what practitioners ACTUALLY built and reported: case studies, repositories, benchmarks, someone who shipped it and said it worked. Label every research-derived claim by tier, and never let a lower tier wear a higher tier's clothes:

1. **MEASURED** - you ran it, or it is documented for this exact case.
2. **COMMUNITY-PROVEN** - someone built it and reported it working. Name who, with the URL of a page an agent actually fetched.
3. **MY JUDGMENT** - an inference, marked explicitly as a call, not as research.

- **If the research does not contain the recommendation, say so plainly**: "nobody in the sources built exactly this, this is my inference". Never fill the gap with a confident deduction. If the user's premise turns out to be unworkable, say that instead of silently engineering around it.
- **A subagent's classification is MY JUDGMENT until checked.** Agents return classifications alongside facts: "X is the closest alternative to Y", "X is the market leader", "X is the obvious choice". Before repeating a claim that PLACES a product, tool or company relative to another, check against primary sources that the two actually do the same job, because what an agent gathers is more reliable than the category it files it under.

## Pick the tier

| Tier | Agent (`subagent_type`) | Use when |
|---|---|---|
| 1 | `evisions:research-analyst` | One angle: verify a claim, compare A with B, check whether something exists, understand a concept, get a quick sourced verdict. |
| 2 | `evisions:research-lead` | Several angles that each need their own brief plus a synthesis: "how do teams actually solve X", "what does the landscape of Y look like". It briefs one analyst per angle, runs at most five in parallel, and returns one report. |

Tiers combine freely: a tier-2 run can be followed by a tier-1 check on one loose end.

**The fan-out is capped.** `evisions:research-lead` runs at most five `evisions:research-analyst` workers, and the analyst has no Agent tool, so the workers cannot spawn anything further. Never hand research to any other agent that can dispatch agents without stating a cap in its prompt, because an uncapped fan-out multiplies cost with nothing to stop it.

## Before a tier-2 run: show the angles, then launch

Draft the angles yourself (two to five; two well-briefed angles beat five vague ones) and show them in one short table, headed in the user's language, then dispatch in the SAME turn without waiting for a reply. The table lets the user catch a wrong framing early; the fixed cap (one lead, at most five workers) keeps the cost bounded, so no approval step is needed. If the user corrects the framing, the next run takes the correction.

| Angle | Model |
|---|---|
| What the official documentation of X says | Sonnet (analyst) |
| What practitioners report after using X | Sonnet (analyst) |
| Synthesis of the angles | Opus (lead) |

## What every dispatch prompt carries

The agent sees nothing of this conversation, so anything left out is simply absent:

- the question in full, with the user's steering and any constraints woven in;
- for tier 2, the angles you showed;
- today's date, taken from `date '+%Y-%m-%d'` rather than estimated, and the absolute path of the research file (see the next section);
- a reminder that the return and the research file are both in English.

Dispatch with the Agent tool. Running it in the background is fine when there is other work to progress; either way, collect the result before you answer, and never answer ahead of it.

## Where the research file goes

- **Name:** `{topic}-research-{YYYY-MM-DD}.md`, with `{topic}` a short lowercase slug with hyphens and no diacritics, because diacritics in file names break between macOS, Linux and Windows.
- **Place:** the `research/` folder at the project root as the documentation standard defines it (the git top-level, otherwise the directory the session started in); the agent creates it if missing. When that would be the home directory or a filesystem root, ask the user which folder is the project first, because research files scattered in the home directory are never found again.
- **Environment rules win.** If an instruction in the user's environment restricts where new files may be written, that restriction wins: put the `research/` folder inside the allowed location and tell the user once where the file went.
- **Language:** always English, whatever language the user speaks, so the research archive stays consistent and searchable for everyone who reuses it; verbatim quotes keep their original language. Only the chat report below is in the user's language: relay the agent's English verdict in it.
- **Frontmatter**, minimal and fixed:

```yaml
---
title: "<topic in words>"
summary: "<one or two sentences: what the research concluded>"
status: ai-generated
date: YYYY-MM-DD
sources:
  - <URL>
---
```

`status: ai-generated` says nobody has validated the findings yet; only a person who has checked them changes it.

## Verify a source before it enters anything durable

A URL an agent returned is a CLAIM until you have checked the page's content yourself. The research file is the archive of what the agents reported, which is exactly why it carries `status: ai-generated`. In chat you may relay an agent's sources as agent-reported. Before a finding moves into anything else durable - a document for a client or a colleague, a decision record, code, a knowledge base, a presentation - fetch the load-bearing source yourself and match the claim against the text on the page. A targeted check of a few URLs is verification, not research, so it may run in the main context.

- **An HTTP 200 is not verification.** Sites redirect a mistyped or invented address onto an unrelated page that still answers 200.
- **An agent's "I fetched it and confirmed it" is not verification either.** Agents produce coherent quotes and confirmation language even for pages they never read.
- A source that fails the check stays out of the durable artifact, and the user is told which claim lost its support.

## The chat report

The chat message is, in almost every case, the only version of the research the user reads. Write it in the user's language:

- **Verdict first**, then the claims that carry it, one paragraph each at most, each standing on its own without the sources.
- **Every claim labelled with its tier**, the label translated into the user's language and the three tiers kept distinct.
- **The clickable link beside the claim it supports**, not only in a list at the end, because a link that reaches only the file has not been delivered. Where the claim is "someone built this and it worked", give two links wherever both exist: the write-up (post, thread, issue, talk) and the artifact (repository, release, package).
- **A claim no agent could source stays in the report, labelled "no verifiable source"** (in the user's language). That weakens it, which is exactly the signal the reader needs. A page seen only in search results is not a source: a claim an agent backs with a link marked as found via search but not fetched gets the same label, not the link. A claim resting only on search results is `NO VERIFIABLE SOURCE`, never COMMUNITY-PROVEN or MEASURED; naming the site instead of linking it does not change that. Do not hunt for the missing source on your own initiative; whether it is worth chasing is the user's call.
- **Every abbreviation explained on first use**, in a short clause, because the reader may not be a developer: "API (the interface through which one program talks to another)".
- **Close with the path of the research file.**

<hard_rules_repeat>
Report what the research shows, tier-labelled, and say so plainly where it holds no recommendation. Never invent or reconstruct a URL; an unsourced claim is reported as such. Research runs in an agent, never in the main context, and always leaves a file in `research/` (or inside whatever location the environment allows), while the chat answer stays complete on its own with the links beside the claims. A source's content is checked by you before a finding enters anything durable beyond the research file.
</hard_rules_repeat>
