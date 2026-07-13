import { useEffect, useRef, useState } from "react";
import { Compass, Paperclip, RotateCcw, Send, X } from "lucide-react";
import { getMessages, retryMission, sendChatMessage } from "../api";

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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function OrionChat({
  missionId,
  missionStatut,
  onDiscoveryComplete,
  onPlanGeneratingChange,
  onDocumentsGeneratingChange,
}) {
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
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

      if (reply.discovery_complete && reply.plan_generated) {
        onPlanGeneratingChange?.(true);
        setMessages((prev) => [
          ...prev,
          { role: "system", contenu: "Génération du plan en cours…" },
        ]);
        await sleep(700);
        const count = reply.tasks_created;
        setMessages((prev) => [
          ...prev.slice(0, -1),
          {
            role: "system",
            contenu: `Plan généré : ${count} tâche${count > 1 ? "s" : ""} créée${count > 1 ? "s" : ""}.`,
          },
        ]);
        onPlanGeneratingChange?.(false);
      }

      if (reply.discovery_complete && reply.documents_generated) {
        onDocumentsGeneratingChange?.(true);
        setMessages((prev) => [
          ...prev,
          { role: "system", contenu: "Génération des documents en cours…" },
        ]);
        await sleep(700);
        const docCount = reply.documents_created;
        setMessages((prev) => [
          ...prev.slice(0, -1),
          {
            role: "system",
            contenu: `Documents générés : ${docCount} document${docCount > 1 ? "s" : ""} créé${docCount > 1 ? "s" : ""}.`,
          },
        ]);
        onDocumentsGeneratingChange?.(false);
      }

      if (reply.discovery_complete && reply.action_proposed) {
        const count = reply.actions_created;
        setMessages((prev) => [
          ...prev,
          {
            role: "system",
            contenu:
              count > 1
                ? `${count} actions proposées : en attente de ton approbation dans le panneau Actions.`
                : "Action proposée : en attente de ton approbation dans le panneau Actions.",
          },
        ]);
      }

      if (reply.discovery_complete) {
        onDiscoveryComplete?.();
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
      onDiscoveryComplete?.();
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
                <Compass size={15} strokeWidth={2.25} />
              </div>
            )}
            <div className="chat-bubble">
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
        {thinking && (
          <div className="chat-message assistant">
            <div className="chat-avatar">
              <Compass size={15} strokeWidth={2.25} />
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
