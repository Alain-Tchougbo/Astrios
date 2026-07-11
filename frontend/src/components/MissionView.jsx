import OrionChat from "./OrionChat";
import TimelineCard from "./rail/TimelineCard";
import DocumentsCard from "./rail/DocumentsCard";
import ActionsCard from "./rail/ActionsCard";

const STATUS_PROGRESS = {
  nouvelle: 8,
  en_cours: 45,
  terminee: 100,
};

const STATUS_LABEL = {
  nouvelle: "Nouvelle",
  en_cours: "En cours",
  terminee: "Terminée",
};

export default function MissionView({ mission }) {
  const progress = STATUS_PROGRESS[mission.statut] ?? 8;

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
          <OrionChat missionId={mission.id} />
        </div>
        <aside className="mission-rail">
          <TimelineCard statut={mission.statut} />
          <DocumentsCard />
          <ActionsCard />
        </aside>
      </div>
    </div>
  );
}
