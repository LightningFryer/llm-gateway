/**
 * API client and shared data types for communicating with Relay LLM Gateway.
 */

export interface ChaosSettings {
  is_down?: boolean;
  base_latency_ms?: number;
  jitter_ms?: number;
  error_rate?: number;
  slow_ttft_ms?: number;
  fail_after_tokens?: number | null;
}

export interface ProviderInfo {
  name: string;
  type: string;
  circuit_state: "CLOSED" | "OPEN" | "HALF_OPEN";
  is_down: boolean;
  base_latency_ms: number;
  error_rate: number;
  fail_after_tokens: number | null;
  ewma_latency_ms?: number;
}

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  rate_limit_rpm: number;
  rate_limit_tpm: number;
  monthly_budget_usd: number | null;
  is_active: boolean;
  created_at: string;
  last_used_at: string | null;
  current_month_spend_usd?: number;
}

export interface GatewayStats {
  total_requests: number;
  total_cost_usd: number;
  requests_24h: number;
  errors_24h: number;
  spend_24h_usd: number;
  cache_hit_rate: number;
  p95_latency_ms: number;
  providers: Record<string, { circuit_state: string; is_down: boolean }>;
  top_keys_by_spend: Array<{
    name: string;
    key_prefix: string;
    spend_usd: number;
    requests: number;
  }>;
}

export interface ModelAliasTarget {
  provider: string;
  model: string;
  weight: number;
  cost_input_per_million: number;
  cost_output_per_million: number;
}

export interface ModelAliasConfig {
  strategy: string;
  description: string;
  targets: ModelAliasTarget[];
}

export interface RouterConfig {
  aliases: Record<string, ModelAliasConfig>;
}

export interface RequestEventData {
  request_id?: string;
  alias?: string;
  model?: string;
  provider?: string;
  status_code?: number;
  cached?: boolean;
  latency_ms?: number;
  ttft_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_usd?: number;
  error?: string;
}

export interface RelaySSEEvent {
  event: string;
  timestamp: string;
  data: RequestEventData | Record<string, unknown>;
}

export const GATEWAY_URL =
  process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";
export const ADMIN_KEY =
  process.env.NEXT_PUBLIC_ADMIN_KEY || "relay_admin_secret_key_12345";

function adminHeaders(): HeadersInit {
  return {
    Authorization: `Bearer ${ADMIN_KEY}`,
    "Content-Type": "application/json",
  };
}

export async function fetchHealth(): Promise<{ status: string }> {
  const res = await fetch(`${GATEWAY_URL}/healthz`, { cache: "no-store" });
  if (!res.ok) throw new Error("Gateway offline");
  return res.json();
}

