import { FileText } from "lucide-react";

export default function DocumentsCard() {
  return (
    <section className="rail-section">
      <div className="rail-section-header">
        <FileText size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Documents</span>
      </div>
      <div className="rail-empty">Aucun document généré pour l'instant.</div>
    </section>
  );
}
