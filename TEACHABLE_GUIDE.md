# Teachable Build Guide

Node-by-node reference for the workflow you import from
[`workflows/embed-experience-bank.json`](./workflows/embed-experience-bank.json) — you
don't need to rebuild anything by hand; import the JSON first (see the main
[`README.md`](./README.md#getting-started)), then use this file to understand what each
node does, what every credential/ID placeholder needs to point to, and where the one
customizable block in the tailoring prompt lives. Extracted directly from the live
workflow (`Embed-Experience-Bank V6`, n8n workflow ID `UetyOQDHNiTUKoCE`, 2026-07-30) via
n8n-mcp — not summarized from memory or from `WORKFLOW_GUIDE.md`. Every parameter value,
expression, and code block below is copied from the actual node configuration, so it also
works as a from-scratch reference if you'd rather type nodes in by hand than import.

This file answers "what do I set up and how." For *why* a decision was made or what bug
a rule is patching, see [`WORKFLOW_GUIDE.md`](./WORKFLOW_GUIDE.md) instead — that's kept
as a separate, narrative companion document, not duplicated here. For a full picture of
the finished canvas before diving into per-node detail, see the screenshot in
[`README.md`](./README.md#full-workflow-canvas).

Sections below match the workflow canvas's own sticky-note groupings exactly (9 real
sections). Two are single-node sections (Per-Job Loop Controller, Run Summary) — kept
as their own sections here for fidelity to the canvas, but they're natural candidates
to fold into a neighboring section when this becomes a teaching post, not a real
architectural boundary.

Credential fields below use this build's own internal credential display names (e.g.
"Google Drive account 9900") — create your own credential of the matching type and
point the node at it; the display name itself doesn't matter. Google Sheet/Drive file
IDs and the Telegram chat ID are this build's own — replace with your own file/sheet
IDs and chat ID.

---

## 1. Load Bank, Template & Config

Runs once at the start of every scheduled execution. Pulls in the three things every
later section depends on: the raw experience-bank text, the raw CV template text, and
a small set of config values — before any job search happens.

### Schedule Trigger (`n8n-nodes-base.scheduleTrigger`)

Kicks off the whole workflow once a day.

Setup:
- Trigger rule: Interval, `triggerAtHour: 10` (runs once daily at 10:00, instance
  timezone).

### Download Experience Bank (`n8n-nodes-base.googleDrive`)

Downloads the experience-bank markdown file as binary data.

Setup:
- Operation: Download.
- File: point at your `experience-bank.md` file in Drive (file picker, or paste the
  file ID directly).
- Credential: a Google Drive OAuth2 credential.

### Collect Bank Entries (`n8n-nodes-base.code`)

Parses the downloaded experience-bank markdown into a flat list of bullet/skill
entries. Mode: **Run Once for All Items**. Place immediately after the bank file
download.

Required source-file format this parser expects:
```
## Skills Pool
Skill one | Skill two | Skill three

## Role Name (dates)
- [tag1, tag2] Bullet text.
- [tag3] Another bullet.
```

Code (paste as-is):
```js
// n8n Code node — "Collect Bank Entries"
// Mode: Run Once for All Items
// Place IMMEDIATELY after the Google Drive download of experience-bank.md.

const buffer = await this.helpers.getBinaryDataBuffer(0, 'data');
const mdText = buffer.toString('utf-8');

const lines = mdText.split('\n');

const bankEntries = [];
let currentRole = null;
let bulletIndex = 0;

for (const rawLine of lines) {
  const line = rawLine.trim();

  const roleHeaderMatch = line.match(/^##\s+(.+)$/);
  if (roleHeaderMatch && !roleHeaderMatch[1].toLowerCase().includes('skills pool')) {
    currentRole = roleHeaderMatch[1].trim();
    bulletIndex = 0;
    continue;
  }

  if (line.toLowerCase().includes('skills pool')) {
    currentRole = '__SKILLS_POOL__';
    continue;
  }
  if (currentRole === '__SKILLS_POOL__' && line.includes('|')) {
    const skills = line.split('|').map(s => s.trim()).filter(Boolean);
    skills.forEach((skill, idx) => {
      bankEntries.push({
        id: `skill-${idx}`,
        type: 'skill',
        role: null,
        tags: [],
        text: skill,
      });
    });
    currentRole = null;
    continue;
  }

  const bulletMatch = line.match(/^-\s*\[([^\]]*)\]\s*(.+)$/);
  if (bulletMatch && currentRole && currentRole !== '__SKILLS_POOL__') {
    const tags = bulletMatch[1].split(',').map(t => t.trim()).filter(Boolean);
    const text = bulletMatch[2].trim();
    bulletIndex += 1;
    const roleSlug = currentRole
      .toLowerCase()
      .normalize('NFD').replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '');
    bankEntries.push({
      id: `${roleSlug}-${bulletIndex}`,
      type: 'bullet',
      role: currentRole,
      tags,
      text,
    });
  }
}

const skillCount = bankEntries.filter(e => e.type === 'skill').length;
const bulletCount = bankEntries.filter(e => e.type === 'bullet').length;

if (skillCount === 0) {
  throw new Error(
    `Parser found 0 skills. Expected a "## Skills Pool" section with a ` +
    `pipe-separated line underneath. Check the source file matches the ` +
    `expected experience-bank.md format.`
  );
}
if (bulletCount === 0) {
  throw new Error(
    `Parser found 0 bullets. Expected "## Role Name (dates)" headers ` +
    `followed by "- [tag1, tag2] text" lines. Check the source file ` +
    `matches the expected experience-bank.md format.`
  );
}

return [{
  json: {
    bank_entries: bankEntries,
    entry_count: bankEntries.length,
    skill_count: skillCount,
    bullet_count: bulletCount,
  },
}];
```

### CV Generation Config (`n8n-nodes-base.set`)

A plain values node — no logic, just constants read by later nodes.

Setup — add these fields (Edit Fields / Set node, one assignment each):

| Field | Type | Value | What it's for |
|---|---|---|---|
| `cv_tailor_threshold` | Number | `30` | Minimum AI match score (0–100) to bother tailoring a CV |
| `cv_owner_name` | String | your name | Used in the generated PDF's filename |

### Download cv.tex (`n8n-nodes-base.googleDrive`)

Downloads your CV LaTeX template as binary data.

Setup:
- Operation: Download.
- File: your `<<TOKEN>>`-placeholder LaTeX template file in Drive.
- Credential: same Google Drive credential as above.

### Discover Placeholders (`n8n-nodes-base.code`)

Decodes the template, extracts every `<<TOKEN>>` placeholder, and reads every
`\jobentry{Title}{Company}{Dates}` call to describe each CV role slot in plain
language for the AI prompt later. Mode: **Run Once for All Items**. Place
immediately after the template download.

Template placeholder syntax this expects: `<<NAME>>` or `<<NAME:MAXLENGTH>>` (the
`:MAXLENGTH` part is optional, used for the skills-line character budgets). Role
slots come from `\jobentry{Title}{Company}{Dates}` calls in the `.tex` file — one per
role you want tailored, with placeholders named `ROLE<N>_BULLET<M>` for that role's
bullets.

