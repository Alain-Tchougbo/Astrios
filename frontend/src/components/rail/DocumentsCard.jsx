import { FileText } from "lucide-react";

export default function DocumentsCard({ documents, onSelect }) {
  return (
    <section className="rail-section">
      <div className="rail-section-header">
        <FileText size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Documents</span>
      </div>

      {documents.length === 0 ? (
        <div className="rail-empty">Aucun document généré pour l'instant.</div>
      ) : (
        <ul className="document-list">
          {documents.map((doc) => (
            <li key={doc.id}>
              <button className="document-item" onClick={() => onSelect(doc)}>
                <span className="document-item-title">{doc.titre}</span>
                <span className="document-item-type">{doc.type}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
