---
name: ui
description: Launch the local Job Search OS web UI for scraping roles, viewing the DuckDB pipeline, and recording applications. Use when the user wants buttons for scrape, a job table, or a browser control center.
---

# /ui - Local Job Search OS

Start the local web control center. It serves a browser UI for scraping, filtering roles, and
recording applications in DuckDB.

## Deep Code runtime mapping

- Run the server with `bash`.
- `$ARGUMENTS` may contain `--port <n>` or `--open`.
- The UI is optional; the CLI skills in `.deepcode/skills/` remain the primary workflow.

## Step 1: Start the server

```bash
python3 ui/server.py --open
```

Default URL: <http://127.0.0.1:8765/>

With a different port:

```bash
python3 ui/server.py --port 8766 --open
```

Leave the process running while you use the UI. Stop it with `Ctrl+C`.

## Step 2: Use the UI

- **Scrape all markets / Scrape selected** — runs LinkedIn and freehire for Malaysia, Singapore,
  Thailand, UAE, and Germany. Results are written to `job_scraper/seen_jobs.json` and imported into
  DuckDB.
- **Roles** — search and filter by market, fit, status, and source. **AI generate** calls DeepSeek
  and writes `cover_letters/cover_<company>_<role>.tex` plus a compiled `.pdf`. **Mark applied**
  records the application reference in DuckDB and writes the tracker CSV. **Cover letter** opens an
  editable textbox and saves `documents/applications/<company>_<role>/cover_letter.txt`, then
  regenerates `cover_letters/cover_<company>_<role>.tex` and `.pdf`.
- **Applications** — change status; the tracker CSV and DuckDB stay in sync.
- **Open report** — opens the static `/dashboard` output.

## Data model

DuckDB file: `data/job_search.duckdb` (gitignored).

- `jobs` — scraped roles, fit, status, rank score, applied state.
- `applications` — one row per application, including the posting reference, date, status, notes,
  CV file, cover-letter file, and deadline.
- `scrape_runs` — scrape history and errors.

Canonical data remains:

- `job_scraper/seen_jobs.json`
- `job_search_tracker.csv`

Every UI write goes back to those files, so `/rank`, `/apply`, and `/outcome` keep working.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `duckdb: command not found` | Install the DuckDB CLI or set `DUCKDB` to its path. |
| `Address already in use` | Use `--port 8766`. |
| Scrape slow | Bun is missing, so the server uses `npx -y tsx`; install Bun for faster runs. |
| No roles | Run a scrape or check `job_scraper/seen_jobs.json`. |
| API key error | The UI does not need an API key; only the DeepSeek skills do. |
