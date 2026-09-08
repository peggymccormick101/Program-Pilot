import { useEffect, useState } from "react";
import {
  advancePhase2Feature,
  completeNode,
  createProgram,
  downloadUrl,
  getWorkflow,
  listJiraPrograms,
  listPhase2Features,
  listReleases,
  reopenNode,
  runNode,
  selectProgram,
  selectRelease,
  submitCapacity,
  updateProject,
} from "./api.js";
import heroImage from "./hero.png";

const PHASE_CLASS = {
  1: "phase-1",
  2: "phase-2",
  3: "phase-3",
  4: "phase-4",
  5: "phase-5",
};

function StatusBadge({ status }) {
  const label = { locked: "Locked", available: "Up next", in_progress: "In progress", complete: "Complete" }[status];
  return <span className={`status-badge status-${status}`}>{label}</span>;
}

function parseCapacity(outputJson) {
  try {
    const data = JSON.parse(outputJson);
    return { frontend: String(data.total_frontend_days ?? ""), backend: String(data.total_backend_days ?? "") };
  } catch {
    return { frontend: "", backend: "" };
  }
}

function CapacityForm({ node, busyId, onSubmitCapacity }) {
  const existing = node.output ? parseCapacity(node.output) : { frontend: "", backend: "" };
  const [frontend, setFrontend] = useState(existing.frontend);
  const [backend, setBackend] = useState(existing.backend);
  const isBusy = busyId === node.id;
  const isEditing = node.status === "complete";

  function submit(e) {
    e.preventDefault();
    if (!frontend || !backend) return;
    onSubmitCapacity(node.id, {
      total_frontend_days: Number(frontend),
      total_backend_days: Number(backend),
    });
  }

  return (
    <form className="capacity-form" onSubmit={submit}>
      <label>
        Frontend staff-days
        <input type="number" min="0" step="1" value={frontend} onChange={(e) => setFrontend(e.target.value)} required />
      </label>
      <label>
        Backend staff-days
        <input type="number" min="0" step="1" value={backend} onChange={(e) => setBackend(e.target.value)} required />
      </label>
      <button type="submit" disabled={isBusy}>
        {isBusy ? "Saving..." : isEditing ? "Save" : "Submit"}
      </button>
    </form>
  );
}

function StepRow({ node, busyId, nodeErrors, onComplete, onRun, onReopen, onSubmitCapacity }) {
  const isBusy = busyId === node.id;
  const isAutomated = node.automation_type === "automated";
  const isInput = node.automation_type === "input";
  const error = nodeErrors[node.id];

  return (
    <div className={`step-row step-${node.status}`}>
      <div className="step-main">
        <div className="step-title-row">
          <span className="step-title">{node.title}</span>
          <StatusBadge status={node.status} />
          {isAutomated && <span className="harness-chip">{node.ai_harness}</span>}
        </div>
        {node.description && <p className="step-description">{node.description}</p>}
        {error && <p className="step-error">{error}</p>}

        {node.output_file_id && (
          <a className="step-download" href={downloadUrl(node.output_file_id)}>
            Download roadmap options (.docx)
          </a>
        )}

        {isInput && (node.status === "available" || node.status === "complete") && (
          <CapacityForm node={node} busyId={busyId} onSubmitCapacity={onSubmitCapacity} />
        )}
      </div>

      <div className="step-actions">
        {node.status === "available" && node.automation_type === "manual" && (
          <button onClick={() => onComplete(node.id)} disabled={isBusy}>
            {isBusy ? "Saving..." : "Mark complete"}
          </button>
        )}
        {node.status === "available" && isAutomated && (
          <button onClick={() => onRun(node.id)} disabled={isBusy}>
            {isBusy ? "Running..." : "Run"}
          </button>
        )}
        {node.status === "complete" && !isInput && (
          <button className="ghost-button" onClick={() => onReopen(node.id)} disabled={isBusy}>
            Undo
          </button>
        )}
      </div>
    </div>
  );
}

