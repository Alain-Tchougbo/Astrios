import { useEffect, useRef, useState } from "react";
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import OrionChat from "./OrionChat";
import DocumentViewer from "./DocumentViewer";
import TimelineCard from "./rail/TimelineCard";
import PlanCard from "./rail/PlanCard";
import DocumentsCard from "./rail/DocumentsCard";
import ActionsCard from "./rail/ActionsCard";
import { getActions, getDocument, getDocuments, getTasks } from "../api";
import { useMissionRenameDelete } from "../hooks/useMissionRenameDelete";

// Progression recalculée à partir de l'état réel (tâches cochées / total, actions
// traitées / total) plutôt qu'une valeur figée par statut — une mission "terminee"
// qui se réouvre (nouveau besoin ajouté après coup, voir chat_with_orion côté
// backend) doit refléter le vrai ratio de travail accompli, pas retomber sur un
// pourcentage bas générique qui sous-estimerait tout ce qui était déjà fait, ni
// rester figée à 100% pendant que de nouvelles tâches/actions sont en cours.
function computeProgress(statut, tasks, actions) {
  if (statut === "nouvelle") return 10;
  if (tasks.length === 0) return 25;
  const taskRatio = tasks.filter((t) => t.statut === "terminee").length / tasks.length;
  if (actions.length === 0) return Math.round(taskRatio * 100);
  const actionRatio = actions.filter((a) => a.statut !== "en_attente").length / actions.length;
  return Math.round(((taskRatio + actionRatio) / 2) * 100);
}

const STATUS_LABEL = {
  nouvelle: "Nouvelle",
  en_cours: "En cours",
  plan_pret: "Plan prêt",
  documents_prets: "Documents prêts",
  action_en_attente: "Action en attente",
  terminee: "Terminée",
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function MissionView({ mission, onMissionUpdated, onMissionRenamed, onMissionDeleted }) {
  const [tasks, setTasks] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [actions, setActions] = useState([]);
  const progress = computeProgress(mission.statut, tasks, actions);
  const [selectedDoc, setSelectedDoc] = useState(null);

  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(mission.titre);
  const cancelingRef = useRef(false);
  const { rename, remove } = useMissionRenameDelete({
    onRenamed: onMissionRenamed,
    onDeleted: onMissionDeleted,
  });

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
    setMenuOpen(false);
    setEditing(false);
    setEditValue(mission.titre);
  }, [mission.id]);

  useEffect(() => {
    function handleClickOutside(e) {
      if (!e.target.closest(".mission-header-menu-wrap")) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function handleSelectDocument(doc) {
    try {
      const detail = await getDocument(mission.id, doc.id);
      setSelectedDoc(detail);
    } catch {
      setSelectedDoc(doc);
    }
  }

  function handleDocumentUpdated(updatedDoc) {
    setSelectedDoc(updatedDoc);
    setDocuments((prev) => prev.map((d) => (d.id === updatedDoc.id ? updatedDoc : d)));
  }

  function handleActionUpdated() {
    getActions(mission.id)
      .then(setActions)
      .catch(() => {});
    getTasks(mission.id)
      .then(setTasks)
      .catch(() => {});
    onMissionUpdated?.();
  }

  // Coche les tâches liées une par une avec un court délai entre chaque, pour donner
  // une vraie sensation de progression séquentielle au lieu d'un cochage instantané en
  // bloc. Purement visuel : le refetch déclenché juste après par ActionsCard (via
  // onActionUpdated) reste la source de vérité finale.
  async function handleAllActionsApproved(results) {
    const taskIds = results
      .filter((r) => r.success && r.action.task_id)
      .map((r) => r.action.task_id);
    for (const taskId of taskIds) {
      await sleep(450);
      setTasks((prev) => prev.map((t) => (t.id === taskId ? { ...t, statut: "terminee" } : t)));
    }
  }

  async function commitRename() {
    const value = editValue;
    setEditing(false);
    try {
      await rename(mission, value);
    } catch {
      // échec silencieux : le titre reste inchangé, l'utilisateur peut réessayer
    }
  }

  function handleEditKeyDown(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      commitRename();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelingRef.current = true;
      setEditing(false);
      setEditValue(mission.titre);
    }
  }

  function handleEditBlur() {
    if (cancelingRef.current) {
      cancelingRef.current = false;
      return;
    }
    commitRename();
  }

  async function handleDelete() {
    setMenuOpen(false);
    try {
      await remove(mission);
    } catch {
      // échec silencieux : la mission reste affichée, l'utilisateur peut réessayer
    }
  }

  return (
    <div className="mission-view">
      <header className="mission-header">
        <div className="mission-header-top">
          <div className="mission-header-title-group">
            {editing ? (
              <input
                className="mission-header-edit-input"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={handleEditKeyDown}
                onBlur={handleEditBlur}
                autoFocus
              />
            ) : (
              <h1>{mission.titre}</h1>
            )}
            <span className={`mission-status-badge ${mission.statut}`}>
              {STATUS_LABEL[mission.statut] ?? mission.statut}
            </span>
          </div>

          <div className="mission-header-menu-wrap">
            <button
              className="mission-header-menu-btn"
              onClick={() => setMenuOpen((v) => !v)}
              aria-label="Options de la mission"
            >
              <MoreHorizontal size={18} strokeWidth={2.25} />
            </button>

            {menuOpen && (
              <div className="context-menu">
                <button
                  className="context-menu-item"
                  onClick={() => {
                    setMenuOpen(false);
                    setEditValue(mission.titre);
                    setEditing(true);
                  }}
                >
                  <Pencil size={13} strokeWidth={2.25} />
                  Renommer
                </button>
                <button className="context-menu-item danger" onClick={handleDelete}>
                  <Trash2 size={13} strokeWidth={2.25} />
                  Supprimer
                </button>
              </div>
            )}
          </div>
        </div>

        {mission.objectif && <p className="mission-objectif">{mission.objectif}</p>}
      </header>

      <div className="mission-body">
        <div className="mission-chat-col">
          {selectedDoc ? (
            <DocumentViewer
              doc={selectedDoc}
              missionId={mission.id}
              onBack={() => setSelectedDoc(null)}
              onDocumentUpdated={handleDocumentUpdated}
            />
          ) : (
            <OrionChat
              missionId={mission.id}
              missionStatut={mission.statut}
              onMissionUpdated={onMissionUpdated}
            />
          )}
        </div>
        <aside className="mission-rail">
          <TimelineCard statut={mission.statut} progress={progress} actions={actions} />
          <PlanCard tasks={tasks} />
          <DocumentsCard
            documents={documents}
            onSelect={handleSelectDocument}
            missionStatut={mission.statut}
          />
          <ActionsCard
            missionId={mission.id}
            actions={actions}
            onActionUpdated={handleActionUpdated}
            onAllApproved={handleAllActionsApproved}
          />
        </aside>
      </div>
    </div>
  );
}
