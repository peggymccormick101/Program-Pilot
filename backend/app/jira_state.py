"""Persists a program's name and per-release capacity to a Jira Task
issue (one per program). Render's free-tier disk is ephemeral -- the
local SQLite database does not survive an instance restart -- so Jira
is the durable store; the local database is a fast working copy that
gets (re)built from Jira whenever a program is selected or created."""

import json
from datetime import datetime

from app import jira_client, models
from app.seed import build_workflow_tree

# Tracks how far a program has progressed through the per-feature /
# roadmap steps in Define Initial Roadmap, via the "Program State"
# select field on the program's Jira issue -- this is the durable
# record of task completion, so it survives an ephemeral disk wipe the
# same way name and capacity do. Order matters: index N means every
# step up to and including index N is complete. The two earlier Phase 1
# steps (Define Bus Strategy, Define Stakeholders) aren't part of this
# mapping and are not restored on reload.
PROGRAM_STATE_SEQUENCE = [
    ("Initiated", None),
    ("FeaturesInJira", "Create Feature in Jira"),
    ("EstimatesProvided", "Provide Feature Estimates"),
    ("RICEcalculated", "Calculate RICE Score"),
    ("DepsDefined", "Define Inter-Feature Dependencies and Assumptions"),
    ("DevCapProvided", "Provide Per Release Development Capacity"),
    ("RoadmapOptionsProvided", "Generate Draft Roadmap Options"),
    ("RoadmapOptionSelected", "Review, Refine and Select Roadmap Options"),
]
_STATE_NAME_BY_STEP_TITLE = {title: name for name, title in PROGRAM_STATE_SEQUENCE if title}


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


def sync_step_state_to_jira(project: models.Project, step_title: str) -> None:
    """Called whenever a tracked step completes -- advances the
    program's "Program State" field in Jira to match. No-ops for steps
    outside PROGRAM_STATE_SEQUENCE (e.g. Define Bus Strategy)."""
    state_name = _STATE_NAME_BY_STEP_TITLE.get(step_title)
    if not state_name or not project.jira_issue_key:
        return
    jira_client.update_issue(
        project.jira_issue_key,
        {jira_client.PROGRAM_STATE_STATUS_FIELD: {"value": state_name}},
    )


def _apply_program_state(db, project: models.Project, state_value: str | None) -> None:
    """Marks every tracked step up to and including `state_value` as
    complete locally, catching the local workflow tree up to whatever
    Jira says has actually been done."""
    state_names = [name for name, _ in PROGRAM_STATE_SEQUENCE]
    if not state_value or state_value not in state_names:
        return
    target_index = state_names.index(state_value)
    now = datetime.utcnow()
    for _, title in PROGRAM_STATE_SEQUENCE[1:target_index + 1]:
        node = (
            db.query(models.WorkflowNode)
            .filter(models.WorkflowNode.project_id == project.id, models.WorkflowNode.title == title)
            .first()
        )
        if node and not node.completed_at:
            node.completed_at = now


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
    """Selects a program from the picker. Jira is the durable store, and
    can be edited directly there (not just through this app), so name +
    capacity are refreshed from Jira on every select -- even for a
    program already loaded locally this run. Only the workflow tree
    itself (Phase 1 progress) is preserved locally rather than rebuilt,
    since that's not tracked in Jira."""
    issue = jira_client.get_issue(issue_key)
    existing = db.query(models.Project).filter(models.Project.jira_issue_key == issue_key).first()
    if existing:
        existing.name = issue["name"] or existing.name
        existing.selected_at = datetime.utcnow()
        _apply_capacity(db, existing, issue.get("frontend_estimate"), issue.get("backend_estimate"))
        _apply_program_state(db, existing, issue.get("state"))
        db.commit()
        return existing

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
    _apply_program_state(db, project, issue.get("state"))
    db.commit()
    return project


def create_program(db, name: str) -> models.Project:
    """Creates a brand new program: a new Jira Task issue plus a fresh
    local workflow tree for it."""
    project_key = jira_client.DEFAULT_PROJECT_KEY
    issue_key = jira_client.create_issue(
        project_key,
        "Task",
        {
            "summary": name,
            "labels": [jira_client.PROGRAM_STATE_LABEL],
            jira_client.PROGRAM_STATE_STATUS_FIELD: {"value": "Initiated"},
        },
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
