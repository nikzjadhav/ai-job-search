#!/usr/bin/env python3
"""Build a self-contained dashboard for scraped jobs and tracked applications.

The existing `html-report` skill renders the application tracker only. This
script renders the whole pipeline: every entry in `job_scraper/seen_jobs.json`
plus every row in `job_search_tracker.csv`. The output is one offline HTML file
with inline CSS, inline JavaScript, and hand-written SVG charts. No CDN, no
external requests, no server.

Usage:
  python3 tools/build_dashboard.py
  python3 tools/build_dashboard.py --output reports/dashboard.html --open
  python3 tools/build_dashboard.py --jobs job_scraper/seen_jobs.json --tracker job_search_tracker.csv
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sys
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOBS = ROOT / "job_scraper" / "seen_jobs.json"
DEFAULT_TRACKER = ROOT / "job_search_tracker.csv"
DEFAULT_OUTPUT = ROOT / "reports" / "dashboard.html"

COUNTRY_ORDER = ["Malaysia", "Singapore", "Thailand", "UAE", "Germany", "Remote", "Other"]
FIT_ORDER = ["high", "medium", "low", "skipped"]
FIT_LABELS = {"high": "High", "medium": "Medium", "low": "Low", "skipped": "Skipped"}
FIT_COLORS = {"high": "#16a34a", "medium": "#d97706", "low": "#dc2626", "skipped": "#64748b"}
PORTAL_LABELS = {
    "linkedin-search": "LinkedIn",
    "freehire-search": "freehire",
    "websearch": "Web search",
}
PORTAL_COLORS = {
    "linkedin-search": "#0a66c2",
    "freehire-search": "#7c3aed",
    "websearch": "#64748b",
}

COUNTRY_HINTS = [
    ("Malaysia", ("malaysia", "kuala lumpur", "selangor", "penang", "johor", "petaling", "shah alam", "george town")),
    ("Singapore", ("singapore",)),
    ("Thailand", ("thailand", "bangkok", "pathum thani", "chiang mai", "phuket")),
    ("UAE", ("united arab emirates", "dubai", "abu dhabi", "sharjah", " ajman", "ae")),
    ("Germany", ("germany", "deutschland", "berlin", "munich", "münchen", "cologne", "köln", "hamburg", "frankfurt", "stuttgart", "düsseldorf", "dusseldorf", "nuremberg", "nürnberg", "karlsruhe", "tübingen", "tubingen", "wiesbaden", "bonn", "dresden", "leipzig", "hannover")),
    ("Remote", ("remote",)),
]


def escape(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def safe_url(value) -> str:
    raw = "" if value is None else str(value).strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return raw
    return ""


def country_for(location: str) -> str:
    low = (location or "").lower()
    for country, hints in COUNTRY_HINTS:
        if any(hint in low for hint in hints):
            return country
    return "Other"


def normalize_fit(value) -> str:
    fit = (value or "medium").strip().lower()
    return fit if fit in FIT_LABELS else "medium"


def normalize_status(value) -> str:
    status = (value or "new").strip().lower()
    return status or "new"


def parse_date(value) -> str:
    raw = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    return ""


def load_jobs(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"build_dashboard: cannot read {path}: {exc}") from exc
    entries = data.get("seen") if isinstance(data, dict) and "seen" in data else data
    if not isinstance(entries, dict):
        raise SystemExit(f"build_dashboard: {path} must contain a 'seen' object")
    jobs = []
    for key, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        location = str(raw.get("location") or "")
        job = {
            "key": key,
            "title": str(raw.get("title") or "Untitled role"),
            "company": str(raw.get("company") or "Unknown company"),
            "url": safe_url(raw.get("url")),
            "location": location,
            "country": country_for(location),
            "posted": parse_date(raw.get("posted_date")),
            "first_seen": parse_date(raw.get("first_seen")),
            "deadline": parse_date(raw.get("deadline")),
            "fit": normalize_fit(raw.get("fit")),
            "status": normalize_status(raw.get("status")),
            "portal": str(raw.get("portal") or "unknown"),
            "rank_score": raw.get("rank_score"),
            "rank_verdict": str(raw.get("rank_verdict") or ""),
        }
        jobs.append(job)
    jobs.sort(key=lambda job: (job["posted"] or job["first_seen"] or "", job["company"].lower()), reverse=True)
    return jobs


def load_tracker(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise SystemExit(f"build_dashboard: cannot read {path}: {exc}") from exc
    return [row for row in rows if any((value or "").strip() for value in row.values())]


def canonical_app_status(status: str) -> str:
    value = (status or "").strip().lower()
    if value == "drafted":
        return "Drafted"
    if value in ("rejected", "no_response", "no response", "offer_declined", "offer declined", "withdrawn"):
        return "Closed"
    if value == "applied":
        return "Active"
    if value == "interview":
        return "Interview"
    if value == "offer":
        return "Offer"
    if value == "hired":
        return "Hired"
    return "Other"


APP_ORDER = ["Drafted", "Active", "Interview", "Offer", "Hired", "Closed", "Other"]
APP_COLORS = {
    "Drafted": "#64748b",
    "Active": "#3b82f6",
    "Interview": "#f59e0b",
    "Offer": "#8b5cf6",
    "Hired": "#22c55e",
    "Closed": "#ef4444",
    "Other": "#94a3b8",
}


def bar_chart(items: list[tuple[str, int, str]], *, width: int = 520, row: int = 30, label_width: int = 100) -> str:
    """Horizontal bar chart as inline SVG. items = [(label, count, color)]."""
    if not items:
        return '<p class="muted">No data yet.</p>'
    height = max(80, 20 + len(items) * row)
    max_count = max((count for _, count, _ in items), default=0) or 1
    bar_area = width - label_width - 60
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Bar chart">']
    for index, (label, count, color) in enumerate(items):
        y = 12 + index * row
        length = max(2, round(bar_area * count / max_count))
        parts.append(f'<text x="0" y="{y + 13}" class="chart-label">{escape(label)}</text>')
        parts.append(f'<rect x="{label_width}" y="{y}" width="{bar_area}" height="18" rx="4" fill="#e2e8f0"/>')
        parts.append(f'<rect x="{label_width}" y="{y}" width="{length}" height="18" rx="4" fill="{color}"/>')
        parts.append(f'<text x="{width - 6}" y="{y + 13}" text-anchor="end" class="chart-value">{count}</text>')
    parts.append("</svg>")
    return "".join(parts)


def doughnut_chart(items: list[tuple[str, int, str]], *, total: int | None = None) -> str:
    """Doughnut chart as inline SVG. items = [(label, count, color)]."""
    if not items or sum(count for _, count, _ in items) == 0:
        return '<p class="muted">No data yet.</p>'
    total = total if total is not None else sum(count for _, count, _ in items)
    radius = 62
    circumference = 2 * math.pi * radius
    offset = 0.0
    parts = [
        '<svg viewBox="0 0 180 180" role="img" aria-label="Doughnut chart">',
        f'<circle cx="90" cy="90" r="{radius}" fill="none" stroke="#e2e8f0" stroke-width="26"/>',
    ]
    for _, count, color in items:
        if count <= 0:
            continue
        length = circumference * count / total
        parts.append(
            f'<circle cx="90" cy="90" r="{radius}" fill="none" stroke="{color}" stroke-width="26" '
            f'stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 90 90)"/>'
        )
        offset += length
    parts.append(f'<text x="90" y="84" text-anchor="middle" class="donut-total">{total}</text>')
    parts.append('<text x="90" y="104" text-anchor="middle" class="donut-caption">total</text>')
    parts.append("</svg>")
    return "".join(parts)


def legend(items: list[tuple[str, int, str]]) -> str:
    if not items:
        return ""
    return '<ul class="legend">' + "".join(
        f'<li><span class="dot" style="background:{color}"></span>{escape(label)} <b>{count}</b></li>'
        for label, count, color in items
    ) + "</ul>"


def stat_card(value: int | str, label: str, accent: str = "#4d6bfe") -> str:
    return (
        f'<div class="stat" style="border-left-color:{accent}">'
        f'<div class="stat-number">{escape(value)}</div><div class="stat-label">{escape(label)}</div></div>'
    )


def fit_badge(fit: str) -> str:
    return f'<span class="badge fit-{escape(fit)}">{escape(FIT_LABELS.get(fit, fit))}</span>'


def status_badge(status: str) -> str:
    return f'<span class="badge status-{escape(status)}">{escape(status)}</span>'


def app_badge(status: str) -> str:
    bucket = canonical_app_status(status)
    return f'<span class="badge app-{escape(bucket.lower())}">{escape(bucket)}</span>'


def job_table(jobs: list[dict]) -> str:
    if not jobs:
        return '<p class="muted">No jobs stored yet. Run <code>/scrape</code> first.</p>'
    rows = []
    for job in jobs:
        title = escape(job["title"])
        if job["url"]:
            title = f'<a href="{escape(job["url"])}" target="_blank" rel="noopener noreferrer">{title}</a>'
        score = job.get("rank_score")
        if isinstance(score, (int, float)):
            score_text = f"{score:.0f}"
        else:
            score_text = "—"
        search_blob = " ".join(
            [job["title"], job["company"], job["location"], job["fit"], job["status"], job["portal"]]
        ).lower()
        rows.append(
            '<tr '
            f'data-country="{escape(job["country"])}" '
            f'data-fit="{escape(job["fit"])}" '
            f'data-status="{escape(job["status"])}" '
            f'data-portal="{escape(job["portal"])}" '
            f'data-search="{escape(search_blob)}">'
            f'<td>{escape(job["posted"] or "—")}</td>'
            f'<td>{title}</td>'
            f'<td>{escape(job["company"])}</td>'
            f'<td>{escape(job["country"])}<br><span class="muted small">{escape(job["location"])}</span></td>'
            f'<td>{fit_badge(job["fit"])}</td>'
            f'<td>{status_badge(job["status"])}</td>'
            f'<td>{escape(PORTAL_LABELS.get(job["portal"], job["portal"]))}</td>'
            f'<td>{score_text}</td>'
            "</tr>"
        )
    return f"""
    <div class="table-wrap">
      <table class="data-table" id="jobs-table">
        <thead><tr><th>Posted</th><th>Role</th><th>Company</th><th>Market</th><th>Fit</th><th>Status</th><th>Source</th><th>Score</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    <p class="muted small" id="jobs-count">{len(jobs)} roles</p>
    """


def application_table(rows: list[dict]) -> str:
    if not rows:
        return (
            '<div class="empty-state">'
            "<h3>No applications tracked yet</h3>"
            "<p>Run <code>/apply &lt;job URL&gt;</code> to draft and record an application. "
            "Use <code>/outcome</code> when you get a response.</p>"
            "</div>"
        )
    body = []
    for row in rows:
        source = safe_url(row.get("source"))
        link = (
            f'<a href="{escape(source)}" target="_blank" rel="noopener noreferrer">Open</a>'
            if source
            else "—"
        )
        body.append(
            "<tr>"
            f'<td>{escape(row.get("date") or "—")}</td>'
            f'<td>{escape(row.get("company") or "—")}</td>'
            f'<td>{escape(row.get("role") or "—")}</td>'
            f'<td>{escape(row.get("sector") or "—")}</td>'
            f'<td>{app_badge(row.get("status") or "")}</td>'
            f'<td>{escape(row.get("fit_rating") or "—")}</td>'
            f'<td>{escape(row.get("deadline") or "—")}</td>'
            f'<td>{link}</td>'
            "</tr>"
        )
    return f"""
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Date</th><th>Company</th><th>Role</th><th>Sector</th><th>Status</th><th>Fit</th><th>Deadline</th><th>Source</th></tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table>
    </div>
    <p class="muted small">{len(rows)} applications</p>
    """


def build(jobs_path: Path, tracker_path: Path, output: Path) -> dict:
    jobs = load_jobs(jobs_path)
    applications = load_tracker(tracker_path)

    eligible = [job for job in jobs if job["status"] not in ("skipped", "expired")]
    high = [job for job in eligible if job["fit"] == "high"]
    by_country = Counter(job["country"] for job in jobs)
    by_fit = Counter(job["fit"] for job in jobs)
    by_portal = Counter(job["portal"] for job in jobs)
    by_app = Counter(canonical_app_status(row.get("status") or "") for row in applications)

    country_items = [
        (country, by_country.get(country, 0), "#4d6bfe")
        for country in COUNTRY_ORDER
        if by_country.get(country, 0)
    ]
    fit_items = [(FIT_LABELS[fit], by_fit.get(fit, 0), FIT_COLORS[fit]) for fit in FIT_ORDER if by_fit.get(fit, 0)]
    portal_items = [
        (PORTAL_LABELS.get(portal, portal.title()), count, PORTAL_COLORS.get(portal, "#64748b"))
        for portal, count in by_portal.most_common()
    ]
    app_items = [(bucket, by_app.get(bucket, 0), APP_COLORS[bucket]) for bucket in APP_ORDER if by_app.get(bucket, 0)]

    interviews = sum(by_app.get(bucket, 0) for bucket in ("Interview", "Offer", "Hired"))
    offers = sum(by_app.get(bucket, 0) for bucket in ("Offer", "Hired"))
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Search Dashboard</title>
<style>
  :root {{
    --ink: #0f172a; --muted: #64748b; --line: #e2e8f0; --bg: #f8fafc;
    --card: #ffffff; --accent: #4d6bfe; --accent-soft: #eef2ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
         color: var(--ink); background: var(--bg); line-height: 1.5; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .hero {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 55%, #4d6bfe 100%);
           color: #fff; padding: 34px clamp(18px, 5vw, 54px) 30px; }}
  .hero h1 {{ margin: 0 0 6px; font-size: clamp(24px, 3vw, 36px); letter-spacing: -0.02em; }}
  .hero p {{ margin: 0; color: #c7d2fe; }}
  .hero .meta {{ margin-top: 12px; font-size: 13px; color: #a5b4fc; }}
  main {{ max-width: 1240px; margin: 0 auto; padding: 24px clamp(14px, 3vw, 30px) 50px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-top: -34px; }}
  .stat {{ background: var(--card); border-radius: 12px; padding: 16px 18px; box-shadow: 0 8px 24px rgba(15,23,42,.07);
           border-left: 4px solid var(--accent); }}
  .stat-number {{ font-size: 28px; font-weight: 750; line-height: 1.1; }}
  .stat-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .06em; margin-top: 4px; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 18px; }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px; box-shadow: 0 2px 8px rgba(15,23,42,.03); }}
  .card h2 {{ margin: 0 0 12px; font-size: 15px; letter-spacing: .01em; }}
  .card h3 {{ margin: 0 0 6px; font-size: 14px; }}
  .chart-label {{ font-size: 11px; fill: #334155; }}
  .chart-value {{ font-size: 11px; fill: #0f172a; font-weight: 700; }}
  .donut-total {{ font-size: 26px; font-weight: 750; fill: #0f172a; }}
  .donut-caption {{ font-size: 10px; fill: #64748b; text-transform: uppercase; letter-spacing: .08em; }}
  .legend {{ list-style: none; padding: 0; margin: 10px 0 0; display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 12px; color: var(--muted); }}
  .legend b {{ color: var(--ink); }}
  .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; }}
  .muted {{ color: var(--muted); }}
  .small {{ font-size: 12px; }}
  .filters {{ display: grid; grid-template-columns: 2fr repeat(4, minmax(120px, 1fr)); gap: 8px; margin: 14px 0 10px; }}
  .filters input, .filters select {{ width: 100%; padding: 9px 10px; border: 1px solid var(--line); border-radius: 8px;
                                    background: #fff; color: var(--ink); font: inherit; font-size: 13px; }}
  .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }}
  .data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; min-width: 860px; }}
  .data-table th {{ text-align: left; background: #f1f5f9; color: #475569; font-size: 11px; text-transform: uppercase;
                    letter-spacing: .05em; padding: 10px 12px; position: sticky; top: 0; }}
  .data-table td {{ padding: 10px 12px; border-top: 1px solid var(--line); vertical-align: top; }}
  .data-table td:first-child, .data-table td:nth-child(8) {{ white-space: nowrap; }}
  .data-table tbody tr:nth-child(even) {{ background: #fbfdff; }}
  .data-table tbody tr:hover {{ background: var(--accent-soft); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 650; white-space: nowrap; }}
  .fit-high {{ background: #dcfce7; color: #15803d; }}
  .fit-medium {{ background: #fef3c7; color: #b45309; }}
  .fit-low {{ background: #fee2e2; color: #b91c1c; }}
  .fit-skipped {{ background: #e2e8f0; color: #475569; }}
  .status-new {{ background: #dbeafe; color: #1d4ed8; }}
  .status-ranked {{ background: #ede9fe; color: #6d28d9; }}
  .status-skipped {{ background: #e2e8f0; color: #475569; }}
  .status-expired {{ background: #fee2e2; color: #b91c1c; }}
  .status-unreachable {{ background: #ffedd5; color: #c2410c; }}
  .app-drafted {{ background: #e2e8f0; color: #475569; }}
  .app-active {{ background: #dbeafe; color: #1d4ed8; }}
  .app-interview {{ background: #fef3c7; color: #b45309; }}
  .app-offer {{ background: #ede9fe; color: #6d28d9; }}
  .app-hired {{ background: #dcfce7; color: #15803d; }}
  .app-closed {{ background: #fee2e2; color: #b91c1c; }}
  .app-other {{ background: #f1f5f9; color: #475569; }}
  .empty-state {{ border: 1px dashed #cbd5e1; border-radius: 12px; padding: 22px; text-align: center; color: var(--muted); }}
  .empty-state h3 {{ color: var(--ink); margin-top: 0; }}
  .section {{ margin-top: 18px; }}
  footer {{ margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }}
  @media (max-width: 900px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .filters {{ grid-template-columns: 1fr 1fr; }}
    .filters input {{ grid-column: 1 / -1; }}
  }}
  @media print {{
    body {{ background: #fff; }}
    .hero {{ background: #0f172a !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .filters {{ display: none; }}
  }}
</style>
</head>
<body>
<header class="hero">
  <h1>Job Search Dashboard</h1>
  <p>Scraped roles and applications · target markets are configured in the profile</p>
  <div class="meta">Generated {escape(generated)} · {len(jobs)} scraped roles · {len(applications)} tracked applications</div>
</header>
<main>
  <section class="stats">
    {stat_card(len(jobs), "Roles found")}
    {stat_card(len(eligible), "Eligible", "#0ea5e9")}
    {stat_card(len(high), "High fit", "#16a34a")}
    {stat_card(len(applications), "Applications", "#8b5cf6")}
    {stat_card(interviews, "Interviews+", "#f59e0b")}
    {stat_card(offers, "Offers", "#22c55e")}
  </section>

  <section class="grid">
    <div class="card">
      <h2>Roles by market</h2>
      {bar_chart(country_items)}
    </div>
    <div class="card">
      <h2>Fit distribution</h2>
      <div class="chart-flex">
        {doughnut_chart(fit_items)}
        {legend(fit_items)}
      </div>
    </div>
    <div class="card">
      <h2>Sources</h2>
      {bar_chart(portal_items)}
    </div>
    <div class="card">
      <h2>Applications by status</h2>
      <div class="chart-flex">
        {doughnut_chart(app_items)}
        {legend(app_items)}
      </div>
    </div>
  </section>

  <section class="card section">
    <h2>Scraped roles</h2>
    <div class="filters">
      <input id="search" type="search" placeholder="Search title, company, location…" aria-label="Search roles">
      <select id="filter-country" aria-label="Filter by market"><option value="">All markets</option>{''.join(f'<option value="{escape(c)}">{escape(c)}</option>' for c in COUNTRY_ORDER if by_country.get(c, 0))}</select>
      <select id="filter-fit" aria-label="Filter by fit"><option value="">All fit</option>{''.join(f'<option value="{fit}">{escape(FIT_LABELS[fit])}</option>' for fit in FIT_ORDER if by_fit.get(fit, 0))}</select>
      <select id="filter-status" aria-label="Filter by status"><option value="">All status</option>{''.join(f'<option value="{escape(s)}">{escape(s)}</option>' for s in sorted({j["status"] for j in jobs}))}</select>
      <select id="filter-portal" aria-label="Filter by source"><option value="">All sources</option>{''.join(f'<option value="{escape(p)}">{escape(PORTAL_LABELS.get(p, p))}</option>' for p in sorted({j["portal"] for j in jobs}))}</select>
    </div>
    {job_table(jobs)}
  </section>

  <section class="card section">
    <h2>Applications</h2>
    {application_table(applications)}
  </section>

  <footer>
    Generated by <code>tools/build_dashboard.py</code> · single self-contained HTML file · no external requests.
    Data sources: <code>job_scraper/seen_jobs.json</code> and <code>job_search_tracker.csv</code>.
  </footer>
</main>
<script>
  const rows = Array.from(document.querySelectorAll('#jobs-table tbody tr'));
  const search = document.getElementById('search');
  const country = document.getElementById('filter-country');
  const fit = document.getElementById('filter-fit');
  const status = document.getElementById('filter-status');
  const portal = document.getElementById('filter-portal');
  const count = document.getElementById('jobs-count');

  function applyFilters() {{
    const q = (search.value || '').toLowerCase().trim();
    let visible = 0;
    for (const row of rows) {{
      const matches =
        (!q || (row.dataset.search || '').includes(q)) &&
        (!country.value || row.dataset.country === country.value) &&
        (!fit.value || row.dataset.fit === fit.value) &&
        (!status.value || row.dataset.status === status.value) &&
        (!portal.value || row.dataset.portal === portal.value);
      row.style.display = matches ? '' : 'none';
      if (matches) visible++;
    }}
    if (count) count.textContent = visible + ' role' + (visible === 1 ? '' : 's');
  }}

  for (const el of [search, country, fit, status, portal]) {{
    el.addEventListener('input', applyFilters);
    el.addEventListener('change', applyFilters);
  }}
  applyFilters();
</script>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return {
        "output": output,
        "jobs": len(jobs),
        "eligible": len(eligible),
        "high": len(high),
        "applications": len(applications),
        "interviews": interviews,
        "offers": offers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the job-search dashboard")
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    parser.add_argument("--tracker", type=Path, default=DEFAULT_TRACKER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    args = parser.parse_args(argv)

    result = build(args.jobs, args.tracker, args.output)
    print(f"dashboard: {result['output']}")
    print(
        f"  roles={result['jobs']} eligible={result['eligible']} high_fit={result['high']} "
        f"applications={result['applications']} interviews={result['interviews']} offers={result['offers']}"
    )
    if args.open:
        webbrowser.open(result["output"].resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
