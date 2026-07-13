import { useEffect, useRef, useState } from "react";
import {
  Layers,
  MoreVertical,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Settings,
  Trash2,
} from "lucide-react";
import logo from "../assets/logo.svg";
import { useMissionRenameDelete } from "../hooks/useMissionRenameDelete";

export default function Sidebar({
  missions,
  selectedId,
  onSelect,
  onNewMission,
  onMissionRenamed,
  onMissionDeleted,
}) {
  const [menuOpenId, setMenuOpenId] = useState(null);
  const [menuOpensUp, setMenuOpensUp] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editValue, setEditValue] = useState("");
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const cancelingRef = useRef(false);
  const { rename, remove } = useMissionRenameDelete({
    onRenamed: onMissionRenamed,
    onDeleted: onMissionDeleted,
  });

  useEffect(() => {
    function handleClickOutside(e) {
      if (!e.target.closest(".mission-item-menu-wrap")) {
        setMenuOpenId(null);
      }
      if (!e.target.closest(".sidebar-user-wrap")) {
        setProfileMenuOpen(false);
        setSettingsMessage(null);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function handleToggleMenu(e, mission) {
    if (menuOpenId === mission.id) {
      setMenuOpenId(null);
      return;
    }
    // Estimation de la hauteur du menu (2 items + padding) : pas besoin de le
    // mesurer réellement, sa structure est fixe (Renommer/Supprimer).
    const ESTIMATED_MENU_HEIGHT = 90;
    const rect = e.currentTarget.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    setMenuOpensUp(spaceBelow < ESTIMATED_MENU_HEIGHT);
    setMenuOpenId(mission.id);
  }

  function handleStartRename(mission) {
    setMenuOpenId(null);
    setEditingId(mission.id);
    setEditValue(mission.titre);
  }

  async function commitRename(mission) {
    const value = editValue;
    setEditingId(null);
    try {
      await rename(mission, value);
    } catch {
      // échec silencieux : le titre reste inchangé côté liste, l'utilisateur peut réessayer
    }
  }

  function handleEditKeyDown(e, mission) {
    if (e.key === "Enter") {
      e.preventDefault();
      commitRename(mission);
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelingRef.current = true;
      setEditingId(null);
    }
  }

  function handleEditBlur(mission) {
    if (cancelingRef.current) {
      cancelingRef.current = false;
      return;
    }
    commitRename(mission);
  }

  async function handleDelete(mission) {
    setMenuOpenId(null);
    try {
      await remove(mission);
    } catch {
      // échec silencieux : la mission reste dans la liste, l'utilisateur peut réessayer
    }
  }

  function handleSettingsClick() {
    setSettingsMessage("Bientôt disponible.");
  }

  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-brand">
        <img src={logo} alt="" className="sidebar-brand-mark" />
        {!collapsed && <span className="sidebar-brand-name">Astrios</span>}
        <button
          className="sidebar-collapse-btn"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? "Agrandir la barre latérale" : "Réduire la barre latérale"}
          title={collapsed ? "Agrandir" : "Réduire"}
        >
          {collapsed ? (
            <PanelLeftOpen size={16} strokeWidth={2.25} />
          ) : (
            <PanelLeftClose size={16} strokeWidth={2.25} />
          )}
        </button>
      </div>

      <button className="btn-new-mission" onClick={onNewMission} title="Nouvelle mission">
        <Plus size={16} strokeWidth={2.5} />
        {!collapsed && "Nouvelle mission"}
      </button>

      {!collapsed && (
        <div className="sidebar-section-label">
          <Layers size={13} strokeWidth={2.25} />
          <span>Missions</span>
        </div>
      )}

      <nav className="mission-list">
        {missions.length === 0 && !collapsed && (
          <p className="mission-list-empty">Aucune mission pour l'instant.</p>
        )}
        {missions.map((mission) => (
          <div
            key={mission.id}
            className={`mission-item-row ${mission.id === selectedId ? "active" : ""} ${
              menuOpenId === mission.id ? "menu-open" : ""
            }`}
          >
            {editingId === mission.id && !collapsed ? (
              <input
                className="mission-item-edit-input"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => handleEditKeyDown(e, mission)}
                onBlur={() => handleEditBlur(mission)}
                autoFocus
              />
            ) : (
              <>
                <button
                  className="mission-item"
                  onClick={() => onSelect(mission.id)}
                  title={collapsed ? mission.titre : undefined}
                >
                  {collapsed && (
                    <span className="mission-item-avatar">
                      {mission.titre?.trim().charAt(0).toUpperCase() || "?"}
                    </span>
                  )}
                  {!collapsed && <span className="mission-item-title">{mission.titre}</span>}
                </button>

                {!collapsed && (
                  <div className="mission-item-menu-wrap">
                    <button
                      className="mission-item-menu-btn"
                      onClick={(e) => handleToggleMenu(e, mission)}
                      aria-label="Options de la mission"
                    >
                      <MoreVertical size={15} strokeWidth={2.25} />
                    </button>

                    {menuOpenId === mission.id && (
                      <div className={`context-menu ${menuOpensUp ? "context-menu-flip-up" : ""}`}>
                        <button className="context-menu-item" onClick={() => handleStartRename(mission)}>
                          <Pencil size={13} strokeWidth={2.25} />
                          Renommer
                        </button>
                        <button className="context-menu-item danger" onClick={() => handleDelete(mission)}>
                          <Trash2 size={13} strokeWidth={2.25} />
                          Supprimer
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
      </nav>

      <div className="sidebar-user-wrap">
        <button
          className="sidebar-user"
          onClick={() => setProfileMenuOpen((v) => !v)}
          title={collapsed ? "Max" : undefined}
        >
          <span className="sidebar-user-avatar">M</span>
          {!collapsed && <span className="sidebar-user-name">Max</span>}
        </button>

        {profileMenuOpen && (
          <div className="context-menu context-menu-up">
            <button className="context-menu-item" onClick={handleSettingsClick}>
              <Settings size={13} strokeWidth={2.25} />
              Paramètres
            </button>
            {settingsMessage && <div className="context-menu-note">{settingsMessage}</div>}
          </div>
        )}
      </div>
    </aside>
  );
}
