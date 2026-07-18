import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp, Download, FileText } from "lucide-react";
import { downloadDocument } from "../../utils/download";
import { getDocumentPurpose } from "../../utils/documents";

const STATUS_ORDER = [
  "nouvelle",
  "en_cours",
  "plan_pret",
  "documents_prets",
  "action_en_attente",
  "terminee",
];

export default function DocumentsCard({ documents, onSelect, missionStatut }) {
  const [expanded, setExpanded] = useState(true);
  const [autoCollapsedOnce, setAutoCollapsedOnce] = useState(false);

  const pastDocuments =
    STATUS_ORDER.indexOf(missionStatut) > STATUS_ORDER.indexOf("documents_prets");

  useEffect(() => {
    if (pastDocuments && !autoCollapsedOnce) {
      setExpanded(false);
      setAutoCollapsedOnce(true);
    }
    if (!pastDocuments && autoCollapsedOnce) {
      setAutoCollapsedOnce(false);
    }
  }, [pastDocuments, autoCollapsedOnce]);

  return (
    <section className="rail-section">
      <button className="rail-section-header rail-section-toggle" onClick={() => setExpanded((v) => !v)}>
        <FileText size={15} strokeWidth={2.25} />
        <span className="rail-section-title">Documents</span>
        {documents.length > 0 && (
          <span className="rail-section-summary">{documents.length}</span>
        )}
        {expanded ? (
          <ChevronUp size={15} strokeWidth={2.25} className="rail-chevron" />
        ) : (
          <ChevronDown size={15} strokeWidth={2.25} className="rail-chevron" />
        )}
      </button>

      <div className={`rail-section-body ${expanded ? "" : "collapsed"}`}>
        {documents.length === 0 ? (
          <div className="rail-empty">Aucun document généré pour l'instant.</div>
        ) : (
          <ul className="document-list">
            {documents.map((doc) => (
              <li key={doc.id} className="document-list-row">
                <button className="document-item" onClick={() => onSelect(doc)}>
                  <span className="document-item-main">
                    <span className="document-item-title">{doc.titre}</span>
                    <span className="document-item-type">{doc.type}</span>
                  </span>
                  <span className="document-item-purpose">{getDocumentPurpose(doc)}</span>
                </button>
                <button
                  type="button"
                  className="document-download-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    downloadDocument(doc, "md");
                  }}
                  aria-label={`Télécharger ${doc.titre}`}
                  title="Télécharger (.md)"
                >
                  <Download size={14} strokeWidth={2.25} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
