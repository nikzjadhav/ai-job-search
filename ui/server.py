#!/usr/bin/env python3
"""Local web control center for the job search, backed by DuckDB.

The UI is served by Python's standard library. DuckDB is used through its CLI
(`duckdb -json`), so no Python DuckDB package or web framework is required.

Canonical data stays in the files the Deep Code skills already use:
  * `job_scraper/seen_jobs.json`  - scraped roles
  * `job_search_tracker.csv`      - applications

DuckDB is the UI's query/index store. Every write goes back to those files, so
`/rank`, `/apply`, and `/outcome` keep working unchanged.

Usage:
  python3 ui/server.py --open
  python3 ui/server.py --port 8765
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "job_search.duckdb"
JOBS_JSON = ROOT / "job_scraper" / "seen_jobs.json"
TRACKER = ROOT / "job_search_tracker.csv"
DASHBOARD = ROOT / "reports" / "dashboard.html"

DUCKDB = shutil.which("duckdb") or str(Path.home() / ".local" / "bin" / "duckdb")
BUN = shutil.which("bun")


def _find_xelatex() -> str:
    candidates = [
        shutil.which("xelatex"),
        str(Path.home() / "Library" / "TinyTeX" / "bin" / "universal-darwin" / "xelatex"),
        "/Library/TeX/texbin/xelatex",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return ""


XELATEX = _find_xelatex()
TEX_BIN = str(Path(XELATEX).parent) if XELATEX else ""

DB_LOCK = threading.Lock()
SCRAPE_LOCK = threading.Lock()
SCRAPE_RUNNING = False

STANDARD_HEADER = [
    "date", "company", "sector", "role", "role_type", "channel", "status",
    "contact_person", "fit_rating", "notes", "cv_file", "cover_letter_file",
    "source", "deadline",
]

MARKETS = [
    {"name": "Malaysia", "linkedin": "Malaysia", "code": "MY"},
    {"name": "Singapore", "linkedin": "Singapore", "code": "SG"},
    {"name": "Thailand", "linkedin": "Thailand", "code": "TH"},
    {"name": "UAE", "linkedin": "United Arab Emirates", "code": "AE"},
    {"name": "Germany", "linkedin": "Germany", "code": "DE"},
]
MARKET_BY_NAME = {m["name"]: m for m in MARKETS}

COUNTRY_HINTS = [
    ("Malaysia", ("malaysia", "kuala lumpur", "selangor", "penang", "johor", "petaling", "shah alam", "george town")),
    ("Singapore", ("singapore",)),
    ("Thailand", ("thailand", "bangkok", "pathum thani", "chiang mai", "phuket")),
    ("UAE", ("united arab emirates", "dubai", "abu dhabi", "sharjah", "ajman")),
    ("Germany", ("germany", "deutschland", "berlin", "munich", "münchen", "cologne", "köln", "hamburg", "frankfurt", "stuttgart", "düsseldorf", "dusseldorf", "nuremberg", "nürnberg", "karlsruhe", "tübingen", "tubingen", "wiesbaden", "bonn", "dresden", "leipzig", "hannover")),
    ("Remote", ("remote",)),
]

FIT_LABELS = {"high": "High", "medium": "Medium", "low": "Low", "skipped": "Skipped"}
APP_STATUSES = {"drafted", "applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined", "withdrawn"}


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_iso() -> str:
    return date.today().isoformat()


def normalize_date(value) -> str | None:
    raw = ("" if value is None else str(value)).strip()
    return raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else None


def country_for(location: str) -> str:
    low = (location or "").lower()
    for country, hints in COUNTRY_HINTS:
        if any(hint in low for hint in hints):
            return country
    return "Other"


def sql_lit(value) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    if text == "":
        return "NULL"
    return "'" + text.replace("'", "''") + "'"


def sql_num(value) -> str:
    try:
        return repr(float(value))
    except (TypeError, ValueError):
        return "NULL"


def sql_bool(value) -> str:
    return "TRUE" if value else "FALSE"


def run_sql(sql: str, *, expect_json: bool = True, timeout: int = 60):
    """Run one SQL statement (or semicolon-separated batch) via the DuckDB CLI."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    cmd = [DUCKDB, "-no-init", "-no-agent", "-batch"]
    if expect_json:
        cmd.append("-json")
    cmd.extend(["-c", sql, str(DB_PATH)])
    with DB_LOCK:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "duckdb failed").strip())
    if not expect_json:
        return proc.stdout
    text = proc.stdout.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"duckdb returned non-JSON output: {text[:400]}") from exc


