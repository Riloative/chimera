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
  /** Manual context size; null = what the server reports. */
  context_override: number | null;
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
  active_leaf_id?: string | null;
  context_strategy?: "sliding" | "summarize" | "off" | null;
  created_at: string;
  updated_at: string;
  generating?: boolean;
  snippet?: string;
  match_message_id?: string | null;
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
  /** Parent in the message tree (null for a first message). */
  parent_id: string | null;
  /** Depth in the tree. */
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
  web?: WebState;
  pinned?: boolean;
  context?: MessageContext;
  created_at: string;
  updated_at: string;
}

/** How the model's context was built for an answer (see services/context.py). */
export interface MessageContext {
  window?: number;
  window_source?: "server" | "manual" | "default";
  exact?: boolean;
  strategy?: "sliding" | "summarize" | "off";
  used?: number;
  breakdown?: { system: number; summary: number; latest: number; pinned: number; history: number; reserved: number };
  dropped?: number;
  cut_before?: string | null;
  summary?: string | null;
  summary_failed?: boolean;
  retried?: boolean;
}

/** GET /api/conversations/{id}/context: what the next request would contain. */
export interface ContextEstimate extends MessageContext {
  window: number;
  window_source: "server" | "manual" | "default";
  window_slots?: number | null;
  too_large?: boolean;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
  active_path: string[];
  web_sources: WebSource[];
  generation_id: string | null;
}

/* ------------------------------------------------------------------ web */
export type WebStepKind = "read_links" | "queries" | "search" | "read_results" | "budget" | "older_omitted" | "history_trimmed";
export type WebStepStatus = "running" | "done" | "error" | "cancelled";

export interface WebStep {
  id: number;
  kind: WebStepKind;
  status: WebStepStatus;
  code: string | null;
  params: {
    total?: number;
    done?: number;
    failed?: number;
    domains?: string[];
    queries?: string[];
    fallback?: boolean;
    count?: number;
    used?: number;
  };
}

export interface WebRef {
  n: number;
  source_id: string;
  included: boolean;
}

/** The web part of an answer: progress steps, queries and the citation map. */
export interface WebState {
  search?: boolean;
  links?: boolean;
  steps?: WebStep[];
  queries?: string[];
  refs?: WebRef[];
  citations?: { used: number[]; invalid: number[] };
  context_tokens?: number;
}

export interface WebSource {
  id: string;
  message_id: string;
  origin: "pasted" | "search";
  position: number;
  url: string;
  final_url: string | null;
  domain: string;
  title: string | null;
  site_name: string | null;
  description: string | null;
  published: string | null;
  kind: string | null;
  status: "ok" | "snippet" | "error";
  error_code: string | null;
  error_params: Record<string, unknown>;
  chars: number;
  truncated: boolean;
  notes: string[];
  query: string | null;
  snippet: string | null;
  rank: number | null;
  /** How the page was read: plain HTTP or the headless browser. */
  loader?: "static" | "browser";
  fetched_at: string | null;
}

export interface WebStatus {
  enabled: boolean;
  search_available: boolean;
  engine: WebEngineKind | null;
  max_urls_per_message: number;
  max_results: number;
  is_admin: boolean;
  /** The headless browser can render pages (configured, on and reachable). */
  browser_available?: boolean;
}

export type WebEngineKind = "searxng" | "brave" | "tavily";

export interface WebUserSettings {
  auto_read_links: boolean;
  search_by_default: boolean;
  keep_search_on: boolean;
  results: number;
  expand_steps: boolean;
  task_provider_id: string | null;
  task_model: string | null;
  privacy_acknowledged: boolean;
}

export interface WebAdminSettings {
  enabled: boolean;
  engine: WebEngineKind | null;
  searxng_url: string;
  searxng_auth_header: string;
  query_generation: boolean;
  query_prompt: string;
  grounding_prompt: string;
  fetch_allowlist: string[];
  domain_allowlist: string[];
  domain_denylist: string[];
  allowed_ports: number[];
  robots_for_search: boolean;
  user_agent: string;
  max_page_kb: number;
  timeout_seconds: number;
  max_urls_per_message: number;
  max_results: number;
  per_source_chars: number;
  total_chars: number;
  fetches_per_hour: number;
  searches_per_hour: number;
  browser_enabled: boolean;
  browser_auto: boolean;
  browser_timeout_seconds: number;
  browser_renders_per_hour: number;
}

/** State of the optional headless browser service (admin view). */
export interface WebBrowserStatus {
  configured: boolean;
  reachable: boolean;
  error?: string;
  version?: string;
  active?: number;
  proxy?: boolean;
}

export interface WebEngineTest {
  ok: boolean;
  latency_ms: number | null;
  result_count: number;
  error_code: string | null;
  error_params: Record<string, unknown>;
  checked_at?: string;
}

export interface WebAdminView {
  settings: WebAdminSettings;
  engines: { kind: WebEngineKind; configured: boolean; has_key: boolean; status: WebEngineTest | null }[];
  defaults: { query_prompt: string; grounding_prompt: string };
  browser?: WebBrowserStatus;
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
  web: WebUserSettings;
  context: ContextSettings;
}

export interface ContextSettings {
  strategy: "sliding" | "summarize" | "off";
  fallback_window: number;
  hysteresis: number;
  safety_margin_pct: number;
  reserve_tokens: number;
  summary_provider_id: string | null;
  summary_model: string | null;
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
