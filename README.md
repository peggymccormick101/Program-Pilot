# Program Pilot

A program-management workflow assistant: a defined 5-phase workflow per
project, where each step is either something a person marks done, or
something Claude runs automatically (using data pulled from Jira).

This is a proof of concept. **Phase 1 (Multi-Year Roadmap Planning) is
fully built out; Phases 2–5 show in the UI as upcoming placeholders** —
their task names are visible, nothing in them is wired up yet.

## How it works

Each step in the workflow is a **leaf** in a 3-level tree (Phase → Task →
Sub-step) and unlocks only once the step before it is done:

1. **Manual steps** — a person does the work (often directly in Jira) and
   clicks "Mark complete" to move the workflow forward.
2. **Automated steps** — Claude does the work. Two of Phase 1's steps are
   automated:
   - **Provide Development Capacity** — Claude totals the front-end and
     backend story-point estimates already entered on each Jira Feature
     issue for the release.
   - **Generate Draft Roadmap Options** — Claude analyzes every Feature
     issue (RICE score, estimates, dependencies, confidence) and drafts
     three roadmap options (Highest Product Value / Balanced / Lower
     Risk). The analysis comes back as structured data; the app renders
     it into a consistent, branded Word document every time — Claude
     supplies the thinking, the app supplies the formatting.

Everything else in Phase 1 (defining strategy/stakeholders, creating the
Jira Feature issue, entering estimates/RICE scores/dependencies, and the
final review) is manual — the app tracks that it happened, but the work
itself is done by a person, mostly in Jira.

## Stack

- **Backend:** FastAPI + SQLAlchemy (SQLite) + the Claude API (`anthropic`
  Python SDK, structured JSON output) + Jira Cloud REST API (read-only for
  now) + `python-docx` for the generated roadmap document
- **Frontend:** React (Vite)

## Setup

### Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit .env — see below
.venv/bin/uvicorn app.main:app --reload --port 8000
```

`.env` needs:

- `ANTHROPIC_API_KEY` — required for both automated steps.
- `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` — required for both
  automated steps, since they read Feature issue data from Jira. Create an
  API token at
  [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
  Until these are set, those two steps stay blocked with a clear "Jira
  isn't connected yet" message rather than failing silently.

**One more thing before the automated steps will produce correct
results:** `backend/app/jira_client.py` maps our field names (RICE score,
Feature ID, Prod Mgmt Backend/Frontend Estimate, etc.) to this Jira
instance's custom field IDs, and right now those are placeholders
(`customfield_10001`, `customfield_10002`, ...). Swap in the real IDs from
your Jira instance (Jira admin → Issues → Custom fields, or
`GET /rest/api/3/field`) once we've connected to the real instance.

The API runs at `http://localhost:8000` (docs at `/docs`). It creates
`backend/program_pilot.db` (SQLite) and seeds one demo project + the full
Phase 1 workflow on first run.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Runs at `http://localhost:5173`, proxying `/api` to the backend on port
8000 (see `vite.config.js`).

## API

- `GET /api/workflow` — the full phase tree for the demo project, with
  each step's status (`locked` / `available` / `in_progress` / `complete`)
- `POST /api/workflow/nodes/{id}/complete` — mark a manual step done
- `POST /api/workflow/nodes/{id}/reopen` — undo a completed step
- `POST /api/workflow/nodes/{id}/run` — run an automated step
- `GET /api/files/{file_id}` — download a generated file (the roadmap
  options `.docx`)

## Deployment (Render)

Same single-service pattern as the other apps: one Docker image builds
the React frontend, then FastAPI serves both the API and the built
frontend from one process.

1. Push this repo to GitHub (already done if you're reading this there).
2. On [render.com](https://render.com): **New > Blueprint**, point it at
   this repo — Render reads `render.yaml` and creates the service.
3. Enter `ANTHROPIC_API_KEY`, `JIRA_BASE_URL`, `JIRA_EMAIL`,
   `JIRA_API_TOKEN` (and `ANTHROPIC_WORKSPACE_ID` if needed) as the
   service's environment variables in Render's dashboard.
4. Deploy.

**Note on data:** the free tier's disk is ephemeral, so the workflow's
progress (and any generated roadmap docs) resets on every redeploy —
expected for a POC, worth knowing before you rely on it for anything real.
