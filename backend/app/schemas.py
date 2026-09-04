from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class WorkflowNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    phase_number: int
    is_leaf: bool
    automation_type: Optional[str] = None
    ai_harness: Optional[str] = None
    status: str
    completed_at: Optional[datetime] = None
    output: Optional[str] = None
    output_file_id: Optional[str] = None
    children: list["WorkflowNodeOut"] = []


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    jira_project_key: Optional[str] = None


class WorkflowOut(BaseModel):
    project: ProjectOut
    phases: list[WorkflowNodeOut]


class RunNodeResult(BaseModel):
    node: WorkflowNodeOut
    error: Optional[str] = None
