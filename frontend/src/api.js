async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      // ignore
    }
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}

export function getWorkflow() {
  return request("/workflow");
}

export function completeNode(id) {
  return request(`/workflow/nodes/${id}/complete`, { method: "POST" });
}

export function reopenNode(id) {
  return request(`/workflow/nodes/${id}/reopen`, { method: "POST" });
}

export function runNode(id) {
  return request(`/workflow/nodes/${id}/run`, { method: "POST" });
}

export function submitCapacity(id, payload) {
  return request(`/workflow/nodes/${id}/capacity`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateProject(payload) {
  return request("/project", { method: "PATCH", body: JSON.stringify(payload) });
}

export function downloadUrl(fileId) {
  return `/api/files/${fileId}`;
}
