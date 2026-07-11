import { useState } from "react";
import { Check, Mail, X, Zap } from "lucide-react";
import { approveAction, rejectAction } from "../../api";

const STATUS_LABEL = {
  en_attente: "En attente",
  approuvee: "Approuvée",
  rejetee: "Rejetée",
  executee: "Envoyée",
};

export default function ActionsCard({ missionId, actions, onActionUpdated }) {
  const [pendingId, setPendingId] = useState(null);
  const [feedback, setFeedback] = useState(null);

  async function handleApprove(action) {
    setPendingId(action.id);
    setFeedback(null);
    try {
      const res = await approveAction(missionId, action.id);
      setFeedback({ actionId: action.id, type: "success", text: res.message });
      onActionUpdated?.();
    } catch (err) {
      setFeedback({
        actionId: action.id,
        type: "error",
        text: err.message || "Échec de l'envoi.",
      });
    } finally {
      setPendingId(null);
    }
  }

  async function handleReject(action) {
    setPendingId(action.id);
    setFeedback(null);
    try {
      await rejectAction(missionId, action.id);
      setFeedback({ actionId: action.id, type: "info", text: "Action rejetée. Aucun email envoyé." });
      onActionUpdated?.();
    } catch (err) {
      setFeedback({
        actionId: action.id,
        type: "error",
        text: err.message || "Échec du rejet.",
      });
    } finally {
      setPendingId(null);
    }
  }

  return (
    <section className="rail-section">
      <div className="rail-section-header">
        <Zap size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Actions</span>
      </div>

      {actions.length === 0 ? (
        <div className="rail-empty">Aucune action en attente d'approbation.</div>
      ) : (
        <ul className="action-list">
          {actions.map((action) => (
            <li key={action.id} className="action-item">
              <div className="action-item-header">
                <Mail size={13} strokeWidth={2.25} />
                <span className="action-item-status">{STATUS_LABEL[action.statut] ?? action.statut}</span>
              </div>
              <div className="action-item-to">À : {action.destinataire}</div>
              <div className="action-item-subject">{action.sujet}</div>
              <p className="action-item-preview">
                {action.contenu.length > 140 ? `${action.contenu.slice(0, 140)}…` : action.contenu}
              </p>

              {action.statut === "en_attente" && (
                <div className="action-item-buttons">
                  <button
                    className="action-approve-btn"
                    disabled={pendingId === action.id}
                    onClick={() => handleApprove(action)}
                  >
                    <Check size={14} strokeWidth={2.5} />
                    Approuver et envoyer
                  </button>
                  <button
                    className="action-reject-btn"
                    disabled={pendingId === action.id}
                    onClick={() => handleReject(action)}
                  >
                    <X size={14} strokeWidth={2.5} />
                    Rejeter
                  </button>
                </div>
              )}

              {feedback?.actionId === action.id && (
                <div className={`action-feedback ${feedback.type}`}>{feedback.text}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
