"""Thin Jira Cloud REST client.

Custom field IDs are Jira-instance-specific (e.g. "customfield_10050")
and unknown until the real instance is connected. JIRA_FEATURE_FIELDS
below is a placeholder map -- swap in the real field IDs once we have
them (Jira admin -> issue fields, or GET /rest/api/3/field).

create_issue/update_issue/attach_file back the "store program state in
Jira" design: one Jira issue per Program Pilot program, holding its
state and the generated roadmap docx as an attachment."""

import os
from base64 import b64encode
from typing import Optional

import requests

# Maps our internal field names to this Jira instance's custom field IDs
# (peggymccormick101-pgmframework, project PB) -- read from the live
# /api/jira/fields listing. Note there are two fields named "Impact" on
# this instance (customfield_10083 and customfield_10093); 10093 is the
# right one -- it sits in the same contiguous id block (10089-10104) as
# every other Feature/RICE field below, while 10083 belongs to an
# unrelated older field set.
JIRA_FEATURE_FIELDS = {
    "feature_id": "customfield_10090",
    "feature_purpose": "customfield_10091",
    "rice_score": "customfield_10098",
    "reach": "customfield_10092",
    "impact": "customfield_10093",
    "confidence": "customfield_10094",
    "pdm_effort_estimate": "customfield_10100",
    "pdm_backend_estimate": "customfield_10101",
    "pdm_frontend_estimate": "customfield_10102",
    "feature_dependencies": "customfield_10103",
    "rice_assumptions": "customfield_10104",
}


# Program-level state is persisted on a Task issue (one per program),
# reusing the same three fields Feature issues use for PdM estimates --
# here they hold the user-entered frontend/backend capacity and their
# total, not a per-feature estimate.
PROGRAM_STATE_FIELDS = {
    "frontend_estimate": JIRA_FEATURE_FIELDS["pdm_frontend_estimate"],
    "backend_estimate": JIRA_FEATURE_FIELDS["pdm_backend_estimate"],
    "effort_estimate": JIRA_FEATURE_FIELDS["pdm_effort_estimate"],
}

# Marks a Task issue as a program (vs. any other Task in the project) so
# list_program_issues can find it for the program picker. Any Task
# issue with this label is selectable -- hand-labeled ones included, not
# just ones the app created itself.
PROGRAM_STATE_LABEL = "Program"

# Which Jira project to create/list program Task issues in, before any
# local Project row (which normally carries its own jira_project_key)
# exists yet.
DEFAULT_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "PB")

# "Program State" -- a single-select dropdown field on the program's
# Task issue tracking how far through Phase 1 the program has gotten.
# A select field's value comes back as {"value": "...", "id": "...",
# "self": "..."} and must be written the same way, e.g.
# {"value": "FeaturesInJira"} -- not a plain string.
PROGRAM_STATE_STATUS_FIELD = "customfield_10139"


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
    """Every field on this Jira instance (name + id + type). Used by the
    /api/jira/fields debug route to find the real customfield_XXXXX ids
    (and whether they're a select-list, text field, etc.) for
    JIRA_FEATURE_FIELDS/PROGRAM_STATE_FIELDS above -- no need to hunt
    through Jira admin screens by hand."""
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
        [
            {
                "id": f["id"],
                "name": f["name"],
                "custom": f.get("custom", False),
                "type": (f.get("schema") or {}).get("type"),
                "custom_type": (f.get("schema") or {}).get("custom"),
            }
            for f in fields
        ],
        key=lambda f: (not f["custom"], f["name"].lower()),
    )


def get_linked_issue_keys(issue_key: str, link_type_name: str) -> list[str]:
    """Keys of every issue linked to `issue_key` via a link of type
    `link_type_name` (matched case-insensitively against the link
    type's name, not its inward/outward description text -- e.g. an
    "Implements" link type shows up as {"name": "Implements", ...}
    regardless of which side of the relationship this issue is on).
    Used instead of the JQL linkedIssues() function, whose text-match
    rules against inward/outward descriptions are less predictable."""
    base_url, email, api_token = _get_config()
    response = requests.get(
        f"{base_url}/rest/api/3/issue/{issue_key}",
        headers={**_auth_header(email, api_token), "Accept": "application/json"},
        params={"fields": "issuelinks"},
        timeout=15,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira issue link lookup failed (status {response.status_code}): {response.text[:300]}"
        )
    links = response.json().get("fields", {}).get("issuelinks", [])
    keys = []
    for link in links:
        if link.get("type", {}).get("name", "").lower() != link_type_name.lower():
            continue
        other = link.get("inwardIssue") or link.get("outwardIssue")
        if other:
            keys.append(other["key"])
    return keys


