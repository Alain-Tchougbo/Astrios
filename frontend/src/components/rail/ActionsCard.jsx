import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Calendar,
  Check,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Info,
  Mail,
  Pencil,
  X,
  Zap,
} from "lucide-react";
import { GOOGLE_LOGIN_URL, approveAllActions, excludeAction, getExecutionMode } from "../../api";
import ActionEditDrawer from "./ActionEditDrawer";

const STATUS_LABEL = {
  en_attente: "En attente",
  approuvee: "Approuvée",
  rejetee: "Retirée",
  executee: "Exécutée",
};

const MODE_BANNER = {
  server:
    "Exécution via le compte de démonstration Orion (orionastrios@gmail.com). En production, Astrios agit depuis votre propre compte Google.",
  simulation: "Mode simulation : aucun envoi réel (le service de démonstration n'est pas configuré).",
};

function hasFakeAddress(text) {
  return (text || "").toLowerCase().includes("@exemple.com");
}

function formatEventDate(dateStr) {
  if (!dateStr) return "";
  // date_debut/date_fin sont des horaires "naïfs" (sans fuseau, ex. "2026-07-22T11:00:00")
  // envoyés à Google Calendar avec un champ timeZone: "Europe/Paris" séparé — Google les
  // interprète donc comme des heures murales Europe/Paris, sans ambiguïté.
  // new Date(dateStr) sans le "Z" interpréterait ces mêmes chiffres comme une heure
  // locale du fuseau système du navigateur (pas forcément Europe/Paris), ce qui peut
  // décaler l'heure affichée ici par rapport à celle réellement créée dans Calendar.
  // En ajoutant "Z" et en formatant avec timeZone: "UTC", on force l'affichage à
  // reprendre exactement les mêmes chiffres que ceux envoyés à Google, sans aucune
  // conversion de fuseau.
  const d = new Date(`${dateStr}Z`);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleString("fr-FR", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  });
}

