from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    jira_project_key = Column(String, nullable=True)
    # Key of the Jira Task issue that holds this program's persisted state
    # (name + capacity fields + the generated roadmap docx as an
    # attachment) -- Render's free-tier disk is ephemeral, so this is how
    # the program survives an instance restart.
    jira_issue_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    nodes = relationship(
        "WorkflowNode", back_populates="project", cascade="all, delete-orphan"
    )


class WorkflowNode(Base):
    """One node in the 3-level program workflow tree (phase -> task ->
    sub-step). Only leaf nodes (is_leaf=True) are actionable; container
    nodes exist purely to group their children under a title."""

    __tablename__ = "workflow_nodes"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("workflow_nodes.id"), nullable=True)

    phase_number = Column(Integer, nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    is_leaf = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=False)
    # Only meaningful when is_leaf is True.
    automation_type = Column(String, nullable=True)  # "manual" | "automated"
    ai_harness = Column(String, nullable=True)  # "claude" | "chatgpt"
    ai_prompt = Column(Text, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    output = Column(Text, nullable=True)
    output_file_id = Column(String, nullable=True)

    project = relationship("Project", back_populates="nodes")
    parent = relationship("WorkflowNode", remote_side=[id], back_populates="children")
    children = relationship(
        "WorkflowNode",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="WorkflowNode.order_index",
    )
