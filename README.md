# Content QA Agent

An AI agent that keeps your website's content in shape. It reviews every page, finds what needs fixing and suggests the fix, so your editors only have to say yes or no.

## What it does
- Reads every page from a sitemap
- Runs code checks: alt text, heading order, broken links, meta length
- Uses Claude for what code can't catch: alt text that doesn't match the image, weak meta descriptions, off-brand tone
- Suggests a fix for each problem, which an editor approves or rejects
- Agent mode: chat about a run; Claude reads its pages and findings with tools and proposes exact fixes, which a person approves or rejects
- MCP server: AI assistants such as Claude Desktop or Claude Code can read runs and propose fixes with the same tools, which still wait for a person's review

## Tech stack
<a name="tech-python"><img align="texttop" src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
<a name="tech-fastapi"><img align="texttop" src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"></a>
<a name="tech-sqlalchemy"><img align="texttop" src="https://img.shields.io/badge/SQLAlchemy-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy"></a>
<a name="tech-postgresql"><img align="texttop" src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL"></a>
<a name="tech-typescript"><img align="texttop" src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript"></a>
<a name="tech-react"><img align="texttop" src="https://img.shields.io/badge/React-20232A?style=flat-square&logo=react&logoColor=61DAFB" alt="React"></a>
<a name="tech-next-js"><img align="texttop" src="https://img.shields.io/badge/Next.js-000000?style=flat-square&logo=nextdotjs&logoColor=white" alt="Next.js"></a>
<a name="tech-claude-api"><img align="texttop" src="https://img.shields.io/badge/Claude_API-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude API"></a>
<a name="tech-mcp"><img align="texttop" src="https://img.shields.io/badge/MCP-1A1A1A?style=flat-square&logo=modelcontextprotocol&logoColor=white" alt="MCP"></a>
<a name="tech-langfuse"><img align="texttop" src="https://img.shields.io/badge/Langfuse-0A0A0A?style=flat-square" alt="Langfuse"></a>
<a name="tech-pytest"><img align="texttop" src="https://img.shields.io/badge/pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white" alt="pytest"></a>
<a name="tech-github-actions"><img align="texttop" src="https://img.shields.io/badge/GitHub_Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white" alt="GitHub Actions"></a>
<a name="tech-docker"><img align="texttop" src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker"></a>

## Architecture

Click the diagram to open the interactive version: click a box to see only what touches it, and use the tabs to walk through each flow step by step.

<a href="https://chris-cybula.github.io/content-qa-agent/architecture/"><img src="assets/architecture.svg" alt="Content QA Agent architecture: dashboard, API, checks, agent, MCP server, Claude guard, Postgres, Claude API and Langfuse"></a>

- **Crawl:** `POST /crawl` reads the sitemap, downloads each page, parses it and runs the code checks. Everything is saved as a run.
- **AI checks:** `POST /runs/{id}/ai-checks` sends each page to Claude once (tone, meta wording, headings) and each image once (alt text against the picture). Code runs first, so Claude skips what code already reported. Answers come back as structured JSON and replace the run's earlier AI findings.
- **Claude guard:** the only way the app calls Claude. It prices the worst case of each call, reserves it against the monthly budget before sending, then charges the real cost and logs the call. Every call is traced in Langfuse.
- **Agent mode and MCP:** the chat page and any MCP client use the same four run tools. Both can only propose fixes: a person approves or rejects them on the run page.

## Evals
The checks run on 56 demo pages with a known answer key: 20 clean pages, 30 with planted problems and 6 that hide instructions aimed at the AI. Precision is how many reported problems are real; recall is how many real problems were found. Scores are from the run on Claude Haiku 5.5, the model the app uses.

| Checks | Found | False alarms | Missed | Precision | Recall |
|--------|-------|--------------|--------|-----------|--------|
| Code checks (11 rules) | 15 | 0 | 0 | 100% | 100% |
| AI checks (4 rules) | 23 | 1 | 0 | 96% | 100% |
| All 15 rules | 38 | 1 | 0 | 97% | 100% |

The clean pages got no findings at all, and no hidden instruction changed the results. Every pull request runs the tests and the code-check eval in GitHub Actions; the AI eval runs when a pull request gets the `run-ai-evals` label.

Every saved run, the per-rule scores and what each experiment changed are in [README_results.md](README_results.md).

### Experiments
Each change is measured on the same 56 pages, all 15 rules.