export default function ActionsCard({ missionId, actions, onActionUpdated, onAllApproved }) {
  const [expanded, setExpanded] = useState(false);
  const [excludingId, setExcludingId] = useState(null);
  const [approving, setApproving] = useState(false);
  const [groupError, setGroupError] = useState(null);
  const [rateLimited, setRateLimited] = useState(false);
  const [results, setResults] = useState(null);
  const [editingAction, setEditingAction] = useState(null);
  const [executionMode, setExecutionMode] = useState(null);

  const pendingActions = actions.filter((a) => a.statut === "en_attente");
  const resolvedActions = actions.filter((a) => a.statut !== "en_attente");

  useEffect(() => {
    getExecutionMode()
      .then((res) => setExecutionMode(res.mode))
      .catch(() => setExecutionMode(null));
  }, [missionId, actions.length]);

  async function handleExclude(action) {
    setExcludingId(action.id);
    setGroupError(null);
    try {
      await excludeAction(missionId, action.id);
      onActionUpdated?.();
    } catch (err) {
      setGroupError(err.message || "Échec du retrait de cette action.");
    } finally {
      setExcludingId(null);
    }
  }

  async function handleApproveAll(forceSimulation = false) {
    setApproving(true);
    setGroupError(null);
    setRateLimited(false);
    try {
      const response = await approveAllActions(missionId, forceSimulation);
      setResults(response.results);
      // La requête peut réussir (HTTP 200) tout en contenant des échecs PAR action
      // (ex. SMTPAuthenticationError capturée côté backend, action restée "en_attente")
      // — ça ne lève jamais d'exception ici, donc sans ce contrôle explicite l'échec
      // passait inaperçu : l'utilisateur ne voyait que le bouton redevenir cliquable,
      // sans aucun message (bug rapporté). On affiche le résumé ET on déplie le détail
      // pour que le message précis par action (voir plus bas) soit visible sans clic.
      const failures = response.results.filter((r) => !r.success);
      if (failures.length > 0) {
        setGroupError(
          failures.length === 1
            ? `1 action a échoué : ${failures[0].message}`
            : `${failures.length} actions ont échoué. Voir le détail ci-dessous.`
        );
        setExpanded(true);
      }
      await onAllApproved?.(response.results);
      onActionUpdated?.();
    } catch (err) {
      setGroupError(err.message || "Échec de l'exécution.");
      setRateLimited(err.status === 429);
    } finally {
      setApproving(false);
    }
  }

  function resultFor(actionId) {
    return results?.find((r) => r.action.id === actionId) ?? null;
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
        <>
          {pendingActions.length > 0 && (
            <div className="action-group-card">
              <div className="action-group-header">
                <div>
                  <div className="action-group-title">Mission prête à être exécutée</div>
                  <div className="action-group-subtitle">
                    {pendingActions.length} action{pendingActions.length > 1 ? "s" : ""} à exécuter
                  </div>
                </div>
                <button
                  type="button"
                  className="action-group-toggle"
                  onClick={() => setExpanded((v) => !v)}
                >
                  Voir le détail
                  {expanded ? (
                    <ChevronUp size={14} strokeWidth={2.25} />
                  ) : (
                    <ChevronDown size={14} strokeWidth={2.25} />
                  )}
                </button>
              </div>

              {executionMode && MODE_BANNER[executionMode] && (
                <div className="action-mode-banner">
                  <Info size={14} strokeWidth={2.25} />
                  {MODE_BANNER[executionMode]}
                </div>
              )}

              {expanded && (
                <ul className="action-group-detail">
                  {pendingActions.map((action) => {
                    const isEvent = action.type === "calendar_event";
                    const details = action.details || {};
                    const fakeAddress = isEvent
                      ? hasFakeAddress(details.participants)
                      : hasFakeAddress(action.destinataire);
                    // Une action reste "en_attente" après un essai raté (voir
                    // handleApproveAll) : son résultat d'échec le plus récent, s'il y
                    // en a un, doit rester visible ici — sinon rien ne distingue une
                    // action jamais essayée d'une action qui vient d'échouer pour de
                    // vrai (bug rapporté : bouton redevenu cliquable, aucun indice).
                    const failedResult = resultFor(action.id);
                    return (
                      <li key={action.id} className="action-group-item">
                        <div className="action-item-header">
                          {isEvent ? (
                            <Calendar size={13} strokeWidth={2.25} />
                          ) : (
                            <Mail size={13} strokeWidth={2.25} />
                          )}
                          <span className="action-item-status">{isEvent ? "Événement" : "Email"}</span>
                        </div>

                        {isEvent ? (
                          <>
                            <div className="action-item-subject">{details.titre}</div>
                            <div className="action-item-to action-item-to-wrap">
                              {formatEventDate(details.date_debut)}
                              {details.date_fin ? ` → ${formatEventDate(details.date_fin)}` : ""}
                            </div>
                            <div className="action-item-to action-item-to-wrap">
                              <span className="action-item-to-label">Participant(s) :</span>{" "}
                              {details.participants || "—"}
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="action-item-to action-item-to-wrap">
                              <span className="action-item-to-label">À :</span> {action.destinataire}
                            </div>
                            <div className="action-item-subject">{action.sujet}</div>
                          </>
                        )}

                        {fakeAddress && (
                          <div className="action-fake-address-badge">
                            <AlertTriangle size={13} strokeWidth={2.5} />
                            Adresse à compléter
                          </div>
                        )}

                        {failedResult && !failedResult.success && (
                          <div className="action-feedback error">
                            <AlertTriangle size={13} strokeWidth={2.5} />
                            {failedResult.message}
                          </div>
                        )}

                        <div className="action-item-buttons">
                          <button
                            type="button"
                            className="action-edit-btn"
                            onClick={() => setEditingAction(action)}
                            aria-label={isEvent ? "Modifier l'événement" : "Modifier l'email"}
                          >
                            <Pencil size={13} strokeWidth={2.25} />
                            Modifier
                          </button>
                          <button
                            type="button"
                            className="action-reject-btn"
                            disabled={excludingId === action.id}
                            onClick={() => handleExclude(action)}
                          >
                            <X size={14} strokeWidth={2.5} />
                            Retirer
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}

              {groupError && (
                <div className="action-feedback error">
                  {groupError}
                  {rateLimited && (
                    <button
                      type="button"
                      className="action-simulation-fallback-btn"
                      onClick={() => handleApproveAll(true)}
                      disabled={approving}
                    >
                      Continuer en mode simulation
                    </button>
                  )}
                </div>
              )}

              <button
                type="button"
                className="action-approve-all-btn"
                disabled={approving}
                onClick={() => handleApproveAll(false)}
              >
                <Check size={15} strokeWidth={2.5} />
                {approving ? "Exécution en cours…" : "Approuver et exécuter tout"}
              </button>

              <a className="action-google-connect-btn advanced" href={GOOGLE_LOGIN_URL}>
                <ExternalLink size={13} strokeWidth={2.25} />
                Se connecter à Google (réservé aux comptes testeurs approuvés)
              </a>
            </div>
          )}

          {resolvedActions.length > 0 && (
            <ul className="action-list action-history">
              {resolvedActions.map((action) => {
                const isEvent = action.type === "calendar_event";
                const details = action.details || {};
                const result = resultFor(action.id);
                return (
                  <li key={action.id} className="action-item">
                    <div className="action-item-header">
                      {isEvent ? (
                        <Calendar size={13} strokeWidth={2.25} />
                      ) : (
                        <Mail size={13} strokeWidth={2.25} />
                      )}
                      <span className="action-item-status">{STATUS_LABEL[action.statut] ?? action.statut}</span>
                    </div>

                    {isEvent ? (
                      <div className="action-item-subject">{details.titre}</div>
                    ) : (
                      <>
                        <div className="action-item-to">À : {action.destinataire}</div>
                        <div className="action-item-subject">{action.sujet}</div>
                      </>
                    )}

                    {result && (
                      <div className={`action-feedback ${result.success ? "success" : "error"}`}>
                        {result.success ? "✓ " : "✗ "}
                        {result.message}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}

      <ActionEditDrawer
        missionId={missionId}
        action={editingAction}
        onClose={() => setEditingAction(null)}
        onSaved={onActionUpdated}
      />
    </section>
  );
}
