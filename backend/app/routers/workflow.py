import json
import os
import uuid
from datetime import datetime

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import ai, jira_client, models, roadmap_docx, schemas
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
    project = db.query(models.Project).first()
    if not project:
        raise HTTPException(status_code=404, detail="No project has been set up yet.")
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
    db.commit()
    db.refresh(project)
    return project


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


def _check_available(db: Session, node: models.WorkflowNode):
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
    if _node_status(node, all_leaves_in_order) != "available":
        raise HTTPException(status_code=409, detail="Complete the earlier steps first.")


@router.post("/workflow/nodes/{node_id}/complete", response_model=schemas.WorkflowNodeOut)
def complete_node(node_id: int, db: Session = Depends(get_db)):
    node = _get_leaf(db, node_id)
    if node.automation_type != "manual":
        raise HTTPException(status_code=400, detail="This step isn't a manual step.")
    _check_available(db, node)
    node.completed_at = datetime.utcnow()
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
    """"Provide Development Capacity" isn't derived from Jira -- Jira
    Feature estimates are how big each feature is, not how much the team
    can do. Capacity is a fact only a person knows, so they enter it
    directly here; it's then used as the assumed capacity per release in
    the roadmap-options step."""
    node = _get_leaf(db, node_id)
    if node.automation_type != "input":
        raise HTTPException(status_code=400, detail="This step doesn't take a capacity input.")
    _check_available(db, node)

    node.output = json.dumps({
        "total_frontend_days": payload.total_frontend_days,
        "total_backend_days": payload.total_backend_days,
    })
    node.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(node)
    phase_1 = db.query(models.WorkflowNode).filter(
        models.WorkflowNode.project_id == node.project_id,
        models.WorkflowNode.parent_id.is_(None),
        models.WorkflowNode.phase_number == 1,
    ).first()
    return _serialize(node, _ordered_leaves(phase_1))


def _run_roadmap_options(db: Session, project: models.Project, node: models.WorkflowNode) -> dict:
    capacity_node = (
        db.query(models.WorkflowNode)
        .filter(
            models.WorkflowNode.project_id == project.id,
            models.WorkflowNode.title == "Provide Development Capacity",
        )
        .first()
    )
    capacity = json.loads(capacity_node.output) if capacity_node and capacity_node.output else {}
    features = jira_client.search_features(project.jira_project_key or "")
    result = ai.generate_roadmap_options(features, capacity)

    docx_bytes = roadmap_docx.build_roadmap_docx(
        result, program_name=project.name, release_number=project.release_number
    )
    file_id = f"{uuid.uuid4().hex}.docx"
    with open(os.path.join(GENERATED_FILES_DIR, file_id), "wb") as f:
        f.write(docx_bytes)
    node.output_file_id = file_id
    node.output = json.dumps({"recommended_option_name": result.get("recommended_option_name")})
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
