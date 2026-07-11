import { Check, Milestone } from "lucide-react";

const STEPS = [
  { key: "questions", label: "Questions" },
  { key: "plan", label: "Plan" },
  { key: "documents", label: "Documents" },
  { key: "execution", label: "Exécution" },
];

// Statut de mission -> index de l'étape en cours (squelette, sera piloté par Orion plus tard).
const STATUS_STEP = {
  nouvelle: 0,
  en_cours: 1,
  terminee: 4,
};

export default function TimelineCard({ statut }) {
  const currentStep = STATUS_STEP[statut] ?? 0;

  return (
    <section className="rail-section">
      <div className="rail-section-header">
        <Milestone size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Timeline</span>
      </div>

      <ul className="timeline-list">
        {STEPS.map((step, i) => {
          const done = i < currentStep;
          const active = i === currentStep;
          return (
            <li key={step.key} className={`timeline-step ${done ? "done" : ""} ${active ? "active" : ""}`}>
              <span className="timeline-marker">
                {done && <Check size={12} strokeWidth={3} />}
                {active && <span className="timeline-active-dot" />}
              </span>
              <span className="timeline-label">{step.label}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
