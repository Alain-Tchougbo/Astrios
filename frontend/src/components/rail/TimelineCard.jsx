import { Check, Milestone } from "lucide-react";

const STEPS = [
  { key: "questions", label: "Questions" },
  { key: "plan", label: "Plan" },
  { key: "documents", label: "Documents" },
  { key: "execution", label: "Exécution" },
];

// Statut de mission -> index de l'étape en cours. Chaque étape de génération
// (/generate-plan, /generate-documents, /generate-actions) est un appel réseau séparé
// et met à jour ce statut dès qu'elle se termine ; pendant l'attente d'une étape, le
// statut est encore celui de l'étape précédente, ce qui fait naturellement "pulser"
// (état actif) la bonne étape de la Timeline sans booléen dédié.
const STATUS_STEP = {
  nouvelle: 0,
  en_cours: 1,
  plan_pret: 2,
  documents_prets: 3,
  action_en_attente: 3,
  terminee: 4,
};

export default function TimelineCard({ statut, progress, actions = [] }) {
  const currentStep = STATUS_STEP[statut] ?? 0;

  const totalActions = actions.length;
  const treatedActions = actions.filter((a) => a.statut !== "en_attente").length;
  const showActionsProgress = statut === "action_en_attente" && totalActions > 1;

  return (
    <section className="rail-section">
      <div className="rail-section-header">
        <Milestone size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Timeline</span>
        {typeof progress === "number" && (
          <span className="rail-section-summary">{progress}%</span>
        )}
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
              <span className="timeline-label">
                {step.label}
                {step.key === "execution" && showActionsProgress && (
                  <span className="timeline-substep">
                    {treatedActions}/{totalActions} actions traitées
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
