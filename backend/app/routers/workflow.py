import json
import os
import uuid
from datetime import datetime

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import ai, jira_client, jira_state, models, roadmap_docx, schemas
from app.database import get_db

router = APIRouter(prefix="/api", tags=["workflow"])

GENERATED_FILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "generated_files"
)
os.makedirs(GENERATED_FILES_DIR, exist_ok=True)


def _handle_errors(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except jira_client.JiraNotConfiguredError as e:
        raise HTTPException(status_code=424, detail=str(e))
    except jira_client.JiraRequestError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except anthropic.APIStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"Claude API error ({e.status_code}): {e.message}"
        )
    except anthropic.APIConnectionError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach the Claude API: {e}")


def _get_project(db: Session) -> models.Project:
    project = (
        db.query(models.Project)
        .order_by(models.Project.selected_at.desc())
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="No program has been selected yet.")
    return project


def _ordered_leaves(node: models.WorkflowNode) -> list[models.WorkflowNode]:
    """Depth-first, order_index order -- the sequence steps unlock in."""
    if node.is_leaf:
        return [node]
    leaves = []
    for child in sorted(node.children, key=lambda c: c.order_index):
        leaves.extend(_ordered_leaves(child))
    return leaves


def _node_status(node: models.WorkflowNode, all_leaves_in_order: list[models.WorkflowNode]) -> str:
    if node.is_leaf:
        if node.completed_at:
            return "complete"
        if node not in all_leaves_in_order:
            # Placeholder-phase leaf -- not wired to unlock logic yet.
            return "locked"
        position = all_leaves_in_order.index(node)
        if position == 0 or all_leaves_in_order[position - 1].completed_at:
            return "available"
        return "locked"
    # Container: complete if every descendant leaf is complete, else in_progress
    # once its first leaf is available/complete, else locked.
    descendant_leaves = _ordered_leaves(node)
    if all(leaf.completed_at for leaf in descendant_leaves):
        return "complete"
    if any(leaf.completed_at for leaf in descendant_leaves) or (
        descendant_leaves and _node_status(descendant_leaves[0], all_leaves_in_order) == "available"
    ):
        return "in_progress"
    return "locked"


def _serialize(node: models.WorkflowNode, all_leaves_in_order: list[models.WorkflowNode]) -> schemas.WorkflowNodeOut:
    return schemas.WorkflowNodeOut(
        id=node.id,
        title=node.title,
        description=node.description,
        phase_number=node.phase_number,
        is_leaf=node.is_leaf,
        automation_type=node.automation_type,
        ai_harness=node.ai_harness,
        status=_node_status(node, all_leaves_in_order),
        completed_at=node.completed_at,
        output=node.output,
        output_file_id=node.output_file_id,
        children=[
            _serialize(child, all_leaves_in_order)
            for child in sorted(node.children, key=lambda c: c.order_index)
        ],
    )


@router.get("/project", response_model=schemas.ProjectOut)
def get_project(db: Session = Depends(get_db)):
    return _get_project(db)


