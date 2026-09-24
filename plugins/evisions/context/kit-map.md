<kit_map>

# Kit map: which skill or agent for which job

Skills load as `evisions:<skill>` (the user can also type `/<skill>`); agents are dispatched with
`subagent_type: evisions:<agent>`. Route by what the user wants, not by a keyword alone; when nothing
below fits, work normally.

| The user wants | Use |
|---|---|
| progress saved mid-session ("checkpoint", "ulož postup") | skill `evisions:checkpoint` |
| the session closed ("končíme", "/end") | skill `evisions:end` |
| a decision recorded ("zapiš rozhodnutí") | a `docs/decision-log.md` row by default; skill `evisions:adr` only when the user's own words say it is hard to reverse or its reason is non-obvious |
| an ADR written or superseded ("zapiš ADR", "decision record") | skill `evisions:adr` |
| feature docs brought in line with the code ("zdokumentuj featury") | agent `evisions:features-documenter` (`/end` also runs it) |
| a prompt, system prompt, skill or agent file written or improved ("napiš prompt") | journals first if missing (hard rule), then agent `evisions:prompt-engineer`: pass the user's request as they said it, without choosing the prompt's language or file location yourself, and relay its `ARTIFACT`, `SANITY` and `QUALITY: UNEVALUATED` lines verbatim |
| an existing prompt checked ("zkontroluj prompt") | skill `evisions:prompt-eval` (static sanity check) |
| research: how others do X, options compared, a claim verified ("udělej research") | journals first if missing (hard rule), then skill `evisions:research`; it picks `evisions:research-analyst` (one angle) or `evisions:research-lead` (several) |
| their own idea or direction thought through ("potřebuju si to promyslet", "rozviň můj nápad") | skill `evisions:socratic-brainstormer`: questions that develop their thinking, not your answer |
| requirements for something to be built ("napiš PRD", "sepiš zadání") | skill `evisions:prd`: the WHAT and WHY; HOW to build it comes after (companion rows below) |
| code reviewed ("udělej review", "zreviewuj to") | agent `evisions:code-reviewer`; for code AND security also `evisions:security-auditor`, both in parallel. Pass the scope and the user's language |
| a security review ("je to bezpečné?", "security audit") | agent `evisions:security-auditor`, with the scope and the user's language |
| a bug or tech debt noticed along the way ("zapiš bug", "log tech debt") | a `BUG-TODO:` or `DEBT-TODO:` line per the documentation standard; to write the entry now, skill `evisions:bug-log` or `evisions:tech-debt-log` |
| a third-party skill or plugin checked before installing ("můžu to nainstalovat?") | skill `evisions:skill-scanner`: a static check that never installs or runs the target |

## Companion plugins (if installed)

Installed separately and optional. When one is needed but absent, say so in one line and continue
without it.

| The user wants | Use |
|---|---|
| a software build heading to implementation | `superpowers:brainstorming`, then `superpowers:writing-plans` (their own idea or direction stays with `evisions:socratic-brainstormer`) |
| a finished plan validated before execution | `/replan` (skill `replan:replan`) |
| an implementation checked against its plan | `/recheck` (skill `replan:recheck`) |
| a bug, failing test or unexpected behaviour, before any fix | `superpowers:systematic-debugging` |
| a plan or decision grilled by hard questions ("grill me", "rozgriluj to") | the grilling skill (`grill me`) |

</kit_map>
