export interface MissionStep {
  id: string;
  step_order: number;
  title: string;
  description: string;
  status: string;
  assigned_role: string;
  action_type: string;
  risk_tier: number;
  requires_approval: boolean;
}

export interface Mission {
  id: string;
  title: string;
  objective: string;
  status: string;
  mission_type: "general" | "desktop" | "browser" | "build";
  workspace_path?: string | null;
  selected_coding_agent?: string | null;
  build_supervisor_state?: string | null;
  correction_iteration: number;
  max_correction_iterations: number;
  steps: MissionStep[];
}

export interface ApprovalRequest {
  id: string;
  mission_id: string;
  action_summary: string;
  target_summary: string;
  consequence_summary: string;
  reversible: boolean;
  risk_tier: number;
  status: string;
}

export interface EvidenceRecord {
  id: string;
  mission_id: string;
  evidence_type: string;
  local_path?: string | null;
  metadata: Record<string, any>;
  redaction_status: string;
  created_at: string;
}

export interface VoiceStatus {
  state: string;
  listening_enabled: boolean;
  wake_word: string;
  volume: number;
  selected_microphone: string;
  selected_mic_index: number | null;
  in_conversation_window: boolean;
  conversation_window_remaining_sec: number;
  conversation_Window_remaining_sec: number;
  last_transcript: string;
  last_spoken_response: string;
  current_action: string;
  current_structured_task: any;
  windows_startup_enabled: boolean;
  error?: string | null;
  history: Array<{
    id: string;
    timestamp: string;
    transcript: string;
    spoken_response: string;
    structured_task: any;
    mission_id?: string | null;
  }>;
}