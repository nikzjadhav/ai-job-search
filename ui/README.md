# Job Search OS — local UI

A local-only web control center for scraping roles, filtering the pipeline, and recording
applications. It is served by Python's standard library and uses the DuckDB CLI; there is no Flask,
no FastAPI, no Node server, and no extra Python package.

## Start

```bash
python3 ui/server.py --open
```

Default URL: <http://127.0.0.1:8765/>

```bash
python3 ui/server.py --port 8766          # different port
python3 ui/server.py --db data/other.duckdb
```

Stop with `Ctrl+C`.

## What the buttons do

| Control | Action |
|---|---|
| **Scrape all markets** | LinkedIn + freehire for Malaysia, Singapore, Thailand, UAE, Germany |
| **Scrape selected** | Same, but only the checked markets |
| **Re-import files** | Re-read `seen_jobs.json` and `job_search_tracker.csv` into DuckDB |
| **Mark applied** | Store the posting reference, date, status, notes, CV, and cover letter |
| **AI generate** | Calls DeepSeek with the job and profile, writes `cover_letters/cover_<company>_<role>.tex`, compiles the PDF with `xelatex`, and stores both paths |
| **Cover letter** | Opens an editable textbox prefilled with a model-free draft; saving writes `documents/applications/<company>_<role>/cover_letter.txt` |
| **Status dropdown** | Move an application between applied / interview / offer / hired / rejected |
| **Open report** | Open the static `/dashboard` HTML |

## Data model

Database: `data/job_search.duckdb` (gitignored).

```sql
jobs (
  id, title, company, url, location, country, portal,
  posted_date, first_seen, deadline, fit, status, rank_score,
  applied, applied_date, application_status, notes, updated_at
)

applications (
  id, job_id, company, title, applied_date, status, source,
  cv_file, cover_letter_file, cover_letter_pdf, cover_letter_text,
  notes, deadline, updated_at
)

scrape_runs (
  id, started_at, finished_at, status, markets, result_count, error
)
```

### Write-through

The canonical files are still:

- `job_scraper/seen_jobs.json` — scraped roles
- `job_search_tracker.csv` — applications

DuckDB is the query/index layer. A UI write also updates the canonical file, which is what keeps
`/rank`, `/apply`, `/outcome`, `/html-report`, and `/dashboard` consistent.

### Cover letter textbox and AI generation

**AI generate** calls DeepSeek using the job title, company, location, posting URL, and the candidate
profile. It fetches the posting text where `tools/fetch_page.py` allows it (freehire pages work;
LinkedIn blocks the fetch), writes:

```text
cover_letters/cover_<company>_<role>.tex
cover_letters/cover_<company>_<role>.pdf
```

and stores both paths in DuckDB. The PDF is compiled with `xelatex`; if `xelatex` is missing, the
`.tex` is still saved and the API returns the compile error.

The **Cover letter** button opens an editable textbox. It is prefilled with a personalized,
model-free draft based on the job title, company, and location. Saving the form:

1. stores the full text in DuckDB (`applications.cover_letter_text`);
2. creates `documents/applications/<company>_<role>/`;
3. writes `cover_letter.txt` inside that folder;
4. regenerates `cover_letters/cover_<company>_<role>.tex`;
5. recompiles `cover_letters/cover_<company>_<role>.pdf` with `xelatex`;
6. records the `.tex` path in the tracker CSV (`cover_letter_file`) and both paths in DuckDB.

This is separate from `/apply`, which produces the fully researched LaTeX cover letter under
`cover_letters/cover_<company>_<role>.tex` and compiles it to PDF. Use the textbox for a quick draft
or to keep the exact text you submitted.

## Inspect DuckDB directly

Interactive shell:

```bash
duckdb data/job_search.duckdb
```

Useful commands inside the shell:

```sql
.tables
.schema jobs
.schema applications
SELECT status, count(*) FROM jobs GROUP BY 1 ORDER BY 2 DESC;
SELECT country, count(*) FROM jobs GROUP BY 1 ORDER BY 2 DESC;
SELECT * FROM applications ORDER BY updated_at DESC;
SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 5;
.quit
```

One-off queries without entering the shell:

```bash
duckdb -json -c "SELECT company, title, status, applied_date FROM applications" data/job_search.duckdb
duckdb -csv  -c "SELECT * FROM applications" data/job_search.duckdb > applications.csv
```

DuckDB also ships a built-in web UI if the extension is available:

```bash
duckdb -ui data/job_search.duckdb
```

## Models

The UI does **not** call a language model:

- **Scrape** runs the LinkedIn/freehire CLIs (HTTP only, no tokens).
- **Cover letter** textbox draft is a local template (no tokens).
- **Mark applied** and **status changes** write DuckDB + the tracker CSV.
- **Open report** renders the static dashboard.
- **AI generate** calls DeepSeek with the model and key from `~/.deepcode/settings.json` (or
  `DEEPCODE_API_KEY`). It does not change the configured model.

`/apply` in Deep Code uses the same configured model. Use `/model` inside Deep Code, or edit
`~/.deepcode/settings.json`, to switch between `deepseek-v4-pro` and `deepseek-flash`.

## Is DuckDB lightweight?

Yes, for this use case:

- embedded, no server process, one file
- columnar engine, excellent for the dashboard/filter queries
- zero extra services; scrape and status updates need no API key (AI generation reuses the existing DeepSeek key)
- the UI already has DuckDB installed, so it adds no new dependency

The trade-off is that DuckDB is an analytical database, not a transactional CRUD database. This UI is
single-user and serializes writes through one lock, so that trade-off does not matter here. If you
later want multiple concurrent users, a hosted UI, or many small writes per second, use **SQLite** for
transactional app state, or **PostgreSQL** for multi-user. SQLite would be the smallest swap; the
schema above maps almost one-to-one.

## Security

- Binds to `127.0.0.1` only.
- No authentication: do not expose it through a tunnel or reverse proxy.
- The POST endpoints can scrape and write the tracker; run only on a trusted machine.
- DuckDB contains the same personal data as the tracker and `seen_jobs.json`; it is gitignored.