def init_db() -> None:
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id VARCHAR PRIMARY KEY,
            title VARCHAR NOT NULL,
            company VARCHAR NOT NULL,
            url VARCHAR,
            location VARCHAR,
            country VARCHAR,
            portal VARCHAR,
            posted_date DATE,
            first_seen DATE,
            deadline DATE,
            fit VARCHAR,
            status VARCHAR,
            rank_score DOUBLE,
            applied BOOLEAN DEFAULT FALSE,
            applied_date DATE,
            application_status VARCHAR,
            notes VARCHAR,
            updated_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS applications (
            id VARCHAR PRIMARY KEY,
            job_id VARCHAR,
            company VARCHAR,
            title VARCHAR,
            applied_date DATE,
            status VARCHAR,
            source VARCHAR,
            cv_file VARCHAR,
            cover_letter_file VARCHAR,
            cover_letter_pdf VARCHAR,
            cover_letter_text VARCHAR,
            notes VARCHAR,
            deadline DATE,
            updated_at TIMESTAMP
        );
        ALTER TABLE applications ADD COLUMN IF NOT EXISTS cover_letter_text VARCHAR;
        ALTER TABLE applications ADD COLUMN IF NOT EXISTS cover_letter_pdf VARCHAR;
        CREATE TABLE IF NOT EXISTS scrape_runs (
            id VARCHAR PRIMARY KEY,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            status VARCHAR,
            markets VARCHAR,
            result_count INTEGER DEFAULT 0,
            error VARCHAR
        );
        """,
        expect_json=False,
    )


def load_jobs_json() -> list[dict]:
    if not JOBS_JSON.is_file():
        return []
    try:
        data = json.loads(JOBS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("seen") if isinstance(data, dict) and "seen" in data else data
    if not isinstance(entries, dict):
        return []
    jobs = []
    for key, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        jobs.append(
            {
                "id": key,
                "title": str(raw.get("title") or "Untitled role"),
                "company": str(raw.get("company") or "Unknown company"),
                "url": str(raw.get("url") or ""),
                "location": str(raw.get("location") or ""),
                "country": country_for(str(raw.get("location") or "")),
                "portal": str(raw.get("portal") or "unknown"),
                "posted_date": normalize_date(raw.get("posted_date")),
                "first_seen": normalize_date(raw.get("first_seen")),
                "deadline": normalize_date(raw.get("deadline")),
                "fit": str(raw.get("fit") or "medium").lower(),
                "status": str(raw.get("status") or "new").lower(),
                "rank_score": raw.get("rank_score"),
            }
        )
    return jobs


def import_jobs() -> int:
    jobs = load_jobs_json()
    if not jobs:
        return 0
    statements = []
    for job in jobs:
        statements.append(
            "INSERT INTO jobs (id,title,company,url,location,country,portal,posted_date,first_seen,deadline,fit,status,rank_score,updated_at) VALUES ("
            f"{sql_lit(job['id'])},{sql_lit(job['title'])},{sql_lit(job['company'])},{sql_lit(job['url'])},"
            f"{sql_lit(job['location'])},{sql_lit(job['country'])},{sql_lit(job['portal'])},"
            f"{sql_lit(job['posted_date'])},{sql_lit(job['first_seen'])},{sql_lit(job['deadline'])},"
            f"{sql_lit(job['fit'])},{sql_lit(job['status'])},{sql_num(job.get('rank_score'))},now()) "
            "ON CONFLICT (id) DO UPDATE SET "
            "title=excluded.title, company=excluded.company, url=excluded.url, location=excluded.location, "
            "country=excluded.country, portal=excluded.portal, posted_date=excluded.posted_date, "
            "first_seen=excluded.first_seen, deadline=excluded.deadline, fit=excluded.fit, "
            "status=excluded.status, rank_score=excluded.rank_score, updated_at=now()"
        )
    for index in range(0, len(statements), 80):
        run_sql(";\n".join(statements[index : index + 80]) + ";", expect_json=False)
    return len(jobs)


def read_tracker() -> tuple[list[str], list[dict]]:
    if not TRACKER.is_file():
        return list(STANDARD_HEADER), []
    with TRACKER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or STANDARD_HEADER)
        rows = [row for row in reader if any((value or "").strip() for value in row.values())]
    if "deadline" not in header:
        header.append("deadline")
    return header, rows


def tracker_id(company: str, role: str) -> str:
    digest = hashlib.sha1(f"{company.strip().lower()}|{role.strip().lower()}".encode("utf-8")).hexdigest()[:16]
    return f"tracker:{digest}"


def import_tracker() -> int:
    header, rows = read_tracker()
    if not rows:
        return 0
    jobs = {f"{j['company'].strip().lower()}|{j['title'].strip().lower()}": j["id"] for j in load_jobs_json()}
    statements = []
    for row in rows:
        company = (row.get("company") or "").strip()
        role = (row.get("role") or "").strip()
        if not company and not role:
            continue
        job_id = jobs.get(f"{company.lower()}|{role.lower()}")
        app_id = f"app:{job_id}" if job_id else tracker_id(company, role)
        status = (row.get("status") or "applied").strip().lower()
        applied = normalize_date(row.get("date"))
        deadline = normalize_date(row.get("deadline"))
        statements.append(
            "INSERT INTO applications (id,job_id,company,title,applied_date,status,source,cv_file,cover_letter_file,notes,deadline,updated_at) VALUES ("
            f"{sql_lit(app_id)},{sql_lit(job_id)},{sql_lit(company)},{sql_lit(role)},"
            f"{sql_lit(applied)},{sql_lit(status)},{sql_lit(row.get('source'))},"
            f"{sql_lit(row.get('cv_file'))},{sql_lit(row.get('cover_letter_file'))},"
            f"{sql_lit(row.get('notes'))},{sql_lit(deadline)},now()) "
            "ON CONFLICT (id) DO UPDATE SET job_id=excluded.job_id, company=excluded.company, title=excluded.title, "
            "applied_date=excluded.applied_date, status=excluded.status, source=excluded.source, "
            "cv_file=excluded.cv_file, cover_letter_file=excluded.cover_letter_file, notes=excluded.notes, "
            "deadline=excluded.deadline, updated_at=now()"
        )
        if job_id:
            statements.append(
                "UPDATE jobs SET applied=" + sql_bool(status != "drafted") + ", applied_date="
                f"{sql_lit(applied)}, application_status={sql_lit(status)}, "
                f"notes=CASE WHEN notes IS NULL OR notes='' THEN {sql_lit(row.get('notes'))} ELSE notes END, "
                f"updated_at=now() WHERE id={sql_lit(job_id)}"
            )
    for index in range(0, len(statements), 80):
        run_sql(";\n".join(statements[index : index + 80]) + ";", expect_json=False)
    return len(rows)


def sanitize_note(value: str) -> str:
    text = (value or "").replace(",", ";").replace('"', "").replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", text).strip()


def fit_rating(job: dict) -> str:
    return {"high": "5", "medium": "3", "low": "1"}.get(str(job.get("fit") or "").lower(), "")


def write_tracker_row(job: dict, *, status: str, applied_date: str, notes: str, cv_file: str, cover_letter_file: str) -> None:
    header, rows = read_tracker()
    company = (job.get("company") or "").strip()
    role = (job.get("title") or "").strip()
    clean_notes = sanitize_note(notes)
    match = None
    for row in rows:
        if (row.get("company") or "").strip().lower() == company.lower() and (row.get("role") or "").strip().lower() == role.lower():
            match = row
            break
    if match is None:
        match = {key: "" for key in header}
        match.update(
            {
                "date": applied_date,
                "company": company,
                "sector": "",
                "role": role,
                "role_type": "",
                "channel": "portal",
                "status": status,
                "contact_person": "",
                "fit_rating": fit_rating(job),
                "notes": clean_notes,
                "cv_file": cv_file,
                "cover_letter_file": cover_letter_file,
                "source": job.get("url") or "",
                "deadline": job.get("deadline") or "",
            }
        )
        rows.append(match)
    else:
        old_notes = sanitize_note(match.get("notes") or "")
        match["status"] = status
        if applied_date and not match.get("date"):
            match["date"] = applied_date
        if clean_notes:
            match["notes"] = (old_notes + " | " if old_notes else "") + clean_notes
        if cv_file:
            match["cv_file"] = cv_file
        if cover_letter_file:
            match["cover_letter_file"] = cover_letter_file
        if not match.get("source"):
            match["source"] = job.get("url") or ""
        if not match.get("deadline"):
            match["deadline"] = job.get("deadline") or ""
    TRACKER.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(TRACKER.parent), prefix=".tracker.", suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in header})
        os.replace(temp_name, TRACKER)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def get_job(job_id: str) -> dict | None:
    rows = run_sql(
        "SELECT id,title,company,url,location,country,portal,CAST(posted_date AS VARCHAR) AS posted_date,"
        "CAST(first_seen AS VARCHAR) AS first_seen,CAST(deadline AS VARCHAR) AS deadline,fit,status,rank_score,"
        "applied,CAST(applied_date AS VARCHAR) AS applied_date,application_status,notes "
        f"FROM jobs WHERE id={sql_lit(job_id)} LIMIT 1"
    )
    return rows[0] if rows else None


def slugify(value: str) -> str:
    text = (value or "").lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return re.sub(r"_+", "_", text).strip("_")


def application_folder(job: dict) -> Path:
    company = slugify(job.get("company") or "")
    role = slugify(job.get("title") or "")
    name = f"{company}_{role}".strip("_") or "unknown_application"
    return ROOT / "documents" / "applications" / name


def save_cover_letter_text(job: dict, text: str) -> str:
    """Write the textbox content into the per-application folder.

    The folder follows documents/README.md's `applications/<company>_<role>/`
    rule. Returns the repo-relative path stored in the tracker and DuckDB.
    """
    folder = application_folder(job)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "cover_letter.txt"
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return str(path.relative_to(ROOT))


def draft_cover_letter(job: dict) -> str:
    """A model-free first draft; the user edits it in the UI before saving."""
    title = job.get("title") or "the role"
    company = job.get("company") or "your team"
    location = job.get("location") or ""
    where = f" in {location}" if location else ""
    return f"""Dear Hiring Manager,

