"use client";

import React, { useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import {
  BarChart3,
  Coins,
  CreditCard,
  Database,
  DollarSign,
  Gauge,
  KeyRound,
  Layers,
} from "lucide-react";
import { fetchKeys, fetchStats, ApiKey, GatewayStats } from "@/lib/api";

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"];

export default function AnalyticsPage() {
  const [stats, setStats] = useState<GatewayStats | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    async function load() {
      try {
        const [statsData, keysData] = await Promise.all([
          fetchStats(),
          fetchKeys(),
        ]);
        setStats(statsData);
        setKeys(keysData);
      } catch (err) {
        console.warn("Failed to load analytics data", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const topKeysData = (() => {
    const fromStats = stats?.top_keys_by_spend || [];
    if (fromStats.length > 0) {
      return fromStats.map((k) => ({
        name: k.name || k.key_prefix,
        spend: parseFloat((k.spend_usd ?? 0).toFixed(4)),
        requests: k.requests ?? 0,
      }));
    }
    return keys
      .map((k) => ({
        name: k.name || k.key_prefix,
        spend: parseFloat((k.current_month_spend_usd ?? 0).toFixed(4)),
        requests: 0,
      }))
      .filter((k) => k.spend > 0);
  })();

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="border-b border-border pb-4">
        <h1 className="text-xl font-bold text-white flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-blue-400" /> Gateway Analytics & Spend Rollups
        </h1>
        <p className="text-xs text-gray-400">
          Historical request metrics, token accounting, and budget enforcement.
        </p>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-1">
            <span className="text-xs">Total Gateway Spend</span>
            <DollarSign className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-amber-400">
            ${stats?.total_cost_usd !== undefined ? stats.total_cost_usd.toFixed(4) : "0.0000"}
          </div>
          <div className="text-[11px] text-gray-500 mt-1">All time</div>
        </div>

        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-1">
            <span className="text-xs">24h Moving Spend</span>
            <Coins className="w-4 h-4 text-blue-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            ${stats?.spend_24h_usd !== undefined ? stats.spend_24h_usd.toFixed(4) : "0.0000"}
          </div>
          <div className="text-[11px] text-gray-500 mt-1">Last 24 hours</div>
        </div>

        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-1">
            <span className="text-xs">Cache Ratio</span>
            <Database className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-emerald-400">
            {stats?.cache_hit_rate !== undefined ? (stats.cache_hit_rate * 100).toFixed(1) : "0.0"}%
          </div>
          <div className="text-[11px] text-gray-500 mt-1">Cost savings</div>
        </div>

        <div className="p-4 rounded-xl bg-[#111827] border border-border">
          <div className="flex items-center justify-between text-gray-400 mb-1">
            <span className="text-xs">p95 Latency</span>
            <Gauge className="w-4 h-4 text-purple-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-purple-300">
            {stats?.p95_latency_ms !== undefined ? Math.round(stats.p95_latency_ms) : 0} ms
          </div>
          <div className="text-[11px] text-gray-500 mt-1">95th percentile</div>
        </div>
      </div>

      {/* Chart: Top Keys Spend Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="p-5 rounded-2xl bg-[#111827] border border-border">
          <h2 className="text-sm font-bold text-gray-300 uppercase tracking-wider mb-2 flex items-center gap-2">
            <CreditCard className="w-4 h-4 text-blue-400" /> Spend by API Key ($ USD)
          </h2>
          <p className="text-xs text-gray-500 mb-4">
            Aggregated cost incurred per client key
          </p>

          <div className="h-60 w-full">
            {topKeysData.length === 0 ? (
              <div className="h-full flex items-center justify-center text-gray-500 text-xs">
                No spend data logged yet
              </div>
            ) : mounted ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={topKeysData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
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
                  <Bar dataKey="spend" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : null}
          </div>
        </div>

        {/* Budget Progress Bars */}
        <div className="p-5 rounded-2xl bg-[#111827] border border-border space-y-4">
          <div>
            <h2 className="text-sm font-bold text-gray-300 uppercase tracking-wider mb-1 flex items-center gap-2">
              <KeyRound className="w-4 h-4 text-emerald-400" /> Key Monthly Budget Utilization
            </h2>
            <p className="text-xs text-gray-500">
              HTTP 402 is returned if monthly budget is exhausted
            </p>
          </div>

          <div className="space-y-4 overflow-y-auto max-h-60 pr-1">
            {keys.length === 0 ? (
              <div className="py-8 text-center text-gray-500 text-xs">
                No API keys found
              </div>
            ) : (
              keys.map((k) => {
                const spend = (k.current_month_spend_usd ?? 0) as number;
                const budget = k.monthly_budget_usd;
                const percent =
                  budget && budget > 0 ? Math.min(100, (spend / budget) * 100) : null;

                return (
                  <div key={k.id} className="p-3 rounded-xl bg-[#0d131f] border border-gray-800 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <div className="font-mono font-bold text-gray-200">
                        {k.name} <span className="text-gray-500 text-[10px]">({k.key_prefix})</span>
                      </div>
                      <div className="font-mono text-gray-300">
                        ${spend.toFixed(4)}{" "}
                        <span className="text-gray-500">
                          / {budget ? `$${budget.toFixed(2)}` : "∞"}
                        </span>
                      </div>
                    </div>

                    {budget && budget > 0 ? (
                      <div>
                        <div className="w-full bg-gray-800 h-1.5 rounded-full overflow-hidden">
                          <div
                            className={`h-full transition-all ${
                              (percent || 0) > 90
                                ? "bg-rose-500"
                                : (percent || 0) > 75
                                ? "bg-amber-500"
                                : "bg-emerald-500"
                            }`}
                            style={{ width: `${percent}%` }}
                          />
                        </div>
                        <div className="flex justify-between text-[10px] text-gray-500 mt-1">
                          <span>{percent?.toFixed(1)}% consumed</span>
                          <span>{percent && percent >= 100 ? "402 REJECT ACTIVE" : "Available"}</span>
                        </div>
                      </div>
                    ) : (
                      <div className="text-[10px] text-gray-500">Unlimited Monthly Budget</div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