Code (paste as-is):
```js
// n8n Code node — "Discover Placeholders"
// Mode: Run Once for All Items
// Place immediately after the Google Drive download of the .tex template.

const buffer = await this.helpers.getBinaryDataBuffer(0, 'data');
const templateText = buffer.toString('utf-8');

if (!templateText || !templateText.includes('\\documentclass')) {
  throw new Error(
    'Decoded content does not look like a LaTeX file (missing ' +
    '\\documentclass). Check the Drive download node and file.'
  );
}

const placeholderMatches = [...templateText.matchAll(/<<([A-Z0-9_]+)(?::(\d+))?>>/g)];
const seen = new Map();
for (const m of placeholderMatches) {
  const name = m[1];
  const maxLength = m[2] ? parseInt(m[2], 10) : null;
  if (!seen.has(name)) seen.set(name, { name, maxLength });
}
const allPlaceholders = [...seen.values()];

const roleBulletPlaceholders = [];
const skillsLinePlaceholders = [];
const freeTextPlaceholders = [];

for (const p of allPlaceholders) {
  const roleBulletMatch = p.name.match(/^ROLE(\d+)_BULLET(\d+)$/);
  const skillsLineMatch = p.name.match(/^SKILLS_LINE_(\d+)$/);
  if (roleBulletMatch) {
    roleBulletPlaceholders.push({ ...p, roleIndex: parseInt(roleBulletMatch[1], 10), bulletIndex: parseInt(roleBulletMatch[2], 10) });
  } else if (skillsLineMatch) {
    skillsLinePlaceholders.push({ ...p, lineIndex: parseInt(skillsLineMatch[1], 10) });
  } else {
    freeTextPlaceholders.push(p.name);
  }
}

if (allPlaceholders.length === 0) {
  throw new Error('No <<PLACEHOLDER>> tokens found in template.');
}

function stripLatexComments(text) {
  return text.split('\n').map(line => {
    let backslashRun = 0;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '\\') { backslashRun++; continue; }
      if (ch === '%' && backslashRun % 2 === 0) return line.slice(0, i);
      backslashRun = 0;
    }
    return line;
  }).join('\n');
}
const cleaned = stripLatexComments(templateText);
const jobEntryPattern = /\\jobentry\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}/g;
const roleMeta = [];
let m;
let idx = 0;
while ((m = jobEntryPattern.exec(cleaned)) !== null) {
  idx += 1;
  roleMeta.push({ roleIndex: idx, title: m[1], company: m[2], dates: m[3] });
}

const roleSummary = roleMeta.map(r => ({
  ...r,
  bulletsNeeded: roleBulletPlaceholders.filter(p => p.roleIndex === r.roleIndex).length,
}));

if (roleSummary.length === 0) {
  throw new Error('No \\jobentry{} calls found in template -- cannot describe roles to the AI.');
}

return [{
  json: {
    tex_content: templateText,
    all_placeholders: allPlaceholders.map(p => p.name),
    role_bullet_placeholders: roleBulletPlaceholders,
    skills_line_placeholders: skillsLinePlaceholders,
    free_text_placeholders: freeTextPlaceholders,
    role_summary: roleSummary,
  },
}];
```

This section wires into Section 2's first node (`Filters`).

---

## 2. Search LinkedIn for Job Postings

Reads your target job titles and search filters from a Google Sheet, builds a
combined search query, fetches the results page, and extracts every job link on it.

### Filters (`n8n-nodes-base.googleSheets`)

Reads your global search filters (one row of settings, not one row per job).

Setup:
- Operation: Read rows.
- Document: your copy of
  [`templates/job-search-sheet-template.xlsx`](./templates/job-search-sheet-template.xlsx)
  (import it into Google Sheets via *File → Import*, or build your own with a matching
  tab/column layout — see below).
- Sheet: the `Filters` tab (referenced **By Name**, not by gid, so this keeps working
  after you make your own copy) — one settings row with columns: `location`,
  `experience_level`, `remote`, `job_type`, `easy_apply`. Valid values for the mapped
  columns: `experience_level` ∈ {Internship, Entry level, Associate, Mid-Senior level,
  Director, Executive}, `remote` ∈ {On-Site, Remote, Hybrid}, `job_type` ∈ {Full-time,
  Part-time, Contract, Temporary, Other, Internship} — any of these may be
  comma-separated to match more than one. `location` is free text; `easy_apply` is
  `TRUE`/`FALSE`.
- Set **Execute Once** on (it should only read the settings row once, not per item).
- Credential: a Google Sheets OAuth2 credential.

### Read Titles (`n8n-nodes-base.googleSheets`)

Reads your list of target job titles, one row per title, column named `title`.

Setup:
- Operation: Read rows.
- Document: the same spreadsheet as `Filters` above (your copy of
  `job-search-sheet-template.xlsx`).
- Sheet: the `Titles` tab (**By Name**) — one column, `title`, one job title per row.
- Credential: same Google Sheets credential.

### Build LinkedIn Search URL (`n8n-nodes-base.code`)

Combines every title into one Boolean-OR query, layers on the filters, and builds
one URL per page (3 pages, 10 results each = ~30 postings per run). Mode: **Run Once
for All Items**.

Code (paste as-is):
```js
// n8n Code node — "Build LinkedIn Search URL"
// Mode: Run Once for All Items
//
// Input: items from Google Sheets, one row per title (column: "title").
//        Fixed/global filter fields read from the "Filters" node (location,
//        experience level, remote, job type, easy apply).

const PAGES_PER_RUN = 3;       // start=0, 10, 20 -- ~30 postings/run
const RESULTS_PER_PAGE = 10;   // per this endpoint's observed real output

const titleRows = $input.all().map(item => item.json);

if (!titleRows.length) {
  throw new Error('No title rows received from Google Sheets input.');
}

const titles = titleRows
  .map(row => row.title)
  .filter(Boolean);

if (titles.length === 0) {
  throw new Error('No valid "title" values found in the input rows.');
}

const booleanKeywordQuery = titles
  .map(t => `"${t.replace(/"/g, '')}"`)
  .join(' OR ');

const config = $('Filters').first()?.json || {};
const location = config.location || '';
const experienceLevel = config.experience_level || '';
const remote = config.remote || '';
const jobType = config.job_type || '';
const easyApply = config.easy_apply === true ||
  String(config.easy_apply).trim().toLowerCase() === 'true';

