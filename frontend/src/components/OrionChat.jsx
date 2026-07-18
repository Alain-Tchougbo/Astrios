import { useEffect, useRef, useState } from "react";
import { Check, Paperclip, RotateCcw, Send, X } from "lucide-react";
import logo from "../assets/logo.svg";
import logoAnimated from "../assets/logo-animated.svg";
import {
  generateActions,
  generateDocuments,
  generatePlan,
  getMessages,
  retryMission,
  sendChatMessage,
} from "../api";

const WELCOME = {
  role: "assistant",
  contenu:
    "Bonjour, je suis Orion. Décris-moi ta mission et je t'aiderai à la structurer.",
};

const MAX_ATTACHMENT_SIZE = 2 * 1024 * 1024; // 2 Mo
const ALLOWED_ATTACHMENT_EXTENSIONS = [".txt", ".pdf"];

// Messages affichés en cascade pendant une attente longue (plan/documents/action),
// pour rassurer visuellement même quand l'appel réel prend plusieurs dizaines de secondes.
const THINKING_STAGES = [
  { after: 8000, text: "Analyse du contexte…" },
  { after: 25000, text: "Structuration du plan…" },
  { after: 45000, text: "Rédaction des documents…" },
  { after: 65000, text: "Finalisation de l'action proposée…" },
  { after: 90000, text: "Encore un instant, la génération peut prendre jusqu'à quelques minutes…" },
];

