"""Thin Jira Cloud REST client. Read-only for now (Phase 1 has no Jira
write steps) -- add_comment/transition_issue/create_issue can be added
once a later phase actually needs them.

Custom field IDs are Jira-instance-specific (e.g. "customfield_10050")
and unknown until the real instance is connected. JIRA_FEATURE_FIELDS
below is a placeholder map -- swap in the real field IDs once we have
them (Jira admin -> issue fields, or GET /rest/api/3/field)."""

import os
from base64 import b64encode
from typing import Optional

import requests

# Maps our internal field names to this Jira instance's custom field IDs.
# PLACEHOLDERS -- replace with the real customfield_XXXXX ids once known.
JIRA_FEATURE_FIELDS = {
    "feature_id": "customfield_10001",
    "feature_purpose": "customfield_10002",
    "rice_score": "customfield_10003",
    "reach": "customfield_10004",
    "impact": "customfield_10005",
    "confidence": "customfield_10006",
    "pdm_effort_estimate": "customfield_10007",
    "pdm_backend_estimate": "customfield_10008",
    "pdm_frontend_estimate": "customfield_10009",
    "feature_dependencies": "customfield_10010",
    "rice_assumptions": "customfield_10011",
}


class JiraNotConfiguredError(Exception):
    """Jira credentials aren't set yet."""


class JiraRequestError(Exception):
    """A Jira API call failed."""


def _get_config() -> tuple[str, str, str]:
    base_url = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    api_token = os.environ.get("JIRA_API_TOKEN")
    if not base_url or not email or not api_token:
        raise JiraNotConfiguredError(
            "Jira isn't connected yet. Set JIRA_BASE_URL, JIRA_EMAIL, and "
            "JIRA_API_TOKEN (Jira Cloud: Account Settings -> Security -> "
            "API tokens) in your environment to enable this step."
        )
    return base_url.rstrip("/"), email, api_token


def _auth_header(email: str, api_token: str) -> dict:
    token = b64encode(f"{email}:{api_token}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def search_features(project_key: str, extra_jql: Optional[str] = None) -> list[dict]:
    """Return every Feature-type issue in the given project, with the
    fields the roadmap-planning prompts need."""
    base_url, email, api_token = _get_config()

    jql = f'project = "{project_key}" AND issuetype = "Feature"'
    if extra_jql:
        jql += f" AND {extra_jql}"

    custom_fields = list(JIRA_FEATURE_FIELDS.values())
    fields = ["summary", "description"] + custom_fields

    response = requests.get(
        f"{base_url}/rest/api/3/search",
        headers={**_auth_header(email, api_token), "Accept": "application/json"},
        params={"jql": jql, "fields": ",".join(fields), "maxResults": 200},
        timeout=15,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira search failed (status {response.status_code}): {response.text[:300]}"
        )

    issues = response.json().get("issues", [])
    results = []
    for issue in issues:
        f = issue.get("fields", {})
        entry = {
            "issue_key": issue.get("key"),
            "summary": f.get("summary"),
            "description": f.get("description"),
        }
        for name, field_id in JIRA_FEATURE_FIELDS.items():
            entry[name] = f.get(field_id)
        results.append(entry)
    return results
