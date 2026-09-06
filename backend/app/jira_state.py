"""Persists a program's name and per-release capacity to a Jira Task
issue (one per program). Render's free-tier disk is ephemeral -- the
local SQLite database does not survive an instance restart -- so Jira
is the durable store; the local database is a fast working copy that
gets (re)built from Jira whenever a program is selected or created."""

import json
from datetime import datetime

from app import jira_client, models
from app.seed import build_workflow_tree


def sync_program_to_jira(db, project: models.Project, capacity: dict | None = None) -> None:
    """Create the program's Jira Task issue if it doesn't have one yet,
    else update it in place. `capacity` is
    {"total_frontend_days", "total_backend_days"} when known; pass None
    to update only the name (summary) and leave capacity fields as-is."""
    fields = {"summary": project.name}
    if capacity is not None:
        frontend = capacity.get("total_frontend_days")
        backend = capacity.get("total_backend_days")
        fields[jira_client.PROGRAM_STATE_FIELDS["frontend_estimate"]] = frontend
        fields[jira_client.PROGRAM_STATE_FIELDS["backend_estimate"]] = backend
        if frontend is not None and backend is not None:
            fields[jira_client.PROGRAM_STATE_FIELDS["effort_estimate"]] = frontend + backend

    if project.jira_issue_key:
        jira_client.update_issue(project.jira_issue_key, fields)
        return

    create_fields = dict(fields)
    create_fields["labels"] = [jira_client.PROGRAM_STATE_LABEL]
    project_key = project.jira_project_key or jira_client.DEFAULT_PROJECT_KEY
    issue_key = jira_client.create_issue(project_key, "Task", create_fields)
    project.jira_issue_key = issue_key
    db.commit()


def _apply_capacity(db, project: models.Project, frontend, backend) -> None:
    if frontend is None or backend is None:
        return
    capacity_node = (
        db.query(models.WorkflowNode)
        .filter(
            models.WorkflowNode.project_id == project.id,
            models.WorkflowNode.title == "Provide Per Release Development Capacity",
        )
        .first()
    )
    if capacity_node:
        capacity_node.output = json.dumps({
            "total_frontend_days": frontend,
            "total_backend_days": backend,
        })
        capacity_node.completed_at = datetime.utcnow()


def load_program(db, issue_key: str) -> models.Project:
    """Selects a program from the picker. If it's already loaded locally
    this run, just marks it current; otherwise fetches its name and
    capacity from Jira and builds a fresh local workflow tree for it."""
    existing = db.query(models.Project).filter(models.Project.jira_issue_key == issue_key).first()
    if existing:
        existing.selected_at = datetime.utcnow()
        db.commit()
        return existing

    issue = jira_client.get_issue(issue_key)
    project = models.Project(
        name=issue["name"] or issue_key,
        jira_project_key=jira_client.DEFAULT_PROJECT_KEY,
        jira_issue_key=issue_key,
        selected_at=datetime.utcnow(),
    )
    db.add(project)
    db.flush()
    build_workflow_tree(db, project)
    _apply_capacity(db, project, issue.get("frontend_estimate"), issue.get("backend_estimate"))
    db.commit()
    return project


def create_program(db, name: str) -> models.Project:
    """Creates a brand new program: a new Jira Task issue plus a fresh
    local workflow tree for it."""
    project_key = jira_client.DEFAULT_PROJECT_KEY
    issue_key = jira_client.create_issue(
        project_key, "Task", {"summary": name, "labels": [jira_client.PROGRAM_STATE_LABEL]}
    )
    project = models.Project(
        name=name,
        jira_project_key=project_key,
        jira_issue_key=issue_key,
        selected_at=datetime.utcnow(),
    )
    db.add(project)
    db.flush()
    build_workflow_tree(db, project)
    db.commit()
    return project
