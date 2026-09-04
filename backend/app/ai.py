import json
import os
from typing import Optional

import anthropic

MODEL = "claude-sonnet-5"

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy backend/.env.example to "
                "backend/.env and add your key."
            )
        default_headers = {}
        workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        if workspace_id:
            default_headers["anthropic-workspace-id"] = workspace_id
        _client = anthropic.Anthropic(api_key=api_key, default_headers=default_headers)
    return _client


def _run_structured(system: str, user_content: str, schema: dict, max_tokens: int) -> dict:
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user_content}],
    )
    text_blocks = [b.text for b in response.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude did not return a response.")
    return json.loads(text_blocks[-1])


DEV_CAPACITY_SCHEMA = {
    "type": "object",
    "properties": {
        "total_frontend_days": {
            "type": "number",
            "description": "Sum of frontend story-point estimates across all Feature issues provided, in staff-days.",
        },
        "total_backend_days": {
            "type": "number",
            "description": "Sum of backend story-point estimates across all Feature issues provided, in staff-days.",
        },
        "feature_count": {"type": "integer"},
        "notes": {
            "type": "string",
            "description": "Any Features missing an estimate, or other caveats worth flagging.",
        },
    },
    "required": ["total_frontend_days", "total_backend_days", "feature_count", "notes"],
    "additionalProperties": False,
}


def compute_dev_capacity(features: list[dict]) -> dict:
    """Step vi, "Provide Development Capacity": total the front end and
    backend estimates already entered on each Feature issue in Jira."""
    return _run_structured(
        system=(
            "You are a program-management assistant. Collect total "
            "available development capacity for a release, split by "
            "front end and backend, from the Jira Feature data provided. "
            "Sum the Prod Mgmt Frontend Estimate and Prod Mgmt Backend "
            "Estimate fields across every Feature. Do not invent values "
            "for Features missing an estimate -- note them instead."
        ),
        user_content=f"Feature issues (JSON):\n{json.dumps(features, indent=2)}",
        schema=DEV_CAPACITY_SCHEMA,
        max_tokens=1024,
    )


ROADMAP_RELEASE_SCHEMA = {
    "type": "object",
    "properties": {
        "release_label": {"type": "string", "description": "e.g. 'Release 1'"},
        "features": {
            "type": "string",
            "description": "Feature IDs and names in this release, e.g. 'FID-67891 - Customer Profile Update'.",
        },
        "frontend_days": {"type": "string", "description": "e.g. '10/50'"},
        "backend_days": {"type": "string", "description": "e.g. '35/35'"},
        "rationale": {"type": "string"},
    },
    "required": ["release_label", "features", "frontend_days", "backend_days", "rationale"],
    "additionalProperties": False,
}

ROADMAP_OPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "e.g. 'Highest Product Value'"},
        "intro": {"type": "string", "description": "One-sentence framing of this option's strategy."},
        "releases": {"type": "array", "items": ROADMAP_RELEASE_SCHEMA},
        "key_rationale": {"type": "string"},
        "key_risks": {"type": "string"},
        "deferred_features": {"type": "string", "description": "'None.' if nothing was deferred."},
    },
    "required": ["name", "intro", "releases", "key_rationale", "key_risks", "deferred_features"],
    "additionalProperties": False,
}

ROADMAP_OPTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation_summary": {
            "type": "string",
            "description": "1-2 sentence executive recommendation shown at the top of the document.",
        },
        "planning_basis": {
            "type": "array",
            "description": "Rows for the 'Planning basis' table.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["label", "value"],
                "additionalProperties": False,
            },
        },
        "executive_comparison": {
            "type": "array",
            "description": "One row per option for the executive comparison table.",
            "items": {
                "type": "object",
                "properties": {
                    "option": {"type": "string"},
                    "primary_outcome": {"type": "string"},
                    "planned_releases": {"type": "string"},
                    "relative_risk": {"type": "string"},
                    "tradeoff": {"type": "string"},
                },
                "required": ["option", "primary_outcome", "planned_releases", "relative_risk", "tradeoff"],
                "additionalProperties": False,
            },
        },
        "options": {
            "type": "array",
            "description": "Exactly three: Highest Product Value, Balanced, Lower Risk.",
            "items": ROADMAP_OPTION_SCHEMA,
        },
        "recommended_option_name": {"type": "string"},
        "why_this_option": {"type": "array", "items": {"type": "string"}},
        "pdm_review_decisions": {"type": "array", "items": {"type": "string"}},
        "disclaimer": {
            "type": "string",
            "description": "Must state this reflects Product intent based on preliminary estimates, not a Development commitment.",
        },
    },
    "required": [
        "recommendation_summary",
        "planning_basis",
        "executive_comparison",
        "options",
        "recommended_option_name",
        "why_this_option",
        "pdm_review_decisions",
        "disclaimer",
    ],
    "additionalProperties": False,
}


def generate_roadmap_options(features: list[dict], capacity: dict) -> dict:
    """Step vii, "Generate Draft Roadmap Options". Returns structured data;
    app/roadmap_docx.py renders it into the executive-style Word doc --
    Claude supplies the analysis, the template supplies consistent
    formatting run to run."""
    return _run_structured(
        system=(
            "You are a Senior Product Manager. Use the extracted Jira "
            "Feature data to create three draft multi-year roadmap "
            "options: Highest Product Value, Balanced, and Lower Risk. "
            f"Assume each release has {capacity.get('total_frontend_days', 50)} "
            f"frontend and {capacity.get('total_backend_days', 35)} backend "
            "staff-days, unless told otherwise. Use PdM effort estimates "
            "and consider RICE scores, priority, commitment status, "
            "feature dependencies, confidence, capacity, and parallel "
            "work. For each option, sequence Features by release, show "
            "capacity use, sequencing rationale, key risks, and deferred "
            "Features. Do not exceed capacity or invent missing "
            "information. Compare the options and recommend one for PdM "
            "review. Clearly state the roadmap reflects Product intent "
            "based on preliminary estimates and is not a Development "
            "commitment."
        ),
        user_content=f"Feature issues (JSON):\n{json.dumps(features, indent=2)}",
        schema=ROADMAP_OPTIONS_SCHEMA,
        max_tokens=8192,
    )
