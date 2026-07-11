import { ArrowLeft } from "lucide-react";

function renderInline(text, keyPrefix) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={`${keyPrefix}-${i}`}>{part}</span>
    )
  );
}

function renderMarkdown(text) {
  const lines = (text || "").split("\n");
  const blocks = [];
  let listBuffer = [];

  function flushList() {
    if (listBuffer.length) {
      blocks.push({ type: "ul", items: listBuffer });
      listBuffer = [];
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (!line.trim()) {
      flushList();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)/);
    if (heading) {
      flushList();
      blocks.push({ type: `h${heading[1].length}`, text: heading[2] });
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.*)/);
    if (bullet) {
      listBuffer.push(bullet[1]);
      continue;
    }
    flushList();
    blocks.push({ type: "p", text: line });
  }
  flushList();

  return blocks.map((block, i) => {
    if (block.type === "ul") {
      return (
        <ul key={i}>
          {block.items.map((item, j) => (
            <li key={j}>{renderInline(item, `${i}-${j}`)}</li>
          ))}
        </ul>
      );
    }
    if (block.type === "h1") return <h3 key={i}>{renderInline(block.text, `${i}`)}</h3>;
    if (block.type === "h2") return <h4 key={i}>{renderInline(block.text, `${i}`)}</h4>;
    if (block.type === "h3") return <h5 key={i}>{renderInline(block.text, `${i}`)}</h5>;
    return <p key={i}>{renderInline(block.text, `${i}`)}</p>;
  });
}

export default function DocumentViewer({ doc, onBack }) {
  return (
    <div className="mission-chat document-viewer">
      <div className="document-viewer-header">
        <button className="document-back-btn" onClick={onBack}>
          <ArrowLeft size={15} strokeWidth={2.25} />
          Retour à la conversation
        </button>
      </div>

      <div className="document-viewer-body">
        <span className="document-type-badge">{doc.type}</span>
        <h2 className="document-viewer-title">{doc.titre}</h2>
        <div className="document-viewer-content">{renderMarkdown(doc.contenu)}</div>
      </div>
    </div>
  );
}
