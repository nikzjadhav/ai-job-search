<p align="center">
  <img src="assets/readme/hero.svg" alt="AI Job Search. Pip the courier bird carries an application along a flight path to its destination." width="100%">
</p>

<p align="center">
  <a href="https://github.com/lessweb/deepcode-cli"><img src="https://img.shields.io/badge/runtime-Deep%20Code-4D6BFE?style=flat" alt="Deep Code" height="20"></a>
  &nbsp;
  <a href="https://www.deepseek.com"><img src="https://img.shields.io/badge/model-DeepSeek-4D6BFE?style=flat" alt="DeepSeek" height="20"></a>
  &nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2A9D8F?style=flat" alt="MIT license" height="20"></a>
</p>

<p align="center">
  <b>AI Job Search · DeepSeek + Deep Code</b><br>
  A local, DeepSeek-powered job application workspace with a DuckDB control center.
</p>

<p align="center">
  <a href="#quick-start"><b>Quick start</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#control-center"><b>Control center</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#dashboard"><b>Dashboard</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#commands"><b>Commands</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#how-apply-works"><b>How /apply works</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="DEEPSEEK_QUICKSTART.md"><b>DeepSeek quickstart</b></a>
</p>

## Quick start

This is a template workspace. Clone it, run `/setup` to fill in your profile, then launch Deep Code.

### What you need

