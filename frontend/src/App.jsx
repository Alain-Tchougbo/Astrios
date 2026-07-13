import { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";
import EmptyState from "./components/EmptyState";
import MissionView from "./components/MissionView";
import { getMissions, getMission, createMission } from "./api";
import "./App.css";

function App() {
  const [missions, setMissions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedMission, setSelectedMission] = useState(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    refreshMissions(true);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("google_connected");
    const googleError = params.get("google_error");

    if (connected === "true") {
      setNotice("Google Calendar connecté avec succès.");
    } else if (googleError) {
      setNotice(`Échec de la connexion à Google Calendar (${googleError}).`);
    }

    if (connected || googleError) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  useEffect(() => {
    if (selectedId == null) {
      setSelectedMission(null);
      return;
    }
    getMission(selectedId)
      .then(setSelectedMission)
      .catch(() => setError("Impossible de charger cette mission."));
  }, [selectedId]);

  async function refreshMissions(selectFirst = false) {
    try {
      const data = await getMissions();
      setMissions(data);
      if (selectFirst && data.length > 0) {
        setSelectedId(data[0].id);
      }
    } catch {
      setError("Backend injoignable sur localhost:8000.");
    }
  }

  async function handleCreate(titre, objectif) {
    setCreating(true);
    setError(null);
    try {
      const mission = await createMission(titre, objectif);
      await refreshMissions(false);
      setSelectedId(mission.id);
    } catch {
      setError("La création de la mission a échoué.");
    } finally {
      setCreating(false);
    }
  }

  async function handleMissionUpdated() {
    if (selectedId == null) return;
    try {
      const detail = await getMission(selectedId);
      setSelectedMission(detail);
      setMissions((prev) => prev.map((m) => (m.id === detail.id ? { ...m, statut: detail.statut } : m)));
    } catch {
      setError("Impossible de rafraîchir cette mission.");
    }
  }

  function handleMissionRenamed(missionId, newTitre) {
    setMissions((prev) => prev.map((m) => (m.id === missionId ? { ...m, titre: newTitre } : m)));
    setSelectedMission((prev) => (prev && prev.id === missionId ? { ...prev, titre: newTitre } : prev));
  }

  function handleMissionDeleted(missionId) {
    setMissions((prev) => prev.filter((m) => m.id !== missionId));
    if (selectedId === missionId) {
      setSelectedId(null);
    }
  }

  return (
    <div className="app-shell">
      <Sidebar
        missions={missions}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onNewMission={() => setSelectedId(null)}
        onMissionRenamed={handleMissionRenamed}
        onMissionDeleted={handleMissionDeleted}
      />
      <main className="app-main">
        {notice && (
          <div className="app-notice">
            {notice}
            <button className="app-notice-close" onClick={() => setNotice(null)} aria-label="Fermer">
              ×
            </button>
          </div>
        )}
        {error && <div className="app-error">{error}</div>}
        {selectedMission ? (
          <MissionView
            mission={selectedMission}
            onMissionUpdated={handleMissionUpdated}
            onMissionRenamed={handleMissionRenamed}
            onMissionDeleted={handleMissionDeleted}
          />
        ) : (
          <EmptyState onCreate={handleCreate} creating={creating} />
        )}
      </main>
    </div>
  );
}

export default App;