I am writing to apply for the {title} role at {company}{where}. [Add two or three sentences about your strongest and most relevant experience for this role.]

[Add a paragraph with one or two concrete achievements, including metrics where possible. Keep every claim grounded in your profile.]

[Add a short paragraph explaining why this company and role interest you. Reference something specific and verified.]

I would welcome the opportunity to discuss how I can contribute to {company}.

Sincerely,
[YOUR NAME]
[YOUR EMAIL]
[YOUR PHONE]
[YOUR LINKEDIN]
"""


def latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def clean_cover_text(text: str) -> str:
    """Normalize model output into plain paragraphs for the textbox/LaTeX."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = text.replace("`", "")
    lines = [line.rstrip() for line in text.splitlines()]
    # Remove a salutation the model sometimes adds; the LaTeX wrapper adds one.
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().lower().startswith("dear "):
        lines.pop(0)
    # Drop a model-added closing/signature block.
    for index, line in enumerate(lines):
        if line.strip().lower() in ("sincerely,", "kind regards,", "best regards,", "yours sincerely,"):
            lines = lines[:index]
            break
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_cover_tex(company: str, role: str, body: str) -> str:
    safe_company = latex_escape(company)
    safe_role = latex_escape(role)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    body_tex = "\n\n".join(r"\lettercontent{" + latex_escape(p).replace("\n", " ") + "}" for p in paragraphs)
    return rf"""%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% Cover Letter - {safe_company}, {safe_role}
% Generated by the local Job Search OS
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

\documentclass[]{{cover}}
\usepackage{{fancyhdr}}

\pagestyle{{fancy}}
\fancyhf{{}}
\rfoot{{Page \thepage \hspace{{0pt}}}}
\thispagestyle{{empty}}
\renewcommand{{\headrulewidth}}{{0pt}}
\begin{{document}}

\namesection{{}}{{\Huge{{[YOUR NAME]}}}}{{  \href{{mailto:your.email@example.com}}{{your.email@example.com}} | +XX XXXXXXXXXX |  \urlstyle{{same}}\href{{https://linkedin.com/in/your-profile}}{{LinkedIn}}
}}

\currentdate{{\today}}
\lettercontent{{Dear Hiring Manager,}}

\lettercontent{{Re: {safe_role} at {safe_company}}}

{body_tex}

\begin{{flushright}}
\closing{{Kind regards,}}

\signature{{[YOUR NAME]}}
\end{{flushright}}
\end{{document}}
"""


def read_api_config() -> tuple[str, str, str]:
    settings_path = Path.home() / ".deepcode" / "settings.json"
    config = {}
    try:
        config = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    env = config.get("env") or {}
    key = os.environ.get("DEEPCODE_API_KEY") or env.get("API_KEY") or ""
    base = env.get("BASE_URL") or "https://api.deepseek.com"
    model = env.get("MODEL") or config.get("model") or "deepseek-v4-pro"
    return key, base.rstrip("/"), model


def fetch_job_text(url: str) -> str:
    """Best-effort description fetch through the robots-aware helper."""
    if not url or "linkedin.com" in url:
        return ""
    try:
        with tempfile.NamedTemporaryFile(prefix="cover-job-", suffix=".html", delete=False) as handle:
            out_path = handle.name
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "fetch_page.py"), url, "--out", out_path, "--text", "--max-chars", "12000"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        Path(out_path).unlink(missing_ok=True)
        if proc.returncode != 0:
            return ""
        text = proc.stdout
        marker = "--- text ---"
        return text.split(marker, 1)[1].strip() if marker in text else text.strip()
    except Exception:
        return ""


def call_deepseek_cover_letter(job: dict, description: str = "") -> str:
    key, base, model = read_api_config()
    if not key:
        raise RuntimeError("No DeepSeek API key found in ~/.deepcode/settings.json or DEEPCODE_API_KEY")
    profile_path = ROOT / ".deepcode" / "skills" / "job-application-assistant" / "01-candidate-profile.md"
    profile = profile_path.read_text(encoding="utf-8")[:14000] if profile_path.is_file() else ""
    job_facts = (
        f"Role: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nPosting URL: {job.get('url')}"
    )
    if description:
        job_facts += f"\n\nPosting text:\n{description[:12000]}"
    system = (
        "You write concise, specific cover letters for a senior data engineer. "
        "Use only facts from the candidate profile and the job information. "
        "Never invent experience, metrics, or company claims. "
        "Write 3 to 4 plain-text paragraphs, 250-350 words total. "
        "Do not include a salutation, date, address, signature, markdown, or bullet points. "
        "No em dashes. Return only the body paragraphs."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"CANDIDATE PROFILE:\n{profile}\n\nJOB:\n{job_facts}"},
        ],
        "temperature": 0.35,
        "max_tokens": 4096,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DeepSeek request failed: {exc.reason}") from exc
    try:
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek returned an unexpected response") from exc
    cleaned = clean_cover_text(content)
    if not cleaned:
        reason = choice.get("finish_reason") or "unknown"
        usage = data.get("usage") or {}
        raise RuntimeError(
            f"DeepSeek returned an empty cover letter (finish={reason}, "
            f"reasoning_tokens={(usage.get('completion_tokens_details') or {}).get('reasoning_tokens')})"
        )
    return cleaned


def compile_cover_letter(tex_path: Path) -> tuple[str, str]:
    if not XELATEX:
        return "", "xelatex not found; .tex saved but PDF was not compiled"
    env = dict(os.environ)
    if TEX_BIN:
        env["PATH"] = TEX_BIN + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [XELATEX, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=str(tex_path.parent),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except Exception as exc:
        return "", str(exc)[:500]
    if proc.returncode != 0:
        log_tail = (proc.stderr or proc.stdout or "")[-800:]
        return "", log_tail or "xelatex failed"
    pdf_path = tex_path.with_suffix(".pdf")
    for suffix in (".aux", ".log", ".out"):
        tex_path.with_suffix(suffix).unlink(missing_ok=True)
    return str(pdf_path.relative_to(ROOT)), ""


def persist_cover_letter(job: dict, text: str) -> dict:
    """Save textbox text to the app folder and regenerate .tex/.pdf."""
    folder_file = save_cover_letter_text(job, text)
    company_slug = slugify(job.get("company") or "")
    role_slug = slugify(job.get("title") or "")
    filename = f"cover_{company_slug}_{role_slug}".strip("_") or "cover_application"
    tex_path = ROOT / "cover_letters" / f"{filename}.tex"
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(build_cover_tex(job.get("company") or "", job.get("title") or "", text), encoding="utf-8")
    pdf_file, compile_error = compile_cover_letter(tex_path)
    return {
        "tex_file": str(tex_path.relative_to(ROOT)),
        "pdf_file": pdf_file,
        "folder_file": folder_file,
        "compiled": bool(pdf_file),
        "error": compile_error,
    }


def generate_cover_letter(job_id: str, description: str = "") -> dict:
    job = get_job(job_id)
    if not job:
        raise ValueError("job not found")
    if not description:
        description = fetch_job_text(job.get("url") or "")
    text = call_deepseek_cover_letter(job, description)
    company_slug = slugify(job.get("company") or "")
    role_slug = slugify(job.get("title") or "")
    filename = f"cover_{company_slug}_{role_slug}".strip("_") or "cover_application"
    tex_path = ROOT / "cover_letters" / f"{filename}.tex"
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(build_cover_tex(job.get("company") or "", job.get("title") or "", text), encoding="utf-8")
    pdf_file, compile_error = compile_cover_letter(tex_path)
    folder_file = save_cover_letter_text(job, text)
    app_id = f"app:{job_id}"
    existing = run_sql(f"SELECT id FROM applications WHERE job_id={sql_lit(job_id)} LIMIT 1")
    if existing:
        app_id = existing[0]["id"]
    run_sql(
        "INSERT INTO applications (id,job_id,company,title,applied_date,status,source,cv_file,cover_letter_file,cover_letter_pdf,cover_letter_text,notes,deadline,updated_at) VALUES ("
        f"{sql_lit(app_id)},{sql_lit(job_id)},{sql_lit(job['company'])},{sql_lit(job['title'])},"
        f"{sql_lit(today_iso())},'drafted',{sql_lit(job.get('url'))},NULL,"
        f"{sql_lit(str(tex_path.relative_to(ROOT)))},{sql_lit(pdf_file)},{sql_lit(text)},"
        f"'AI-generated cover letter',{sql_lit(job.get('deadline'))},now()) "
        "ON CONFLICT (id) DO UPDATE SET cover_letter_file=excluded.cover_letter_file, "
        "cover_letter_pdf=excluded.cover_letter_pdf, cover_letter_text=excluded.cover_letter_text, "
        "notes=excluded.notes, updated_at=now()",
        expect_json=False,
    )
    write_tracker_row(
        job,
        status="drafted",
        applied_date=today_iso(),
        notes="AI-generated cover letter",
        cv_file="",
        cover_letter_file=str(tex_path.relative_to(ROOT)),
    )
    return {
        "text": text,
        "tex_file": str(tex_path.relative_to(ROOT)),
        "pdf_file": pdf_file,
        "folder_file": folder_file,
        "compiled": bool(pdf_file),
        "error": compile_error,
        "application_id": app_id,
    }


def mark_applied(payload: dict) -> dict:
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    job = get_job(job_id)
    if not job:
        raise ValueError("job not found")
    status = str(payload.get("status") or "applied").strip().lower()
    if status not in APP_STATUSES:
        status = "applied"
    applied_date = normalize_date(payload.get("applied_date")) or today_iso()
    notes = str(payload.get("notes") or "")
    cv_file = str(payload.get("cv_file") or "")
    cover_letter_text = str(payload.get("cover_letter_text") or "").strip()
    cover_letter_file = str(payload.get("cover_letter_file") or "")
    cover_letter_pdf = ""
    cover_error = ""
    if cover_letter_text:
        paths = persist_cover_letter(job, cover_letter_text)
        cover_letter_file = paths["tex_file"]
        cover_letter_pdf = paths["pdf_file"]
        cover_error = paths["error"]
    existing = run_sql(
        f"SELECT id FROM applications WHERE job_id={sql_lit(job_id)} LIMIT 1"
    )
    app_id = existing[0]["id"] if existing else f"app:{job_id}"
    run_sql(
        "INSERT INTO applications (id,job_id,company,title,applied_date,status,source,cv_file,cover_letter_file,cover_letter_pdf,cover_letter_text,notes,deadline,updated_at) VALUES ("
        f"{sql_lit(app_id)},{sql_lit(job_id)},{sql_lit(job['company'])},{sql_lit(job['title'])},"
        f"{sql_lit(applied_date)},{sql_lit(status)},{sql_lit(job.get('url'))},"
        f"{sql_lit(cv_file)},{sql_lit(cover_letter_file)},{sql_lit(cover_letter_pdf)},{sql_lit(cover_letter_text)},{sql_lit(notes)},{sql_lit(job.get('deadline'))},now()) "
        "ON CONFLICT (id) DO UPDATE SET applied_date=excluded.applied_date, status=excluded.status, "
        "source=excluded.source, cv_file=excluded.cv_file, cover_letter_file=excluded.cover_letter_file, "
        "cover_letter_pdf=excluded.cover_letter_pdf, cover_letter_text=excluded.cover_letter_text, "
        "notes=excluded.notes, deadline=excluded.deadline, updated_at=now()",
        expect_json=False,
    )
    run_sql(
        "UPDATE jobs SET applied=" + sql_bool(status != "drafted") + ", applied_date="
        f"{sql_lit(applied_date)}, application_status={sql_lit(status)}, notes={sql_lit(notes)}, updated_at=now() "
        f"WHERE id={sql_lit(job_id)}",
        expect_json=False,
    )
    write_tracker_row(
        job,
        status=status,
        applied_date=applied_date,
        notes=notes,
        cv_file=cv_file,
        cover_letter_file=cover_letter_file,
    )
    return {
        "ok": True,
        "job_id": job_id,
        "application_id": app_id,
        "cover_letter_file": cover_letter_file,
        "cover_letter_pdf": cover_letter_pdf,
        "cover_error": cover_error,
    }


def update_application(payload: dict) -> dict:
    app_id = str(payload.get("id") or "").strip()
    if not app_id:
        raise ValueError("application id is required")
    rows = run_sql(f"SELECT id,job_id,company,title,source,notes,cover_letter_text,cover_letter_file,CAST(applied_date AS VARCHAR) AS applied_date,status FROM applications WHERE id={sql_lit(app_id)} LIMIT 1")
    if not rows:
        raise ValueError("application not found")
    app = rows[0]
    status = str(payload.get("status") or app.get("status") or "applied").strip().lower()
    if status not in APP_STATUSES:
        status = "applied"
    notes = str(payload.get("notes") if payload.get("notes") is not None else app.get("notes") or "")
    cover_text = str(
        payload.get("cover_letter_text")
        if payload.get("cover_letter_text") is not None
        else app.get("cover_letter_text") or ""
    ).strip()
    cover_file = str(app.get("cover_letter_file") or "")
    cover_pdf = ""
    if cover_text:
        paths = persist_cover_letter(app, cover_text)
        cover_file = paths["tex_file"]
        cover_pdf = paths["pdf_file"]
    run_sql(
        "UPDATE applications SET status="
        f"{sql_lit(status)}, notes={sql_lit(notes)}, cover_letter_text={sql_lit(cover_text)}, "
        f"cover_letter_file={sql_lit(cover_file)}, cover_letter_pdf={sql_lit(cover_pdf)}, "
        f"updated_at=now() WHERE id={sql_lit(app_id)}",
        expect_json=False,
    )
    if app.get("job_id"):
        run_sql(
            "UPDATE jobs SET applied=" + sql_bool(status != "drafted") + ", application_status="
            f"{sql_lit(status)}, notes={sql_lit(notes)}, updated_at=now() WHERE id={sql_lit(app['job_id'])}",
            expect_json=False,
        )
    job = {
        "company": app.get("company") or "",
        "title": app.get("title") or "",
        "url": app.get("source") or "",
        "deadline": "",
        "fit": "",
    }
    if app.get("job_id"):
        full = get_job(app["job_id"])
        if full:
            job.update({"url": full.get("url") or job["url"], "deadline": full.get("deadline") or "", "fit": full.get("fit") or ""})
    write_tracker_row(
        job,
        status=status,
        applied_date=app.get("applied_date") or today_iso(),
        notes=notes,
        cv_file="",
        cover_letter_file=cover_file,
    )
    return {
        "ok": True,
        "application_id": app_id,
        "status": status,
        "cover_letter_file": cover_file,
        "cover_letter_pdf": cover_pdf,
    }


def get_state() -> dict:
    jobs = run_sql(
        "SELECT id,title,company,url,location,country,portal,CAST(posted_date AS VARCHAR) AS posted_date,"
        "CAST(first_seen AS VARCHAR) AS first_seen,CAST(deadline AS VARCHAR) AS deadline,fit,status,rank_score,"
        "applied,CAST(applied_date AS VARCHAR) AS applied_date,application_status,notes "
        "FROM jobs ORDER BY COALESCE(posted_date, first_seen) DESC NULLS LAST, company"
    )
    applications = run_sql(
        "SELECT a.id,a.job_id,a.company,a.title,CAST(a.applied_date AS VARCHAR) AS applied_date,a.status,"
        "a.source,a.cv_file,a.cover_letter_file,a.cover_letter_pdf,a.cover_letter_text,a.notes,CAST(a.deadline AS VARCHAR) AS deadline,j.url AS job_url "
        "FROM applications a LEFT JOIN jobs j ON a.job_id=j.id "
        "ORDER BY COALESCE(a.applied_date, DATE '1900-01-01') DESC, a.company"
    )
    runs = run_sql(
        "SELECT id,CAST(started_at AS VARCHAR) AS started_at,CAST(finished_at AS VARCHAR) AS finished_at,"
        "status,markets,result_count,error FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
    )
    total = len(jobs)
    eligible = sum(1 for job in jobs if job.get("status") not in ("skipped", "expired"))
    high = sum(1 for job in jobs if job.get("fit") == "high" and job.get("status") not in ("skipped", "expired"))
    app_statuses = [str(app.get("status") or "").lower() for app in applications]
    interviews = sum(1 for status in app_statuses if status in ("interview", "offer", "hired"))
    offers = sum(1 for status in app_statuses if status in ("offer", "hired"))
    by_country = {}
    for job in jobs:
        by_country[job.get("country") or "Other"] = by_country.get(job.get("country") or "Other", 0) + 1
    by_fit = {}
    for job in jobs:
        by_fit[job.get("fit") or "medium"] = by_fit.get(job.get("fit") or "medium", 0) + 1
    by_portal = {}
    for job in jobs:
        by_portal[job.get("portal") or "unknown"] = by_portal.get(job.get("portal") or "unknown", 0) + 1
    apps_by_status = {}
    for status in app_statuses:
        apps_by_status[status] = apps_by_status.get(status, 0) + 1
    return {
        "jobs": jobs,
        "applications": applications,
        "stats": {
            "total": total,
            "eligible": eligible,
            "high": high,
            "applications": len(applications),
            "interviews": interviews,
            "offers": offers,
        },
        "by_country": by_country,
        "by_fit": by_fit,
        "by_portal": by_portal,
        "apps_by_status": apps_by_status,
        "last_scrape": runs[0] if runs else None,
        "meta": {
            "duckdb": DUCKDB,
            "bun": bool(BUN),
            "db": str(DB_PATH),
            "jobs_json": str(JOBS_JSON),
            "tracker": str(TRACKER),
            "markets": [m["name"] for m in MARKETS],
        },
    }


def fit_for_job(title: str, company: str, location: str) -> str:
    text = f"{title} {company} {location}".lower()
    if re.search(r"\b(junior|associate|intern|graduate|trainee)\b", title.lower()):
        return "low"
    if any(word in text for word in ("mandarin", "emirati", "thai & english", "thai and english")):
        return "low"
    if re.search(r"\b(senior|lead|staff|principal|architect)\b", title.lower()) and re.search(
        r"(data engineer|data platform|big data|streaming|lakehouse|analytics engineer|data architect)", title.lower()
    ):
        return "high"
    if re.search(r"(data engineer|data platform|big data|streaming|lakehouse|analytics engineer|data architect)", title.lower()):
        return "medium"
    return "low"


def run_portal(kind: str, market: dict) -> list[dict]:
    if kind == "linkedin":
        script = ROOT / ".agents" / "skills" / "linkedin-search" / "cli" / "src" / "cli.ts"
        args = ["search", "-q", "data engineer", "-l", market["linkedin"], "--jobage", "14", "--limit", "15", "--format", "json"]
    else:
        script = ROOT / ".agents" / "skills" / "freehire-search" / "cli" / "src" / "cli.ts"
        args = [
            "search", "-q", "data engineer OR senior data engineer OR big data engineer OR data platform engineer",
            "--country", market["code"], "--jobage", "14", "--limit", "25", "--no-description", "--format", "json",
        ]
    if not script.is_file():
        raise RuntimeError(f"portal CLI missing: {script}")
    if BUN:
        cmd = [BUN, "run", str(script), *args]
    else:
        cmd = ["npx", "-y", "tsx", str(script), *args]
    env = dict(os.environ)
    env.setdefault("npm_config_cache", "/tmp/npm-cache")
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "portal CLI failed").strip()[:400])
    text = proc.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"portal CLI returned non-JSON output: {text[:300]}") from exc
    return data.get("results", []) if isinstance(data, dict) else []


def store_scrape_results(results: list[dict]) -> None:
    if not results:
        return
    fd, temp_name = tempfile.mkstemp(prefix="ai-job-search-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(results, handle)
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / "scrape_state.py"), "store", "--results", temp_name],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        Path(temp_name).unlink(missing_ok=True)


def run_scrape(run_id: str, markets: list[str]) -> None:
    global SCRAPE_RUNNING
    collected = []
    errors = []
    try:
        for market_name in markets:
            market = MARKET_BY_NAME.get(market_name)
            if not market:
                continue
            for kind in ("linkedin", "freehire"):
                try:
                    rows = run_portal(kind, market)
                except Exception as exc:  # noqa: BLE001 - surface per-portal failure
                    errors.append(f"{market_name}/{kind}: {exc}")
                    continue
                for row in rows:
                    title = str(row.get("title") or "").strip()
                    company = str(row.get("company") or "").strip()
                    if not title or not company:
                        continue
                    location = str(row.get("location") or market_name)
                    url = str(row.get("url") or "")
                    if kind == "freehire" and row.get("id"):
                        url = f"https://freehire.me/jobs/{row['id']}"
                    collected.append(
                        {
                            "title": title,
                            "company": company,
                            "url": url,
                            "location": location,
                            "first_seen": today_iso(),
                            "posted_date": normalize_date(row.get("date")) or today_iso(),
                            "deadline": None,
                            "fit": fit_for_job(title, company, location),
                            "status": "new",
                            "portal": "linkedin-search" if kind == "linkedin" else "freehire-search",
                            "source": "cli",
                        }
                    )
        # Deduplicate within this run by company + title, preferring the first.
        unique = {}
        for job in collected:
            unique.setdefault((job["company"].lower(), job["title"].lower()), job)
        store_scrape_results(list(unique.values()))
        imported = import_jobs()
        import_tracker()
        status = "completed" if not errors else "completed_with_errors"
        error_text = " | ".join(errors)[:1000] if errors else None
        run_sql(
            "UPDATE scrape_runs SET finished_at=now(), status="
            f"{sql_lit(status)}, result_count={len(unique)}, error={sql_lit(error_text)} WHERE id={sql_lit(run_id)}",
            expect_json=False,
        )
        _ = imported
    except Exception as exc:  # noqa: BLE001 - keep the UI alive and report the error
        run_sql(
            "UPDATE scrape_runs SET finished_at=now(), status='failed', error="
            f"{sql_lit(str(exc)[:1000])} WHERE id={sql_lit(run_id)}",
            expect_json=False,
        )
    finally:
        with SCRAPE_LOCK:
            SCRAPE_RUNNING = False


def start_scrape(markets: list[str]) -> dict:
    global SCRAPE_RUNNING
    with SCRAPE_LOCK:
        if SCRAPE_RUNNING:
            raise ValueError("a scrape is already running")
        SCRAPE_RUNNING = True
    run_id = f"run:{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_sql(
        "INSERT INTO scrape_runs (id,started_at,status,markets,result_count) VALUES ("
        f"{sql_lit(run_id)},now(),'running',{sql_lit(','.join(markets))},0)",
        expect_json=False,
    )
    thread = threading.Thread(target=run_scrape, args=(run_id, markets), daemon=True)
    thread.start()
    return {"run_id": run_id, "markets": markets}


class Handler(BaseHTTPRequestHandler):
    server_version = "AIJobSearchUI/1.0"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        # Keep the console usable while the scrape subprocesses log to stderr.
        sys.stderr.write("[ui] " + (format % args) + "\n")

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._send_bytes((UI_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/app.js":
            return self._send_bytes((UI_DIR / "app.js").read_bytes(), "text/javascript; charset=utf-8")
        if path == "/styles.css":
            return self._send_bytes((UI_DIR / "styles.css").read_bytes(), "text/css; charset=utf-8")
        if path == "/dashboard":
            if DASHBOARD.is_file():
                return self._send_bytes(DASHBOARD.read_bytes(), "text/html; charset=utf-8")
            return self._send_json({"error": "dashboard not generated yet; run /dashboard or tools/build_dashboard.py"}, 404)
        if path == "/api/health":
            return self._send_json({"ok": True, "duckdb": DUCKDB, "db": str(DB_PATH)})
        if path == "/api/state":
            try:
                return self._send_json(get_state())
            except Exception as exc:  # noqa: BLE001
                return self._send_json({"error": str(exc)}, 500)
        if path == "/api/scrape/status":
            try:
                runs = run_sql(
                    "SELECT id,CAST(started_at AS VARCHAR) AS started_at,CAST(finished_at AS VARCHAR) AS finished_at,"
                    "status,markets,result_count,error FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
                )
                return self._send_json({"running": SCRAPE_RUNNING, "last": runs[0] if runs else None})
            except Exception as exc:  # noqa: BLE001
                return self._send_json({"error": str(exc)}, 500)
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = urlsplit(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/scrape":
                markets = payload.get("markets") or [m["name"] for m in MARKETS]
                if not isinstance(markets, list):
                    raise ValueError("markets must be a list")
                markets = [m for m in markets if m in MARKET_BY_NAME]
                if not markets:
                    raise ValueError("no valid markets selected")
                return self._send_json(start_scrape(markets), 202)
            if path == "/api/cover-letter/draft":
                job_id = str(payload.get("job_id") or "").strip()
                job = get_job(job_id)
                if not job:
                    raise ValueError("job not found")
                return self._send_json({"text": draft_cover_letter(job)})
            if path == "/api/cover-letter/generate":
                job_id = str(payload.get("job_id") or "").strip()
                description = str(payload.get("job_description") or "")
                return self._send_json(generate_cover_letter(job_id, description))
            if path == "/api/apply":
                return self._send_json(mark_applied(payload))
            if path == "/api/application":
                return self._send_json(update_application(payload))
            if path == "/api/refresh":
                imported = import_jobs()
                tracked = import_tracker()
                return self._send_json({"ok": True, "jobs": imported, "applications": tracked, "state": get_state()})
            return self._send_json({"error": "not found"}, 404)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": str(exc)}, 500)


def main(argv: list[str] | None = None) -> int:
    global DB_PATH
    parser = argparse.ArgumentParser(description="AI Job Search local UI (DuckDB-backed)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    DB_PATH = args.db

    init_db()
    import_jobs()
    import_tracker()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"AI Job Search UI: {url}")
    print(f"DuckDB: {DB_PATH}")
    print(f"Bun: {'found' if BUN else 'not found (using npx tsx fallback)'}")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