function buildFilterSuffix() {
  let suffix = '';

  if (location) {
    suffix += `&location=${encodeURIComponent(location)}`;
  }

  if (experienceLevel) {
    const expLevelMap = {
      'Internship': '1',
      'Entry level': '2',
      'Associate': '3',
      'Mid-Senior level': '4',
      'Director': '5',
      'Executive': '6',
    };
    const transformed = experienceLevel
      .split(',')
      .map(e => expLevelMap[e.trim()] || '')
      .filter(Boolean);
    if (transformed.length) suffix += `&f_E=${transformed.join(',')}`;
  }

  if (remote) {
    const remoteMap = { 'On-Site': '1', 'Remote': '2', 'Hybrid': '3' };
    const transformed = remote
      .split(',')
      .map(e => remoteMap[e.trim()] || '')
      .filter(Boolean);
    if (transformed.length) suffix += `&f_WT=${transformed.join(',')}`;
  }

  if (jobType) {
    const jobTypeMap = {
      'Full-time': 'F', 'Part-time': 'P', 'Contract': 'C',
      'Temporary': 'T', 'Other': 'O', 'Internship': 'I',
    };
    const transformed = jobType
      .split(',')
      .map(t => jobTypeMap[t.trim()] || '')
      .filter(Boolean);
    if (transformed.length) suffix += `&f_JT=${transformed.join(',')}`;
  }

  if (easyApply) {
    suffix += '&f_EA=true';
  }

  return suffix;
}

const filterSuffix = buildFilterSuffix();
const keywordParam = `&keywords=${encodeURIComponent(booleanKeywordQuery)}`;

const pages = [];
for (let page = 0; page < PAGES_PER_RUN; page++) {
  const start = page * RESULTS_PER_PAGE;
  const url =
    `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` +
    `?f_TPR=r604800&start=${start}${keywordParam}${filterSuffix}`;

  pages.push({
    json: {
      url,
      page_index: page,
      start,
      titles_combined: booleanKeywordQuery,
      title_count: titles.length,
    },
  });
}

return pages;
```

### Fetch LinkedIn Search Page (`n8n-nodes-base.httpRequest`)

Fetches each search-result page (this node runs once per item from the previous
node, so once per page).

Setup:
- Method: GET.
- URL (expression): `={{$json.url}}`
- Send headers: on. Set headers as JSON:
```json
{
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.9",
  "Referer": "https://www.linkedin.com/",
  "Upgrade-Insecure-Requests": "1"
}
```
Use a current real Chrome User-Agent string, not a spoofed Googlebot one.

### Extract Job Links (`n8n-nodes-base.html`)

Pulls every job link out of the fetched HTML.

Setup:
- Operation: Extract HTML Content.
- One extraction value: key `links`, CSS selector `a.base-card__full-link`, return
  value: Attribute, attribute `href`, **Return Array** on.

### Split Out (`n8n-nodes-base.splitOut`)

Splits the `links` array into one item per link.

Setup:
- Field to split out: `links`.

### Dedup Job Links (`n8n-nodes-base.code`)

Drops duplicate links that showed up across multiple fetched pages, before anything
reaches the per-job loop. Mode: **Run Once for All Items**.

Code (paste as-is):
```js
// n8n Code node — "Dedup Job Links"
// Mode: Run Once for All Items
// Wire: Split Out -> Dedup Job Links -> Loop Over Items

const seen = new Set();
const deduped = [];

for (const item of $input.all()) {
  const link = item.json.links;
  if (!link) continue;
  if (seen.has(link)) continue;
  seen.add(link);
  deduped.push(item);
}

console.log(`[INFO] Dedup Job Links: ${$input.all().length} in, ${deduped.length} unique out.`);

return deduped;
```

This section wires into Section 3 (`Loop Over Items`).

---

## 3. Per-Job Loop Controller

One node — the loop that turns "a list of job links" into "process one job at a
time." Small enough that it's a natural fold-in with Section 2 or Section 4 when
this becomes a teaching post, but it's its own real thing on the canvas.

### Loop Over Items (`n8n-nodes-base.splitInBatches`)

Standard n8n batch loop. Default settings (batch size 1) work fine here since each
job needs its own AI call and its own delay.

Setup:
- No special options needed — defaults are fine.
- **Output 0 ("done")** wires to Section 9 (`output collector`) — fires once, after
  every job in the batch has been processed.
- **Output 1 ("loop")** wires to Section 4's first node (`Random Delay 15-25s`) —
  fires once per item, this is the per-job path.
- The end of the per-job path (Section 8's Telegram/error nodes) wires back into
  this same node to pull the next item.

---

## 4. Fetch Job & Skip Duplicates

For each job link: wait a randomized delay, fetch the individual job page, extract
its details, check whether it's already been processed, and skip straight back to
the loop if so.

### Random Delay 15-25s (`n8n-nodes-base.code`)

Waits a random 15–25 seconds before the next fetch, so requests don't land on an
obviously bot-like fixed interval. Mode: **Run Once for Each Item**. Place inside
the loop, before fetching the individual job page.

Code (paste as-is):
```js
// n8n Code node — "Random Delay 15-25s"
// Mode: Run Once for Each Item

const MIN_MS = 15000;
const MAX_MS = 25000;
const delayMs = Math.floor(Math.random() * (MAX_MS - MIN_MS + 1)) + MIN_MS;

await new Promise(resolve => setTimeout(resolve, delayMs));

return $input.item;
```

### Fetch Individual Job Page (`n8n-nodes-base.httpRequest`)

Fetches the full job posting page.

Setup:
- Method: GET.
- URL (expression): `={{ $json.links }}`

### Extract Job Details (`n8n-nodes-base.html`)

Pulls the fields needed for scoring and delivery out of the job page HTML.

Setup — Operation: Extract HTML Content, extraction values:

| Key | CSS selector | Return value |
|---|---|---|
| `title` | `h1.top-card-layout__title` | text (default) |
| `company` | `a.topcard__org-name-link` | text (default) |
| `location` | `span.topcard__flavor--bullet` | text (default) |
| `description` | `div.show-more-less-html__markup` | text (default) |
| `applicants_raw` | `.num-applicants__caption` | text (default) |
| `apply_url` | `link[rel='canonical']` | Attribute → `href` |

### Check Already Processed (`n8n-nodes-base.googleSheets`)

Looks up this job's URL in your tracker sheet to see if it's already been handled.

Setup:
- Operation: Read rows (with a filter, first match only).
- Document: the same spreadsheet as `Titles`/`Filters` above.
- Sheet: the `Result` tab (**By Name**) — see `Send Info To Sheet` below for its
  required columns.
- Filter: column `Link` equals `={{ $('Extract Job Details').item.json.apply_url }}`.
- **Always Output Data**: on (so the IF node below always gets an item to check,
  even on zero matches).
- **Retry On Fail**: on.
- Credential: same Google Sheets credential.

### Not Already In Sheet? (`n8n-nodes-base.if`)

Gate: true branch continues to scoring, false branch skips straight back to the
loop.

Setup — one condition:
- Left value (expression): `={{ $('Check Already Processed').item.json.isNotEmpty() }}`
- Operator: Boolean → is false
- True output wires to Section 5's first node (`Parse Applicant Count`).
- False output wires back to `Loop Over Items`.

### Parse Applicant Count (`n8n-nodes-base.code`)

Converts the raw "23 applicants" / "Over 100 applicants" text into a structured
number. Mode: **Run Once for Each Item**. Note: nothing downstream currently filters
on this value — it's captured, not yet acted on.

Code (paste as-is):
```js
// n8n Code node — "Parse Applicant Count"
// Mode: Run Once for Each Item