| Change | Precision | Recall | Cost per eval run |
|--------|-----------|--------|-------------------|
| Page prompt v1 → v2: headings judged with the page title around them | 95% → 97% | 100% → 100% | $0.24 → $0.23 |
| Claude checks everything → code checks first: Claude skips what code already found | 88% → 97% | 100% → 100% | $0.25 → $0.23 |
| Claude Opus 5.5 → Claude Haiku 5.5 for both AI checks | 97% → 97% | 100% → 100% | $0.23 → $0.007 |
| Claude Haiku 5.5 → Gemma 3 4B, a free local model through Ollama | 97% → 34% | 100% → 74% | $0.007 → $0 |

Without the code checks first, Claude made 4 more calls (71 instead of 67) and its 5 extra findings all repeated problems code had already reported. Claude's answers also vary a little between runs: one heading was flagged in one run and not in the other.

Haiku matched Opus on every rule for about 3% of the cost. Both made one false alarm, on a different short heading each, and Haiku ignored every hidden instruction too, so both AI checks run on Haiku.

The local model costs nothing but called almost every page off-brand, judged most correct alt text wrong and found none of the unclear headings, so the AI checks stay on Claude.

## Cost and speed
Crawling and the code checks make no AI calls, so they cost nothing. The AI checks make about 1.2 Claude calls per page: one per page and one per image that code didn't already flag.

| Model | Cost per 100 pages | Time per 100 pages |
|-------|--------------------|--------------------|
| Claude Haiku 5.5 (used by the app) | about $0.012 | about 12 minutes |
| Claude Opus 5.5 | about $0.41 | about 12 minutes |

Worked out from the eval runs: 56 pages and 67 calls cost $0.0067 on Haiku and $0.23 on Opus. The eval pages are short, so longer pages cost more. Time is set by the rate limit, 10 calls a minute by default (`CLAUDE_REQUESTS_PER_MINUTE`), not by the model. At Haiku's rate the default $3 monthly budget covers about 25,000 pages. Each chat message in agent mode is a paid call too, with up to 8 tool rounds per answer.

## Use it from an AI assistant (MCP)
`api/app/mcp_server.py` is a stdio MCP server with five tools: `list_runs`, `list_pages`, `get_page`, `get_findings` and `propose_fix`. The last four are the same code agent mode runs. The server makes no Claude calls of its own, so it costs nothing; the assistant brings its own model. There is no tool to approve, reject or publish: every proposed fix waits on the run page for a person.

Claude Code (from the repo root, with Postgres running):
```
claude mcp add content-qa -e PYTHONPATH=api -- api/.venv/Scripts/python.exe -m app.mcp_server
```

Claude Desktop (`claude_desktop_config.json`, with full paths to your clone):
```json
{
  "mcpServers": {
    "content-qa": {
      "command": "C:/path/to/content-qa-agent/api/.venv/Scripts/python.exe",
      "args": ["-m", "app.mcp_server"],
      "env": { "PYTHONPATH": "C:/path/to/content-qa-agent/api" }
    }
  }
}
```

## How to run
You need Docker Desktop, Python 3.12, Node.js 20.9 or later, and an Anthropic API key for the AI checks. Crawling, the code checks, the dashboard and the MCP server work without a key. The commands are for PowerShell, from the repo root.

1. Start Postgres and the demo site (http://localhost:8080):

   ```powershell
   docker compose up -d --wait db demo-site
   ```

2. Set up the API:

   ```powershell
   cd api
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   Copy-Item .env.example .env
   ```

   Put your key after `ANTHROPIC_API_KEY=` in `api/.env`. `api/.env` is git-ignored, and its values win over environment variables.

3. Create the tables, check the key with one small call (skip it without a key), and start the API on http://localhost:8000:

   ```powershell
   .venv\Scripts\python.exe -m alembic upgrade head
   .venv\Scripts\python.exe -m scripts.check_claude
   .venv\Scripts\fastapi.exe dev app\main.py
   ```

4. In a second terminal, start the dashboard on http://localhost:3000:

   ```powershell
   cd web
   npm install
   npm run dev
   ```

5. Open http://localhost:8000/docs and run `POST /crawl` with:

   ```json
   { "sitemap_url": "http://localhost:8080/sitemap.xml" }
   ```

   Then run `POST /runs/{run_id}/ai-checks` with the `run_id` it returned, and open http://localhost:3000 to review the findings, download the report or chat about the run.

Run the evals from `api/`. Without `--ai` only the code checks run, which is free; `--ai` adds the AI checks for about $0.007 on Haiku:

```powershell
.venv\Scripts\python.exe -m evals.run_evals
.venv\Scripts\python.exe -m evals.run_evals --ai --label my-run
```
