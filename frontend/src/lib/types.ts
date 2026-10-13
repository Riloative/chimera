export type ProviderKind = "llamacpp" | "openai" | "ollama";
export type ProviderStatus = "unknown" | "connected" | "error" | "disabled";

export interface Provider {
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
  api_path: string;
  has_api_key: boolean;
  auth_header: string;
  timeout: number;
  enabled: boolean;
  vision: "auto" | "on" | "off";
  manual_models: string[];
  status: ProviderStatus;
  last_error: string | null;
  last_error_code: string | null;
  last_error_params: Record<string, unknown>;
  shared: boolean;
  owned: boolean;
  editable: boolean;
  latency_ms: number | null;
  checked_at: string | null;
  models_fetched_at: string | null;
  capabilities: Capabilities;
  supported_params: string[];
  created_at: string;
  updated_at: string;
}

export interface Capabilities {
  chat?: boolean;
  streaming?: boolean;
  vision?: boolean;
  audio?: boolean;
  n_ctx?: number;
  slots?: number;
  build?: string;
  model_path?: string;
  model_alias?: string;
  health_endpoint?: boolean;
  params?: string[];
}

export interface ModelInfo {
  id: string;
  provider_id: string;
  name: string | null;
  context_length: number | null;
  vision: boolean | null;
  loaded: boolean | null;
  source: "discovered" | "manual";
  meta: Record<string, string | number | boolean>;
}

export interface ProviderModels {
  provider: Provider;
  models: ModelInfo[];
  stale: boolean;
  error: string | null;
  error_code?: string | null;
  error_params?: Record<string, unknown>;
}

export interface HealthCheck {
  name: string;
  ok: boolean | null;
  code?: string;
  params?: Record<string, unknown>;
  detail: string;
  latency_ms?: number;
}

export interface TestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  error_code: string | null;
  error_params: Record<string, unknown>;
  models: ModelInfo[];
  models_ok: boolean;
  capabilities: Capabilities;
  checks: HealthCheck[];
  provider?: Provider;
}

export interface GenerationParams {
  temperature?: number | null;
  top_p?: number | null;
  top_k?: number | null;
  min_p?: number | null;
  max_tokens?: number | null;
  stop?: string[] | null;
  repeat_penalty?: number | null;
  presence_penalty?: number | null;
  frequency_penalty?: number | null;
  seed?: number | null;
}

export interface Conversation {
  id: string;
  title: string;
  title_source: "default" | "fallback" | "model" | "user";
  provider_id: string | null;
  model: string | null;
  system_prompt: string | null;
  params: GenerationParams;
  collection_ids: string[];
  pinned: boolean;
  created_at: string;
  updated_at: string;
  generating?: boolean;
  snippet?: string;
}

export type MessageStatus = "complete" | "streaming" | "error" | "cancelled" | "interrupted";

export interface Attachment {
  id: string;
  filename: string;
  mime: string;
  kind: "text" | "image";
  size: number;
  truncated: boolean;
  char_count?: number;
}

export interface Source {
  n: number;
  filename: string;
  document_id: string;
  chunk_index: number;
  text: string;
  score: number;
  method: string;
}

export interface MessageMetrics {
  finish_reason?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  tokens_per_second?: number;
  prompt_ms?: number;
  duration_ms?: number;
  ttft_ms?: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  position: number;
  role: "user" | "assistant" | "system";
  content: string;
  reasoning: string;
  status: MessageStatus;
  error: string | null;
  error_code?: string | null;
  error_params?: Record<string, unknown>;
  provider_id: string | null;
  model: string | null;
  attachments: Attachment[];
  sources: Source[];
  metrics: MessageMetrics;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
  generation_id: string | null;
}

export interface KnowledgeSettings {
  embedding_provider_id: string | null;
  embedding_model: string | null;
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
}

export interface AppSettings {
  theme: "system" | "light" | "dark";
  font_size: "sm" | "md" | "lg";
  chat_width: "narrow" | "default" | "wide";
  reduce_motion: boolean;
  sidebar_collapsed: boolean;
  default_provider_id: string | null;
  default_model: string | null;
  default_system_prompt: string;
  params: GenerationParams;
  enter_to_send: boolean;
  stream: boolean;
  auto_title: boolean;
  show_reasoning: boolean;
  max_attachment_mb: number;
  max_attachment_chars: number;
  knowledge: KnowledgeSettings;
}

export interface Prompt {
  id: string;
  title: string;
  description: string;
  category: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface Collection {
  id: string;
  name: string;
  description: string;
  document_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocument {
  id: string;
  collection_id: string;
  filename: string;
  mime: string;
  size: number;
  status: string;
  error: string | null;
  warning: string | null;
  warning_code?: string | null;
  chunk_count: number;
  char_count: number;
  embedding_model: string | null;
  created_at: string;
}

export interface SearchHit {
  chunk_id: number;
  document_id: string;
  filename: string;
  index: number;
  text: string;
  score: number;
  method: string;
}

export interface ModelRef {
  providerId: string;
  model: string;
}

export interface User {
  id: string;
  email: string;
  username: string;
  role: "admin" | "user";
  language: "en" | "lt" | null;
  active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
  legacy_notice?: Record<string, number> | null;
}

export interface AuthStatus {
  authenticated: boolean;
  user: User | null;
  registration_open: boolean;
  needs_setup: boolean;
  setup_token_required: boolean;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  user_agent: string | null;
  ip: string | null;
  remember: boolean;
  current: boolean;
}

export interface AdminSettings {
  registration_mode: "open" | "closed" | "first_user_only";
  provider_url_policy: "any" | "private" | "custom";
  provider_url_allowlist: string[];
}
