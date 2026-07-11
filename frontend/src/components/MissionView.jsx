import { useEffect, useState } from "react";
import OrionChat from "./OrionChat";
import DocumentViewer from "./DocumentViewer";
import TimelineCard from "./rail/TimelineCard";
import PlanCard from "./rail/PlanCard";
import DocumentsCard from "./rail/DocumentsCard";
import ActionsCard from "./rail/ActionsCard";
import { getActions, getDocument, getDocuments, getTasks } from "../api";

const STATUS_PROGRESS = {
  nouvelle: 10,
  en_cours: 25,
  plan_pret: 50,
  documents_prets: 75,
  action_en_attente: 90,
  terminee: 100,
};

const STATUS_LABEL = {
  nouvelle: "Nouvelle",
  en_cours: "En cours",
  plan_pret: "Plan prêt",
  documents_prets: "Documents prêts",
  action_en_attente: "Action en attente",
  terminee: "Terminée",
};

export default function MissionView({ mission, onMissionUpdated }) {
  const progress = STATUS_PROGRESS[mission.statut] ?? 10;
  const [tasks, setTasks] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [actions, setActions] = useState([]);
  const [selectedDoc, setSelectedDoc] = useState(null);
  const [planGenerating, setPlanGenerating] = useState(false);
  const [documentsGenerating, setDocumentsGenerating] = useState(false);

  useEffect(() => {
    getTasks(mission.id)
      .then(setTasks)
      .catch(() => setTasks([]));
    getDocuments(mission.id)
      .then(setDocuments)
      .catch(() => setDocuments([]));
    getActions(mission.id)
      .then(setActions)
      .catch(() => setActions([]));
  }, [mission.id, mission.statut]);

  useEffect(() => {
    setSelectedDoc(null);
  }, [mission.id]);

  async function handleSelectDocument(doc) {
    try {
      const detail = await getDocument(mission.id, doc.id);
      setSelectedDoc(detail);
    } catch {
      setSelectedDoc(doc);
    }
  }

  function handleActionUpdated() {
    getActions(mission.id)
      .then(setActions)
      .catch(() => {});
    onMissionUpdated?.();
  }

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
          {selectedDoc ? (
            <DocumentViewer doc={selectedDoc} onBack={() => setSelectedDoc(null)} />
          ) : (
            <OrionChat
              missionId={mission.id}
              missionStatut={mission.statut}
              onDiscoveryComplete={onMissionUpdated}
              onPlanGeneratingChange={setPlanGenerating}
              onDocumentsGeneratingChange={setDocumentsGenerating}
            />
          )}
        </div>
        <aside className="mission-rail">
          <TimelineCard
            statut={mission.statut}
            planGenerating={planGenerating}
            documentsGenerating={documentsGenerating}
          />
          <PlanCard tasks={tasks} />
          <DocumentsCard documents={documents} onSelect={handleSelectDocument} />
          <ActionsCard missionId={mission.id} actions={actions} onActionUpdated={handleActionUpdated} />
        </aside>
      </div>
    </div>
  );
}
