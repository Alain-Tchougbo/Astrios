import { useEffect, useRef, useState } from "react";
import { Compass, Paperclip, Send } from "lucide-react";

const WELCOME = {
  role: "assistant",
  contenu:
    "Bonjour, je suis Orion. Décris-moi ta mission et je t'aiderai à la structurer.",
};

export default function OrionChat({ missionId }) {
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const messagesRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    setMessages([WELCOME]);
    setInput("");
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

  function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;

    setMessages((prev) => [...prev, { role: "user", contenu: text }]);
    setInput("");
    setThinking(true);

    // Placeholder en attendant le branchement de l'API de chat / Qwen.
    setTimeout(() => {
      setThinking(false);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          contenu: "(connexion à Orion pas encore branchée sur cette mission)",
        },
      ]);
    }, 900);
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
            <div className="chat-bubble">{m.contenu}</div>
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
        <button type="submit" className="chat-send" disabled={!input.trim()} aria-label="Envoyer">
          <Send size={16} strokeWidth={2.25} />
        </button>
      </form>
    </div>
  );
}