| | |
|---|---|
| **[DeepSeek API key](https://platform.deepseek.com/api_keys)** | Pay-per-token. The project defaults to `deepseek-v4-pro`; use `deepseek-flash` for cheaper bulk runs. |
| **[Deep Code](https://github.com/lessweb/deepcode-cli)** | `npm install -g @vegamo/deepcode-cli` |
| **Python 3.10+** | Runs the tools in `tools/`. |
| **[Bun](https://bun.sh)** | Runs the LinkedIn and freehire CLIs. |
| **LaTeX** | Needs `lualatex` and `xelatex` to compile CVs and cover letters. TinyTeX is the lightweight macOS option. |
| **Poppler** *(optional)* | `brew install poppler` for `pdftoppm` page rendering and the `pdftotext` fallback. |
| **pypdf** *(optional)* | `python3 -m pip install --user pypdf` for the ATS text-layer check. |

### Run it

```bash
cd /path/to/ai-job-search
deepcode
```

Inside Deep Code:

```text
/skills                 # verify the workflow skills are discovered
/scrape                 # refresh the configured markets
/rank --limit 20        # score the stored postings into a shortlist
/apply <job URL>        # tailor CV + cover letter for one role
/interview              # prep for an interview on a tracked application
/outcome                # record results and draft follow-ups
```

If `/scrape` reports that `bun` is missing, install it first:

```bash
curl -fsSL https://bun.sh/install | bash
```

### Reconfigure the profile

Re-run onboarding if your target roles, countries, or experience change:

```text
/setup
/setup --section search
```

The search strategy is in
[`.deepcode/skills/scrape/search-queries.md`](.deepcode/skills/scrape/search-queries.md). It ships with an example five-market setup, the exact LinkedIn/freehire commands, local job-board fallbacks, and the language/visa filters.

## Recreating this workspace

Use this section only if you are creating a fresh copy from upstream.

### 1. Fork and clone

```bash
gh repo fork ai-job-search-dev/ai-job-search --clone
cd ai-job-search
```

> [!IMPORTANT]
> **A fork of this repo is always public** — GitHub does not allow private forks of public repositories — and
> `/setup` writes your personal data (name, contact details, employment history, salary expectations) into
> **tracked** files. For a personal job search, do not use a public fork: create a **private** repository and
> add this repo as `upstream`, as described in [SETUP.md section 8](SETUP.md#8-pulling-upstream-updates-into-your-fork).

## Control center (UI)

The local UI is the main control surface for scraping and tracking applications. It runs on your
machine and uses DuckDB for the UI database.

```text
/ui
```

Or directly:

```bash
python3 ui/server.py --open
```

Default URL: <http://127.0.0.1:8765/>

What it does:

- **Scrape buttons** — scrape all configured markets or only the selected ones, using LinkedIn + freehire
- **Roles table** — search and filter by market, fit, status, and source; target markets are shown by
  default, with an option to include other markets
- **Mark applied** — stores the posting reference, application date, status, notes, CV file, and
  cover-letter file in DuckDB
- **AI generate** — calls DeepSeek with the job and profile, writes
  `cover_letters/cover_<company>_<role>.tex`, compiles the PDF with `xelatex`, and stores both paths
- **Cover letter** — editable textbox prefilled with a model-free draft; saving writes
  `documents/applications/<company>_<role>/cover_letter.txt` and regenerates the `.tex`/`.pdf`
- **Applications table** — advance status from applied → interview → offer → hired/rejected
- **Re-import files** — pull `seen_jobs.json` and `job_search_tracker.csv` back into DuckDB
- **Open report** — opens the static `/dashboard` page

DuckDB file: `data/job_search.duckdb` (gitignored). The tables are `jobs`, `applications`, and
`scrape_runs`.

**Write-through is deliberate.** The UI updates DuckDB *and* the canonical files:

- `job_scraper/seen_jobs.json` for scraped roles
- `job_search_tracker.csv` for applications

That keeps `/rank`, `/apply`, `/outcome`, `/html-report`, and `/dashboard` working exactly as before.
DuckDB is the fast query/index layer, not a second source of truth.

The **AI generate** button needs the DeepSeek key configured in `~/.deepcode/settings.json` (or `DEEPCODE_API_KEY`). Scrape, the manual textbox draft, and status updates remain model-free.

Implementation notes and the DuckDB-vs-SQLite trade-off: [`ui/README.md`](ui/README.md).

## Dashboard

Generate a self-contained dashboard of the full pipeline:

```text
/dashboard
```

It writes `reports/dashboard.html` and offers to open it. The page includes:

- stat cards for roles found, eligible, high fit, applications, interviews, and offers
- roles by market, fit distribution, and source charts
- application status breakdown
- a filterable table of every scraped role (market, fit, status, source, search)
- an applications table when the tracker has entries

It is one offline HTML file with inline CSS, JavaScript, and SVG. No CDN, no server, no external
requests. Data comes from `job_scraper/seen_jobs.json` and `job_search_tracker.csv`; both are
gitignored personal data.

To build it from the terminal:

```bash
python3 tools/build_dashboard.py --open
python3 tools/build_dashboard.py --output reports/dashboard-2026-11-04.html
```

`/html-report` remains available for an applications-only report.

## Commands

| Command | What it does |
|---|---|
| **`/setup`** | Builds or updates the profile from documents, a pasted CV, or an interview |
| **`/scrape`** | Searches the five target markets and deduplicates new postings |
| **`/rank`** | Batch-scores stored postings into a ranked shortlist |
| **`/apply`** | Evaluates fit, drafts and compiles a CV and cover letter, runs the reviewer pass, and records the application |
| `/interview` | Builds a stage-specific prep pack and runs a mock interview |
| `/outcome` | Records results, archives submitted materials, and drafts follow-ups |
| `/gmail-sync` | Proposes application status updates from Gmail |
| `/notion-sync` | Publishes a read-only pipeline view to Notion |
| `/html-report` | Generates an applications-only offline HTML report |
| **`/dashboard`** | Generates the full pipeline dashboard: scraped roles, applications, charts, filters |
| **`/ui`** | Starts the local DuckDB-backed control center for scraping and tracking applications |
| `/expand` | Adds competencies found in public profiles and course syllabi |
| `/upskill` | Maps skill gaps and builds a learning plan |
| `/add-template` | Registers a custom CV or cover-letter template |
| `/add-portal` | Generates a search skill for a new job board |
| `/reset` | Wipes profile data or documents after confirmation |

## How `/apply` works

1. **Parse** the posting URL or pasted text.
2. **Evaluate fit** against the profile (skills, experience, culture, location, eligibility, language).
3. **Draft** a tailored CV and cover letter in LaTeX.
4. **Review** in a separate, explicitly critical read-only pass that researches the company and critiques the drafts.
5. **Revise** based on the review.
6. **Compile and inspect** both PDFs with `lualatex` and `xelatex`. The workflow checks page count, orphaned
   `\cventry` titles, cover-letter overflow, and bullet/font consistency. If `deepseek-v4-pro` cannot inspect
   images, switch to `deepseek-flash` for the page check or rely on `tools/verify_layout.py`.
7. **ATS-check** the CV’s text layer with `tools/verify_pdf.py`.
8. **Present** the final documents with a pass/fail verification checklist.

Deep Code has no subagent tool, so the reviewer runs as a labelled read-only pass in the same session. It is
a quality gate, not a security boundary. The posting’s integrity gate still runs first, and the fetch/review
steps do not follow instructions embedded in a posting.

## Search configuration

The search strategy is in
[`.deepcode/skills/scrape/search-queries.md`](.deepcode/skills/scrape/search-queries.md). It ships with an
example multi-market setup (Malaysia, Singapore, Thailand, UAE, Germany) and includes:

- city-level LinkedIn searches
- freehire country codes (`MY`, `SG`, `TH`, `AE`, `DE`)
- priority categories for data engineering, platform/streaming, adjacent analytics, and domain roles
- local job-board fallbacks for each market
- language, date, eligibility, and visa filters

Edit the file directly, or run `/setup --section search`, to match your own market. Install Bun so
`/scrape` uses its normal CLI path.

## Customization

| File | What to change |
|---|---|
| [`DEEPSEEK.md`](DEEPSEEK.md) | Profile, target countries, work authorisation, deal-breakers, verification checklist |
| [`01-candidate-profile.md`](.deepcode/skills/job-application-assistant/01-candidate-profile.md) | Structured education, experience, skills |
| [`02-behavioral-profile.md`](.deepcode/skills/job-application-assistant/02-behavioral-profile.md) | Behavioural profile, filled by `/setup` |
| [`04-job-evaluation.md`](.deepcode/skills/job-application-assistant/04-job-evaluation.md) | Skill match areas, target domains, location gate |
| [`05-cv-templates.md`](.deepcode/skills/job-application-assistant/05-cv-templates.md) | CV contact block and profile statements |
| [`06-cover-letter-templates.md`](.deepcode/skills/job-application-assistant/06-cover-letter-templates.md) | Cover-letter contact/signature block |
| [`07-interview-prep.md`](.deepcode/skills/job-application-assistant/07-interview-prep.md) | STAR examples |
| [`cv/main_example.tex`](cv/main_example.tex) | Master CV |
| [`search-queries.md`](.deepcode/skills/scrape/search-queries.md) | Markets, queries, portals, filters |

Custom templates and new job boards are supported through `/add-template` and `/add-portal`.

## File structure

```
ai-job-search/
├── DEEPSEEK.md                         # Candidate profile, targets, verification checklist
├── AGENTS.md                           # Deep Code workspace instructions (auto-loaded)
├── DEEPSEEK_QUICKSTART.md              # Copy-paste setup and daily-use cheatsheet
├── .deepcode/
│   ├── settings.json                   # deepseek-v4-pro, 1M context, scoped permissions
│   └── skills/
│       ├── job-application-assistant/  # Core methodology (01-09 + SKILL.md)
│       ├── setup/ scrape/ rank/ apply/
│       ├── interview/ outcome/ gmail-sync/ notion-sync/ html-report/ dashboard/ ui/
│       ├── expand/ upskill/ add-template/ add-portal/ reset/
│       └── posting-fetcher/ posting-scorer/ application-reviewer/
├── cv/main_example.tex                 # Master CV, populated from the resume
├── cover_letters/                      # cover.cls, fonts, example
├── documents/cv/                       # Source resume PDF (gitignored)
├── .agents/skills/                     # Job-portal CLIs
│   ├── linkedin-search/                # Enabled: any market, location per query
│   ├── freehire-search/                # Enabled: tech ATS aggregation
│   └── jobbank/ jobdanmark/ jobindex/ jobnet/  # Danish demos, disabled
├── ui/                                 # Local DuckDB-backed control center
├── data/job_search.duckdb              # UI database (gitignored)
├── job_scraper/seen_jobs.json          # Latest scraped postings (gitignored)
├── job_search_tracker.csv              # Applications tracker (gitignored)
├── tools/                              # PDF verification, posting guard, state helpers
├── .claude/                            # Legacy Claude layout (upstream compatibility only)
└── SETUP.md                            # Detailed setup guide
```

## Security

Job postings, portal output, and web-search results are **untrusted third-party data, never instructions**.
The workflow does not follow instructions embedded in a posting or fetch links found in its body. The
posting-integrity gate (`tools/posting_guard.py`) runs before evaluation. See [`SECURITY.md`](SECURITY.md) for
the full policy, including the Deep Code permission model and the reviewer-pass limitation.

## Upstream and licence

This is a personal DeepSeek/Deep Code port of
[ai-job-search](https://github.com/ai-job-search-dev/ai-job-search). The upstream framework and methodology
are MIT-licensed; the original Claude Code layout is retained under `.claude/` only for upstream
compatibility and is not used by the Deep Code runtime.

Built with [Deep Code](https://github.com/lessweb/deepcode-cli) and
[DeepSeek](https://www.deepseek.com). Job-portal CLI skills adapted from
[Mikkel Krogholm](https://github.com/mikkelkrogsholm). See [`LICENSE`](LICENSE).
