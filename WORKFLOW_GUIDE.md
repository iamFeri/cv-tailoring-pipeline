# Workflow Guide

A complete, plain-language walkthrough of this pipeline — what it does, how each
section works, and exactly what a new user needs to stand it up themselves. Companion
to [`TEACHABLE_GUIDE.md`](./TEACHABLE_GUIDE.md) (the node-by-node reference): this file
is the "why," that one is the "what do I set up."

Ground truth for everything here: [`README.md`](./README.md),
[`experience-bank/experience-bank.md`](./experience-bank/experience-bank.md),
[`templates/CV2_placeholders.tex`](./templates/CV2_placeholders.tex),
[`workflows/embed-experience-bank.json`](./workflows/embed-experience-bank.json) (v6,
current), and [`latex-api/`](./latex-api/). Where this guide notes a "doc vs. code
discrepancy," that means the written docs and the live workflow file disagree — flagged
explicitly rather than silently resolved one way, since it's genuinely interesting
material for a technical audience.

---

## 1. The pipeline, end to end

Daily, unattended: scrape LinkedIn job postings → score each against a personal experience
bank and tailor a CV in one AI call → render the result to PDF → deliver over Telegram,
archive to Drive, log to Sheets. Runs entirely on free-tier infrastructure: self-hosted n8n,
a self-hosted LaTeX-compiler microservice, Gemini's free tier. No paid APIs, no paid scraping.

**Why it's built this way — two decisions that replaced approaches that looked reasonable
but didn't hold up:**

