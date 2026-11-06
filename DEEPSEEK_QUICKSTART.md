# DeepSeek Quick Start

This repository runs on **DeepSeek** through **Deep Code**, the open-source terminal agent
optimized for DeepSeek V4. Every workflow is a Deep Code skill, invoked exactly like a slash
command (`/setup`, `/scrape`, `/apply`, ...). No Anthropic subscription or Claude Code install is
required.

## 1. One-time setup (about 10 minutes)

### 1.1 Install Deep Code

Requires Node.js 18+ (22+ recommended).

```bash
npm install -g @vegamo/deepcode-cli
deepcode --version
```

### 1.2 Add your DeepSeek API key

Create a key at <https://platform.deepseek.com/api_keys>, add a small balance, then either export
the key:

```bash
# Put this in ~/.zshrc or ~/.bashrc to persist it
export DEEPCODE_API_KEY="sk-..."
```

or write the full config to `~/.deepcode/settings.json`:

```json
{
  "env": {
    "MODEL": "deepseek-v4-pro",
    "BASE_URL": "https://api.deepseek.com",
    "API_KEY": "sk-..."
  },
  "thinkingEnabled": true,
  "reasoningEffort": "high"
}
```

The repository's [`.deepcode/settings.json`](.deepcode/settings.json) already sets
`deepseek-v4-pro`, a 1M context window, and scoped permissions. It contains **no API key** and is
safe to commit.

### 1.3 Install Bun (needed by the job-portal CLIs)

```bash
# macOS / Linux
curl -fsSL https://bun.sh/install | bash

# Windows PowerShell
powershell -ExecutionPolicy Bypass -c "irm https://bun.sh/install.ps1 | iex"
```

### 1.4 Install LaTeX (needed to compile CVs and cover letters)

