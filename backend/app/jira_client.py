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


def list_fields() -> list[dict]:
    """Every field on this Jira instance (name + id). Used by the
    /api/jira/fields debug route to find the real customfield_XXXXX ids
    for JIRA_FEATURE_FIELDS above -- no need to hunt through Jira admin
    screens by hand."""
    base_url, email, api_token = _get_config()
    response = requests.get(
        f"{base_url}/rest/api/3/field",
        headers={**_auth_header(email, api_token), "Accept": "application/json"},
        timeout=15,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira field list failed (status {response.status_code}): {response.text[:300]}"
        )
    fields = response.json()
    # Custom fields first (that's what we actually need to map), then
    # sorted by name so it's easy to scan.
    return sorted(
        [{"id": f["id"], "name": f["name"], "custom": f.get("custom", False)} for f in fields],
        key=lambda f: (not f["custom"], f["name"].lower()),
    )


def search_features(project_key: str, extra_jql: Optional[str] = None) -> list[dict]:
    """Return every Feature-type issue in the given project, with the
    fields the roadmap-planning prompts need.

    Uses /rest/api/3/search/jql -- Atlassian removed the old
    /rest/api/3/search endpoint (CHANGE-2046) in favor of this one, which
    pages via nextPageToken instead of startAt."""
    base_url, email, api_token = _get_config()

    jql = f'project = "{project_key}" AND issuetype = "Feature"'
    if extra_jql:
        jql += f" AND {extra_jql}"

    custom_fields = list(JIRA_FEATURE_FIELDS.values())
    fields = ["summary", "description"] + custom_fields

    results = []
    next_page_token = None
    while True:
        params = {"jql": jql, "fields": ",".join(fields), "maxResults": 200}
        if next_page_token:
            params["nextPageToken"] = next_page_token

        response = requests.get(
            f"{base_url}/rest/api/3/search/jql",
            headers={**_auth_header(email, api_token), "Accept": "application/json"},
            params=params,
            timeout=15,
        )
        if not response.ok:
            raise JiraRequestError(
                f"Jira search failed (status {response.status_code}): {response.text[:300]}"
            )

        data = response.json()
        for issue in data.get("issues", []):
            f = issue.get("fields", {})
            entry = {
                "issue_key": issue.get("key"),
                "summary": f.get("summary"),
                "description": f.get("description"),
            }
            for name, field_id in JIRA_FEATURE_FIELDS.items():
                entry[name] = f.get(field_id)
            results.append(entry)

        next_page_token = data.get("nextPageToken")
        if not next_page_token or len(results) >= 1000:  # safety cap
            break

    return results