function WorkflowNode({ node, ...actions }) {
  if (node.is_leaf) {
    return <StepRow node={node} {...actions} />;
  }
  return (
    <div className="task-group">
      <h3 className="task-group-title">{node.title}</h3>
      <div className="task-group-children">
        {node.children.map((child) => (
          <WorkflowNode key={child.id} node={child} {...actions} />
        ))}
      </div>
    </div>
  );
}

const FEATURE_STATE_LABELS = ["Requirements", "Architecture", "Epics", "Estimate", "Commit", "Roadmap"];

function FeatureStateRow({ feature, onAdvance, busy }) {
  const isDone = feature.state_index >= FEATURE_STATE_LABELS.length - 1;
  return (
    <div className="feature-state-row">
      <div className="feature-state-heading">
        <span className="feature-state-id">{feature.feature_id || feature.issue_key}</span>
        <span className="feature-state-summary">{feature.summary}</span>
      </div>
      <div className="feature-state-chips">
        {FEATURE_STATE_LABELS.map((label, i) => (
          <span key={label} className={`feature-chip ${i <= feature.state_index ? "feature-chip-done" : ""}`}>
            {label}
          </span>
        ))}
      </div>
      <button onClick={() => onAdvance(feature)} disabled={busy || isDone}>
        {busy ? "Saving..." : isDone ? "Complete" : "Mark Next Complete"}
      </button>
    </div>
  );
}

