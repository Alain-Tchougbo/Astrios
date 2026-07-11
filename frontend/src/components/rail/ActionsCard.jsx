import { Zap } from "lucide-react";

export default function ActionsCard() {
  return (
    <section className="rail-section">
      <div className="rail-section-header">
        <Zap size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Actions</span>
      </div>
      <div className="rail-empty">Aucune action en attente d'approbation.</div>
    </section>
  );
}
