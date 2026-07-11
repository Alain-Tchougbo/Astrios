const API_URL = "http://localhost:8000";

async function request(path, options) {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`Erreur API ${res.status} sur ${path}`);
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
