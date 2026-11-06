---
name: dashboard
description: Generate a self-contained offline HTML dashboard of scraped jobs and tracked applications, with market, fit, source, and status charts plus filters. Use when the user wants to view the pipeline, open the dashboard, or check progress.
---

# /dashboard - Job Search Dashboard

Generate a single self-contained HTML file from the scraped-job pipeline and the
application tracker. It opens directly in a browser, works fully offline, and makes no
external requests.

This is broader than `/html-report`: `/html-report` renders the application tracker only,
while `/dashboard` includes every role in `job_scraper/seen_jobs.json` as well as every
tracked application.

## Deep Code runtime mapping

- Run the generator with `bash`; no model calls are needed to build the page.
- `$ARGUMENTS` may contain an output path or `--open`.

## Step 1: Build the dashboard

Run from the repository root:

```bash
python3 tools/build_dashboard.py --open
```

Default output: `reports/dashboard.html`.

With a custom path:

```bash
python3 tools/build_dashboard.py --output reports/dashboard-2026-11-04.html --open
```

If the user does not want the browser opened automatically, omit `--open` and give them the path.

## Step 2: Report what was generated

The script prints a summary line, for example:

```text
dashboard: reports/dashboard.html
  roles=96 eligible=86 high_fit=43 applications=0 interviews=0 offers=0
```

Present:

> **Dashboard generated:** `reports/dashboard.html`
>
> Open it in any browser. It is one offline file, with filters for market, fit, status, and
> source, plus charts for roles by market, fit distribution, sources, and application status.

If there are no applications yet, say so and point to `/apply` and `/outcome`. If there are
no scraped roles, point to `/scrape`.

## Options

| Flag | Meaning |
|---|---|
| `--output PATH` | Write the dashboard somewhere other than `reports/dashboard.html` |
| `--open` | Open the finished file in the default browser |
| `--jobs PATH` | Use a different `seen_jobs.json` |
| `--tracker PATH` | Use a different tracker CSV |

## Data sources

- `job_scraper/seen_jobs.json` — scraped roles, fit labels, portal, posted date, rank score.
- `job_search_tracker.csv` — applications and their statuses.

Both are gitignored personal data. The dashboard reads them; it never writes to either.

## Design rules

- One self-contained HTML file: inline CSS, inline JavaScript, inline SVG charts. No CDN, no
  external fonts, no server.
- Escape every value from the CSV or JSON before putting it in the page.
- Do not invent numbers. Every count comes from the two source files.
- Keep the output idempotent: re-running overwrites the same path.
- Use the candidate's target markets and current profile, not a generic template.
