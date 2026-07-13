const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const GOOGLE_LOGIN_URL = `${API_URL}/auth/google/login`;

async function parseResponse(res, path) {
  if (!res.ok) {
    let detail = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      // corps de réponse non-JSON ou vide, on garde le message générique
    }
    const error = new Error(detail || `Erreur API ${res.status} sur ${path}`);
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) {
    return null;
  }
  return res.json();
}

async function request(path, options) {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  return parseResponse(res, path);
}

export function getMissions() {
  return request("/missions");
}

export function getMission(id) {
  return request(`/missions/${id}`);
}

export function createMission(titre, objectif) {
  return request("/missions", {
    method: "POST",
    body: JSON.stringify({ titre, objectif }),
  });
}

export function updateMission(id, titre) {
  return request(`/missions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ titre }),
  });
}

export function deleteMission(id) {
  return request(`/missions/${id}`, {
    method: "DELETE",
  });
}

export function getMessages(missionId) {
  return request(`/missions/${missionId}/messages`);
}

export async function sendChatMessage(missionId, contenu, file) {
  const path = `/missions/${missionId}/chat`;
  const formData = new FormData();
  formData.append("contenu", contenu);
  if (file) {
    formData.append("file", file);
  }
  // Pas de header Content-Type ici : le navigateur doit fixer lui-même le
  // boundary multipart, un header manuel casserait le parsing côté serveur.
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    body: formData,
  });
  return parseResponse(res, path);
}

export function getTasks(missionId) {
  return request(`/missions/${missionId}/tasks`);
}

export function getDocuments(missionId) {
  return request(`/missions/${missionId}/documents`);
}

export function getDocument(missionId, docId) {
  return request(`/missions/${missionId}/documents/${docId}`);
}

export function updateDocument(missionId, docId, contenu) {
  return request(`/missions/${missionId}/documents/${docId}`, {
    method: "PATCH",
    body: JSON.stringify({ contenu }),
  });
}

export function getActions(missionId) {
  return request(`/missions/${missionId}/actions`);
}

export function approveAction(missionId, actionId) {
  return request(`/missions/${missionId}/actions/${actionId}/approve`, {
    method: "POST",
  });
}

export function rejectAction(missionId, actionId) {
  return request(`/missions/${missionId}/actions/${actionId}/reject`, {
    method: "POST",
  });
}

export function excludeAction(missionId, actionId) {
  return request(`/missions/${missionId}/actions/${actionId}/exclude`, {
    method: "POST",
  });
}

export function approveAllActions(missionId) {
  return request(`/missions/${missionId}/actions/approve-all`, {
    method: "POST",
  });
}

export function updateAction(missionId, actionId, { destinataire, sujet, contenu }) {
  return request(`/missions/${missionId}/actions/${actionId}`, {
    method: "PATCH",
    body: JSON.stringify({ destinataire, sujet, contenu }),
  });
}

export function retryMission(missionId) {
  return request(`/missions/${missionId}/retry`, {
    method: "POST",
  });
}

export function getGoogleStatus() {
  return request("/auth/google/status");
}
