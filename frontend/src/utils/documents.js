const PURPOSE_FALLBACK_BY_TYPE = {
  fiche: "Document de référence pour cadrer cette mission.",
  checklist: "Liste des étapes à suivre pour mener cette mission à bien.",
  communication: "Message prêt à partager avec les personnes concernées.",
  brief: "Cadre de travail pour orienter la réalisation.",
  plan: "Plan d'action détaillé pour cette mission.",
  annonce: "Annonce prête à diffuser.",
};

const DEFAULT_PURPOSE_FALLBACK = "Document de travail généré pour cette mission.";

export function getDocumentPurpose(doc) {
  return doc.purpose || PURPOSE_FALLBACK_BY_TYPE[doc.type] || DEFAULT_PURPOSE_FALLBACK;
}