const rawText = ($input.item.json.applicants_raw || '').trim();

let applicantCount = null;
let isCapped = false;

if (rawText) {
  const numberMatch = rawText.match(/(\d+)/);
  if (numberMatch) {
    applicantCount = parseInt(numberMatch[1], 10);
    isCapped = /over/i.test(rawText);
  }
}

return {
  json: {
    ...$input.item.json,
    applicant_count: applicantCount,
    applicant_count_capped: isCapped,
    applicant_count_raw: rawText,
  },
};
```

---

## 5. AI Scoring & Content Selection

One Gemini call scores the job against your experience bank and picks/writes the CV
content, in the same pass, using a schema-enforced response.

### Format Bank For Prompt (`n8n-nodes-base.code`)

Builds the full prompt text and the JSON `responseSchema` for the Gemini call. Mode:
**Run Once for All Items**. This is the largest node in the workflow — the prompt
text and schema live entirely inside this code block; nothing needs to be configured
outside it beyond pasting it in.

Code (paste as-is):
```js
// n8n Code node — "Format Bank For Prompt" (rev 3 — prompt genericized, see below)
// Mode: Run Once for All Items

// ── DOMAIN CUSTOMIZATION — edit this block for your own field, nothing else ──
// SIGNAL_CATEGORIES: the checklist rule 4 below uses to find a JOB's most
// specific requirements before falling back to generic skills. Each `hint`
// is written field-agnostic on purpose -- swap in 2-3 examples from your
// own field if you want sharper detection (e.g. for nursing: named clinical
// systems/EHR software, named certifications like ACLS/BLS, patient
// caseload/unit size). Leave the hints as-is and the categories still work,
// just less sharply tuned to your field.
const SIGNAL_CATEGORIES = [
  {
    label: 'Named industry or vertical',
    hint: 'e.g. healthcare, manufacturing, education, finance, software -- ' +
      'replace with verticals relevant to your own field',
  },
  {
    label: 'Named tool, platform, system, or piece of equipment',
    hint: 'e.g. a specific EHR system, CAD tool, CRM, programming language, ' +
      'or piece of equipment -- replace with tools relevant to your own field',
  },
  {
    label: 'Geographic or international/multinational scope',
    hint: 'the words "international", "multinational", "global", a stated ' +
      'number of countries/sites/locations, cross-border or multi-region ' +
      'operations',
  },
  {
    label: 'Named methodology, framework, certification, or standard',
    hint: 'e.g. a clinical protocol, engineering standard, agile framework, ' +
      'or professional certification -- replace with ones relevant to your ' +
      'own field',
  },
  {
    label: 'Scale, complexity, or seniority signals',
    hint: 'e.g. team size, budget, caseload, project scope, transaction ' +
      'volume -- replace with whichever scale signal matters most in your ' +
      'own field',
  },
];

// TAGLINE_EXAMPLE_TITLES: 0-2 example job titles in your own field, shown to
// the AI only to demonstrate the *shape* of a real title (length, phrasing)
// -- never copied verbatim into output. Leave empty and the prompt falls
// back to your own CV template's role titles (from experience-bank.md /
// CV2_placeholders.tex) automatically, so this is optional polish, not a
// required edit.
const TAGLINE_EXAMPLE_TITLES = []; // e.g. ['ICU Charge Nurse', 'Registered Nurse']
// ── end DOMAIN CUSTOMIZATION ──────────────────────────────────────────────

const bankEntries = $('Collect Bank Entries').first().json.bank_entries;
const roleSummary = $('Discover Placeholders').first().json.role_summary;

const bulletEntries = bankEntries.filter(e => e.type === 'bullet');
const skillEntries = bankEntries.filter(e => e.type === 'skill');

// Derived generically from the bank's own role headers instead of a
// hardcoded domain name -- keeps the rubric accurate for any bank, not
// just a sales one.
const bankDomainPhrase = [...new Set(bulletEntries.map(e => e.role).filter(Boolean))]
  .join(', ') || 'the BANK\'s professional background';

// Plain-text bank listings the prompt references by [id] -- the AI must
// select FROM these, never invent new bullet/skill text from scratch.
const bulletBankText = bulletEntries
  .map(e => `[${e.id}] (${e.role}) ${e.text}`)
  .join('\n');

const skillBankText = skillEntries
  .map(e => `[${e.id}] ${e.text}`)
  .join('\n');

// Plain-language description of each CV role slot, built from the
// template's actual \jobentry{} calls (via Discover Placeholders) --
// NOT from strict date-string matching, which is what made the old
// cosine-similarity architecture brittle against template changes.
const roleSlotsText = roleSummary
  .map(r => `ROLE${r.roleIndex}: "${r.title}" at ${r.company}, ${r.dates} -- needs ${r.bulletsNeeded} bullets (placeholders: ROLE${r.roleIndex}_BULLET1..${r.bulletsNeeded})`)
  .join('\n');

const jobTitle = $('Extract Job Details').first().json.title || '';
const jobDescription = $('Extract Job Details').first().json.description || '';

// Rule 4's signal checklist, built from SIGNAL_CATEGORIES above.
const signalCategoriesText = SIGNAL_CATEGORIES
  .map((c, i) => `   ${String.fromCharCode(97 + i)}) ${c.label} (${c.hint}).`)
  .join('\n');
const categoryCountWord = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'][SIGNAL_CATEGORIES.length] || String(SIGNAL_CATEGORIES.length);

// Rule 9's tagline examples: use TAGLINE_EXAMPLE_TITLES if set, else fall
// back to the CV template's own role titles -- either way, no sales-domain
// default leaks in.
const taglineExamples = (TAGLINE_EXAMPLE_TITLES.length ? TAGLINE_EXAMPLE_TITLES : roleSummary.map(r => r.title))
  .filter(Boolean)
  .slice(0, 2);
const taglineExampleText = taglineExamples.length
  ? taglineExamples.map(t => `"${t}"`).join(', ')
  : 'a real, concise job title in your field';

