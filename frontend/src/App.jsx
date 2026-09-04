import { useEffect, useState } from "react";
import { completeNode, downloadUrl, getWorkflow, reopenNode, runNode } from "./api.js";

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

function StepRow({ node, busyId, nodeErrors, onComplete, onRun, onReopen }) {
  const isBusy = busyId === node.id;
  const isAutomated = node.automation_type === "automated";
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

        {node.output && node.status === "complete" && node.title === "Provide Development Capacity" && (
          <p className="step-result">{formatCapacity(node.output)}</p>
        )}
        {node.output_file_id && (
          <a className="step-download" href={downloadUrl(node.output_file_id)}>
            Download roadmap options (.docx)
          </a>
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
        {node.status === "complete" && (
          <button className="ghost-button" onClick={() => onReopen(node.id)} disabled={isBusy}>
            Undo
          </button>
        )}
      </div>
    </div>
  );
}

function formatCapacity(outputJson) {
  try {
    const data = JSON.parse(outputJson);
    return `Total capacity: ${data.total_frontend_days} frontend / ${data.total_backend_days} backend staff-days across ${data.feature_count} feature(s).`;
  } catch {
    return null;
  }
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

function PhaseCard({ phase, active, ...actions }) {
  const phaseClass = PHASE_CLASS[phase.phase_number];
  return (
    <section className={`phase-card ${phaseClass} ${active ? "phase-active" : "phase-placeholder"}`}>
      <div className="phase-header">
        <span className="phase-number">{phase.phase_number}</span>
        <div>
          <h2>{phase.title}</h2>
          {!active && <p className="phase-coming-soon">Coming soon</p>}
        </div>
        <StatusBadge status={phase.status} />
      </div>

      {active ? (
        <div className="phase-body">
          {phase.children.map((child) => (
            <WorkflowNode key={child.id} node={child} {...actions} />
          ))}
        </div>
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

export default function App() {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [nodeErrors, setNodeErrors] = useState({});

  async function refresh() {
    try {
      const result = await getWorkflow();
      setData(result);
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
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

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-program">Program</span> <span className="brand-pilot">Pilot</span>
        </div>
        <p className="tagline">Plan Smarter | Align Teams | Execute with Confidence | Deliver Impact</p>
      </header>

      <main className="app-main">
        {loadError && <p className="load-error">Couldn't load the workflow: {loadError}</p>}

        {data && (
          <>
            <p className="project-name">{data.project.name}</p>
            <div className="phase-list">
              {data.phases.map((phase) => (
                <PhaseCard
                  key={phase.id}
                  phase={phase}
                  active={phase.phase_number === 1}
                  busyId={busyId}
                  nodeErrors={nodeErrors}
                  onComplete={handleComplete}
                  onReopen={handleReopen}
                  onRun={handleRun}
                />
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
