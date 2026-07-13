import { deleteMission, updateMission } from "../api";

// Logique métier partagée pour renommer/supprimer une mission — utilisée à la
// fois par Sidebar (menu contextuel par item) et MissionView (menu de l'en-tête).
// Chaque appelant garde son propre état local (item en édition, menu ouvert) ;
// seule la mutation (appel API + confirmation + callback) est mutualisée ici.
export function useMissionRenameDelete({ onRenamed, onDeleted } = {}) {
  async function rename(mission, newTitre) {
    const trimmed = newTitre.trim();
    if (!trimmed || trimmed === mission.titre) return false;
    await updateMission(mission.id, trimmed);
    onRenamed?.(mission.id, trimmed);
    return true;
  }

  async function remove(mission) {
    const confirmed = window.confirm(
      `Supprimer la mission "${mission.titre}" ? Cette action est irréversible.`
    );
    if (!confirmed) return false;
    await deleteMission(mission.id);
    onDeleted?.(mission.id);
    return true;
  }

  return { rename, remove };
}
