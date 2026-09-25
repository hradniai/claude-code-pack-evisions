# User manual: the evisions kit for Claude Code

Claude Code (the app in which Claude reads and edits the files in your project directly) remembers nothing from earlier conversations. When a conversation gets long, its older part is also condensed automatically into a short summary, and details get lost. The `evisions` kit solves this simply: while you work, Claude writes the state of the work, its history and the decisions into plain text files inside the project. Tomorrow you, a colleague or a new conversation can pick up from them. The kit also adds a few helpers: research with sources, prompt writing with a check, a sparring partner for your own ideas, an interview that turns an idea for an app into written requirements, code and security reviews, a list of bugs and weak spots noticed along the way, and a check of a downloaded skill before you install it. On top of that, a safety check stops a few dangerous actions, such as deleting a whole folder at once, and keeps Claude from opening the files where passwords and access keys are kept. An optional settings step during installation adds the rest: which actions Claude may take without asking and which it must ask about first. On the company server containers, the server's own rules stay in charge.

You can talk to Claude in Czech, English or any other language. The files the kit writes into your project are always in English, so everyone on the team can read them.

## A normal day

1. **Start Claude Code in your project.** The kit loads by itself. At the start, Claude receives the rules for keeping records, the safety rules and a map of its helpers. There is nothing to switch on.
2. **Work as usual, in your own language.** Give tasks the way you always do. While working, Claude keeps `WORKSTATE.md` up to date with the current state and adds what was done to `worklog.md`.
3. **After a finished step, type `/checkpoint`.** You type a command into Claude Code like a normal message, slash included. Claude records what happened since the last save and refreshes the state. If the project uses git (a tool that keeps the history of file versions), Claude also saves a new version, called a commit. A commit stays on your machine and can be undone. Nothing is sent anywhere. It takes a few seconds.
4. **At the end of your work, type `/end`.** Claude first shows you the decisions it noticed during the session and asks which ones to record. It records only the ones you confirm. Then it updates the history and the state, writes down the bugs and weak spots noticed during the session (see [Bugs and tech debt](#bugs-and-tech-debt)), updates the description of the project's features and saves a version. Before it sends anything to a shared server (called a push, for example to GitHub), it asks you.

Coming back to a project after a week? Just write "Where did we leave off?" Claude reads `WORKSTATE.md` and continues from there.

## The files in your project

The kit creates files only when they are needed. They are all plain text in Markdown format (text with simple marks for headings and lists), so you can open them in any editor.

| File | What it is for | Should you read it? |
|---|---|---|
| `WORKSTATE.md` | The current state: what is being worked on, what works, what is stuck, what comes next and which decisions are waiting for confirmation. It is short, and Claude rewrites it. | Yes, when you return to a project after a break or take it over from someone. |
| `worklog.md` | The history of what was done when, and why, newest entry at the top. Nothing is ever deleted from it. | Only when you are looking for when and why something happened. |
| `docs/decision-log.md` | Smaller decisions, one per row. | When you wonder why something is the way it is. |
| `docs/decisions/` | ADRs: detailed records of big decisions, one file per decision. | When you revisit a decision or want to change it. |
| `docs/features/` | A description of the project's features, based on what the code actually does. Appears mostly in projects with code. | When you need to understand what the project can do. |
| `docs/prd/` | Requirements documents (PRDs) written with `/prd`. | Yes, before anything gets built. |
| `BUGS.md` | Bugs Claude noticed while working on something else. | When you plan what to fix next. |
| `TECH-DEBT.md` | Things that work but are fragile or costly to maintain. | When you plan clean-up work. |
| `research/` | Research results with sources. | Yes, they are for you. |
| `prompts/` | Prompts Claude wrote for you. | Yes. |

Passwords and access keys never go into these files. At most, Claude names the setting under which a key is stored, never the key itself.

## Recording a decision

Just say it. For example: "We decided to send monthly reports to clients as a PDF by the fifth day of the month." Claude records the decision. If it is not sure whether this is a settled decision or only an idea, it makes a note and asks you about it at `/end`. Only what you decide or confirm goes into the records, never Claude's own conclusions.

Decisions are recorded in two ways.

**A row in the decision log** (`docs/decision-log.md`) says in one sentence what applies and why. It suits everyday decisions that are easy to change, and it is the default. Examples: "We send the client report as a PDF by the 5th of each month, because the client prints it for their management." Or: "We build client reports in Looker Studio rather than Power BI, because clients know it and it is free."

**An ADR** (Architecture Decision Record) is a separate file in the `docs/decisions/` folder. It is for a decision where you consciously rejected other options and which is also hard to reverse, or whose reason is not obvious at first sight. An ADR captures the context, the decision itself, the rejected options and the consequences. Example: "All new automations are built in n8n instead of Make. We considered staying with Make or moving to Zapier. Self-hosting (running it on our own server) and the lower price decided it. Going back would mean rewriting dozens of scenarios." When someone suggests another tool six months later, the ADR shows why it went this way and what was considered back then.

Claude records reasons and rejected options only as you state them. If you give no reason, it writes "not stated" and does not make one up.

An ADR is created when you type `/adr` or say "record this as an ADR". An old ADR is never rewritten. When you change a decision, a new ADR is created and the old one gets a link to it. Not sure where a decision belongs? Don't worry about it. Claude suggests, you confirm.

## Research

Write, for example, "Research how agencies use AI for writing ad copy", or type `/research` followed by your question. The research is done by a research agent (another Claude that runs on its own, searches and reads sources on the web, and returns the result). A narrow question needs one. A broad question is split by a research lead (an agent that coordinates the others) into several angles. It sends an agent to each, at most five at once, and combines the results.

You get:

- a verdict right at the top, the answer to your question in one or two sentences,
- the findings, with a link to the source next to each claim,
- a file in the `research/` folder named after the topic and the date, so the team can still find it a month later.

Each finding carries a label that tells you how much to trust it:

- **MEASURED**: Claude tried it itself, or a source documents it for exactly your case. The strongest level.
- **COMMUNITY-PROVEN**: someone actually built or used it and publicly reported that it works. The claim says who, with a link.
- **MY JUDGMENT**: Claude's own conclusion from what it found. Treat it as an opinion, not a fact.

When the research finds nothing on a point, it says so and does not fill the gap. Open and read the links behind any claim you base a decision on.

## Prompts

A prompt is the instruction given to an AI model, for example the instructions for a chatbot on a client's website, or a template an automation uses to write product descriptions. Write "Write me a prompt for…" or "Improve this prompt", and the prompt engineer takes over (an agent that specializes in writing prompts). The more you tell it at the start (what the prompt is for, where its inputs come from and what the result should look like), the better. It cannot ask you questions while it works, so it fills gaps with reasonable assumptions and lists them for you to correct. It saves the prompt in the `prompts/` folder and checks it straight away. The prompt itself is written in English, but it can tell the model to answer in Czech or any other language. You can also run the check on any prompt yourself with `/prompt-eval`.

The result of the check looks like this:

```
SANITY: PASS
QUALITY: UNEVALUATED
```

**SANITY** is a check of the text alone. No model is run. It looks for things like a pasted key or password, no description of what the output should look like, no instruction for what to do when an input is missing or unusable, and places where inserted data is not separated from the instructions. PASS means it found none of these. WARN means something is missing. FAIL means a problem that must be fixed, typically a key in the text.

**QUALITY: UNEVALUATED** says honestly that nobody has tested whether the prompt does its job. A prompt that passes can still give bad results.

So before you put a prompt to use, try it on 3-5 real inputs, including one empty or incomplete one, and compare the results with what a good output should look like. Keep clients' personal data out of test inputs.

## Writing down what to build: a PRD

Before anyone builds an app, an automation or a tool, it helps to write down what it should do. That document is called a PRD (product requirements document). Type `/prd` or write, for example, "I need a PRD for a client portal where customers download their invoices." Claude interviews you in your own language, a few questions at a time, and waits for your answers before it moves on.

It asks about the business side first: what problem this solves, for whom, what success looks like and what is out of scope. Then it covers what a non-developer rarely thinks to ask: whether GDPR or the EU AI Act applies, what kind of data the thing will hold, how people will log in, how the data is backed up and restored after a failure, and what could go wrong security-wise (a threat model). The legal part is a first check, not legal advice; where it matters, Claude recommends asking a lawyer or your data protection officer.

The finished PRD is saved in English in the `docs/prd/` folder. It says WHAT and WHY. HOW to build it comes afterwards, as a separate step. Only your answers count as facts; anything Claude assumes is labelled as an assumption for you to confirm or correct.

## Code review and security review

When code has been written or changed, ask for a check: "Do a code review of the changes" or "Is this secure?" You can name the scope: the whole application, one feature, certain files, or only what changed since the last save.

- **Code review** looks for bugs, errors that are silently swallowed, needless complexity, changes beyond what was asked, and unclear naming.
- **Security review** looks for passwords and keys left in the code, weak login and access control, data that leaks out, and gaps against GDPR and the EU AI Act. For AI features it also checks the combination of private data, untrusted input and the ability to act on its own.

Ask for both at once ("review the code and its security") and Claude runs them side by side. Each reviewer double-checks every finding before it reports it and drops the ones that do not hold up. You get the findings ordered by severity, in your language, each with a plain explanation of why it matters. The reviewers only report; they never change your files. Fixing is a separate step you ask for.

## Bugs and tech debt

While working on one thing, Claude sometimes notices a problem somewhere else. Instead of wandering off to fix it, it writes a short note into `WORKSTATE.md` and keeps going. At `/end`, these notes are written out automatically, with no question to you (unlike decisions, which you confirm):

- **`BUGS.md`** collects bugs: something that is broken, gives a wrong result or crashes.
- **`TECH-DEBT.md`** collects tech debt: something that works but is fragile or costly to maintain, for example duplicated code, a temporary workaround or missing tests.

You can also say "log this bug" or "log this as tech debt" at any time. When something gets fixed, Claude marks the entry as fixed rather than deleting it.

## Thinking an idea through, or testing a plan?

Both helpers ask questions, each with a different goal. Sparring is part of the kit. Grilling is an optional add-on by another author (see Add-ons from other authors).

**Socratic sparring** (`/socratic-brainstormer`) is for a raw idea you want to think through. Claude does not hand you a finished solution. It asks so that the answer comes from you, develops what occurs to you and points out blind spots. You could start like this: "I'm thinking of offering online shops a monthly audit of their ad accounts. Help me think it through."

**Grilling** (`/grill-me`, only with the add-on) is for a finished plan or decision you want to test before you commit to it. Claude asks in rounds. It asks every question that can already be answered, suggests its own answer to each, and continues until nothing in the plan is left unclear. You could start like this: "/grill-me Here is the plan for launching a client's Q4 campaign: …"

There can be dozens of questions. If you prefer one question at a time, say so right at the start: "Ask me one question at a time." Start grilling in a new conversation, and do not just nod along. When you disagree or do not know the answer, say so. Otherwise the plan that comes out is Claude's, not yours.

A simple rule: when you do not yet know what you want, choose sparring. When you know and want to find out whether it holds up, choose grilling.

## Add-ons from other authors

Public add-ons fit the kit. They are not part of it. Each one is installed separately, straight from its author, and only if you want it. Claude offers them during installation and installs each one only after you say yes. Every add-on takes up some of Claude's attention in every conversation, so install only the ones you will actually use.

- **Superpowers** (for building software) is a disciplined way to build software or automations with Claude: first brainstorming, then a written spec, a plan and its execution, plus systematic bug hunting and writing tests. At the start of every conversation Claude loads its rules and then reaches for skills (packages of instructions for specific tasks) more readily. That is expected. It is how the add-on works.
- **Replan** (checking a plan and the result, by Jiří George Dolejš) adds two commands. `/replan` sends a finished plan to several reviewing agents at once, before any work starts. `/recheck` checks afterwards that the result matches the plan.
- **Grilling** (by Matt Pocock) is `/grill-me`, described above.

Use Socratic sparring while an idea is still taking shape and you want to develop it. Grilling comes in when you already have a plan or decision and want to pin it down and test it with questions. When the plan turns into building software or an automation, Superpowers helps. `/replan` and `/recheck` check the written plan before work starts, and then the finished result against it.

If you build software or web pages, five more add-ons from Anthropic's official catalogue can help. Skip them if you do not write code.

- **security-guidance** warns about risky patterns while code is being written.
- **context7** gives Claude the current documentation of the libraries your code uses, instead of what it remembers.
- **playwright** lets Claude open a real browser and check that a web page actually works.
- **code-review** reviews pull requests (proposed changes to shared code) with several agents.
- **frontend-design** helps build polished web pages and landing pages.

For hunting down a bug, Superpowers already has a step-by-step method, so no extra add-on is needed.

## Checking a downloaded skill before you install it

Skills and plugins from the internet are instructions and small programs written by someone you do not know, and Claude would follow them. Before you install one, let Claude check it. Download it first (on GitHub: the green "Code" button, then "Download ZIP"), then type `/skill-scanner` or write "Is this skill safe to install?" and give the path to the downloaded file or folder.

Claude never installs or runs the thing it checks. It reads the files and answers with one of three verdicts, with the reasons: **BLOCK** (do not install), **REVIEW** (something needs a closer look before you decide) or **PERMIT WITHIN COVERAGE** (nothing found in what the check could see). The last one is not a guarantee: the check only reads the files, so something hidden or triggered later can slip past it. Installing stays your own step, after the verdict. The check needs Python 3.9 or newer, like the safety check below.

## Safety: what the kit stops, and what to do

Claude runs commands (short instructions for the computer, the kind you would type into a terminal) and reads and changes files on your computer, with the same rights as you have. The kit adds a safety check that looks at each command and each file read before Claude goes ahead, and stops a few kinds of action that are dangerous or would expose a secret:

- opening files that hold passwords and access keys: `.env` files (settings files in a project that hold passwords and keys for other services) and the keys your computer uses to log in to servers, GitHub or cloud services,
- deleting a folder with everything in it in one go (deleting a single file or an empty folder is fine),
- downloading a program from the internet and running it straight away,
- moving a file onto another one so that the other one is silently overwritten,
- a handful of commands that can wipe a disk or freeze the computer.

**When something is stopped,** Claude stops too. It tells you what was stopped and why, and if you really want it done, it gives you the exact command to run yourself. The message starts with `evisions safety:`. Claude is told never to look for a way around the check, so asking it to try another way does not help. If you are sure, run the command yourself.

**Passwords and keys.** Claude does not show you the values from `.env` files, even when you ask. To find out which keys a project has, ask "Which keys are in .env?" Claude answers with the names only (it uses a small helper called `list-env-keys`) and can also tell you which keys are empty, which hold only sample text and which are filled in.

**The status line.** If you went through the optional settings step, the bottom of the Claude Code window shows three short lines: the model Claude is using, your project and git branch (a separate line of work in git), how full the conversation is, and how much of your 5-hour and 7-day usage limit you have used. When the conversation is nearly full, Claude will soon condense its older part, so that is a good moment for `/checkpoint`.

**Python.** The safety check runs on Python (a programming language that has to be installed on the computer), version 3.9 or newer. Without it the check cannot start, and the kit then stops Claude's commands and file reads rather than let them through unchecked. When Claude installs the kit, it checks for Python first.

**What the check does not do.** It is a seatbelt, not a lock: it protects against accidents by an overeager Claude, not against someone determined to break in. It looks at a command the way the computer reads it, so a commit message that merely mentions `.env` is not stopped. It catches commands that name a secret file, but some roads still lead to a key: a search through a whole folder, reading an older version of a `.env` file from git history, a Docker command that prints its whole configuration, or copying a `.env` file under another name and reading the copy. A project can also switch the check off in its own settings, so in a project someone else set up it may not be active. On the company server containers the server has its own safety rules as well, so you may see one command stopped twice, once by each. On Windows the check needs Git Bash and Python, and it covers PowerShell commands only on a best-effort basis, untested so far.

## Frequently asked questions

**A command does not show up in the menu.** Quit Claude Code and start it again. The kit loads at startup, so a restart is needed after installing or updating.

**`/checkpoint` runs my own skill, not the kit's.** A skill is a package of instructions Claude loads when you call it. If you have your own with the same name, type the full name of the kit's one, `/evisions:checkpoint`. With the `evisions:` prefix, every command of the kit always works.

**Where do files go on the server containers?** A container is your own separate environment on the company server. Files go where the server's rules allow. If they allow writing only into a certain folder, Claude saves the files there and tells you once where exactly. The server's rules always take precedence over the kit.

**The prompt check says NOT RUN.** Python 3.9 or newer, the program the check runs with, is missing on the machine. The safety check needs it too. Install it, or ask whoever manages your machine or container.

**Claude says `evisions safety:` and mentions Python.** The safety check needs Python 3.9 or newer and cannot find it, so it stops Claude's commands and file reads instead of letting them through unchecked. Install Python (from python.org; on a Mac, Apple's command line tools include it), or ask whoever manages your machine or container. Then restart Claude Code.

**Claude refused something I really want.** That is the safety check at work. Claude is told not to get around it, so rephrasing the request does not help. If you are sure, run the command Claude gave you yourself, in a terminal (the window where you type commands).

**Claude asks for permission more often than before.** On a laptop, the optional settings step makes Claude ask before installing software, sending changes to a shared server (a push) or deleting files. That is on purpose. Everyday harmless commands, such as listing files, still run without a question. These few questions come even when you start Claude Code with the `cc` shortcut (see [A shortcut for starting without questions](#a-shortcut-for-starting-without-questions)), because the kit asks for them in every mode.

**Will Claude send anything out without my knowing?** No. `/checkpoint` and `/end` save versions only on your machine. Nothing goes to a shared server until you answer "yes" to the question in `/end`.

**`/grill-me` does not show up in the menu.** Grilling is an add-on that is installed separately. Tell Claude "Install grilling following INSTRUCTIONS.md" and then restart Claude Code.

**Do I have to talk to Claude in English?** No. Use Czech or any other language. The files in your project are written in English so the whole team can read them.

## Installing and updating

The kit is a plugin (an add-on for Claude Code). Everyone installs it for themselves, on their own laptop or container. It takes a few minutes and needs no special access, because the repository (the kit's folder with its version history) is public on GitHub. The computer needs Python 3.9 or newer for the safety check; when Claude installs the kit, it checks this first and tells you how to get Python if it is missing. There are two ways.

**Let Claude do it.** Download the repository with `git clone https://github.com/hradniai/claude-code-pack-evisions`, open Claude Code in that folder and write "Install it following INSTRUCTIONS.md." Claude shows you each command before running it and waits for your yes. It also offers the optional settings step and the optional add-ons, and installs only what you pick.

**Or run two commands yourself** in a terminal (the window where you type commands):

```
claude plugin marketplace add https://github.com/hradniai/claude-code-pack-evisions
claude plugin install evisions@claude-code-pack-evisions
```

Then restart Claude Code.

**The settings step (optional).** Claude Code does not let a plugin change its settings, so the kit brings a small installer, `evisions-settings`. Run on its own, it only shows what it would change and changes nothing (a dry run). With `--apply` it makes the changes, keeps a backup of your previous settings, and can undo them later. When Claude installs the kit, it runs the dry run, explains the plan and applies it only after your yes. If you installed the kit yourself, ask Claude after the restart: "Run evisions-settings and tell me what it would change." If you agree, let it run `evisions-settings --apply`, then restart Claude Code once more so the settings take effect.

**Checking your settings.** Ask Claude "Run evisions-settings --check" at any time, or run it yourself. It goes through your whole settings file, not only the kit's part, and writes one line per problem it finds: `Error` means Claude Code ignores the file or a part of it, `Warning` means something that does not work as written, or that an editor such as VS Code marks as an error. At the end it says whether the kit's settings are in place. The installation ends with this check.

To update, run these two commands:

```
claude plugin marketplace update claude-code-pack-evisions
claude plugin update evisions@claude-code-pack-evisions
```

Then restart Claude Code, ask Claude to run `evisions-settings --apply` so your settings follow the new version, and restart once more. If you skipped the settings step, you can skip this too, or do it now.

## Keeping Claude Code up to date

At the start of every conversation the kit checks whether a newer Claude Code has been published than the one you are running. If so, Claude tells you right at the start, a warning line appears on screen, and you get the command to update. To look this up, the kit asks the public npm registry (the place Claude Code is published from) at most once every six hours, and the question carries nothing about your work. Without an internet connection it simply says nothing.

To update, run the command that matches how Claude Code was installed, in a terminal, then quit Claude Code and start it again. If you do not know how it was installed, `claude doctor` tells you on its `Running:` line.

- The standard installer (most people): `claude update`
- Installed with npm: `npm install -g @anthropic-ai/claude-code@latest`
- Installed with Homebrew on a Mac: `brew upgrade claude-code` (or `brew upgrade claude-code@latest`, if that is the one you installed)
- Installed with WinGet on Windows: `winget upgrade Anthropic.ClaudeCode`

On the company server containers you do not update anything yourself: automatic updates are turned off there, and the administrator updates Claude Code for everyone. If Claude tells you there about a newer version, let the administrator know.

## A shortcut for starting without questions

Claude Code can be started so that it does not stop to ask before each action: `claude --dangerously-skip-permissions`, called bypass mode. The kit's settings do not turn it off. The kit's safety check keeps working in bypass mode, and with the kit's settings step done, the commands those settings refuse stay refused and Claude still asks before installing software, sending changes to a shared server (a push) or deleting files. Everything else runs without a question, so use it only when you are happy for Claude to work on its own.

If you use it often, a short command helps: `cc`. You set it up once, yourself, in a terminal (the window where you type commands). Claude does not do this step for you: the files that set up your terminal are yours, and with the kit's settings Claude is not even allowed to change them. Run the one command for your computer:

- Mac:

  ```
  echo "alias cc='claude --dangerously-skip-permissions'" >> ~/.zshrc
  ```

  Then run `source ~/.zshrc`, or open a new terminal window.
- Linux, or Git Bash on Windows:

  ```
  echo "alias cc='claude --dangerously-skip-permissions'" >> ~/.bashrc
  ```

  Then run `source ~/.bashrc`, or open a new terminal window.
- Windows PowerShell:

  ```
  if (!(Test-Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force | Out-Null }; Add-Content -Path $PROFILE -Value 'function cc { claude --dangerously-skip-permissions @args }'
  ```

  Then run `. $PROFILE`, or open a new PowerShell window. If PowerShell says that running scripts is disabled, ask whoever manages your computer.

To test it, type `cc --version`: it must print the Claude Code version. From then on `cc` starts Claude Code in bypass mode, and plain `claude` starts it with the questions as before. One side effect: computers have a program for building software that is also called `cc`, and your shortcut now hides it in the terminal. If you ever need that program, type `command cc`. On the company server containers `cc` is already set up, so skip this there.

## Using it with Codex

If you work in OpenAI's Codex CLI (the command line app that comes with a ChatGPT subscription), the kit's safety check works there too. Codex gets only the safety part: `/checkpoint`, `/end` and the other helpers described above are for Claude Code only for now.

To install it, download the repository as described above, open Codex in that folder and write "Install it following CODEX.md." Codex shows you each command before running it and waits for your yes. One step only you can do: after the install, quit Codex, start it again, and on the screen "Hooks need review" choose "Trust all and continue". Hooks are the small programs the kit runs before every command. Until you trust them, Codex does not run them, and the safety check is off. After an update Codex may show the screen again; trust them again.

In Codex the check stops the same kinds of action as described in the safety section above, and also git commands that throw away work or overwrite shared history, and attempts to switch the check off. It keeps working when Codex is started in its mode that does everything without asking (`--yolo`). The message again starts with `evisions safety:`. If Codex tells you at the start of a conversation that the evisions safety hooks are not active, the trust step is missing: type `/hooks` in Codex and trust them. The details, including what the check does not cover, are in [`CODEX.md`](CODEX.md).