function Phase2Board() {
  const [features, setFeatures] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [busyKey, setBusyKey] = useState(null);

  useEffect(() => {
    listPhase2Features()
      .then(setFeatures)
      .catch((e) => setLoadError(e.message));
  }, []);

  async function handleAdvance(feature) {
    setBusyKey(feature.issue_key);
    setLoadError(null);
    try {
      const updated = await advancePhase2Feature(feature.issue_key, feature.state);
      setFeatures((prev) => prev.map((f) => (f.issue_key === feature.issue_key ? { ...f, ...updated } : f)));
    } catch (e) {
      setLoadError(e.message);
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className="phase2-board">
      {loadError && <p className="load-error">{loadError}</p>}
      {features === null && !loadError && <p>Loading Features for this release...</p>}
      {features && features.length === 0 && (
        <p className="program-picker-empty">No Features found for this release.</p>
      )}
      {features && features.length > 0 && (
        <div className="feature-state-list">
          {features.map((f) => (
            <FeatureStateRow
              key={f.issue_key}
              feature={f}
              onAdvance={handleAdvance}
              busy={busyKey === f.issue_key}
            />
          ))}
        </div>
      )}
      <div className="phase2-summary-action">
        <button className="ghost-button" disabled title="Not yet wired up">
          Generate Exec Feature Summary
        </button>
      </div>
    </div>
  );
}

function PhaseCard({ phase, active, ...actions }) {
  const phaseClass = PHASE_CLASS[phase.phase_number];
  const isPhase2 = phase.phase_number === 2;
  return (
    <section className={`phase-card ${phaseClass} ${active ? "phase-active" : "phase-placeholder"}`}>
      <div className="phase-header">
        <span className="phase-number">{phase.phase_number}</span>
        <div>
          <h2>{phase.title}</h2>
          {!active && !isPhase2 && <p className="phase-coming-soon">Coming soon</p>}
        </div>
        {isPhase2 ? (
          <span className="status-badge status-in_progress">Active</span>
        ) : (
          <StatusBadge status={phase.status} />
        )}
      </div>

      {active ? (
        <div className="phase-body">
          {phase.children.map((child) => (
            <WorkflowNode key={child.id} node={child} {...actions} />
          ))}
        </div>
      ) : isPhase2 ? (
        <Phase2Board />
      ) : (
        <ul className="placeholder-task-list">
          {phase.children.map((child) => (
            <li key={child.id}>{child.title}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ReleaseGate({ selectedRelease, onSelect }) {
  const [editing, setEditing] = useState(!selectedRelease);
  const [releases, setReleases] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) return;
    setReleases(null);
    setLoadError(null);
    listReleases()
      .then((list) => {
        setReleases(list);
        if (list.length > 0) setChoice(list[0]);
      })
      .catch((e) => setLoadError(e.message));
  }, [editing]);

  async function submit(e) {
    e.preventDefault();
    if (!choice) return;
    setBusy(true);
    setLoadError(null);
    try {
      await onSelect(choice);
      setEditing(false);
    } catch (e) {
      setLoadError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="release-gate release-gate-summary">
        <span className="release-gate-label">Release</span>
        <span className="release-gate-value">{selectedRelease}</span>
        <button className="ghost-button" onClick={() => setEditing(true)}>
          Change
        </button>
      </div>
    );
  }

  return (
    <div className="release-gate release-gate-picker">
      <p className="release-gate-intro">
        Phases 2-5 apply to a single release. Select one to continue (pulled from the
        Release field on this program's linked Features).
      </p>
      {loadError && <p className="load-error">{loadError}</p>}
      {releases === null && !loadError && <p>Loading releases from Jira...</p>}
      {releases && releases.length === 0 && (
        <p className="program-picker-empty">
          No releases found yet -- set the Release field on this program's Features in Jira.
        </p>
      )}
      {releases && releases.length > 0 && (
        <form className="release-gate-form" onSubmit={submit}>
          <select value={choice} onChange={(e) => setChoice(e.target.value)}>
            {releases.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <button type="submit" disabled={busy}>
            {busy ? "Selecting..." : "Select Release"}
          </button>
        </form>
      )}
    </div>
  );
}

function ProgramPicker({ onSelect, onCreate }) {
  const [programs, setPrograms] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listJiraPrograms()
      .then(setPrograms)
      .catch((e) => setLoadError(e.message));
  }, []);

  async function pick(issueKey) {
    setBusy(true);
    setLoadError(null);
    try {
      await onSelect(issueKey);
    } catch (e) {
      setLoadError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitNew(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true);
    setLoadError(null);
    try {
      await onCreate(newName.trim());
    } catch (e) {
      setLoadError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="program-picker">
      <h2>Select a program</h2>
      <p className="program-picker-subtitle">
        Programs are stored as Task issues in Jira, so your work is never lost.
      </p>

      {loadError && <p className="load-error">{loadError}</p>}

      {programs === null && !loadError && <p>Loading programs from Jira...</p>}

      {programs && programs.length > 0 && (
        <ul className="program-list">
          {programs.map((p) => (
            <li key={p.issue_key} className="program-list-item">
              <span className="program-list-name">{p.name || p.issue_key}</span>
              <span className="program-list-key">{p.issue_key}</span>
              <button onClick={() => pick(p.issue_key)} disabled={busy}>
                Select
              </button>
            </li>
          ))}
        </ul>
      )}

      {programs && programs.length === 0 && (
        <p className="program-picker-empty">No programs yet -- create the first one below.</p>
      )}

      <form className="program-picker-new" onSubmit={submitNew}>
        <label>
          New program name
          <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} required />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Creating..." : "Create Program"}
        </button>
      </form>
    </div>
  );
}

function ProjectSetup({ project, onSave, saving, onSwitchProgram }) {
  const [name, setName] = useState(project.name || "");
  const [editing, setEditing] = useState(false);

  function submit(e) {
    e.preventDefault();
    onSave({ name });
    setEditing(false);
  }

  if (!editing) {
    return (
      <div className="project-setup project-setup-summary">
        <span className="project-name">{project.name}</span>
        <div className="project-setup-actions">
          <button className="ghost-button" onClick={() => setEditing(true)}>
            Edit
          </button>
          <button className="ghost-button" onClick={onSwitchProgram}>
            Switch Program
          </button>
        </div>
      </div>
    );
  }

  return (
    <form className="project-setup" onSubmit={submit}>
      <label>
        Program name
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <button type="submit" disabled={saving}>
        {saving ? "Saving..." : "Save"}
      </button>
    </form>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [noProgramSelected, setNoProgramSelected] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [nodeErrors, setNodeErrors] = useState({});
  const [savingProject, setSavingProject] = useState(false);

  async function refresh() {
    try {
      const result = await getWorkflow();
      setData(result);
      setLoadError(null);
      setNoProgramSelected(false);
    } catch (e) {
      if (e.status === 404) {
        setData(null);
        setNoProgramSelected(true);
      } else {
        setLoadError(e.message);
      }
    }
  }

  async function handleSelectProgram(issueKey) {
    const result = await selectProgram(issueKey);
    setData(result);
    setLoadError(null);
    setNoProgramSelected(false);
  }

  async function handleCreateProgram(name) {
    const result = await createProgram(name);
    setData(result);
    setLoadError(null);
    setNoProgramSelected(false);
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleComplete(id) {
    setBusyId(id);
    setNodeErrors((prev) => ({ ...prev, [id]: null }));
    try {
      await completeNode(id);
      await refresh();
    } catch (e) {
      setNodeErrors((prev) => ({ ...prev, [id]: e.message }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReopen(id) {
    setBusyId(id);
    try {
      await reopenNode(id);
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function handleRun(id) {
    setBusyId(id);
    setNodeErrors((prev) => ({ ...prev, [id]: null }));
    try {
      await runNode(id);
      await refresh();
    } catch (e) {
      setNodeErrors((prev) => ({ ...prev, [id]: e.message }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleSubmitCapacity(id, payload) {
    setBusyId(id);
    setNodeErrors((prev) => ({ ...prev, [id]: null }));
    try {
      await submitCapacity(id, payload);
      await refresh();
    } catch (e) {
      setNodeErrors((prev) => ({ ...prev, [id]: e.message }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleSaveProject(payload) {
    setSavingProject(true);
    try {
      await updateProject(payload);
      await refresh();
    } finally {
      setSavingProject(false);
    }
  }

  function handleSwitchProgram() {
    setData(null);
    setNoProgramSelected(true);
  }

  async function handleSelectRelease(release) {
    const result = await selectRelease(release);
    setData(result);
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="hero-row">
          <img className="hero-photo" src={heroImage} alt="" />
          <div className="hero-text">
            <div className="brand">
              <span className="brand-program">Program Management</span>
              <span className="brand-pilot">AI Assistant</span>
            </div>
            <p className="tagline">Plan Smarter | Align Teams | Execute with Confidence | Deliver Impact</p>
          </div>
        </div>
      </header>

      <main className="app-main">
        {loadError && <p className="load-error">Couldn't load the workflow: {loadError}</p>}

        {noProgramSelected && (
          <ProgramPicker onSelect={handleSelectProgram} onCreate={handleCreateProgram} />
        )}

        {data && (
          <>
            <ProjectSetup
              project={data.project}
              onSave={handleSaveProject}
              saving={savingProject}
              onSwitchProgram={handleSwitchProgram}
            />
            <div className="phase-list">
              {data.phases[0] && (
                <PhaseCard
                  key={data.phases[0].id}
                  phase={data.phases[0]}
                  active
                  busyId={busyId}
                  nodeErrors={nodeErrors}
                  onComplete={handleComplete}
                  onReopen={handleReopen}
                  onRun={handleRun}
                  onSubmitCapacity={handleSubmitCapacity}
                />
              )}

              {data.phases.length > 1 && (
                <div className="release-phases">
                  <ReleaseGate
                    selectedRelease={data.project.selected_release}
                    onSelect={handleSelectRelease}
                  />
                  {data.project.selected_release &&
                    data.phases.slice(1).map((phase) => (
                      <PhaseCard
                        key={phase.id}
                        phase={phase}
                        active={false}
                        busyId={busyId}
                        nodeErrors={nodeErrors}
                        onComplete={handleComplete}
                        onReopen={handleReopen}
                        onRun={handleRun}
                        onSubmitCapacity={handleSubmitCapacity}
                      />
                    ))}
                </div>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