def create_issue(project_key: str, issue_type: str, fields: dict) -> str:
    """Create an issue and return its key (e.g. "PB-42"). `fields` is
    merged with project/issuetype into the standard
    {"fields": {...}} create-issue body -- pass plain values for
    system fields (e.g. "summary") and custom field ids
    ("customfield_XXXXX") as keys for anything else."""
    base_url, email, api_token = _get_config()
    body = {
        "fields": {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            **fields,
        }
    }
    response = requests.post(
        f"{base_url}/rest/api/3/issue",
        headers={**_auth_header(email, api_token), "Accept": "application/json", "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira issue creation failed (status {response.status_code}): {response.text[:300]}"
        )
    return response.json()["key"]


def update_issue(issue_key: str, fields: dict) -> None:
    """Update fields on an existing issue. Same field-shape rules as
    create_issue."""
    base_url, email, api_token = _get_config()
    response = requests.put(
        f"{base_url}/rest/api/3/issue/{issue_key}",
        headers={**_auth_header(email, api_token), "Accept": "application/json", "Content-Type": "application/json"},
        json={"fields": fields},
        timeout=15,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira issue update failed (status {response.status_code}): {response.text[:300]}"
        )


def attach_file(issue_key: str, filename: str, content: bytes, content_type: str) -> None:
    """Attach a file (e.g. the generated roadmap docx) to an issue."""
    base_url, email, api_token = _get_config()
    response = requests.post(
        f"{base_url}/rest/api/3/issue/{issue_key}/attachments",
        headers={**_auth_header(email, api_token), "X-Atlassian-Token": "no-check"},
        files={"file": (filename, content, content_type)},
        timeout=30,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira attachment upload failed (status {response.status_code}): {response.text[:300]}"
        )


def list_program_issues(project_key: str) -> list[dict]:
    """Every Program-labeled Task issue in this project (key + name),
    most recently updated first -- backs the "pick a program" screen."""
    base_url, email, api_token = _get_config()

    jql = (
        f'project = "{project_key}" AND issuetype = Task '
        f'AND labels = "{PROGRAM_STATE_LABEL}" ORDER BY updated DESC'
    )
    response = requests.get(
        f"{base_url}/rest/api/3/search/jql",
        headers={**_auth_header(email, api_token), "Accept": "application/json"},
        params={"jql": jql, "fields": "summary", "maxResults": 50},
        timeout=15,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira program list failed (status {response.status_code}): {response.text[:300]}"
        )
    return [
        {"issue_key": issue["key"], "name": issue.get("fields", {}).get("summary")}
        for issue in response.json().get("issues", [])
    ]


def get_issue(issue_key: str) -> dict:
    """Fetch one program's name, capacity fields, and workflow state from
    Jira -- used when the user selects a program from the picker to
    load its progress."""
    base_url, email, api_token = _get_config()
    fields = ["summary", PROGRAM_STATE_STATUS_FIELD] + list(PROGRAM_STATE_FIELDS.values())
    response = requests.get(
        f"{base_url}/rest/api/3/issue/{issue_key}",
        headers={**_auth_header(email, api_token), "Accept": "application/json"},
        params={"fields": ",".join(fields)},
        timeout=15,
    )
    if not response.ok:
        raise JiraRequestError(
            f"Jira issue lookup failed (status {response.status_code}): {response.text[:300]}"
        )
    f = response.json().get("fields", {})
    state_field = f.get(PROGRAM_STATE_STATUS_FIELD)
    return {
        "issue_key": issue_key,
        "name": f.get("summary"),
        "state": (state_field or {}).get("value"),
        "frontend_estimate": f.get(PROGRAM_STATE_FIELDS["frontend_estimate"]),
        "backend_estimate": f.get(PROGRAM_STATE_FIELDS["backend_estimate"]),
    }


def search_features(project_key: str, extra_jql: Optional[str] = None) -> list[dict]:
    """Return every Feature-type issue in the given project, with the
    fields the roadmap-planning prompts need. `extra_jql` narrows this
    further, e.g. to Features linked to a specific program issue:
    'issue in linkedIssues("PB-48", "implements")'.

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
