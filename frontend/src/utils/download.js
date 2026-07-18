const DIACRITICS_REGEX = new RegExp("[\\u0300-\\u036f]", "g");

export function slugify(text) {
  return (text || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(DIACRITICS_REGEX, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function downloadDocument(doc, format = "md") {
  const mime = format === "txt" ? "text/plain" : "text/markdown";
  const blob = new Blob([doc.contenu], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const filename = `${slugify(doc.titre) || "document"}.${format}`;

  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