- macOS: [MacTeX](https://tug.org/mactex/) (large) or [TinyTeX](https://yihui.org/tinytex/) (small)
- Windows: [MiKTeX](https://miktex.org/download)
- Linux: `sudo apt install texlive-full` (or your distribution's equivalent)

The CV compiles with `lualatex`; the cover letter compiles with `xelatex`. Both are included in
the distributions above.

For the visual PDF check, install Poppler as well:

```bash
brew install poppler            # macOS
sudo apt install poppler-utils  # Debian/Ubuntu
```

`pdftoppm` renders each page to PNG for inspection, and `pdftotext` is the ATS-extraction fallback.

### 1.5 Install the portal CLIs

```bash
cd ~/Desktop/projects/ai-job-search
python3 tools/install_portal_clis.py
```

`linkedin-search` and `freehire-search` have zero runtime dependencies and work with plain `bun`;
this step only installs dev types for them.

### 1.6 Verify the port

```bash
python3 tools/verify_deepseek_port.py
```

It checks every skill's frontmatter, the settings file, and that no runtime skill still points at
`.claude/`.

## 2. Launch

```bash
cd ~/Desktop/projects/ai-job-search
deepcode
```

Inside the TUI:

| Command | What it does |
|---|---|
| `/` | Open the skills/commands menu |
| `/skills` | List every discovered skill |
| `/setup` | Build your candidate profile |
| `/scrape` | Find matching jobs |
| `/rank` | Batch-score a scrape into a shortlist |
| `/apply <URL or posting>` | Tailor, compile, and verify an application |
| `/interview` | Build a prep pack and run a mock interview |
| `/outcome` | Record results and draft follow-ups |
| `/dashboard` | Build and open the offline job-search dashboard |
| `/ui` | Launch the local DuckDB-backed scrape and application control center |
| `/model` | Switch between `deepseek-v4-pro` and `deepseek-flash` |
| `/plan` | Enter plan mode before a large change |
| `/new` | Start a fresh conversation |
| `/resume` | Resume a previous session |

The first thing to run is:

```text
/setup
```

## 3. Daily use

### Search for jobs

```text
/scrape
```

Then shortlist the results:

```text
/rank
```

### Apply to a job

```text
/apply https://jobindex.dk/job/1234567
```

Or paste the posting:

```text
/apply <paste the full job description here>
```

Deep Code has no subagent tool, so the drafter-reviewer workflow runs the reviewer as a separate,
read-only pass in the same session. The workflow still compiles both PDFs, renders them for a
visual check, runs the layout checks, and runs the ATS text-layer check before presenting the
result.

### Evaluate fit only

```text
Read .deepcode/skills/job-application-assistant/04-job-evaluation.md
and evaluate my fit for this role: <paste posting>
```

### Interview prep

```text
/interview
```

### Control center (UI)

```text
/ui
```

This opens `http://127.0.0.1:8765/` with scrape buttons, a filterable roles table, application tracking, and an editable cover-letter textbox. **AI generate** on a role writes `cover_letters/cover_<company>_<role>.tex` and compiles the PDF with `xelatex`; the textbox draft saves into `documents/applications/<company>_<role>/cover_letter.txt`. DuckDB stores the UI database; every write also updates the canonical JSON/CSV. No DeepSeek API key is needed for the UI.

### Dashboard

```text
/dashboard
```

This writes `reports/dashboard.html`: scraped roles, applications, market/fit/source charts, and filters. It is one offline file and needs no server.

### Upskill

```text
/upskill
```

### Compile documents manually

```bash
cd cv && lualatex main_<company>_<role>.tex && cd ..
cd cover_letters && xelatex cover_<company>_<role>.tex && cd ..
```

## 4. Choosing a model

| Model | Input (cache miss) | Output | Use it for |
|---|---|---|---|
| `deepseek-flash` | $0.15–0.30 / 1M | $0.60–1.20 / 1M | `/scrape`, `/rank`, bulk triage, cheap drafts |
| `deepseek-v4-pro` | $0.66–1.32 / 1M | $1.98–3.96 / 1M | `/apply`, `/interview`, final quality |

Prices are DeepSeek's published per-million-token rates; off-peak is roughly half of peak. A full
`/apply` run is typically cents, not dollars. Use `/model` to switch per session, or edit
`.deepcode/settings.json` to change the default.

## 5. Cost control

- Default the project to `deepseek-flash` and switch to `deepseek-v4-pro` only for `/apply` and
  `/interview`.
- `/rank` already batches work; do not re-run it on the whole backlog with `--all` unless the
  profile changed.
- DeepSeek caches repeated prompt prefixes automatically; keeping `AGENTS.md` and the profile
  stable makes cache hits more likely.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `deepcode: command not found` | Re-run `npm install -g @vegamo/deepcode-cli`; check `npm bin -g` is on `PATH`. |
| `401` / authentication error | Check `DEEPCODE_API_KEY` or `~/.deepcode/settings.json`; make sure the key has balance. |
| No skills in `/skills` | Run `deepcode` from the repository root, and confirm `.deepcode/skills/*/SKILL.md` exists. |
| `bun: command not found` | Install Bun (step 1.3); the core `/apply` workflow still works without it, but `/scrape` falls back to web search. |
| `lualatex: command not found` | Install a LaTeX distribution (step 1.4) and restart the terminal. |
| PDF layout check fails | Read the rendered PNGs in `/tmp/ai-job-search/`, then apply `\needspace` / `\enlargethispage` as the workflow says. |
| `read-image` says images are unsupported | `deepseek-v4-pro` has no vision. Run `/model`, switch to `deepseek-flash` for the PDF inspection, then switch back. |
| Permission prompt on every fetch | Confirm `.deepcode/settings.json` allows `network`; accept the scope once if prompted. |

## 7. Where things live

- `DEEPSEEK.md` - your profile and verification checklist
- `.deepcode/skills/` - all Deep Code workflows
- `.deepcode/settings.json` - model, context, permissions (no API key)
- `.claude/` - legacy upstream layout, not used by Deep Code
- `AGENTS.md` - workspace instructions loaded automatically by Deep Code

For the full project overview, see [README.md](README.md). For a detailed setup walkthrough
including LaTeX packages, see [SETUP.md](SETUP.md).
