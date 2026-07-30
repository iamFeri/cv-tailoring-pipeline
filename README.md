<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="CV Tailoring Pipeline: a self-hosted n8n pipeline that scrapes job postings, scores and tailors a CV with a schema-enforced AI call, then renders and delivers the PDF unattended.">
</p>

An n8n workflow that scrapes job postings, scores each one against your own
experience bank, tailors a CV for the ones worth applying to, compiles it to
a real PDF, and delivers it — daily, unattended, on free-tier infrastructure
only. This is the actual working build, not a simplified demo: every node,
every setting, every prompt.

## The pipeline, as connected pieces

<p align="center">
  <img src="./assets/readme/pipeline-map.svg" width="100%" alt="The pipeline shown as six connected Lego pieces: Load Bank/Template/Config, Search LinkedIn for Job Postings, Fetch Job and Skip Duplicates, AI Scoring and Content Selection, Build and Compile Tailored CV, Deliver Result — with a Gemini Retry/Backoff branch and a per-job loop back to Fetch.">
</p>

Six macro pieces above; nine real sections underneath (two are single-node
and shown here as the loop itself). Every piece is a real sub-workflow you
can inspect, edit, or swap out independently — that's the actual reason
it's shown as connected Lego pieces instead of one wall of nodes.

## Getting started

1. **Read `TEACHABLE_GUIDE.md` first.** Node-by-node setup for all nine
   sections — node types, exact settings, full prompt/schema text, every
   code block, pasted as-is from the live workflow, not narrated.
2. **Fill in your own data:**
   - `experience-bank/experience-bank.md` — replace the example entries with
     your own real, verifiable work history.
   - `templates/CV2_placeholders.tex` — replace the example header/education/
     experience with your own.
3. **Stand up the infrastructure:** self-hosted n8n, a self-hosted service
   that compiles LaTeX to PDF (`latex-api/` in this repo is a working
   example), an LLM API on a free tier, Google Drive/Sheets access, a
   Telegram bot.
4. **Import `workflows/embed-experience-bank.json`** into your own n8n
   instance and point every credential and file/sheet ID at your own —
   they're placeholders in this export, not live values.

## Why it's built this way

Two mechanisms replaced approaches that looked reasonable but didn't hold
up in practice:

- **One schema-enforced AI call, not a LangChain chain.** n8n's
  `Basic LLM Chain` / `Structured Output Parser` and `AI Agent` node were
  both tried for structured output and found unreliable; a direct HTTP call
  to `generateContent` with a native `responseSchema` replaced them and
  stuck.
- **Placeholder substitution into a fixed LaTeX template, not AI-authored
  LaTeX.** Letting the model write the whole document caused repeated
  structural corruption; the model now only fills `<<TOKEN>>` placeholders
  in a template it can't otherwise break.

Full bug-by-bug history behind individual prompt rules: `WORKFLOW_GUIDE.md`.

## Stack

- **Orchestration:** self-hosted n8n
- **Scoring + tailoring:** any LLM API on a free tier with schema-enforced
  JSON output (this build: Gemini)
- **PDF generation:** a self-hosted LaTeX-compiling service (this build:
  `latex-api/`, a small FastAPI service)
- **Delivery/archiving:** Google Sheets (tracking/dedup), Google Drive
  (archive), Telegram (real-time delivery)

## Contents

```
TEACHABLE_GUIDE.md                     ← start here: node-by-node setup
WORKFLOW_GUIDE.md                      ← the "why": decisions, bugs, history
workflows/embed-experience-bank.json   ← the workflow, credentials/IDs scrubbed
experience-bank/experience-bank.md     ← blank template, fill with your own data
templates/CV2_placeholders.tex         ← blank CV template, fill with your own
latex-api/                             ← working example PDF-compiler service
```

## Known open items

- Score threshold (`cv_tailor_threshold` in `CV Generation Config`) ships at
  a placeholder value, untested on your bank/template combination.
- JD-specific signal detection underperforms on some international-scope
  job descriptions.
- Bullet-selection can converge on the same generically-impressive bullets
  run after run — bank composition vs. model bias, still an open question.
- Applicant count is scraped but nothing filters on it yet.

## License

MIT — use, fork, adapt freely. If you build something on this, I'd like to
hear about it.
