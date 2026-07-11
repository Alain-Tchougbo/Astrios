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

  useEffect(() => {
    refreshMissions(true);
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

  return (
    <div className="app-shell">
      <Sidebar
        missions={missions}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onNewMission={() => setSelectedId(null)}
      />
      <main className="app-main">
        {error && <div className="app-error">{error}</div>}
        {selectedMission ? (
          <MissionView mission={selectedMission} />
        ) : (
          <EmptyState onCreate={handleCreate} creating={creating} />
        )}
      </main>
    </div>
  );
}

export default App;
