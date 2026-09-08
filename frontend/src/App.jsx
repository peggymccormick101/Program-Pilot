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

function StepRow({ node, busyId, nodeErrors, onComplete, onRun, onReopen, onSubmitCapacity, allowUndo = true }) {
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
        {node.status === "complete" && !isInput && allowUndo && (
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

// Titles match exactly what's tracked in Jira's per-Feature "Feature
// State" field (see backend FEATURE_STATE_SEQUENCE) -- description is
// left blank for now, ready for explanatory text once supplied.
const PHASE2_STEPS = [
  { title: "Define & Review Requirements", state: "RequirementsApproved", description: null },
  { title: "Define & Review Architecture", state: "ArchitectureApproved", description: null },
  { title: "Define Epics", state: "EpicsDefined", description: null },
  { title: "Estimate Epics (development)", state: "DevEstimated", description: null },
  { title: "Commit the Release", state: "FeatureCommitted", description: null },
  { title: "Update the Roadmap & Jira", state: "RoadmapJiraUpdated", description: null },
];

// Builds WorkflowNodeOut-shaped virtual nodes for the selected Feature,
// so Phase 2 (and later 3-5) can render through the exact same
// StepRow/WorkflowNode components Phase 1 uses -- same look, same
// interaction pattern -- instead of a bespoke component.
function buildFeatureStepNodes(feature) {
  const nextIndex = feature.state_index + 1;
  return PHASE2_STEPS.map((step, i) => ({
    id: step.state,
    title: step.title,
    description: step.description,
    phase_number: 2,
    is_leaf: true,
    automation_type: "manual",
    ai_harness: null,
    status: i <= feature.state_index ? "complete" : i === nextIndex ? "available" : "locked",
    completed_at: null,
    output: null,
    output_file_id: null,
    children: [],
  }));
}

function Phase2Board({ feature, busy, error, onAdvance }) {
  const stepNodes = feature ? buildFeatureStepNodes(feature) : [];
  // Only the "available" (next) step is ever clickable, so a single
  // shared busyId is enough -- StepRow compares it against each node's
  // own id, so setting it to any other node's id would wrongly mark
  // that node as busy too.
  const nextStep = feature ? PHASE2_STEPS[feature.state_index + 1] : null;
  const busyId = busy && nextStep ? nextStep.state : null;
  const nodeErrors = error && nextStep ? { [nextStep.state]: error } : {};
  return (
    <div className="phase2-board">
      <a
        className="phase2-template-link"
        href="https://raw.githubusercontent.com/peggymccormick101/Program-Pilot/main/Feature_Technical_Specification_Template_v1.docx"
        target="_blank"
        rel="noreferrer"
      >
        Feature Technical Specification Template (reference for Requirements &amp; Architecture)
      </a>

      {!feature && <p>Select a Feature above to see its steps.</p>}

      {feature && (
        <div className="task-group-children">
          {stepNodes.map((node) => (
            <WorkflowNode
              key={node.id}
              node={node}
              busyId={busyId}
              nodeErrors={nodeErrors}
              onComplete={() => onAdvance(feature)}
              onRun={() => {}}
              onReopen={() => {}}
              onSubmitCapacity={() => {}}
              allowUndo={false}
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

function PhaseCard({
  phase,
  active,
  phase2Features,
  selectedFeature,
  phase2Busy,
  phase2Error,
  onAdvancePhase2Feature,
  ...actions
}) {
  const phaseClass = PHASE_CLASS[phase.phase_number];
  const isPhase2 = phase.phase_number === 2;
  const allPhase2Complete =
    isPhase2 &&
    phase2Features &&
    phase2Features.length > 0 &&
    phase2Features.every((f) => f.state_index >= PHASE2_STEPS.length - 1);

  return (
    <section className={`phase-card ${phaseClass} ${active ? "phase-active" : "phase-placeholder"}`}>
      <div className="phase-header">
        <span className="phase-number">{phase.phase_number}</span>
        <div>
          <h2>{phase.title}</h2>
          {!active && !isPhase2 && <p className="phase-coming-soon">Coming soon</p>}
        </div>
        {isPhase2 ? (
          <span className={`status-badge status-${allPhase2Complete ? "complete" : "in_progress"}`}>
            {allPhase2Complete ? "Complete" : "Active"}
          </span>
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
        <Phase2Board
          feature={selectedFeature}
          busy={phase2Busy}
          error={phase2Error}
          onAdvance={onAdvancePhase2Feature}
        />
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

function FeatureGate({ features, loadError, selectedFeatureKey, onSelect }) {
  const [editing, setEditing] = useState(true);
  const [choice, setChoice] = useState("");

  useEffect(() => {
    if (selectedFeatureKey) setEditing(false);
  }, [selectedFeatureKey]);

  useEffect(() => {
    if (features && features.length > 0 && !choice) setChoice(features[0].issue_key);
  }, [features]);

  function submit(e) {
    e.preventDefault();
    if (!choice) return;
    onSelect(choice);
    setEditing(false);
  }

  const selected = features?.find((f) => f.issue_key === selectedFeatureKey);

  if (!editing && selected) {
    return (
      <div className="release-gate release-gate-summary">
        <span className="release-gate-label">Feature</span>
        <span className="release-gate-value">
          {selected.feature_id || selected.issue_key} - {selected.summary}
        </span>
        <button className="ghost-button" onClick={() => setEditing(true)}>
          Change
        </button>
      </div>
    );
  }

  return (
    <div className="release-gate release-gate-picker">
      <p className="release-gate-intro">
        Phases 2-5 apply to a single Feature within this release. Select one to continue.
      </p>
      {loadError && <p className="load-error">{loadError}</p>}
      {features === null && !loadError && <p>Loading Features for this release...</p>}
      {features && features.length === 0 && (
        <p className="program-picker-empty">No Features found for this release.</p>
      )}
      {features && features.length > 0 && (
        <form className="release-gate-form" onSubmit={submit}>
          <select value={choice} onChange={(e) => setChoice(e.target.value)}>
            {features.map((f) => (
              <option key={f.issue_key} value={f.issue_key}>
                {f.feature_id || f.issue_key} - {f.summary}
              </option>
            ))}
          </select>
          <button type="submit">Select Feature</button>
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
  const [phase2Features, setPhase2Features] = useState(null);
  const [phase2Error, setPhase2Error] = useState(null);
  const [phase2Busy, setPhase2Busy] = useState(false);
  const [selectedFeatureKey, setSelectedFeatureKey] = useState(null);

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

  useEffect(() => {
    setSelectedFeatureKey(null);
    if (!data?.project?.selected_release) {
      setPhase2Features(null);
      return;
    }
    setPhase2Features(null);
    setPhase2Error(null);
    listPhase2Features()
      .then(setPhase2Features)
      .catch((e) => setPhase2Error(e.message));
  }, [data?.project?.id, data?.project?.selected_release]);

  async function handleAdvancePhase2Feature(feature) {
    setPhase2Busy(true);
    setPhase2Error(null);
    try {
      const updated = await advancePhase2Feature(feature.issue_key, feature.state);
      setPhase2Features((prev) => prev.map((f) => (f.issue_key === feature.issue_key ? { ...f, ...updated } : f)));
    } catch (e) {
      setPhase2Error(e.message);
    } finally {
      setPhase2Busy(false);
    }
  }

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
                  {data.project.selected_release && (
                    <FeatureGate
                      features={phase2Features}
                      loadError={phase2Error}
                      selectedFeatureKey={selectedFeatureKey}
                      onSelect={setSelectedFeatureKey}
                    />
                  )}
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
                        phase2Features={phase2Features}
                        selectedFeature={phase2Features?.find((f) => f.issue_key === selectedFeatureKey) || null}
                        phase2Busy={phase2Busy}
                        phase2Error={phase2Error}
                        onAdvancePhase2Feature={handleAdvancePhase2Feature}
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
