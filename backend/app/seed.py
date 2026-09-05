"""Seeds the one demo Project and its Phase 1 workflow tree on first boot.
Phases 2-5 are seeded as placeholder containers only (titles, no leaves) --
they light up in later passes once Phase 1 is proven out."""

from app.models import Project, WorkflowNode

ROADMAP_OPTIONS_PROMPT = """You are a Senior Product Manager. Extract the following fields from Jira for all issues of type "Feature"

Issue Type\tIssue key\tSummary\tCustom field (Feature ID)\tCustom field (Feature Purpose)\tRICE Score\tReach\tImpact\tConfidence\tProd Mgmt Effort Estimate\tProd Mgmt Backend Estimate\tProd Mgmt Frontend Estimate\tFeature Dependencies\tRICE Assumptions\tDescription

Now use the extracted data to create three draft multi-year roadmap options:
\tHighest Product Value
\tBalanced
\tLower Risk
Assume each release has 50 frontend and 35 backend staff-days. Use PdM effort estimates and consider RICE scores, priority, commitment status, feature dependencies, confidence, capacity, and parallel work.
For each option, show Features by release, capacity use, sequencing rationale, key risks, and deferred Features. Do not exceed capacity or invent missing information.
Compare the options, recommend one for PdM review, and deliver a concise, executive-style Microsoft Word document.
Clearly state that the roadmap reflects Product intent based on preliminary estimates and is not a Development commitment."""

# Each entry: title, description, and for leaves: automation_type
# ("manual" | "automated") plus ai_harness/ai_prompt when automated.
PHASE_1_TASKS = [
    {
        "title": "Define Bus Strategy",
        "automation_type": "manual",
    },
    {
        "title": "Define Stakeholders",
        "automation_type": "manual",
    },
    {
        "title": "Define Initial Roadmap",
        "children": [
            {
                "title": "Create Single Jira Feature Issue",
                "automation_type": "manual",
                "description": (
                    "Create a new Feature issue type to represent each "
                    "feature. Ensure you add the release number, "
                    "description and feature ID."
                ),
            },
            {
                "title": "Provide Feature Estimates",
                "automation_type": "manual",
                "description": (
                    "Enter your high level feature estimates in the Story "
                    "Points field assuming 1 point = 1 eight hour day of "
                    "work"
                ),
            },
            {
                "title": "Calculate RICE scores",
                "automation_type": "manual",
                "description": "Calculate RICE scores and enter into respective Jira fields",
            },
            {
                "title": "Define Inter-Feature Dependencies and Assumptions",
                "automation_type": "manual",
                "description": (
                    "Document inter-feature dependencies and assumptions "
                    "in the Jira description."
                ),
            },
            {
                "title": "Define High Level Estimates",
                "automation_type": "manual",
                "description": (
                    "Add high level estimates in Jira (consult architects "
                    "and development leads, as needed)"
                ),
            },
            {
                "title": "Provide Per Release Development Capacity",
                "automation_type": "input",
                "description": (
                    "Enter the total available development capacity "
                    "(front end and backend staff-days) per release."
                ),
            },
            {
                "title": "Generate Draft Roadmap Options",
                "automation_type": "automated",
                "ai_harness": "claude",
                "ai_prompt": ROADMAP_OPTIONS_PROMPT,
                "description": "Use AI to generate roadmap commitment options.",
            },
            {
                "title": "Review, Refine and Select Roadmap Options",
                "automation_type": "manual",
                "description": (
                    "Refine and review the roadmap options with necessary "
                    "stakeholders and select plan."
                ),
            },
        ],
    },
]

# Phases 2-5: task titles only, nothing actionable yet.
PLACEHOLDER_PHASES = [
    (2, "Quarterly Release Initiation", [
        "Define Feature Requirements & Architecture",
        "Generate Exec Feature Summary",
        "Define Epics",
        "Commit the Release",
        "Update the Roadmap",
    ]),
    (3, "Release Planning", [
        "Document Design",
        "Create User Stories",
        "Identify Dependencies",
        "Define Critical Chain",
        "Confirm Commitment",
        "Map Stories to Sprints",
        "Produce Feature Schedule",
        "Identify Risks Mgmt Plan",
    ]),
    (4, "Execution & Monitoring", [
        "Define Test Plan & Scripts",
        "Establish Meetings",
        "Generate Meeting Notes",
        "Maintain Updated Backlog Status",
        "Schedule Demos",
        "Remove blocking issues",
        "KPI Definition & Tracking",
        "Status Updates",
    ]),
    (5, "Delivery & Closure", [
        "Release DoD",
        "Release Delivery/Availability",
        "Lessons Learned",
    ]),
]

PHASE_1_TITLE = "Multi-Year Roadmap Planning"


def _insert_children(db, project, parent, phase_number, items):
    for index, item in enumerate(items):
        children = item.get("children")
        node = WorkflowNode(
            project_id=project.id,
            parent_id=parent.id if parent else None,
            phase_number=phase_number,
            order_index=index,
            title=item["title"],
            description=item.get("description"),
            is_leaf=children is None,
            automation_type=item.get("automation_type"),
            ai_harness=item.get("ai_harness"),
            ai_prompt=item.get("ai_prompt"),
        )
        db.add(node)
        db.flush()
        if children:
            _insert_children(db, project, node, phase_number, children)


def seed_if_empty(db):
    if db.query(Project).first():
        return

    # Render's free-tier disk is ephemeral -- a restart wipes this local
    # database. Before seeding a blank demo program, check whether a real
    # program's state already exists in Jira (it's the durable store) and
    # rehydrate from that instead.
    from app.jira_state import bootstrap_project
    if bootstrap_project(db):
        return

    project = Project(name="Demo Program", jira_project_key="PB")
    db.add(project)
    db.flush()

    phase_1 = WorkflowNode(
        project_id=project.id,
        parent_id=None,
        phase_number=1,
        order_index=0,
        title=PHASE_1_TITLE,
        is_leaf=False,
    )
    db.add(phase_1)
    db.flush()
    _insert_children(db, project, phase_1, 1, PHASE_1_TASKS)

    for phase_number, phase_title, task_titles in PLACEHOLDER_PHASES:
        phase_node = WorkflowNode(
            project_id=project.id,
            parent_id=None,
            phase_number=phase_number,
            order_index=phase_number - 1,
            title=phase_title,
            is_leaf=False,
        )
        db.add(phase_node)
        db.flush()
        for index, task_title in enumerate(task_titles):
            db.add(
                WorkflowNode(
                    project_id=project.id,
                    parent_id=phase_node.id,
                    phase_number=phase_number,
                    order_index=index,
                    title=task_title,
                    is_leaf=True,
                    automation_type=None,
                )
            )

    db.commit()
