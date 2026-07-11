import { Layers, Plus } from "lucide-react";

const STATUS_DOT = {
  nouvelle: "var(--text-tertiary)",
  en_cours: "var(--accent-bright)",
  terminee: "var(--accent)",
};

export default function Sidebar({ missions, selectedId, onSelect, onNewMission }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand-mark" />
        <span className="sidebar-brand-name">Astrios</span>
      </div>

      <button className="btn-new-mission" onClick={onNewMission}>
        <Plus size={16} strokeWidth={2.5} />
        Nouvelle mission
      </button>

      <div className="sidebar-section-label">
        <Layers size={14} strokeWidth={2.25} />
        <span>Missions</span>
      </div>

      <nav className="mission-list">
        {missions.length === 0 && (
          <p className="mission-list-empty">Aucune mission pour l'instant.</p>
        )}
        {missions.map((mission) => (
          <button
            key={mission.id}
            className={`mission-item ${mission.id === selectedId ? "active" : ""}`}
            onClick={() => onSelect(mission.id)}
          >
            <span
              className="mission-item-dot"
              style={{ background: STATUS_DOT[mission.statut] || STATUS_DOT.nouvelle }}
            />
            <span className="mission-item-title">{mission.titre}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