export async function fetchProviders(): Promise<ProviderInfo[]> {
  const res = await fetch(`${GATEWAY_URL}/admin/providers`, {
    headers: adminHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch providers");
  const data = await res.json();
  return data.map((p: any) => ({
    name: p.name,
    type: p.is_mock ? "mock" : "upstream",
    circuit_state: p.circuit_state || "CLOSED",
    is_down: p.is_down ?? p.chaos?.is_down ?? false,
    base_latency_ms: p.base_latency_ms ?? p.chaos?.base_latency_ms ?? p.ewma_latency_ms ?? 50,
    error_rate: p.error_rate ?? p.chaos?.error_rate ?? 0,
    fail_after_tokens: p.fail_after_tokens ?? p.chaos?.fail_after_tokens ?? null,
    ewma_latency_ms: p.ewma_latency_ms ?? 50,
  }));
}

export async function updateChaos(
  providerName: string,
  settings: ChaosSettings
): Promise<void> {
  const res = await fetch(`${GATEWAY_URL}/admin/chaos`, {
    method: "POST",
    headers: adminHeaders(),
    body: JSON.stringify({
      provider: providerName,
      ...settings,
    }),
  });
  if (!res.ok) throw new Error("Failed to update chaos settings");
}

export async function resetChaos(): Promise<void> {
  const res = await fetch(`${GATEWAY_URL}/admin/chaos/reset`, {
    method: "POST",
    headers: adminHeaders(),
  });
  if (!res.ok) throw new Error("Failed to reset chaos");
}

export async function fetchStats(): Promise<GatewayStats> {
  const res = await fetch(`${GATEWAY_URL}/admin/stats`, {
    headers: adminHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch stats");
  const data = await res.json();
  const summary = data.summary || {};
  return {
    total_requests: data.total_requests ?? summary.total_requests ?? 0,
    total_cost_usd: data.total_cost_usd ?? summary.spend_today_usd ?? summary.spend_today ?? 0,
    requests_24h: data.requests_24h ?? summary.total_requests ?? 0,
    errors_24h: data.errors_24h ?? summary.error_count ?? 0,
    spend_24h_usd: data.spend_24h_usd ?? summary.spend_today_usd ?? summary.spend_today ?? 0,
    cache_hit_rate: data.cache_hit_rate ?? summary.cache_hit_rate ?? 0,
    p95_latency_ms: data.p95_latency_ms ?? summary.avg_latency_ms ?? summary.avg_latency ?? 0,
    providers: data.providers ?? {},
    top_keys_by_spend: data.top_keys_by_spend ?? [],
  };
}

export async function fetchKeys(): Promise<ApiKey[]> {
  const res = await fetch(`${GATEWAY_URL}/admin/keys`, {
    headers: adminHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch keys");
  const data = await res.json();
  return data.map((k: any) => ({
    id: k.id,
    name: k.name,
    key_prefix: k.key_prefix,
    rate_limit_rpm: k.rate_limit_rpm ?? k.rpm_limit ?? 60,
    rate_limit_tpm: k.rate_limit_tpm ?? k.tpm_limit ?? 60000,
    monthly_budget_usd: k.monthly_budget_usd ?? null,
    is_active: k.is_active ?? k.enabled ?? true,
    created_at: k.created_at || "",
    last_used_at: k.last_used_at ?? null,
    current_month_spend_usd: k.current_month_spend_usd ?? k.spend_this_month_usd ?? 0,
  }));
}

export async function createKey(data: {
  name: string;
  rate_limit_rpm?: number;
  rate_limit_tpm?: number;
  monthly_budget_usd?: number | null;
}): Promise<{ raw_key: string; key: ApiKey }> {
  const res = await fetch(`${GATEWAY_URL}/admin/keys`, {
    method: "POST",
    headers: adminHeaders(),
    body: JSON.stringify({
      name: data.name,
      rpm_limit: data.rate_limit_rpm ?? 60,
      tpm_limit: data.rate_limit_tpm ?? 60000,
      monthly_budget_usd: data.monthly_budget_usd ?? 50.0,
    }),
  });
  if (!res.ok) throw new Error("Failed to create key");
  const json = await res.json();
  return {
    raw_key: json.secret || json.raw_key || "",
    key: {
      id: json.id,
      name: json.name,
      key_prefix: json.key_prefix,
      rate_limit_rpm: json.rpm_limit ?? json.rate_limit_rpm ?? 60,
      rate_limit_tpm: json.tpm_limit ?? json.rate_limit_tpm ?? 60000,
      monthly_budget_usd: json.monthly_budget_usd ?? null,
      is_active: json.enabled ?? json.is_active ?? true,
      created_at: json.created_at || "",
      last_used_at: null,
    },
  };
}

export async function rotateKey(
  keyId: string
): Promise<{ raw_key: string; key?: ApiKey }> {
  const res = await fetch(`${GATEWAY_URL}/admin/keys/${keyId}/rotate`, {
    method: "POST",
    headers: adminHeaders(),
  });
  if (!res.ok) throw new Error("Failed to rotate key");
  const json = await res.json();
  return { raw_key: json.secret || json.raw_key || "" };
}

export async function toggleKeyStatus(
  keyId: string,
  isActive: boolean
): Promise<ApiKey> {
  const res = await fetch(`${GATEWAY_URL}/admin/keys/${keyId}`, {
    method: "PATCH",
    headers: adminHeaders(),
    body: JSON.stringify({ enabled: isActive }),
  });
  if (!res.ok) throw new Error("Failed to update key status");
  return res.json();
}

export async function fetchConfig(): Promise<RouterConfig> {
  const res = await fetch(`${GATEWAY_URL}/admin/config`, {
    headers: adminHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch config");
  return res.json();
}

export async function reloadConfig(): Promise<void> {
  const res = await fetch(`${GATEWAY_URL}/admin/config/reload`, {
    method: "POST",
    headers: adminHeaders(),
  });
  if (!res.ok) throw new Error("Failed to reload config");
}

