"use client";

import React, { useEffect, useState, useRef } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Cpu,
  Database,
  DollarSign,
  Flame,
  Gauge,
  Power,
  Radio,
  Server,
  Zap,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import {
  ADMIN_KEY,
  fetchProviders,
  fetchStats,
  GATEWAY_URL,
  GatewayStats,
  ProviderInfo,
  updateChaos,
} from "@/lib/api";

interface RequestRow {
  id: string;
  timestamp: string;
  alias: string;
  model: string;
  provider: string;
  status: number;
  cached: boolean;
  latency_ms: number;
  ttft_ms?: number;
  tokens: number;
  cost_usd: number;
}

export default function LiveOverviewPage() {
  const [stats, setStats] = useState<GatewayStats | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [requests, setRequests] = useState<RequestRow[]>([]);
  const [trafficShare, setTrafficShare] = useState<Record<string, number>>({});
  const [sseConnected, setSseConnected] = useState(false);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});

  const eventSourceRef = useRef<EventSource | null>(null);

  // Poll stats and providers
  const refreshData = async () => {
    try {
      const [statsData, providersData] = await Promise.all([
        fetchStats(),
        fetchProviders(),
      ]);
      setStats(statsData);
      setProviders(providersData);
    } catch (err) {
      console.warn("Failed to refresh stats/providers", err);
    }
  };

  useEffect(() => {
    refreshData();
    const interval = setInterval(refreshData, 3000);
    return () => clearInterval(interval);
  }, []);

  // Connect to Gateway Live SSE Stream (/admin/events)
  useEffect(() => {
    const sseUrl = `${GATEWAY_URL}/admin/events?token=${ADMIN_KEY}`;
    const es = new EventSource(sseUrl);
    eventSourceRef.current = es;

    es.onopen = () => {
      setSseConnected(true);
    };

    es.onerror = () => {
      setSseConnected(false);
    };

    es.addEventListener("request_completed", (e) => {
      try {
        const payload = JSON.parse(e.data);
        const data = payload.data || {};
        const newReq: RequestRow = {
          id: data.request_id || Math.random().toString(36).substring(7),
          timestamp: new Date().toLocaleTimeString(),
          alias: data.alias || "unknown",
          model: data.model || "unknown",
          provider: data.provider || "none",
          status: data.status_code || 200,
          cached: !!data.cached,
          latency_ms: data.latency_ms || 0,
          ttft_ms: data.ttft_ms,
          tokens: (data.prompt_tokens || 0) + (data.completion_tokens || 0),
          cost_usd: data.cost_usd || 0,
        };

        setRequests((prev) => [newReq, ...prev.slice(0, 49)]);

        if (newReq.provider && newReq.provider !== "none") {
          setTrafficShare((prev) => ({
            ...prev,
            [newReq.provider]: (prev[newReq.provider] || 0) + 1,
          }));
        }
      } catch (err) {
        console.error("Error parsing request_completed SSE", err);
      }
    });

    es.addEventListener("circuit_tripped", () => {
      refreshData();
    });

    es.addEventListener("circuit_recovered", () => {
      refreshData();
    });

    return () => {
      es.close();
    };
  }, []);

  const handleToggleKill = async (provider: ProviderInfo) => {
    const nextDown = !provider.is_down;
    setActionLoading((prev) => ({ ...prev, [provider.name]: true }));
    try {
      await updateChaos(provider.name, { is_down: nextDown });
      await refreshData();
    } catch (err) {
      console.error("Failed to toggle provider", err);
    } finally {
      setActionLoading((prev) => ({ ...prev, [provider.name]: false }));
    }
  };

  // Convert traffic share map to chart data
  const chartData = providers.map((p) => ({
    name: p.name,
    requests: trafficShare[p.name] || 0,
    circuit: p.circuit_state,
    is_down: p.is_down,
  }));

  const errorRatePercent =
    stats && stats.requests_24h > 0
      ? ((stats.errors_24h / stats.requests_24h) * 100).toFixed(1)
      : "0.0";

  const cacheHitPercent = stats
    ? (stats.cache_hit_rate * 100).toFixed(1)
    : "0.0";

  return (
    <div className="space-y-6">
      {/* Demo Callout Banner */}
      <div className="bg-gradient-to-r from-blue-900/30 via-indigo-900/20 to-purple-900/20 border border-blue-500/20 rounded-2xl p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-blue-500/20 border border-blue-500/30 text-blue-400">
            <Zap className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-base font-bold text-white flex items-center gap-2">
              Live Gateway Control & Failover Observability
            </h1>
            <p className="text-xs text-gray-400">
              Real-time request routing, sub-millisecond cache hits, circuit breaking, and instant failover.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 self-end md:self-auto">
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-black/40 border border-gray-800 text-xs">
            <Radio
              className={`w-3.5 h-3.5 ${
                sseConnected ? "text-emerald-400 animate-pulse" : "text-rose-400"
              }`}
            />
            <span className="font-mono text-gray-300 text-[11px]">
              {sseConnected ? "SSE Stream Connected" : "Connecting SSE..."}
            </span>
          </div>
        </div>
      </div>

      {/* Top Metrics Grid */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 lg:gap-4">
        {/* Metric 1: Total Volume */}
        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-2">
            <span className="text-xs font-medium">Total Requests</span>
            <Activity className="w-4 h-4 text-blue-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {stats ? stats.total_requests.toLocaleString() : "..."}
          </div>
          <div className="text-[11px] text-gray-500 mt-1">
            24h: {stats ? stats.requests_24h.toLocaleString() : "0"}
          </div>
        </div>

        {/* Metric 2: Error Rate */}
        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-2">
            <span className="text-xs font-medium">24h Error Rate</span>
            <AlertCircle className="w-4 h-4 text-rose-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {errorRatePercent}%
          </div>
          <div className="text-[11px] text-gray-500 mt-1">
            {stats ? stats.errors_24h : 0} errors logged
          </div>
        </div>

        {/* Metric 3: Cache Hit Rate */}
        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-2">
            <span className="text-xs font-medium">Cache Hit Rate</span>
            <Database className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-emerald-400">
            {cacheHitPercent}%
          </div>
          <div className="text-[11px] text-gray-500 mt-1">Sub-5ms responses</div>
        </div>

        {/* Metric 4: p95 Latency */}
        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-2">
            <span className="text-xs font-medium">p95 Latency</span>
            <Gauge className="w-4 h-4 text-purple-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {stats ? Math.round(stats.p95_latency_ms) : 0}
            <span className="text-xs text-gray-500 ml-1">ms</span>
          </div>
          <div className="text-[11px] text-gray-500 mt-1">Moving 24h window</div>
        </div>

        {/* Metric 5: Total Spend */}
        <div className="col-span-2 md:col-span-1 p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-2">
            <span className="text-xs font-medium">Total Spend</span>
            <DollarSign className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-amber-400">
            ${stats ? stats.total_cost_usd.toFixed(4) : "0.0000"}
          </div>
          <div className="text-[11px] text-gray-500 mt-1">
            24h: ${stats ? stats.spend_24h_usd.toFixed(4) : "0.0000"}
          </div>
        </div>
      </div>

      {/* Provider Health Cards & Traffic Distribution */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Provider Health Cards */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-gray-300 uppercase tracking-wider flex items-center gap-2">
              <Server className="w-4 h-4 text-blue-400" /> Upstream Providers & Circuit Breakers
            </h2>
            <span className="text-xs text-gray-400">
              Auto-failover when circuit opens
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {providers.map((p) => {
              const isKilled = p.is_down;
              const isCircuitOpen = p.circuit_state === "OPEN";
              const isHalfOpen = p.circuit_state === "HALF_OPEN";
              const loading = actionLoading[p.name];

              return (
                <div
                  key={p.name}
                  className={`p-4 rounded-xl border flex flex-col justify-between transition relative overflow-hidden ${
                    isKilled || isCircuitOpen
                      ? "bg-rose-950/20 border-rose-500/40 shadow-lg shadow-rose-950/20"
                      : isHalfOpen
                      ? "bg-amber-950/20 border-amber-500/40"
                      : "bg-[#111827] border-border"
                  }`}
                >
                  <div>
                    {/* Top Tag & Status */}
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-mono font-bold text-sm text-gray-200">
                        {p.name}
                      </span>
                      <span
                        className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${
                          isCircuitOpen
                            ? "bg-rose-500/10 text-rose-400 border-rose-500/30 animate-pulse"
                            : isHalfOpen
                            ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                            : "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                        }`}
                      >
                        {p.circuit_state}
                      </span>
                    </div>

                    {/* Mode & Latency info */}
                    <div className="space-y-1.5 text-xs text-gray-400 my-3">
                      <div className="flex justify-between">
                        <span>Status:</span>
                        <span
                          className={`font-semibold ${
                            isKilled ? "text-rose-400" : "text-emerald-400"
                          }`}
                        >
                          {isKilled ? "Forced Down" : "Healthy"}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span>EWMA Latency:</span>
                        <span className="font-mono text-gray-300">
                          {p.base_latency_ms} ms
                        </span>
                      </div>
                      {p.error_rate > 0 && (
                        <div className="flex justify-between text-rose-400">
                          <span>Injected Errors:</span>
                          <span className="font-mono">
                            {Math.round(p.error_rate * 100)}%
                          </span>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Immediate Action Button for Demo */}
                  <button
                    onClick={() => handleToggleKill(p)}
                    disabled={loading}
                    className={`w-full py-2 px-3 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition ${
                      isKilled
                        ? "bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-950"
                        : "bg-rose-600 hover:bg-rose-500 text-white shadow-md shadow-rose-950"
                    }`}
                  >
                    <Power className="w-3.5 h-3.5" />
                    {isKilled ? "Restore Provider" : "Kill Provider"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right 1 Col: Live Traffic Share Chart */}
        <div className="p-4 rounded-xl bg-[#111827] border border-border flex flex-col justify-between">
          <div>
            <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wider mb-1 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-indigo-400" /> Live Traffic Share
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Real-time request distribution
            </p>
          </div>

          <div className="h-44 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <XAxis dataKey="name" stroke="#6b7280" fontSize={11} tickLine={false} />
                <YAxis stroke="#6b7280" fontSize={11} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#0d131f",
                    borderColor: "#374151",
                    borderRadius: "8px",
                    fontSize: "12px",
                  }}
                />
                <Bar dataKey="requests" radius={[4, 4, 0, 0]}>
                  {chartData.map((entry, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={
                        entry.is_down || entry.circuit === "OPEN"
                          ? "#ef4444"
                          : entry.circuit === "HALF_OPEN"
                          ? "#f59e0b"
                          : "#3b82f6"
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Live Request Stream Table */}
      <div className="p-4 rounded-xl bg-[#111827] border border-border space-y-3">
        <div className="flex items-center justify-between border-b border-gray-800 pb-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wider flex items-center gap-2">
              <Activity className="w-4 h-4 text-emerald-400" /> Live Request Stream
            </h3>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">
              {requests.length} events
            </span>
          </div>
          <span className="text-xs text-gray-500">Auto-scrolling via SSE</span>
        </div>

        {requests.length === 0 ? (
          <div className="py-12 text-center text-gray-500 text-sm">
            Waiting for live traffic... (Send a request via Playground or run the load generator)
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800 pb-2">
                  <th className="py-2 px-3">Time</th>
                  <th className="py-2 px-3">Alias</th>
                  <th className="py-2 px-3">Provider</th>
                  <th className="py-2 px-3">Status</th>
                  <th className="py-2 px-3">Cache</th>
                  <th className="py-2 px-3">Latency</th>
                  <th className="py-2 px-3">Tokens</th>
                  <th className="py-2 px-3">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {requests.map((r) => (
                  <tr key={r.id} className="hover:bg-gray-800/40 transition">
                    <td className="py-2.5 px-3 text-gray-400">{r.timestamp}</td>
                    <td className="py-2.5 px-3 font-semibold text-gray-200">{r.alias}</td>
                    <td className="py-2.5 px-3">
                      <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700">
                        {r.provider}
                      </span>
                    </td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`px-2 py-0.5 rounded font-bold ${
                          r.status >= 200 && r.status < 300
                            ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                            : r.status === 429 || r.status === 402
                            ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                            : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                        }`}
                      >
                        {r.status}
                      </span>
                    </td>
                    <td className="py-2.5 px-3">
                      {r.cached ? (
                        <span className="px-2 py-0.5 rounded font-bold bg-blue-500/10 text-blue-400 border border-blue-500/20">
                          HIT
                        </span>
                      ) : (
                        <span className="text-gray-500">MISS</span>
                      )}
                    </td>
                    <td className="py-2.5 px-3 text-gray-300">
                      {Math.round(r.latency_ms)} ms
                    </td>
                    <td className="py-2.5 px-3 text-gray-400">{r.tokens}</td>
                    <td className="py-2.5 px-3 text-amber-400">
                      ${r.cost_usd.toFixed(5)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