const prompt = `You are selecting and rewording content for a CV to match a job posting,
from a fixed bank of pre-approved, factual bullets and skills. You must
never invent content not present in the bank.

## JOB
Title: ${jobTitle}
Description:
${jobDescription}

## ROLE SLOTS TO FILL (from the CV template)
${roleSlotsText}

## BULLET BANK (pick from these only, by [id])
${bulletBankText}

## SKILL BANK (pick from these only, by [id])
${skillBankText}

## Rules
1. LANGUAGE: Write EVERY output field -- tagline, profile, all bullets, all
   skills -- in English, always, regardless of the language of the JOB
   description above. If the JOB is written in French or any other language,
   still produce all output in English. Never output a bullet, skill, or any
   field in a non-English language, even if the source JOB text is in that
   language. This applies EQUALLY to text copied or lightly adapted from the
   BANK: if a BANK entry itself is written in French or any other
   non-English language (some SKILL BANK entries are), you must still
   translate it to English before using it -- do not pass through
   non-English BANK text unchanged just because it came from the BANK
   rather than being freshly composed.
2. For each ROLE slot above, choose the bullets from the BULLET BANK that
   best fit that role -- use the role's dates/company as a strong signal,
   but you may select any bullet whose real history plausibly overlaps
   that role's timeframe if it is the best available match for the JOB.
   Never invent a bullet. Never reuse the same bullet ID in two different
   role slots.
3. Reword each selected bullet toward the JOB's language/keywords (in
   English per rule 1). Preserve every number and named entity exactly.
   Keep each bullet under ~110 characters.
4. JD-SPECIFIC SIGNAL DETECTION: Before filling any skill slots with
   generic ${bankDomainPhrase} skills, independently check the JOB for
   EACH of these signal categories -- check every category every time, do
   not stop after finding one or two:
${signalCategoriesText}
   For every category that is genuinely present in the JOB, record a short
   phrase describing it in \`jd_specific_signals\`, and select the single
   best-matching BANK skill for that signal if one exists in the SKILL
   BANK -- do this independently per category; finding a match in one
   category is never a reason to skip checking another. If NO bank skill
   matches a genuinely-present signal, do not invent one (see rule 6) --
   just record the signal in \`jd_specific_signals\` with no matching skill.
   Only after all ${categoryCountWord} categories have been checked and their
   available matches selected, fill any remaining skill slots with the most
   relevant general skills. Do not let generic skills crowd out an available
   specific match.
5. Select 12-18 SKILL BANK entries most relevant to the JOB, avoiding two
   entries that express the same underlying concept (e.g. do not select
   two skills that both describe the same responsibility in different
   words -- pick the one that fits better and drop the other).
6. You may reword a skill's phrasing, but you must NEVER add a tool,
   platform, brand, certification name, or any other concept, however
   plausible-sounding, that is not already present, verbatim or as a
   direct translation, in that skill's own bank text -- even if it seems
   relevant to the JOB or appears in the skill bank elsewhere. If nothing
   in the SKILL BANK fits a gap, leave the gap -- do not fill it with an
   invented skill phrase.
7. If a bank skill fits naturally in under ~25 characters, use it as-is
   or lightly reword it. If it does not, use it at its natural length --
   do NOT create abbreviations like "Mgmt", "Dev.", or "Opt." to force a
   skill shorter. Truncated words look unprofessional on a real CV.
   Fitting within line-length limits is handled separately, after your
   selection -- it is not your responsibility to compress wording.
8. Order selected skills by relevance: priority 1 = most relevant. Any
   skill selected because it matched a signal in \`jd_specific_signals\`
   (per rule 4) MUST be ranked in the top 5 priorities -- a correct match
   buried in the middle or end of the list is as good as not selecting it
   at all, since lower-priority skills are the first to be dropped by the
   downstream character-budget packer.
9. TAGLINE: exactly one job title or role name, 2-5 words, matching the
   style of a real job title (e.g. ${taglineExampleText}). Do NOT write a
   descriptive sentence or clause. NEVER use constructions like
   "specializing in...", "with expertise in...", or any phrase joined by
   "and" describing two areas of work. If unsure, default to the closest
   real job title implied by the JOB. CONSERVATIVE BY DEFAULT: prefer the
   JOB posting's own title as closely as possible -- do NOT add seniority
   modifiers ("Senior", "Lead", "Principal", etc.) or extra qualifiers
   ("Manager", "Specialist") that are not present in the JOB's own title,
   even if they sound more impressive or better match the BANK's bullets.
   Only deviate from the JOB's exact title to expand a clear abbreviation
   present in it.
10. PROFILE: 2-4 sentences using only claims supported by selected bullets.
   Do not restate facts already shown elsewhere on the CV (degree,
   language fluency). CRITICAL: never repeat a SPECIFIC fact that already
   appears in a selected bullet -- no numbers, percentages, dollar/budget
   sizes, company names, or figures the bullets already state (the
   recruiter will read both sections; restating a number is redundant,
   not reinforcing). General, qualitative claims are fine as long as they
   do not restate a bullet's specific figure or named entity -- e.g.
   "experience improving operational efficiency" is fine, "improved
   efficiency by 50%" is not if a selected bullet already states that 50%.
11. MATCH_SCORE: Judge how well this JOB matches the BANK as a whole and
   output it as \`match_score\`, an integer from 0-100. Use this rubric:
   - 80-100: The JOB's core function and most of its SPECIFIC requirements
     (named tools, methodologies, verticals) are genuinely represented in
     the BANK -- e.g. a role closely matching ${bankDomainPhrase}, with
     strong matching bullets and skills available.
   - 40-79: The JOB shares the same general professional domain as the
     BANK (${bankDomainPhrase}-adjacent, partially overlapping function)
     but is missing several specific/named requirements the BANK doesn't
     cover.
   - 0-39: The JOB's core function is fundamentally different from the
     BANK's background (${bankDomainPhrase}) -- e.g. a role in an unrelated
     profession with no meaningful skill overlap.
   Still complete bullet_assignments and skill_candidates FULLY and
   normally regardless of the match_score value you assign -- do not
   shorten, skip, or under-invest in content just because you judge the
   score to be low. The score is used downstream to decide whether to use
   your output at all; your job here is to produce it correctly either way.

Return ONLY the requested JSON object -- no explanation, no markdown fences.`;

// Native Gemini responseSchema -- enforced during generation, not parsed
// after the fact. This is the property that makes Option B structurally
// immune to the "Model output doesn't fit required format" failure seen
// with the LangChain Structured Output Parser.
//
// jd_specific_signals is placed right after match_score, before the
// content fields -- same trick as match_score itself: forcing the model
// to commit to an explicit, inspectable list of what it thinks the JOB's
// specific signals are BEFORE it selects skills, rather than leaving
// signal-detection as implicit, unauditable reasoning. This also gives
// a concrete diagnostic to check when auditing a run: if a real signal
// (e.g. "international scope") is missing from this array entirely, the
// miss is in detection; if it's present but no matching skill was
// selected/ranked highly, the miss is in selection or ranking -- two
// different problems that look identical without this field.
const responseSchema = {
  type: 'object',
  properties: {
    match_score: { type: 'integer' },
    jd_specific_signals: {
      type: 'array',
      items: { type: 'string' },
    },
    tagline: { type: 'string' },
    profile: { type: 'string' },
    bullet_assignments: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          placeholder: { type: 'string' },
          text: { type: 'string' },
        },
        required: ['placeholder', 'text'],
      },
    },
    skill_candidates: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          text: { type: 'string' },
          priority: { type: 'integer' },
        },
        required: ['text', 'priority'],
      },
    },
  },
  required: ['match_score', 'jd_specific_signals', 'tagline', 'profile', 'bullet_assignments', 'skill_candidates'],
};

