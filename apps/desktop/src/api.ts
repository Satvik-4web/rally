import { ApprovalRequest, Mission } from "./types";
export const API_BASE = "http://127.0.0.1:8765/api/v1";

let cachedApiToken = "";
let cachedApprovalSecret = "";

export function setSecurityTokens(tokens: { apiToken?: string; approvalSecret?: string }) {
  if (tokens.apiToken) cachedApiToken = tokens.apiToken;
  if (tokens.approvalSecret) cachedApprovalSecret = tokens.approvalSecret;
}

export function getSecurityTokens() {
  return { apiToken: cachedApiToken, approvalSecret: cachedApprovalSecret };
}

export async function initSecurityTokens(): Promise<void> {
  try {
    const sec = (window as any).rallySecurity;
    if (sec && typeof sec.getTokens === "function") {
      const tokens = await sec.getTokens();
      if (tokens?.apiToken) cachedApiToken = tokens.apiToken;
      if (tokens?.approvalSecret) cachedApprovalSecret = tokens.approvalSecret;
    }
  } catch (err) {
    console.warn("[RALLY API] Failed to initialize security credentials:", err);
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  if (!cachedApiToken && (window as any).rallySecurity) {
    await initSecurityTokens();
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options?.headers as Record<string, string>) || {}),
  };

  if (cachedApiToken && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${cachedApiToken}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const rallyApi = {
  getHealth: () => request<any>("/health"),
  listMissions: () => request<Mission[]>("/missions"),
  createMission: (payload: any) => request<Mission>("/missions", { method: "POST", body: JSON.stringify(payload) }),
  startMission: (id: string) => request<Mission>(`/missions/${id}/start`, { method: "POST" }),
  pauseMission: (id: string) => request<Mission>(`/missions/${id}/pause`, { method: "POST" }),
  cancelMission: (id: string) => request<Mission>(`/missions/${id}/cancel`, { method: "POST" }),
  getMissionEvidence: (id: string) => request<any[]>(`/missions/${id}/evidence`).catch(() => []),
  getMissionEvents: (id: string) => request<any[]>(`/missions/${id}/events`).catch(() => []),
  listApprovals: () => request<ApprovalRequest[]>("/approvals"),
  approveRequest: async (id: string) => {
    if (!cachedApprovalSecret && (window as any).rallySecurity) {
      await initSecurityTokens();
    }
    return request<any>(`/approvals/${id}/approve`, {
      method: "POST",
      headers: cachedApprovalSecret ? { "X-Rally-Approval-Token": cachedApprovalSecret } : {},
      body: JSON.stringify({ resolved_by: "local_user" }),
    });
  },
  rejectRequest: async (id: string) => {
    if (!cachedApprovalSecret && (window as any).rallySecurity) {
      await initSecurityTokens();
    }
    return request<any>(`/approvals/${id}/reject`, {
      method: "POST",
      headers: cachedApprovalSecret ? { "X-Rally-Approval-Token": cachedApprovalSecret } : {},
      body: JSON.stringify({ resolved_by: "local_user" }),
    });
  },
  getWindows: () => request<{ windows: any[] }>("/desktop/windows"),
  emergencyStop: () => request<any>("/desktop/stop", { method: "POST" }),
  runBuildCheck: (id: string, kind: string) => request<any>(`/build/missions/${id}/run-check`, { method: "POST", body: JSON.stringify({ command_kind: kind }) }),
  getBuildDefects: (id: string) => request<any[]>(`/build/missions/${id}/defects`),
  requestCorrection: (id: string) => request<any>(`/build/missions/${id}/request-correction`, { method: "POST" }),
  getBuildReport: (id: string) => request<any>(`/build/missions/${id}/report`),
  verifyLedger: () => request<any>("/ledger/verify"),

  // Voice Companion Endpoints
  getVoiceStatus: () => request<any>("/voice/status"),
  getMicrophones: () => request<{ microphones: Array<{ index: number | null; name: string }> }>("/voice/microphones"),
  startVoiceListening: () => request<any>("/voice/start", { method: "POST" }),
  wakeVoice: () => request<any>("/voice/wake", { method: "POST" }),
  pauseVoiceListening: () => request<any>("/voice/pause", { method: "POST" }),
  testMicrophone: () => request<any>("/voice/test-mic", { method: "POST" }),
  updateVoiceConfig: (payload: any) => request<any>("/voice/config", { method: "POST", body: JSON.stringify(payload) }),
  sendVoiceUtterance: (transcript: string, forceAwake = false) =>
    request<any>("/voice/utterance", { method: "POST", body: JSON.stringify({ transcript, force_awake: forceAwake }) }),
  transcribeAudio: async (audioBlob: Blob, autoExecute = true, forceAwake = false) => {
    if (!cachedApiToken && (window as any).rallySecurity) {
      await initSecurityTokens();
    }
    const headers: Record<string, string> = {
      "Content-Type": "audio/wav",
    };
    if (cachedApiToken) {
      headers["Authorization"] = `Bearer ${cachedApiToken}`;
    }
    const res = await fetch(`${API_BASE}/voice/transcribe?auto_execute=${autoExecute}&force_awake=${forceAwake}`, {
      method: "POST",
      headers,
      body: audioBlob,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },
  getEventStreamUrl: () => {
    return cachedApiToken ? `${API_BASE}/events/stream?token=${cachedApiToken}` : `${API_BASE}/events/stream`;
  },
  initSecurityTokens,
};