export default function OrionChat({ missionId, missionStatut, onMissionUpdated }) {
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  // Distinct de "thinking" : le pipeline Plan -> Documents -> Actions a déjà ses propres
  // messages "en cours"/"terminé" par étape (voir runGenerationPipeline). Le bulle
  // générique "Orion réfléchit…" ci-dessous ne doit s'afficher QUE pendant l'attente d'une
  // simple réponse de découverte — sinon les deux animations cohabitent en même temps
  // (le texte générique qui change sur un minuteur fixe, sans rapport avec l'étape réelle
  // en cours), ce qui casse la perception d'une progression séquentielle claire.
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [thinkingLabel, setThinkingLabel] = useState(null);
  const [attachedFile, setAttachedFile] = useState(null);
  const [attachmentError, setAttachmentError] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const messagesRef = useRef(null);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setInput("");
    setSuggestions([]);
    getMessages(missionId)
      .then((history) => {
        if (cancelled) return;
        setMessages(history.length > 0 ? history : [WELCOME]);
      })
      .catch(() => {
        if (!cancelled) setMessages([WELCOME]);
      });
    return () => {
      cancelled = true;
    };
  }, [missionId]);

  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [messages, thinking]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  useEffect(() => {
    if (!thinking) {
      setThinkingLabel(null);
      return;
    }
    const timers = THINKING_STAGES.map((stage) =>
      setTimeout(() => setThinkingLabel(stage.text), stage.after)
    );
    return () => timers.forEach(clearTimeout);
  }, [thinking]);

  function handleFileSelect(e) {
    const selected = e.target.files?.[0];
    e.target.value = "";
    if (!selected) return;

    const ext = selected.name.slice(selected.name.lastIndexOf(".")).toLowerCase();
    if (!ALLOWED_ATTACHMENT_EXTENSIONS.includes(ext)) {
      setAttachmentError("Format non supporté (.txt ou .pdf uniquement).");
      return;
    }
    if (selected.size > MAX_ATTACHMENT_SIZE) {
      setAttachmentError("Le fichier dépasse la taille maximale autorisée (2 Mo).");
      return;
    }

    setAttachmentError(null);
    setAttachedFile(selected);
  }

  function handleRemoveAttachment() {
    setAttachedFile(null);
    setAttachmentError(null);
  }

  async function sendMessage(text, file) {
    if ((!text && !file) || thinking) return;

    setSuggestions([]);
    setMessages((prev) => [
      ...prev,
      { role: "user", contenu: text || `📎 ${file?.name}` },
    ]);
    setInput("");
    setAttachedFile(null);
    setThinking(true);

    try {
      const reply = await sendChatMessage(missionId, text, file);
      setMessages((prev) => [...prev, { role: "assistant", contenu: reply.contenu }]);
      setSuggestions(reply.suggestions?.length ? reply.suggestions : []);

      // Rafraîchit TOUJOURS l'état de la mission après une réponse d'Orion, pas
      // seulement quand la découverte se termine : une mission "terminee" se
      // réouvre (statut -> en_cours) dès le premier message reçu côté backend (voir
      // chat_with_orion), avant même de savoir si ce message va déclencher une
      // nouvelle génération. Sans ce rafraîchissement immédiat, le badge/la
      // Timeline restaient figés sur "Terminée" pendant toute la nouvelle
      // conversation de découverte.
      await onMissionUpdated?.();

      if (reply.discovery_complete) {
        setPipelineRunning(true);
        try {
          await runGenerationPipeline();
        } finally {
          setPipelineRunning(false);
        }
      }
    } catch (err) {
      const retryable = missionStatut != null && missionStatut !== "nouvelle";
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          contenu: err.message || "Une erreur est survenue.",
          retry: retryable,
        },
      ]);
    } finally {
      setThinking(false);
    }
  }

  function replaceLastMessage(newMessage) {
    setMessages((prev) => [...prev.slice(0, -1), newMessage]);
  }

  // Orchestre Plan -> Documents -> Actions en trois appels réseau séparés et réellement
  // séquentiels (chacun attend la réponse Qwen avant de passer au suivant), avec un
  // message de début explicite avant chaque appel et un message de fin (avec indicateur
  // de succès) une fois la réponse arrivée — pas de délai artificiel entre les deux,
  // le rythme perçu correspond au temps réel de chaque étape.
  async function runGenerationPipeline() {
    setMessages((prev) => [
      ...prev,
      { role: "system", status: "pending", contenu: "Je génère le plan…" },
    ]);
    let planResult;
    try {
      planResult = await generatePlan(missionId);
    } catch (err) {
      replaceLastMessage({
        role: "error",
        contenu: err.message || "La génération du plan a échoué.",
        retry: true,
      });
      return;
    }
    const tasksCount = planResult.tasks_created;
    replaceLastMessage({
      role: "system",
      status: "done",
      contenu:
        tasksCount > 0
          ? `Plan généré : ${tasksCount} tâche${tasksCount > 1 ? "s" : ""} créée${tasksCount > 1 ? "s" : ""}.`
          : "Plan déjà à jour : aucune nouvelle tâche nécessaire.",
    });
    await onMissionUpdated?.();
    // Pas de retour anticipé ici si tasksCount === 0 : sur une mission déjà générée
    // (réouverture après un nouveau besoin), il est normal et attendu qu'aucune
    // nouvelle tâche ne soit nécessaire alors qu'un nouveau document ou une nouvelle
    // action le sont — chaque étape juge indépendamment si elle a quelque chose à
    // ajouter. Bug corrigé : la pipeline s'arrêtait ici, empêchant /generate-actions
    // d'être appelé et donc toute nouvelle action d'apparaître après une réouverture.

    setMessages((prev) => [
      ...prev,
      { role: "system", status: "pending", contenu: "Je rédige les documents…" },
    ]);
    let documentsResult;
    try {
      documentsResult = await generateDocuments(missionId);
    } catch (err) {
      replaceLastMessage({
        role: "error",
        contenu: err.message || "La génération des documents a échoué.",
        retry: true,
      });
      return;
    }
    const docsCount = documentsResult.documents_created;
    replaceLastMessage({
      role: "system",
      status: "done",
      contenu:
        docsCount > 0
          ? `Documents générés : ${docsCount} document${docsCount > 1 ? "s" : ""} créé${docsCount > 1 ? "s" : ""}.`
          : "Documents déjà à jour : aucun nouveau document nécessaire.",
    });
    await onMissionUpdated?.();
    // Pas de retour anticipé non plus ici (voir commentaire après /generate-plan) :
    // une nouvelle action peut être nécessaire même sans nouveau document.

    setMessages((prev) => [
      ...prev,
      { role: "system", status: "pending", contenu: "Je propose les actions à exécuter…" },
    ]);
    let actionsResult;
    try {
      actionsResult = await generateActions(missionId);
    } catch (err) {
      replaceLastMessage({
        role: "error",
        contenu: err.message || "La proposition d'action a échoué.",
        retry: true,
      });
      return;
    }
    const actionsCount = actionsResult.actions_created;
    replaceLastMessage({
      role: "system",
      status: "done",
      contenu: actionsResult.action_proposed
        ? actionsCount > 1
          ? `${actionsCount} actions proposées : en attente de ton approbation dans le panneau Actions.`
          : "Action proposée : en attente de ton approbation dans le panneau Actions."
        : "Cette mission ne nécessite aucune action d'envoi — les livrables sont prêts à consulter.",
    });
    await onMissionUpdated?.();
  }

  function handleSend(e) {
    e.preventDefault();
    sendMessage(input.trim(), attachedFile);
  }

  function handleSuggestionClick(suggestion) {
    sendMessage(suggestion, null);
  }

  async function handleRetry(failedMessage) {
    setThinking(true);
    try {
      const result = await retryMission(missionId);
      setMessages((prev) => {
        const cleared = prev.map((m) => (m === failedMessage ? { ...m, retry: false } : m));
        const confirmations = [];
        if (result.plan_generated && result.tasks_created > 0) {
          confirmations.push({
            role: "system",
            contenu: `Plan généré : ${result.tasks_created} tâche${result.tasks_created > 1 ? "s" : ""} créée${result.tasks_created > 1 ? "s" : ""}.`,
          });
        }
        if (result.documents_generated && result.documents_created > 0) {
          confirmations.push({
            role: "system",
            contenu: `Documents générés : ${result.documents_created} document${result.documents_created > 1 ? "s" : ""} créé${result.documents_created > 1 ? "s" : ""}.`,
          });
        }
        if (result.action_proposed) {
          const count = result.actions_created;
          confirmations.push({
            role: "system",
            contenu:
              count > 1
                ? `${count} actions proposées : en attente de ton approbation dans le panneau Actions.`
                : "Action proposée : en attente de ton approbation dans le panneau Actions.",
          });
        }
        return [...cleared, ...confirmations];
      });
      onMissionUpdated?.();
    } catch (err) {
      setMessages((prev) => [
        ...prev.map((m) => (m === failedMessage ? { ...m, retry: false } : m)),
        { role: "error", contenu: err.message || "La relance a échoué.", retry: true },
      ]);
    } finally {
      setThinking(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  }

  return (
    <div className="mission-chat">
      <div className="chat-messages" ref={messagesRef}>
        {messages.map((m, i) => (
          <div key={i} className={`chat-message ${m.role}`}>
            {m.role === "assistant" && (
              <div className="chat-avatar">
                <img src={logo} alt="" className="chat-avatar-mark" />
              </div>
            )}
            <div className="chat-bubble">
              {m.status === "pending" && <span className="chat-step-spinner" aria-hidden="true" />}
              {m.status === "done" && (
                <Check size={13} strokeWidth={3} className="chat-step-check" aria-hidden="true" />
              )}
              {m.contenu}
              {m.retry && (
                <button
                  type="button"
                  className="chat-retry-btn"
                  disabled={thinking}
                  onClick={() => handleRetry(m)}
                >
                  <RotateCcw size={13} strokeWidth={2.5} />
                  Réessayer
                </button>
              )}
            </div>
          </div>
        ))}
        {thinking && !pipelineRunning && (
          <div className="chat-message assistant">
            <div className="chat-avatar">
              <img src={logoAnimated} alt="" className="chat-avatar-mark" />
            </div>
            <div className="chat-bubble chat-thinking">
              <span className="chat-thinking-shimmer">{thinkingLabel || "Orion réfléchit…"}</span>
            </div>
          </div>
        )}
      </div>

      {suggestions.length > 0 && !thinking && (
        <div className="chat-suggestions-row">
          {suggestions.map((suggestion, i) => (
            <button
              key={i}
              type="button"
              className="chat-suggestion-pill"
              onClick={() => handleSuggestionClick(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {(attachedFile || attachmentError) && (
        <div className="chat-attachment-row">
          {attachedFile && (
            <div className="chat-attachment-chip">
              <Paperclip size={13} strokeWidth={2.25} />
              <span className="chat-attachment-name">{attachedFile.name}</span>
              <button
                type="button"
                className="chat-attachment-remove"
                onClick={handleRemoveAttachment}
                aria-label="Retirer le fichier"
              >
                <X size={13} strokeWidth={2.5} />
              </button>
            </div>
          )}
          {attachmentError && <span className="chat-attachment-error">{attachmentError}</span>}
        </div>
      )}

      <form className="chat-input-bar" onSubmit={handleSend}>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.pdf"
          className="chat-file-input"
          onChange={handleFileSelect}
        />
        <button
          type="button"
          className="chat-attach-btn"
          title="Joindre un fichier (.txt, .pdf — 2 Mo max)"
          onClick={() => fileInputRef.current?.click()}
        >
          <Paperclip size={17} strokeWidth={2.1} />
        </button>
        <textarea
          ref={textareaRef}
          className="chat-textarea"
          placeholder="Écris à Orion…"
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          type="submit"
          className="chat-send"
          disabled={(!input.trim() && !attachedFile) || thinking}
          aria-label="Envoyer"
        >
          <Send size={16} strokeWidth={2.25} />
        </button>
      </form>
    </div>
  );
}