return [{ json: { prompt, responseSchema } }];
```

The prompt is field-agnostic by default — `bankDomainPhrase` (rule 4, rule 11) is
derived from your own bank's role headers, and the tagline examples (rule 9) fall back
to your own CV template's role titles. Nothing needs editing to point this at a
non-sales bank. If you want sharper JD-signal detection for your own field, edit the
`SIGNAL_CATEGORIES` / `TAGLINE_EXAMPLE_TITLES` block near the top of the code — it's
the one place in the file meant to be customized; the rest of the prompt-building logic
doesn't need to change.

### Call Gemini For Selection (`n8n-nodes-base.httpRequest`)

Sends the prompt to Gemini with the schema enforced natively.

Setup:
- Method: POST.
- URL: `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`
  (swap the model name for whatever your Gemini free-tier account actually has
  access to — this changes over time and by account).
- Authentication: Generic Credential Type → Header Auth.
- Credential: an HTTP Header Auth credential with your Gemini API key as the header
  value (e.g. header name `x-goog-api-key`).
- Send body: on, JSON, body:
```
={
  "contents": [
    { "parts": [{ "text": {{ JSON.stringify($json.prompt) }} }] }
  ],
  "generationConfig": {
    "responseMimeType": "application/json",
    "responseSchema": {{ JSON.stringify($json.responseSchema) }}
  }
}
```
- Retry On Fail: on, Max Tries 2, Wait Between Tries 5000ms.
- **On Error: Continue (using error output)** — this is what makes the retry/backoff
  loop in Section 6 possible. Without this, a failed call stops the whole run instead
  of routing to the retry logic.
- Output 0 (success) wires to `Parse Gemini Selection Output`.
- Output 1 (error) wires to Section 6 (`Check Retry Limit`).

### Parse Gemini Selection Output (`n8n-nodes-base.code`)

Parses and validates the raw Gemini response exactly once — nothing downstream
touches the raw response again. Mode: **Run Once for All Items**.

Code (paste as-is):
```js
// n8n Code node — "Parse Gemini Selection Output"
// Mode: Run Once for All Items
// Place IMMEDIATELY after "Call Gemini For Selection".

const response = $input.first().json;

if (!response.candidates || response.candidates.length === 0) {
  throw new Error(`No candidates in Gemini response: ${JSON.stringify(response).slice(0, 500)}`);
}
const candidate = response.candidates[0];
if (candidate.finishReason && candidate.finishReason !== 'STOP') {
  throw new Error(`Generation did not finish normally (finishReason: ${candidate.finishReason}).`);
}
if (!candidate.content?.parts?.length) {
  throw new Error('Candidate has no content parts.');
}

let parsed;
try {
  parsed = JSON.parse(candidate.content.parts[0].text);
} catch (e) {
  throw new Error(`Gemini output was not valid JSON despite schema enforcement: ${e.message}`);
}

const required = ['match_score', 'jd_specific_signals', 'tagline', 'profile', 'bullet_assignments', 'skill_candidates'];
const missing = required.filter(key => !(key in parsed));
if (missing.length > 0) {
  throw new Error(`AI output missing required field(s): ${missing.join(', ')}.`);
}

if (typeof parsed.match_score !== 'number' || parsed.match_score < 0 || parsed.match_score > 100) {
  throw new Error(`match_score must be a number 0-100, got: ${JSON.stringify(parsed.match_score)}`);
}

return [{ json: parsed }];
```

### Worth Tailoring? (`n8n-nodes-base.if`)

Gate on the score threshold from Section 1's config node.

Setup — one condition:
- Left value (expression): `={{ $json.match_score }}`
- Right value (expression): `={{ $('CV Generation Config').first().json.cv_tailor_threshold }}`
- Operator: Number → is greater than or equal to.
- True output wires to Section 7 (`Map AI Output And Pack Skills`).
- False output wires back to `Loop Over Items` — jobs below the bar are skipped
  silently, no notification sent.

---

## 6. Gemini Retry / Backoff

A decoupled reliability loop that only activates when the Gemini call itself errors
out (network issue, API outage). Caps retries so one bad job can't stall the whole
run.

### Check Retry Limit (`n8n-nodes-base.code`)

Counts attempts and decides whether to give up. Mode: **Run Once for Each Item**.
Wire: `Call Gemini For Selection` error output → this node → `Give Up?`.

Setup: **Always Output Data**: on.

Code (paste as-is):
```js
// n8n Code node — "Check Retry Limit"
// Mode: Run Once for Each Item

const MAX_MANUAL_RETRIES = 3;

const input = $input.first().json;

const currentCount = input.manual_retry_count || 0;
const newCount = currentCount + 1;

const errorMessage =
  (typeof input.error === 'string' && input.error) ||
  input.error?.message ||
  input.error?.error?.message ||
  JSON.stringify(input.error ?? 'Unknown error shape - inspect this item in a real execution').slice(0, 300);

const giveUp = newCount > MAX_MANUAL_RETRIES;

return [{
  json: {
    ...input,
    manual_retry_count: newCount,
    give_up: giveUp,
    last_error_message: errorMessage,
  },
}];
```

### Give Up? (`n8n-nodes-base.if`)

Gate on the `give_up` flag from the previous node.

Setup — one condition:
- Left value (expression): `={{ $json.give_up }}`
- Operator: Boolean → is true.
- True output wires to `Log Failed Gemini Call`.
- False output wires to `Wait`.

### Wait (`n8n-nodes-base.wait`)

Pauses before retrying.

Setup:
- Amount: `35` (seconds).
- Output wires back to `Call Gemini For Selection`, retrying the same job.

### Log Failed Gemini Call (`n8n-nodes-base.code`)

Builds a plain record of the failure once retries are exhausted. Mode: **Run Once
for Each Item**.

Code (paste as-is):
```js
// n8n Code node — "Log Failed Gemini Call"
// Mode: Run Once for Each Item
// Wire: Give Up? (true branch) -> this node -> Loop Over Items

const jobDetails = $('Extract Job Details').first().json;
const input = $input.first().json;

return [{
  json: {
    status: 'FAILED - Gemini Error',
    title: jobDetails?.title || 'unknown',
    company: jobDetails?.company || 'unknown',
    apply_url: jobDetails?.apply_url || 'unknown',
    failed_at: new Date().toISOString(),
    retries_attempted: input.manual_retry_count || 'unknown',
    error_message: input.last_error_message || 'unknown',
  },
}];
```

Wire this node's output back to `Loop Over Items` — a job that exhausts its retries
must still have an outgoing connection, or it hangs the whole scheduled run.

---

## 7. Build & Compile Tailored CV

Turns the AI's raw output into filled LaTeX, then compiles it to a real PDF via a
self-hosted service.

### Map AI Output And Pack Skills (`n8n-nodes-base.code`)

Maps the AI's bullet/tagline/profile output onto template fields, and bin-packs the
selected skills into the skills-line character budgets, in the AI's own priority
order. Mode: **Run Once for All Items**.

Code (paste as-is):
```js
// n8n Code node — "Map AI Output And Pack Skills"
// Mode: Run Once for All Items
// Runs on the TRUE branch of "Worth Tailoring?".

const parsed = $input.first().json;

const discovery = $('Discover Placeholders').first().json;

const allFields = { TAGLINE: parsed.tagline, PROFILE: parsed.profile };
for (const b of parsed.bullet_assignments) {
  allFields[b.placeholder] = b.text;
}

