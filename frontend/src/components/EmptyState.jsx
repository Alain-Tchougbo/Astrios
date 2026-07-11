import { useState } from "react";
import { ArrowRight } from "lucide-react";

export default function EmptyState({ onCreate, creating }) {
  const [titre, setTitre] = useState("");
  const [objectif, setObjectif] = useState("");

  function handleSubmit(e) {
    e.preventDefault();
    if (!titre.trim()) return;
    onCreate(titre.trim(), objectif.trim());
    setTitre("");
    setObjectif("");
  }

  return (
    <div className="empty-state">
      <div className="empty-state-inner">
        <span className="empty-state-mark" />
        <h1>Bienvenue sur Astrios</h1>
        <p>
          Crée ta première mission. Orion t'aidera à la cadrer, à construire un
          plan, et à passer à l'action.
        </p>

        <form className="empty-state-form" onSubmit={handleSubmit}>
          <input
            type="text"
            placeholder="Ex : Recruter un développeur Flutter"
            value={titre}
            onChange={(e) => setTitre(e.target.value)}
            autoFocus
          />
          <textarea
            placeholder="Objectif ou contexte (optionnel)"
            value={objectif}
            onChange={(e) => setObjectif(e.target.value)}
            rows={2}
          />
          <button type="submit" disabled={!titre.trim() || creating}>
            {creating ? "Création…" : "Créer la mission"}
            {!creating && <ArrowRight size={16} strokeWidth={2.25} />}
          </button>
        </form>
      </div>
    </div>
  );
}
