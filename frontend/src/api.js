const API_URL = "http://localhost:8000";

async function request(path, options) {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      // corps de réponse non-JSON ou vide, on garde le message générique
    }
    throw new Error(detail || `Erreur API ${res.status} sur ${path}`);
  }
  return res.json();
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

export function getMessages(missionId) {
  return request(`/missions/${missionId}/messages`);
}

export function sendChatMessage(missionId, contenu) {
  return request(`/missions/${missionId}/chat`, {
    method: "POST",
    body: JSON.stringify({ contenu }),
  });
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

export function retryMission(missionId) {
  return request(`/missions/${missionId}/retry`, {
    method: "POST",
  });
}