@router.patch("/project", response_model=schemas.ProjectOut)
def update_project(payload: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    project = _get_project(db)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(project, field, value)
    # Jira is the durable store now -- don't commit the local change (and
    # report success) if the Jira write itself failed, or the local copy
    # silently drifts from what actually persists.
    try:
        _handle_errors(jira_state.sync_program_to_jira, db, project)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.refresh(project)
    return project


@router.get("/jira/programs", response_model=list[schemas.ProgramSummary])
def list_jira_programs(db: Session = Depends(get_db)):
    """Every Program-labeled Task issue in Jira, for the "pick a
    program" screen. Not scoped to what's loaded locally -- this always
    reflects what actually exists in Jira."""
    return _handle_errors(jira_client.list_program_issues, jira_client.DEFAULT_PROJECT_KEY)


@router.post("/programs/select", response_model=schemas.WorkflowOut)
def select_program(payload: schemas.SelectProgramRequest, db: Session = Depends(get_db)):
    _handle_errors(jira_state.load_program, db, payload.issue_key)
    return get_workflow(db)


@router.post("/programs", response_model=schemas.WorkflowOut)
def new_program(payload: schemas.NewProgramRequest, db: Session = Depends(get_db)):
    _handle_errors(jira_state.create_program, db, payload.name)
    return get_workflow(db)


@router.get("/workflow", response_model=schemas.WorkflowOut)
def get_workflow(db: Session = Depends(get_db)):
    project = _get_project(db)
    phases = (
        db.query(models.WorkflowNode)
        .filter(models.WorkflowNode.project_id == project.id, models.WorkflowNode.parent_id.is_(None))
        .order_by(models.WorkflowNode.order_index)
        .all()
    )
    # Availability is scoped within Phase 1 only for now; placeholder
    # phases (2-5) have no leaves wired to unlock logic yet.
    phase_1 = next((p for p in phases if p.phase_number == 1), None)
    all_leaves_in_order = _ordered_leaves(phase_1) if phase_1 else []
    return schemas.WorkflowOut(
        project=schemas.ProjectOut.model_validate(project),
        phases=[_serialize(p, all_leaves_in_order) for p in phases],
    )


def _get_leaf(db: Session, node_id: int) -> models.WorkflowNode:
    node = db.query(models.WorkflowNode).filter(models.WorkflowNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Step not found.")
    if not node.is_leaf:
        raise HTTPException(status_code=400, detail="This isn't an actionable step.")
    return node


def _check_available(db: Session, node: models.WorkflowNode, allow_complete: bool = False):
    project = db.query(models.Project).filter(models.Project.id == node.project_id).first()
    phase_1 = (
        db.query(models.WorkflowNode)
        .filter(
            models.WorkflowNode.project_id == project.id,
            models.WorkflowNode.parent_id.is_(None),
            models.WorkflowNode.phase_number == 1,
        )
        .first()
    )
    all_leaves_in_order = _ordered_leaves(phase_1) if phase_1 else []
    if node not in all_leaves_in_order:
        raise HTTPException(status_code=400, detail="This step isn't part of the active phase yet.")
    status = _node_status(node, all_leaves_in_order)
    ok_statuses = {"available", "complete"} if allow_complete else {"available"}
    if status not in ok_statuses:
        raise HTTPException(status_code=409, detail="Complete the earlier steps first.")


@router.post("/workflow/nodes/{node_id}/complete", response_model=schemas.WorkflowNodeOut)
def complete_node(node_id: int, db: Session = Depends(get_db)):
    node = _get_leaf(db, node_id)
    if node.automation_type != "manual":
        raise HTTPException(status_code=400, detail="This step isn't a manual step.")
    _check_available(db, node)
    node.completed_at = datetime.utcnow()

    # Every Phase 1 manual step is tracked in Jira's "Program State"
    # field (see jira_state.PROGRAM_STATE_SEQUENCE) so progress survives
    # an ephemeral disk wipe.
    project = db.query(models.Project).filter(models.Project.id == node.project_id).first()
    try:
        _handle_errors(jira_state.sync_step_state_to_jira, project, node.title)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.refresh(node)
    phase_1 = db.query(models.WorkflowNode).filter(
        models.WorkflowNode.project_id == node.project_id,
        models.WorkflowNode.parent_id.is_(None),
        models.WorkflowNode.phase_number == 1,
    ).first()
    return _serialize(node, _ordered_leaves(phase_1))


@router.post("/workflow/nodes/{node_id}/reopen", response_model=schemas.WorkflowNodeOut)
def reopen_node(node_id: int, db: Session = Depends(get_db)):
    """Undo a completed step -- handy while trying the POC out."""
    node = _get_leaf(db, node_id)
    node.completed_at = None
    node.output = None
    node.output_file_id = None
    db.commit()
    db.refresh(node)
    phase_1 = db.query(models.WorkflowNode).filter(
        models.WorkflowNode.project_id == node.project_id,
        models.WorkflowNode.parent_id.is_(None),
        models.WorkflowNode.phase_number == 1,
    ).first()
    return _serialize(node, _ordered_leaves(phase_1))


@router.post("/workflow/nodes/{node_id}/capacity", response_model=schemas.WorkflowNodeOut)
def submit_capacity(node_id: int, payload: schemas.CapacityInput, db: Session = Depends(get_db)):
    """"Provide Per Release Development Capacity" isn't derived from
    Jira -- Jira Feature estimates are how big each feature is, not how
    much the team can do. Capacity is a fact only a person knows, so
    they enter it directly here; it's then used as the assumed capacity
    per release in the roadmap-options step."""
    node = _get_leaf(db, node_id)
    if node.automation_type != "input":
        raise HTTPException(status_code=400, detail="This step doesn't take a capacity input.")
    _check_available(db, node, allow_complete=True)

    node.output = json.dumps({
        "total_frontend_days": payload.total_frontend_days,
        "total_backend_days": payload.total_backend_days,
    })
    node.completed_at = datetime.utcnow()

    project = db.query(models.Project).filter(models.Project.id == node.project_id).first()
    try:
        _handle_errors(
            jira_state.sync_program_to_jira,
            db,
            project,
            capacity={
                "total_frontend_days": payload.total_frontend_days,
                "total_backend_days": payload.total_backend_days,
            },
        )
        _handle_errors(jira_state.sync_step_state_to_jira, project, node.title)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.refresh(node)

    phase_1 = db.query(models.WorkflowNode).filter(
        models.WorkflowNode.project_id == node.project_id,
        models.WorkflowNode.parent_id.is_(None),
        models.WorkflowNode.phase_number == 1,
    ).first()
    return _serialize(node, _ordered_leaves(phase_1))


def _get_program_features(project: models.Project) -> list[dict]:
    """Every Feature actually scoped to this program -- linked to its
    Jira issue via an "Implements" relationship -- not every Feature in
    the whole Jira project. Reads the program issue's actual link data
    rather than JQL's linkedIssues() function, whose inward/outward
    description text-matching is less predictable than matching on the
    link type's name directly."""
    feature_keys = (
        jira_client.get_linked_issue_keys(project.jira_issue_key, "Implements")
        if project.jira_issue_key
        else []
    )
    if not feature_keys:
        return []
    extra_jql = "key in (" + ",".join(feature_keys) + ")"
    return jira_client.search_features(project.jira_project_key or "", extra_jql=extra_jql)


def _normalize_field_value(raw) -> str | None:
    """A select-type field can come back as a plain string, {"value":
    ...} (select field), or {"name": ...} (Jira Version-type field) --
    normalize whichever shape it is."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw.get("value") or raw.get("name")
    return raw


@router.get("/releases", response_model=list[str])
def list_releases(db: Session = Depends(get_db)):
    """Every distinct Release value used by this program's own linked
    Features -- what the release picker (gating Phases 2-5) offers."""
    project = _get_project(db)
    features = _handle_errors(_get_program_features, project)
    values = {_normalize_field_value(f.get("release")) for f in features}
    return sorted(v for v in values if v)


@router.post("/releases/select", response_model=schemas.WorkflowOut)
def select_release(payload: schemas.SelectReleaseRequest, db: Session = Depends(get_db)):
    project = _get_project(db)
    project.selected_release = payload.release
    db.commit()
    return get_workflow(db)


# Phase 2 (Quarterly Release Initiation) tracks progress per Feature,
# not on the program's own WorkflowNode tree -- each Feature already
# has a stable Jira key, so its state is read/written directly on that
# Feature's own issue rather than mirrored locally. "Generate Exec
# Feature Summary" isn't part of this sequence -- it's a standalone
# action for the whole release, not a per-Feature state.
FEATURE_STATE_SEQUENCE = [
    "RequirementsApproved",
    "ArchitectureApproved",
    "EpicsDefined",
    "DevEstimated",
    "FeatureCommitted",
    "RoadmapJiraUpdated",
]


def _release_features(project: models.Project) -> list[dict]:
    features = _get_program_features(project)
    return [f for f in features if _normalize_field_value(f.get("release")) == project.selected_release]


@router.get("/phase2/features", response_model=list[schemas.FeatureStateOut])
def list_phase2_features(db: Session = Depends(get_db)):
    project = _get_project(db)
    if not project.selected_release:
        raise HTTPException(status_code=400, detail="Select a release first.")
    features = _handle_errors(_release_features, project)
    results = []
    for f in features:
        state = _normalize_field_value(f.get("feature_state"))
        if state in FEATURE_STATE_SEQUENCE:
            state_index = FEATURE_STATE_SEQUENCE.index(state)
        elif state:
            # A Feature can be further along than Phase 2 tracks -- this
            # same field carries states for later phases too (not built
            # yet), so an unrecognized non-null value means "already
            # past Phase 2", not "not started".
            state_index = len(FEATURE_STATE_SEQUENCE) - 1
        else:
            state_index = -1
        results.append(
            schemas.FeatureStateOut(
                issue_key=f["issue_key"],
                feature_id=f.get("feature_id"),
                summary=f.get("summary"),
                state=state,
                state_index=state_index,
            )
        )
    return results


@router.post("/phase2/features/{issue_key}/advance", response_model=schemas.FeatureStateOut)
def advance_phase2_feature(
    issue_key: str, payload: schemas.AdvanceFeatureStateRequest, db: Session = Depends(get_db)
):
    if payload.current_state is None:
        next_index = 0
    elif payload.current_state not in FEATURE_STATE_SEQUENCE:
        raise HTTPException(status_code=400, detail="Unknown feature state.")
    else:
        current_index = FEATURE_STATE_SEQUENCE.index(payload.current_state)
        if current_index >= len(FEATURE_STATE_SEQUENCE) - 1:
            raise HTTPException(status_code=409, detail="This Feature has already reached the last tracked state.")
        next_index = current_index + 1

    next_state = FEATURE_STATE_SEQUENCE[next_index]
    _handle_errors(
        jira_client.update_issue, issue_key, {jira_client.FEATURE_STATE_FIELD: {"value": next_state}}
    )
    return schemas.FeatureStateOut(issue_key=issue_key, state=next_state, state_index=next_index)


def _run_roadmap_options(db: Session, project: models.Project, node: models.WorkflowNode) -> dict:
    capacity_node = (
        db.query(models.WorkflowNode)
        .filter(
            models.WorkflowNode.project_id == project.id,
            models.WorkflowNode.title == "Provide Per Release Development Capacity",
        )
        .first()
    )
    capacity = json.loads(capacity_node.output) if capacity_node and capacity_node.output else {}

    features = _get_program_features(project)
    result = ai.generate_roadmap_options(features, capacity)

    docx_bytes = roadmap_docx.build_roadmap_docx(result, program_name=project.name)
    file_id = f"{uuid.uuid4().hex}.docx"
    with open(os.path.join(GENERATED_FILES_DIR, file_id), "wb") as f:
        f.write(docx_bytes)
    node.output_file_id = file_id
    node.output = json.dumps({"recommended_option_name": result.get("recommended_option_name")})

    # Ensure the program has a Jira issue to attach to (covers the edge
    # case where roadmap options are generated before a name/capacity
    # change has ever triggered issue creation), then attach the docx so
    # it survives an ephemeral disk wipe too.
    jira_state.sync_program_to_jira(db, project)
    jira_client.attach_file(
        project.jira_issue_key,
        "Draft_Multi_Year_Roadmap_Options.docx",
        docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    return result


RUNNERS = {
    "Generate Draft Roadmap Options": _run_roadmap_options,
}


@router.post("/workflow/nodes/{node_id}/run", response_model=schemas.WorkflowNodeOut)
def run_node(node_id: int, db: Session = Depends(get_db)):
    node = _get_leaf(db, node_id)
    if node.automation_type != "automated":
        raise HTTPException(status_code=400, detail="This step isn't automated.")
    _check_available(db, node)

    runner = RUNNERS.get(node.title)
    if not runner:
        raise HTTPException(status_code=501, detail=f"No automation wired up yet for '{node.title}'.")

    project = db.query(models.Project).filter(models.Project.id == node.project_id).first()
    _handle_errors(runner, db, project, node)

    node.completed_at = datetime.utcnow()
    try:
        _handle_errors(jira_state.sync_step_state_to_jira, project, node.title)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.refresh(node)
    phase_1 = db.query(models.WorkflowNode).filter(
        models.WorkflowNode.project_id == node.project_id,
        models.WorkflowNode.parent_id.is_(None),
        models.WorkflowNode.phase_number == 1,
    ).first()
    return _serialize(node, _ordered_leaves(phase_1))


@router.get("/files/{file_id}")
def download_file(file_id: str):
    safe_name = os.path.basename(file_id)
    path = os.path.join(GENERATED_FILES_DIR, safe_name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="Draft_Multi_Year_Roadmap_Options.docx",
    )


@router.get("/jira/fields")
def list_jira_fields():
    """Setup helper: lists every field on the connected Jira instance so
    the real customfield_XXXXX ids can be found and dropped into
    JIRA_FEATURE_FIELDS in jira_client.py, instead of hunting through
    Jira admin screens by hand."""
    return _handle_errors(jira_client.list_fields)


@router.get("/jira/issue-links/{issue_key}")
def list_issue_links(issue_key: str):
    """Debug helper: the raw issuelinks data for an issue, so a link
    type's actual name/inward/outward text can be confirmed directly
    instead of guessing at what get_linked_issue_keys should match."""
    return _handle_errors(jira_client.get_issue_links_raw, issue_key)
