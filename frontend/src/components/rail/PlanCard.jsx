import { useEffect, useState } from "react";
import { Check, ChevronDown, ChevronUp, ListChecks } from "lucide-react";

export default function PlanCard({ tasks }) {
  const [expanded, setExpanded] = useState(true);
  const [autoCollapsedOnce, setAutoCollapsedOnce] = useState(false);

  const doneCount = tasks.filter((t) => t.statut === "terminee").length;
  const allDone = tasks.length > 0 && doneCount === tasks.length;

  useEffect(() => {
    if (allDone && !autoCollapsedOnce) {
      setExpanded(false);
      setAutoCollapsedOnce(true);
    }
    if (!allDone && autoCollapsedOnce) {
      setAutoCollapsedOnce(false);
    }
  }, [allDone, autoCollapsedOnce]);

  return (
    <section className="rail-section">
      <button className="rail-section-header rail-section-toggle" onClick={() => setExpanded((v) => !v)}>
        <ListChecks size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Plan</span>
        {tasks.length > 0 && (
          <span className="rail-section-summary">
            {doneCount}/{tasks.length} tâche{tasks.length > 1 ? "s" : ""} terminée{doneCount > 1 ? "s" : ""}
          </span>
        )}
        {expanded ? (
          <ChevronUp size={15} strokeWidth={2.25} className="rail-chevron" />
        ) : (
          <ChevronDown size={15} strokeWidth={2.25} className="rail-chevron" />
        )}
      </button>

      <div className={`rail-section-body ${expanded ? "" : "collapsed"}`}>
        {tasks.length === 0 ? (
          <div className="rail-empty">Le plan n'a pas encore été généré.</div>
        ) : (
          <ul className="task-list">
            {tasks.map((task) => {
              const checked = task.statut === "terminee";
              return (
                <li key={task.id} className="task-item">
                  {/* Indicateur en lecture seule : le cochage est 100% automatique,
                      déclenché côté backend quand l'action liée à cette tâche est
                      exécutée (voir approve_action). Aucune interaction manuelle. */}
                  <span
                    className={`task-checkbox ${checked ? "checked" : ""}`}
                    aria-hidden="true"
                  >
                    {checked && <Check size={11} strokeWidth={3} />}
                  </span>
                  <div className="task-body">
                    <span className={`task-title ${checked ? "done" : ""}`}>{task.titre}</span>
                    {task.description && (
                      <span className="task-description">{task.description}</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
