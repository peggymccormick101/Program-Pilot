"""Persists a program's name and per-release capacity to a Jira Task
issue (one per program). Render's free-tier disk is ephemeral -- the
local SQLite database does not survive an instance restart -- so Jira
is the durable store; the local database is just a fast working copy
that gets rehydrated from Jira on boot (see bootstrap_project below)."""

from app import jira_client, models


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
    issue_key = jira_client.create_issue(project.jira_project_key or "PB", "Task", create_fields)
    project.jira_issue_key = issue_key
    db.commit()


def bootstrap_project(db) -> models.Project | None:
    """Called on boot when the local database has no Project row (a
    fresh/wiped ephemeral disk). Looks for an existing program-state
    issue in Jira and rehydrates a Project + its capacity from it.
    Returns the rehydrated Project, or None if Jira isn't configured or
    has no such issue yet -- the caller falls back to seeding a blank
    demo program in that case."""
    from app.seed import PHASE_1_TITLE, PHASE_1_TASKS, PLACEHOLDER_PHASES, _insert_children
    import json

    project_key = "PB"
    try:
        found = jira_client.find_program_state_issue(project_key)
    except (jira_client.JiraNotConfiguredError, jira_client.JiraRequestError):
        return None
    if not found:
        return None

    project = models.Project(
        name=found["name"] or "Demo Program",
        jira_project_key=project_key,
        jira_issue_key=found["issue_key"],
    )
    db.add(project)
    db.flush()

    phase_1 = models.WorkflowNode(
        project_id=project.id, parent_id=None, phase_number=1, order_index=0,
        title=PHASE_1_TITLE, is_leaf=False,
    )
    db.add(phase_1)
    db.flush()
    _insert_children(db, project, phase_1, 1, PHASE_1_TASKS)

    for phase_number, phase_title, task_titles in PLACEHOLDER_PHASES:
        phase_node = models.WorkflowNode(
            project_id=project.id, parent_id=None, phase_number=phase_number,
            order_index=phase_number - 1, title=phase_title, is_leaf=False,
        )
        db.add(phase_node)
        db.flush()
        for index, task_title in enumerate(task_titles):
            db.add(models.WorkflowNode(
                project_id=project.id, parent_id=phase_node.id, phase_number=phase_number,
                order_index=index, title=task_title, is_leaf=True, automation_type=None,
            ))

    frontend = found.get("frontend_estimate")
    backend = found.get("backend_estimate")
    if frontend is not None and backend is not None:
        capacity_node = (
            db.query(models.WorkflowNode)
            .filter(
                models.WorkflowNode.project_id == project.id,
                models.WorkflowNode.title == "Provide Per Release Development Capacity",
            )
            .first()
        )
        if capacity_node:
            from datetime import datetime
            capacity_node.output = json.dumps({
                "total_frontend_days": frontend,
                "total_backend_days": backend,
            })
            capacity_node.completed_at = datetime.utcnow()

    db.commit()
    return project
