import { useEffect, useState } from "react";
import OrionChat from "./OrionChat";
import TimelineCard from "./rail/TimelineCard";
import PlanCard from "./rail/PlanCard";
import DocumentsCard from "./rail/DocumentsCard";
import ActionsCard from "./rail/ActionsCard";
import { getTasks } from "../api";

const STATUS_PROGRESS = {
  nouvelle: 8,
  en_cours: 35,
  plan_pret: 60,
  terminee: 100,
};

const STATUS_LABEL = {
  nouvelle: "Nouvelle",
  en_cours: "En cours",
  plan_pret: "Plan prêt",
  terminee: "Terminée",
};

export default function MissionView({ mission, onMissionUpdated }) {
  const progress = STATUS_PROGRESS[mission.statut] ?? 8;
  const [tasks, setTasks] = useState([]);
  const [planGenerating, setPlanGenerating] = useState(false);

  useEffect(() => {
    getTasks(mission.id)
      .then(setTasks)
      .catch(() => setTasks([]));
  }, [mission.id, mission.statut]);

  return (
    <div className="mission-view">
      <header className="mission-header">
        <div className="mission-header-top">
          <h1>{mission.titre}</h1>
          <span className={`mission-status-badge ${mission.statut}`}>
            <span className="status-dot" />
            {STATUS_LABEL[mission.statut] ?? mission.statut}
          </span>
        </div>
        {mission.objectif && <p className="mission-objectif">{mission.objectif}</p>}
        <div className="progress-row">
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${progress}%` }} />
          </div>
          <span className="progress-percent">{progress}%</span>
        </div>
      </header>

      <div className="mission-body">
        <div className="mission-chat-col">
          <OrionChat
            missionId={mission.id}
            onDiscoveryComplete={onMissionUpdated}
            onPlanGeneratingChange={setPlanGenerating}
          />
        </div>
        <aside className="mission-rail">
          <TimelineCard statut={mission.statut} planGenerating={planGenerating} />
          <PlanCard tasks={tasks} />
          <DocumentsCard />
          <ActionsCard />
        </aside>
      </div>
    </div>
  );
}
