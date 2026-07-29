export type PersonaPresetScope = "global" | "user";
export type PersonaPresetVisibility = "public" | "private";
export type PersonaPresetStatus = "draft" | "published" | "archived";
export type PreferredAgentId = "fast" | "search" | "team";
export const DEFAULT_PREFERRED_AGENT_ID: PreferredAgentId = "fast";
export const PREFERRED_AGENT_IDS: PreferredAgentId[] = [
  "fast",
  "search",
  "team",
];
export type LocalizedText = string | Record<string, string>;

export interface PersonaStarterPrompt {
  icon?: string | null;
  text: LocalizedText;
}

export interface PersonaMarketplaceSkillRef {
  name: string;
  version?: string | null;
}

export interface PersonaSkillPublicationItem {
  local_name: string;
  marketplace_name: string;
  version?: string | null;
  reason?: string | null;
}

export interface PersonaSkillPublicationPreflightResponse {
  ready: PersonaSkillPublicationItem[];
  requires_publish: PersonaSkillPublicationItem[];
  conflicts: PersonaSkillPublicationItem[];
}

export interface PersonaPreset {
  id: string;
  scope: PersonaPresetScope;
  owner_user_id?: string | null;
  name: string;
  description: string;
  avatar?: string | null;
  tags: string[];
  system_prompt: string;
  starter_prompts?: PersonaStarterPrompt[];
  skill_names: string[];
  marketplace_skills?: PersonaMarketplaceSkillRef[];
  dify_kb_dataset_ids: string[];
  preferred_agent_id?: PreferredAgentId;
  visibility: PersonaPresetVisibility;
  status: PersonaPresetStatus;
  source_preset_id?: string | null;
  copied_from_version?: number | null;
  version: number;
  usage_count: number;
  is_favorite?: boolean;
  is_pinned?: boolean;
  last_used_at?: string | null;
  created_by?: string | null;
  updated_by?: string | null;
  created_at: string;
  updated_at: string;
  has_wecom?: boolean;
}

export interface PersonaPresetCreate {
  name: string;
  description?: string;
  avatar?: string | null;
  tags?: string[];
  system_prompt: string;
  starter_prompts?: PersonaStarterPrompt[];
  skill_names?: string[];
  dify_kb_dataset_ids?: string[];
  preferred_agent_id?: PreferredAgentId;
  scope?: PersonaPresetScope;
  visibility?: PersonaPresetVisibility;
  status?: PersonaPresetStatus;
  publish_personal_skills?: boolean;
}

export interface PersonaPresetUpdate {
  name?: string;
  description?: string;
  avatar?: string | null;
  tags?: string[];
  system_prompt?: string;
  starter_prompts?: PersonaStarterPrompt[];
  skill_names?: string[];
  dify_kb_dataset_ids?: string[];
  preferred_agent_id?: PreferredAgentId;
  scope?: PersonaPresetScope;
  visibility?: PersonaPresetVisibility;
  status?: PersonaPresetStatus;
  publish_personal_skills?: boolean;
}

export interface PersonaPresetPreferenceUpdate {
  is_favorite?: boolean;
  is_pinned?: boolean;
}

export interface PersonaPresetSnapshot {
  preset_id: string;
  name: string;
  system_prompt: string;
  starter_prompts?: PersonaStarterPrompt[];
  skill_names: string[];
  marketplace_skills?: PersonaMarketplaceSkillRef[];
  dify_kb_dataset_ids: string[];
  preferred_agent_id?: PreferredAgentId;
  missing_skill_names: string[];
  version: number;
  avatar?: string | null;
}

// ============================================
// Persona WeCom Config Types
// ============================================

export interface PersonaWeComConfig {
  preset_id: string;
  aibotid: string;
  has_secret: boolean;
  stream_reply: boolean;
  send_thinking_message: boolean;
  segmented_reply: boolean;
  segment_target_chars: number;
  session_ttl_hours: number;
  created_at?: string;
  updated_at?: string;
}

export interface PersonaWeComConfigCreate {
  aibotid: string;
  secret: string;
  stream_reply?: boolean;
  send_thinking_message?: boolean;
  segmented_reply?: boolean;
  segment_target_chars?: number;
  session_ttl_hours?: number;
}

export type PersonaWeComConnectionState =
  | "connected"
  | "connecting"
  | "reconnecting"
  | "disconnected"
  | "failed"
  | "unknown";

export type PersonaWeComReasonCode =
  | "replaced"
  | "reconnect_exhausted"
  | "auth_failed"
  | "lease_lost"
  | "disconnected";

export interface PersonaWeComStatus {
  preset_id: string;
  state: PersonaWeComConnectionState;
  reason_code?: PersonaWeComReasonCode | null;
  reason_detail?: string | null;
  updated_at?: string | null;
}

export interface PersonaWeComStatusBatchResponse {
  statuses: Record<string, PersonaWeComStatus | null>;
}

export interface PersonaPresetListResponse {
  presets: PersonaPreset[];
  total: number;
  skip: number;
  limit: number;
}

export interface PersonaPresetListParams {
  scope?: PersonaPresetScope;
  status?: PersonaPresetStatus;
  tag?: string;
  q?: string;
  favorite?: boolean;
  pinned?: boolean;
  skip?: number;
  limit?: number;
}