- **One schema-enforced AI call, not a LangChain chain.** n8n's `Basic LLM Chain` +
  `Structured Output Parser` and the `AI Agent` node were both tried for structured output
  and found unreliable in production (a live n8n bug — #21174 — breaks the parser on
  backticks in string values; the chain also failed after 3 clean runs with "Model output
  doesn't fit required format"). Replaced with a direct HTTP call to Gemini's
  `generateContent`, using a native `responseSchema` — the schema is enforced *during*
  generation, not parsed after the fact.
- **Placeholder substitution into a fixed LaTeX template, not AI-authored LaTeX.** Letting
  the model write the whole `.tex` document caused repeated structural corruption. The
  model now only fills `<<TOKEN>>` placeholders in a template it structurally can't break.

**Architecture history, in one line each:**

| Version | Nodes | Headline change |
|---|---|---|
| v1 | 40 | Baseline — manual trigger, separate embedding pipeline, monolithic AI-authored LaTeX |
| v2 | 43 | Scheduled trigger, first placeholder-substitution scaffolding |
| v3 | 43 | LangChain chain experiment (abandoned — unreliable structured output) |
| v4 | 42 | Direct Gemini `generateContent` + native schema adopted — the pattern that stuck |
| v5 | 42 | Embedding/cosine-similarity pipeline removed entirely; scoring folded into the tailoring call |
| v6 | 39 | Cleanup pass — node merges, one dead-end bug fix, hardcoded personal content made configurable, canvas reworked for teaching (current) |

The embedding pipeline (v1–v4) is worth a beat on its own: it cached a 174-entry ×
3072-dimension vector file (~6.5MB) on Drive and cost two Gemini calls per job (embed the
JD, then conditionally tailor). It was cut in v5 because the cache was stressing the
self-hosted instance's disk/memory, and because cosine similarity between professional text
rarely dropped below ~0.7 regardless of actual relevance — it needed a hand-tuned
relative-gap formula just to be usable as a gate. The replacement — one AI call that scores
*and* tailors — accepts a real tradeoff: every scraped job now pays for a full generation
effort, even obviously bad fits. In exchange: 1 Gemini call per job instead of up to 2, and
no cached artifact that can go stale or bloat.

---

## 2. What you need to build this yourself

**Infrastructure**

1. A Linux VPS with enough free disk for the LaTeX toolchain (~1–1.5GB) plus n8n itself —
   the project explicitly designed around a ~4GB-free-disk constraint.
2. Docker + Docker Compose.
3. Self-hosted n8n (Docker Compose), running in **filesystem binary-data mode** — this
   isn't incidental; the binary-handling pattern below only applies in this mode.
4. The `latex-api` microservice (this repo's `latex-api/` folder) deployed in the same
   compose stack, sharing a Docker network with n8n so the workflow can reach it at
   `http://latex-api:8000` by Docker-internal DNS. It is never exposed to the public
   internet — no `ports:` mapping.
5. A reverse proxy (e.g. Caddy) in front of n8n for HTTPS — standard n8n self-hosting
   setup, not specific to this workflow; not included in this repo. See
   [`latex-api/SETUP.md`](./latex-api/SETUP.md) for a from-zero VPS/Docker quickstart.

**Accounts / API keys** (all referenced in the workflow by internal credential name —
never inlined as raw secrets)

6. **Gemini API key** (Google AI Studio, free tier) — used via a generic `httpHeaderAuth`
   credential, calling `gemini-2.5-flash:generateContent`.
7. **Google account with Drive access** — stores/serves the experience bank file and CV
   template, and receives every generated CV PDF.
8. **Google account with Sheets access** — two spreadsheets: one holding target job titles
   + search filters, one acting as the dedup/tracker log that successful runs append to.
9. **A Telegram bot** (via BotFather) — delivers each tailored CV as a document, or an
   error message with the compile log on failure.
10. **No LinkedIn account needed.** The scraper is deliberately cookie-free (LinkedIn's
    `jobs-guest` endpoint) — a design choice made *after* two past LinkedIn session-cookie
    exposure incidents. The alternative (authenticated full-page search) would need either
    a paid scraping API or a live session cookie back in the workflow, which this project
    moved away from on purpose.

**Content you must author yourself**

11. **Your own experience bank** — a markdown file structurally matching
    `experience-bank/experience-bank.md` exactly (full format spec in §4 below). This is
    the "`BankExperience.md`" file — nothing gets tailored without it, and the parser
    throws (not silently fails) if the format is wrong.
12. **Your own CV LaTeX template** — same `<<TOKEN>>` / `<<TOKEN:MAXLEN>>` placeholder
    contract, with `\jobentry{Title}{Company}{Dates}` calls for however many roles you
    want tailored (format spec in §5).
13. Edit the `CV Generation Config` node: `cv_tailor_threshold` (score gate — the
    shipped value of 30 is an untested placeholder even for the original build,
    calibrated for a different scoring mechanism entirely; don't assume it means
    anything for a different bank/domain) and `cv_owner_name` (used in the generated
    PDF's filename).
14. Point every Drive/Sheets node at your own file IDs and spreadsheet IDs — these are
    hardcoded per-user setup, not hidden logic.

---

## 3. Job search & scraping

*Workflow sections: "Search LinkedIn for Job Postings," "Fetch Job & Skip Duplicates."*

The scraper hits LinkedIn's undocumented `jobs-guest/seeMoreJobPostings` endpoint —
cookie-free, no login. It combines every target job title from a Google Sheet into one
Boolean-OR quoted keyword query, layers on filters (location, experience level, remote,
job type, easy-apply) read from the same sheet, and paginates: `PAGES_PER_RUN = 3` ×
`RESULTS_PER_PAGE = 10` (`start=0,10,20`) — roughly 30 postings per run. Two things worth
calling out as real lessons, not just implementation detail:

- **The scraper uses a real Chrome User-Agent, not a spoofed Googlebot UA.** Sites commonly
  verify a claimed Googlebot identity via reverse-DNS against Google's own IP ranges; a
  self-hosted VPS IP fails that check and invites *more* scrutiny than an honest browser UA
  would.
- **Boolean title keywords match against the job description text, not just the title
  field** — a documented, accepted tradeoff of the endpoint, not a bug.

After scraping, each job link is deduplicated within the run (`Dedup Job Links` — guards
against the same posting appearing across multiple fetched pages), then checked against
the tracker Sheet (`Not Already In Sheet?`) — jobs already processed on a previous run skip
straight back to the loop without touching the AI at all. Applicant count is scraped and
parsed (`Parse Applicant Count`, handling both plain counts and "Over 100 applicants" text,
including French locale strings) but **nothing filters on it yet** — that's a real, still-open
gap.

A 15–25 second randomized delay runs before every individual job-page fetch, specifically
to avoid a bot-like fixed-interval fetch pattern.

**Known open items here:** no pagination past 3 pages/run; location is free-text rather
than LinkedIn's numeric `geoId`; whether the live scraper still returns genuine duplicates
on a real run is unconfirmed (an earlier "10 identical jobs" finding turned out to be a
testing artifact — a pinned node output re-read 10 times, not 10 fresh executions); the
Indeed scraper hasn't been started.

---

## 4. Scoring & tailoring

*Workflow section: "AI Scoring & Content Selection."*

One Gemini call does both jobs. `Format Bank For Prompt` builds the full prompt — job
title/description, which role slots need filling (derived from the CV template itself, not
hardcoded), the entire bullet and skill bank text, and 11 numbered rules the model must
follow — plus a native `responseSchema` (`match_score`, `jd_specific_signals`, `tagline`,
`profile`, `bullet_assignments`, `skill_candidates`). `Call Gemini For Selection` posts
that to `gemini-2.5-flash:generateContent`. `Parse Gemini Selection Output` is the single
place the raw response gets parsed — validates the finish reason, `JSON.parse`s it,
confirms every required field is present — so nothing downstream ever touches a raw
response again. `Worth Tailoring?` gates on `match_score >= cv_tailor_threshold`; jobs below
the bar are skipped silently, no notification sent.

**Every one of the 11 prompt rules exists because a real bug was found in production
output**, not written speculatively:

- Told to keep skills short, the model mangled words ("Mgmt", "Dev.") — fixed by
  forbidding abbreviation outright and stating explicitly that fitting content to length is
  the *packing* code's job, not the model's.
- A single French job description flipped all 16 selected bullets to French while the rest
  of the CV stayed English — fixed with an explicit English-always rule. Later found to
  apply to bank-sourced text too: one French skill phrase (`Vente B2B SaaS (PME/ETI)`) reached
  a live CV verbatim before the rule was widened to cover bank text, not just freshly
  composed text.
- The model fabricated a skill — `Customer Centricity` — confirmed absent from all 145 bank
  entries and not a rewording of anything real. It slipped past the original fabrication
  guard because that guard was scoped narrowly to tool/brand names, not general invented
  concepts.
- A subtler bug: a rule that gets a JD-specific match *included* in the candidate list
  doesn't guarantee it's actually *used* — a correctly-matched skill buried at priority #9
  of 18 gets dropped by the length-budget packer exactly like an unselected one would.
  Fixed by pairing every inclusion rule with an explicit ranking requirement.
- Tagline embellishment: the model added seniority modifiers not present in the job's own
  title ("Business Developer" → "Business Developer Manager"). Fixed with an explicit rule.
- Profile-paragraph fact leakage: the summary would restate a selected bullet's specific
  numbers. Fixed — general qualitative claims are fine, restating figures isn't.

**A 10-job real-output audit (2026-07-08)** found bullet fidelity clean (zero fabricated
facts/numbers across 160 selected bullets, checked programmatically), but also found:
JD-specific signal detection worked in only 1 of 3 comparable international-scope cases; and
bullet-selection convergence — the same ~9 generically-impressive bullets got picked in
9–10 of 10 runs regardless of job content, while 5 real, differentiated bullets (recruiting,
a 45%-profitability figure, forecasting support, channel-partner expansion, the n8n/automation
work itself) were **never selected once**, including a case where one of them was plausibly
the most relevant bullet in the bank. That audit predates `match_score`; the prompt fixes
above haven't been re-validated against a fresh batch yet. Both findings are still open.

---

## 5. Building & compiling the CV

*Workflow section: "Build & Compile Tailored CV."*

`Map AI Output And Pack Skills` takes the parsed AI output and bin-packs the selected
skills into the template's three skills-line slots **in the AI's own priority order, never
by string length** — an earlier length-based (first-fit-decreasing) packer silently dropped
the model's #1 priority pick in favor of a shorter #6 pick. Character-budget math had its
own bug: `\;` is a LaTeX spacing command with roughly 3 characters of visual width, but was
originally counted as its 7-character literal length, under-filling lines. The three
skills-line budgets themselves were originally guessed from leftover placeholder text
(97/78/113 characters) and later corrected to a single measured value: **117**.

`Parse And Fill Template` substitutes every `<<TOKEN>>` into the template text, LaTeX-escapes
free-text fields (but not the skills-line strings, which already contain deliberate `\;`
separators baked in earlier), verifies no placeholder is missing or left unfilled, confirms
`\documentclass`/`\end{document}` are both present, and base64-encodes the result for upload.
`latex-api /convert` — a small FastAPI service this project built specifically to avoid any
paid LaTeX-as-a-service API — compiles it with `xelatex` (required because the template uses
`fontspec`/`\setmainfont`, which `pdflatex` can't process), twice per document (a second pass
resolves anything needing a second reference, cheap insurance for a one-page CV), inside a
`~4GB`-disk-friendly Ubuntu base with only the targeted TeX Live packages installed — not the
4–7GB `texlive:latest` image. `Check Compile Result` reads the response: success gets a
sanitized dynamic filename and moves on; failure gets the structured error and compiler log
extracted for the Telegram error path.

**Role-to-bullet matching is deliberately tolerant**, not built on exact string equality —
the AI reads company-name/date proximity from plain text to match bullets to role slots,
so bank and template can be edited independently without a hard-coupling break.

---

## 6. Delivery, tracking & results

*Workflow section: "Deliver Result," plus the run-summary/diagnostic layer.*

On success: `Send Tailored CV` delivers the PDF over Telegram with a caption (title,
company, location, match score, applicant count, apply URL, date) → `Upload CV To Drive`
archives it → `Send Info To Sheet` appends a tracker row (status, position, company,
matching link, score, date, Drive view link, and the full filled `.tex` source for audit).
**The Sheet, not Telegram, is treated as the durable record** — Telegram is real-time
notification, the Sheet is what a person actually reviews and acts on later. On compile
failure, `Send Error` sends a plain-text Telegram message with the same job context plus
the compiler log, so a broken run is diagnosable without SSHing into the VPS.

A separate, deliberately-decoupled reliability layer handles Gemini flakiness: if
`Call Gemini For Selection` errors out, `Check Retry Limit` → `Give Up?` → a 35-second
`Wait` loops back for another attempt, capped at 3 manual retries (worst case ~3 minutes
before a single job gives up, not an unbounded hang) — before v6, a job that exhausted this
budget had no outgoing connection from its failure-logging node and could hang the entire
scheduled run; that dead end is the one concrete bug v6 fixed.

A lightweight diagnostic collector (`Collect For Review` / `output collector`) pushes one
record per processed job into n8n's workflow static data during the run, then dumps and
clears the full array once the loop finishes — used for manual spot-checking, not part of
the delivery path itself. The clearing step matters: static data persists *across* scheduled
runs, not just within one, so an earlier version of this collector would have grown
unboundedly forever — "the same failure class as the old embeddings-cache bloat this
project already hit once."

---

## 7. Known open issues (current, cross-checked against the live workflow)

1. `cv_tailor_threshold` (currently `30`) — untested placeholder on the current AI
   self-assessed 0–100 scale; the old value was calibrated for a completely different
   (cosine-similarity-gap) formula that no longer exists. Recalibrate against your own
   bank/JD volume once you have real runs to look at.
2. JD-specific signal detection underperforms on international-scope language — fixed once,
   not yet re-validated.
3. Bullet-selection convergence — the same handful of bullets can get picked almost every
   run, with some bank entries never selected. Open question: bank composition or model
   bias — worth auditing against your own bank once you have several real runs.
4. Applicant count is captured but nothing filters on it yet — a natural extension if you
   want to deprioritize highly-competitive postings.
5. Multi-language CV output (e.g. bilingual) is not supported — the prompt forces English
   output regardless of the job posting's language (see rule 1 in
   [`TEACHABLE_GUIDE.md`](./TEACHABLE_GUIDE.md#5-ai-scoring--content-selection)).
