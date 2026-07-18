import { useEffect, useState } from "react";
import { AlertTriangle, Calendar, Check, Mail, X } from "lucide-react";
import { updateAction } from "../../api";

const FAKE_EMAIL_DOMAIN = "@exemple.com";

function toDatetimeLocalValue(naiveIso) {
  return naiveIso ? naiveIso.slice(0, 16) : "";
}

// Les horaires stockés (date_debut/date_fin) sont "naïfs" (sans fuseau, ex.
// "2026-07-22T11:00:00") — voir formatEventDate dans ActionsCard.jsx pour le
// contexte complet. On les traite ici comme une horloge neutre en les faisant
// transiter par UTC, uniquement pour faire de l'arithmétique de durée sans
// jamais laisser le fuseau système du navigateur fausser les chiffres.
function parseNaive(naiveStr) {
  return new Date(`${naiveStr}Z`);
}

function formatNaive(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(
    date.getUTCHours()
  )}:${pad(date.getUTCMinutes())}:00`;
}

function computeDurationMinutes(startNaive, endNaive) {
  if (!startNaive || !endNaive) return 60;
  const diffMs = parseNaive(endNaive).getTime() - parseNaive(startNaive).getTime();
  const minutes = Math.round(diffMs / 60000);
  return minutes > 0 ? minutes : 60;
}

export default function ActionEditDrawer({ missionId, action, onClose, onSaved }) {
  const isEvent = action?.type === "calendar_event";

  const [destinataire, setDestinataire] = useState("");
  const [sujet, setSujet] = useState("");
  const [contenu, setContenu] = useState("");

  const [titre, setTitre] = useState("");
  const [dateDebut, setDateDebut] = useState("");
  const [dureeMinutes, setDureeMinutes] = useState(60);
  const [participants, setParticipants] = useState("");
  const [description, setDescription] = useState("");

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!action) return;
    if (action.type === "calendar_event") {
      const d = action.details || {};
      setTitre(d.titre || "");
      setDateDebut(toDatetimeLocalValue(d.date_debut));
      setDureeMinutes(computeDurationMinutes(d.date_debut, d.date_fin));
      setParticipants(d.participants || "");
      setDescription(d.description || "");
    } else {
      setDestinataire(action.destinataire || "");
      setSujet(action.sujet || "");
      setContenu(action.contenu || "");
    }
    setError(null);
  }, [action]);

  useEffect(() => {
    if (!action) return;
    function handleKeyDown(e) {
      if (e.key === "Escape") onClose?.();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [action, onClose]);

  if (!action) return null;

  const isFakeEmail = destinataire.trim().toLowerCase().endsWith(FAKE_EMAIL_DOMAIN);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      if (isEvent) {
        const startNaive = `${dateDebut}:00`;
        const endNaive = formatNaive(
          new Date(parseNaive(startNaive).getTime() + dureeMinutes * 60000)
        );
        await updateAction(missionId, action.id, {
          titre,
          description,
          date_debut: startNaive,
          date_fin: endNaive,
          participants,
        });
      } else {
        await updateAction(missionId, action.id, { destinataire, sujet, contenu });
      }
      onSaved?.();
      onClose?.();
    } catch (err) {
      setError(err.message || "Échec de l'enregistrement.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="action-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="action-drawer-header">
          <div className="action-drawer-title">
            {isEvent ? <Calendar size={16} strokeWidth={2.25} /> : <Mail size={16} strokeWidth={2.25} />}
            {isEvent ? "Modifier l'événement" : "Modifier l'email"}
          </div>
          <button type="button" className="action-drawer-close" onClick={onClose} aria-label="Fermer">
            <X size={18} strokeWidth={2.25} />
          </button>
        </div>

        <div className="action-drawer-body">
          {isEvent ? (
            <>
              <label className="action-drawer-field">
                <span className="action-drawer-label">Titre</span>
                <input
                  className="action-drawer-input"
                  value={titre}
                  onChange={(e) => setTitre(e.target.value)}
                />
              </label>

              <div className="action-drawer-row">
                <label className="action-drawer-field">
                  <span className="action-drawer-label">Date et heure de début</span>
                  <input
                    type="datetime-local"
                    className="action-drawer-input"
                    value={dateDebut}
                    onChange={(e) => setDateDebut(e.target.value)}
                  />
                </label>
                <label className="action-drawer-field action-drawer-field-narrow">
                  <span className="action-drawer-label">Durée (minutes)</span>
                  <input
                    type="number"
                    min={15}
                    step={15}
                    className="action-drawer-input"
                    value={dureeMinutes}
                    onChange={(e) => setDureeMinutes(Number(e.target.value) || 60)}
                  />
                </label>
              </div>

              <label className="action-drawer-field">
                <span className="action-drawer-label">Participants</span>
                <input
                  className="action-drawer-input"
                  value={participants}
                  onChange={(e) => setParticipants(e.target.value)}
                  placeholder="Noms ou emails séparés par des virgules"
                />
              </label>

              <label className="action-drawer-field">
                <span className="action-drawer-label">Description</span>
                <textarea
                  className="action-drawer-textarea"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
            </>
          ) : (
            <>
              <label className="action-drawer-field">
                <span className="action-drawer-label">Destinataire</span>
                <input
                  className="action-drawer-input"
                  value={destinataire}
                  onChange={(e) => setDestinataire(e.target.value)}
                />
              </label>
              {isFakeEmail && (
                <div className="action-drawer-warning">
                  <AlertTriangle size={14} strokeWidth={2.25} />
                  Adresse fictive : remplacez par la vraie adresse avant d'exécuter.
                </div>
              )}

              <label className="action-drawer-field">
                <span className="action-drawer-label">Objet</span>
                <input
                  className="action-drawer-input"
                  value={sujet}
                  onChange={(e) => setSujet(e.target.value)}
                />
              </label>

              <label className="action-drawer-field">
                <span className="action-drawer-label">Message</span>
                <textarea
                  className="action-drawer-textarea action-drawer-textarea-large"
                  value={contenu}
                  onChange={(e) => setContenu(e.target.value)}
                />
              </label>
            </>
          )}

          {error && <div className="action-drawer-error">{error}</div>}
        </div>

        <div className="action-drawer-footer">
          <button type="button" className="action-approve-btn" disabled={saving} onClick={handleSave}>
            <Check size={14} strokeWidth={2.5} />
            Enregistrer
          </button>
          <button type="button" className="action-reject-btn" disabled={saving} onClick={onClose}>
            <X size={14} strokeWidth={2.5} />
            Annuler
          </button>
        </div>
      </div>
    </div>
  );
}
