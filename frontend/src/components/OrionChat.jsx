import { useEffect, useRef, useState } from "react";
import { Compass, Paperclip, RotateCcw, Send } from "lucide-react";
import { getMessages, retryMission, sendChatMessage } from "../api";

const WELCOME = {
  role: "assistant",
  contenu:
    "Bonjour, je suis Orion. Décris-moi ta mission et je t'aiderai à la structurer.",
};

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
  const messagesRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setInput("");
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

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || thinking) return;

    setMessages((prev) => [...prev, { role: "user", contenu: text }]);
    setInput("");
    setThinking(true);

    try {
      const reply = await sendChatMessage(missionId, text);
      setMessages((prev) => [...prev, { role: "assistant", contenu: reply.contenu }]);

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
        setMessages((prev) => [
          ...prev,
          {
            role: "system",
            contenu: "Action proposée : un email est en attente de ton approbation dans le panneau Actions.",
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
          confirmations.push({
            role: "system",
            contenu: "Action proposée : un email est en attente de ton approbation dans le panneau Actions.",
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
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
              {thinkingLabel && <span className="chat-thinking-label">{thinkingLabel}</span>}
            </div>
          </div>
        )}
      </div>

      <form className="chat-input-bar" onSubmit={handleSend}>
        <button
          type="button"
          className="chat-attach-btn"
          title="Joindre un document (bientôt disponible)"
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
        <button type="submit" className="chat-send" disabled={!input.trim() || thinking} aria-label="Envoyer">
          <Send size={16} strokeWidth={2.25} />
        </button>
      </form>
    </div>
  );
}