function escapeLatexPhrase(str) {
  return String(str).replace(/([%&_#{}$])/g, '\\$1');
}

const SEP = ' \\;|\\; ';
const SEP_VISUAL_LENGTH = 3;

const skillsLineSlots = discovery.skills_line_placeholders.sort((a, b) => a.lineIndex - b.lineIndex);
for (const slot of skillsLineSlots) {
  if (!slot.maxLength) {
    throw new Error(`${slot.name} has no declared max length -- template must use <<${slot.name}:NUMBER>>.`);
  }
}

const bins = skillsLineSlots.map(slot => ({ slot, capacity: slot.maxLength, used: 0, items: [] }));

const items = parsed.skill_candidates
  .sort((a, b) => a.priority - b.priority)
  .map((s, originalIndex) => {
    const escaped = escapeLatexPhrase(s.text);
    return { text: escaped, length: escaped.length, originalIndex, priority: s.priority };
  });

const dropped = [];
for (const item of items) {
  let placed = false;
  for (const bin of bins) {
    const addedLength = bin.items.length === 0 ? item.length : item.length + SEP_VISUAL_LENGTH;
    if (bin.used + addedLength <= bin.capacity) {
      bin.items.push(item);
      bin.used += addedLength;
      placed = true;
      break;
    }
  }
  if (!placed) dropped.push(item);
}

if (dropped.length > 0) {
  console.log(`[INFO] ${dropped.length}/${items.length} AI-selected skill(s) dropped: ` +
    dropped.map(d => `priority ${d.priority} "${d.text}"`).join(', '));
}

const skillsLines = {};
for (const bin of bins) {
  skillsLines[bin.slot.name] = bin.items.map(i => i.text).join(SEP);
}
for (const [name, value] of Object.entries(skillsLines)) {
  allFields[name] = value;
}

return [{ json: { all_fields: allFields, raw_ai_output: parsed } }];
```

Note on the character budget: `\;` (a LaTeX spacing command) has ~3 characters of
visual width — that's what `SEP_VISUAL_LENGTH` accounts for. Measure your own
template's actual skills-line width in the rendered PDF rather than guessing from
leftover placeholder text; this build's three lines all measured to 117 characters.

### Collect For Review (`n8n-nodes-base.code`)

Pushes one diagnostic record per job into workflow static data, for manual
spot-checking later. Purely observational — passes data through unchanged. Mode:
**Run Once for All Items**.

Code (paste as-is):
```js
// n8n Code node — "Collect For Review"
// Mode: Run Once for All Items

const staticData = $getWorkflowStaticData('global');
if (!staticData.collected) staticData.collected = [];

const aiOutput = $json.raw_ai_output;
const allFields = $json.all_fields;

staticData.collected.push({
  job_title: $('Extract Job Details').first().json.title,
  jd_description: $('Extract Job Details').first().json.description,
  tagline: aiOutput?.tagline,
  profile: aiOutput?.profile,
  skill_candidates: aiOutput?.skill_candidates,
  bullet_assignments: aiOutput?.bullet_assignments,
  score: aiOutput?.match_score,
  final_skills_lines: {
    SKILLS_LINE_1: allFields?.SKILLS_LINE_1,
    SKILLS_LINE_2: allFields?.SKILLS_LINE_2,
    SKILLS_LINE_3: allFields?.SKILLS_LINE_3,
  },
});

return $input.all();
```

### Parse And Fill Template (`n8n-nodes-base.code`)

Substitutes every field into the template text, verifies nothing is missing or left
unfilled, and base64-encodes the result for upload. Mode: **Run Once for All Items**.

Code (paste as-is):
```js
// n8n Code node — "Parse And Fill Template"
// Mode: Run Once for All Items

const allFields = $input.first().json.all_fields;
const discovery = $('Discover Placeholders').first().json;
const templateText = $('Discover Placeholders').first().json.tex_content;

if (!allFields) {
  throw new Error('Missing all_fields from "Map AI Output And Pack Skills".');
}

const skillsLineNames = new Set(discovery.skills_line_placeholders.map(p => p.name));

function escapeLatex(str) {
  let s = String(str);
  if (/\\/.test(s)) {
    console.log('[WARN] Stripped unexpected literal backslash from generated content.');
    s = s.replace(/\\/g, '');
  }
  return s.replace(/([%&_#{}$])/g, '\\$1');
}

let filled = templateText;
for (const [name, value] of Object.entries(allFields)) {
  const finalText = skillsLineNames.has(name) ? value : escapeLatex(value);
  const tokenPattern = new RegExp(`<<${name}(?::\\d+)?>>`, 'g');
  filled = filled.replace(tokenPattern, finalText);
}

const missing = discovery.all_placeholders.filter(name => !(name in allFields));
if (missing.length > 0) {
  throw new Error(`Missing field(s) from AI output: ${missing.join(', ')}`);
}

const leftover = [...new Set(
  [...filled.matchAll(/<<([A-Z0-9_]+)(?::\d+)?>>/g)].map(m => m[1])
)];
if (leftover.length > 0) {
  throw new Error(`Unfilled placeholder(s) remain after substitution: ${leftover.join(', ')}`);
}

if (!filled.includes('\\documentclass')) {
  throw new Error('Output does not contain \\documentclass -- substitution likely corrupted.');
}
if (!filled.includes('\\end{document}')) {
  throw new Error('Output is missing \\end{document}.');
}

const buffer = Buffer.from(filled, 'utf-8');

return [{
  json: { tailored_tex: filled },
  binary: {
    data: {
      data: buffer.toString('base64'),
      mimeType: 'text/x-tex',
      fileName: 'tailored-cv.tex',
    },
  },
}];
```

### latex-api /convert (`n8n-nodes-base.httpRequest`)

Sends the filled `.tex` file to a self-hosted LaTeX-compiling service and gets a PDF
back. This build uses a small FastAPI service running `xelatex` (required for
`fontspec`/custom fonts), but any service that accepts a `.tex` file and returns a
compiled PDF works here — this node is the integration point, not a prescription for
which compiler service to build.

Setup:
- Method: POST.
- URL: your compiler service's endpoint (this build: `http://latex-api:8000/convert`,
  reachable over an internal Docker network).
- Send body: on, Content Type: Multipart Form-Data.
- Body parameter: type Form Binary Data, name `main_file`, input data field name
  `data` (matches the binary property set by the previous node).
- **On Error: Continue (Using Error Output)** — a compile failure (e.g. HTTP 422
  with a compiler log) should route to the error path, not stop the run.

### Check Compile Result (`n8n-nodes-base.code`)

Distinguishes success (PDF binary present) from failure (error payload with a
compiler log), and builds the output filename. Mode: **Run Once for All Items**.

Code (paste as-is):
```js
// n8n Code node — "Check Compile Result"
// Mode: Run Once for All Items
// Place immediately after "latex-api /convert".

const item = $input.first();

if (item.json && item.json.error) {
  let compileError = null;
  let compileLog = null;
  let parseFailed = false;

  try {
    const rawMessage = item.json.error.message || '';
    const dashIndex = rawMessage.indexOf(' - ');
    const afterDash = dashIndex !== -1 ? rawMessage.slice(dashIndex + 3) : rawMessage;
    const innerJsonString = JSON.parse(afterDash);
    const parsed = JSON.parse(innerJsonString);
    compileError = parsed.detail?.error || null;
    compileLog = parsed.detail?.log || null;
  } catch (e) {
    parseFailed = true;
  }

  return [{
    json: {
      success: false,
      error_message: compileError || item.json.error.message || 'Unknown latex-api error',
      compile_log: compileLog ? compileLog.replace(/\\n/g, '\n').slice(0, 2000) : null,
      parse_failed: parseFailed,
    },
  }];
}

if (!item.binary || !item.binary.data) {
  return [{
    json: {
      success: false,
      error_message: 'latex-api returned no error but also no PDF binary.',
      compile_log: null,
    },
  }];
}

const jobTitle = $('Extract Job Details').first().json.title || 'Untitled';
const company = $('Extract Job Details').first().json.company || 'UnknownCompany';

const sanitize = (s) => s
  .trim()
  .replace(/[\\/:*?"<>|]/g, '')
  .replace(/\s+/g, ' ');

const ownerName = ($('CV Generation Config').first().json.cv_owner_name || 'Candidate').trim();
const fileName = `${sanitize(ownerName)}- ${sanitize(jobTitle)}-${sanitize(company)}.pdf`;

return [{
  json: { success: true },
  binary: {
    data: {
      ...item.binary.data,
      fileName,
    },
  },
}];
```

### Compile Succeeded? (`n8n-nodes-base.if`)

Gate on the `success` field from the previous node.

Setup — one condition:
- Left value (expression): `={{ $json.success }}`
- Operator: Boolean → equals `true`.
- True output wires to Section 8 (`Send Tailored CV`).
- False output wires to Section 8 (`Send Error`).

---

## 8. Deliver Result

Sends the finished CV (or the failure) to Telegram, archives to Drive, and logs to a
tracking sheet.

### Send Tailored CV (`n8n-nodes-base.telegram`)

Delivers the compiled PDF as a document.

Setup:
- Operation: Send Document.
- Chat ID: your own Telegram chat ID (this build's is a numeric personal chat ID —
  get yours from your bot via BotFather/`getUpdates`).
- Binary Data: on, property: `={{$('Check Compile Result').item.binary.data}}`.
- Additional Fields → Caption (Markdown), e.g.:
```
=🔥 New JobOffer for *{{ $now.format('dd-MM-yyyy') }}*

*Title :* {{ $('Extract Job Details').item.json.title }}
*Company :* {{ $('Extract Job Details').item.json.company }}
*Location :* {{ $('Extract Job Details').item.json.location }}
*Score :* {{ $('Parse Gemini Selection Output').item.json.match_score }}
*Applicant No. :* {{ $('Extract Job Details').item.json.applicants_raw }}
*Apply link :*{{ $('Extract Job Details').item.json.apply_url }}
```
- Parse mode: Markdown (Telegram's single-asterisk `*bold*`, not `**bold**`).
- Credential: a Telegram API credential for your own bot.
- Output wires to `Upload CV To Drive`.

### Send Error (`n8n-nodes-base.telegram`)

Sends a plain-text failure notice with the compiler context, on the `Compile
Succeeded?` false branch.

Setup:
- Chat ID: same as above.
- Text, e.g.:
```
=Encountered an error generation.

🔥 New JobOffer for *{{ $now.format('dd-MM-yyyy') }}*

*Title :* {{ $('Extract Job Details').item.json.title }}
*Company :* {{ $('Extract Job Details').item.json.company }}
*Location :* {{ $('Extract Job Details').item.json.location }}
*Score :* {{ $('Parse Gemini Selection Output').item.json.match_score }}
*Applicant No. :* {{ $('Extract Job Details').item.json.applicants_raw }}
*Apply link :*{{ $('Extract Job Details').item.json.apply_url }}
```
- Additional Fields → Append Attribution: off.
- Same Telegram credential.
- Output wires back to `Loop Over Items`.

### Upload CV To Drive (`n8n-nodes-base.googleDrive`)

Archives the PDF.

Setup:
- Input data field name (expression): `={{ $('Check Compile Result').item.binary.data }}`
- Name (expression): `={{ $json.result.document.file_name }}`
- Folder: a Drive folder you create for generated CVs.
- Credential: same Google Drive credential as Section 1.
- On Error: Continue (Regular Output) — an archive failure shouldn't block logging.
- Output wires to `Send Info To Sheet`.

### Send Info To Sheet (`n8n-nodes-base.googleSheets`)

Appends (or updates, matched on the job link) a tracker row — the durable record of
every run, including the full filled `.tex` source for audit.

Setup:
- Operation: Append (or Update, matching on `Link`, so re-runs update rather than
  duplicate).
- Document: the same spreadsheet as `Titles`/`Filters` above.
- Sheet: the `Result` tab (**By Name**) — headers must be exactly `Status`,
  `Position`, `Company`, `Link`, `Score`, `Extract_date`, `CV`, `Latex_code` (this is
  what [`templates/job-search-sheet-template.xlsx`](./templates/job-search-sheet-template.xlsx)
  ships pre-filled with; a hand-built sheet with different/missing column names will
  fail here since these are referenced by name, not position).
- Columns to map: `Status` (constant, e.g. `"Waiting For Action"`), `Company`,
  `Link` (this is the match column), `Score`, `Extract_date`, `CV` (Drive
  `webViewLink` from the previous node's output), `Latex_code` (the filled `.tex`
  source, for audit), `Position`.
- On Error: Continue (Regular Output).
- Credential: same Google Sheets credential.
- Output wires back to `Loop Over Items`.

---

## 9. Run Summary

One node, runs once after the loop finishes (not per job) — dumps the diagnostic
data `Collect For Review` accumulated during the run into a single item for manual
inspection.

### output collector (`n8n-nodes-base.code`)

Reads back everything `Collect For Review` (Section 7) pushed into workflow static
data during this run, and clears nothing itself — clearing happens implicitly
because static data is read fresh each run via `$getWorkflowStaticData`. Mode:
**Run Once for All Items**. Wired from `Loop Over Items`' "done" output (fires once,
after the last job in the batch finishes).

Code (paste as-is):
```js
const staticData = $getWorkflowStaticData('global');
return [{ json: { all_iterations: staticData.collected } }];
```

If you add your own accumulator like this, remember to clear `staticData.collected`
after reading it back (e.g. `staticData.collected = []` at the end of this node) —
n8n's workflow static data persists across scheduled runs, not just within one, so
an uncleared accumulator grows without bound.

---

## What you need before starting

See `WORKFLOW_GUIDE.md` §2 for the account/infrastructure checklist (self-hosted n8n,
a LaTeX-compiler service, Gemini API key, Google Drive/Sheets access, a Telegram bot).
Not duplicated here since it doesn't change based on this rewrite.
