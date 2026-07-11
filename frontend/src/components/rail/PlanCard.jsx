import { ListChecks } from "lucide-react";

export default function PlanCard({ tasks }) {
  return (
    <section className="rail-section">
      <div className="rail-section-header">
        <ListChecks size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Plan</span>
      </div>

      {tasks.length === 0 ? (
        <div className="rail-empty">Le plan n'a pas encore été généré.</div>
      ) : (
        <ul className="task-list">
          {tasks.map((task) => (
            <li key={task.id} className="task-item">
              <span className="task-checkbox" aria-hidden="true" />
              <div className="task-body">
                <span className="task-title">{task.titre}</span>
                {task.description && (
                  <span className="task-description">{task.description}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
