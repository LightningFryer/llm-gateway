"use client";

import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  Flame,
  Power,
  RotateCcw,
  Sliders,
  Zap,
} from "lucide-react";
import { fetchProviders, ProviderInfo, resetChaos, updateChaos } from "@/lib/api";

export default function ChaosPage() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadingMap, setLoadingMap] = useState<Record<string, boolean>>({});
  const [globalResetting, setGlobalResetting] = useState(false);

  const loadData = async () => {
    try {
      const data = await fetchProviders();
      setProviders(data);
    } catch (err) {
      console.warn("Failed to load providers for chaos view", err);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleDown = async (p: ProviderInfo) => {
    const nextState = !p.is_down;
    setLoadingMap((prev) => ({ ...prev, [p.name]: true }));
    try {
      await updateChaos(p.name, { is_down: nextState });
      await loadData();
    } catch (err) {
      console.error("Failed to toggle provider status", err);
    } finally {
      setLoadingMap((prev) => ({ ...prev, [p.name]: false }));
    }
  };

  const handleLatencyChange = async (p: ProviderInfo, latencyMs: number) => {
    try {
      await updateChaos(p.name, { base_latency_ms: latencyMs });
      await loadData();
    } catch (err) {
      console.error("Failed to update latency", err);
    }
  };

  const handleErrorRateChange = async (p: ProviderInfo, ratePercent: number) => {
    try {
      await updateChaos(p.name, { error_rate: ratePercent / 100.0 });
      await loadData();
    } catch (err) {
      console.error("Failed to update error rate", err);
    }
  };

  const handleFailMidStream = async (p: ProviderInfo, checked: boolean) => {
    try {
      await updateChaos(p.name, { fail_after_tokens: checked ? 3 : null });
      await loadData();
    } catch (err) {
      console.error("Failed to update fail after tokens", err);
    }
  };

  const handleResetAll = async () => {
    setGlobalResetting(true);
    try {
      await resetChaos();
      await loadData();
    } catch (err) {
      console.error("Failed to reset chaos", err);
    } finally {
      setGlobalResetting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Flame className="w-5 h-5 text-rose-500" /> Chaos Engineering & Fault Injection
          </h1>
          <p className="text-xs text-gray-400">
            Simulate upstream provider outages, network latency spikes, error rates, and mid-stream drops in real-time.
          </p>
        </div>

        <button
          onClick={handleResetAll}
          disabled={globalResetting}
          className="flex items-center gap-1.5 text-xs font-medium px-4 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 transition"
        >
          <RotateCcw className={`w-3.5 h-3.5 ${globalResetting ? "animate-spin" : ""}`} />
          Reset All Knobs to Healthy
        </button>
      </div>

      {/* Demo Instructions Banner */}
      <div className="p-4 rounded-xl bg-blue-500/10 border border-blue-500/20 text-xs text-blue-300 leading-relaxed">
        <div className="font-semibold text-blue-200 mb-1 flex items-center gap-1.5">
          <Zap className="w-3.5 h-3.5 text-blue-400" /> Live Demo Moment Walkthrough:
        </div>
        Click <span className="font-mono text-rose-300 bg-rose-950/60 px-1 py-0.5 rounded">Kill Provider</span> on any provider. The circuit trips <span className="font-mono text-rose-300">OPEN</span> and traffic immediately fails over to the next target with 0 client errors. Click <span className="font-mono text-emerald-300 bg-emerald-950/60 px-1 py-0.5 rounded">Restore Provider</span> to allow half-open probe recovery.
      </div>

      {/* Provider Chaos Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {providers.map((p) => {
          const isLoading = !!loadingMap[p.name];
          return (
            <div
              key={p.name}
              className={`p-5 rounded-2xl border transition flex flex-col justify-between ${
                p.is_down
                  ? "bg-rose-950/20 border-rose-500/40"
                  : p.circuit_state === "OPEN"
                  ? "bg-amber-950/20 border-amber-500/40"
                  : "bg-[#111827] border-border"
              }`}
            >
              <div>
                {/* Header */}
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <span className="font-mono font-bold text-gray-200 text-base">
                      {p.name}
                    </span>
                    <span className="ml-2 text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full border bg-gray-800 text-gray-300 border-gray-700">
                      {p.type || "mock"}
                    </span>
                  </div>

                  <span
                    className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${
                      p.circuit_state === "OPEN"
                        ? "bg-rose-500/10 text-rose-400 border-rose-500/30 animate-pulse"
                        : p.circuit_state === "HALF_OPEN"
                        ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                        : "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                    }`}
                  >
                    {p.circuit_state}
                  </span>
                </div>

                {/* Status Indicator */}
                <div className="flex items-center gap-4 text-xs text-gray-400 mb-4 pb-3 border-b border-gray-800">
                  <div>
                    Circuit:{" "}
                    <span
                      className={`font-mono font-semibold ${
                        p.circuit_state === "CLOSED"
                          ? "text-emerald-400"
                          : p.circuit_state === "OPEN"
                          ? "text-rose-400"
                          : "text-amber-400"
                      }`}
                    >
                      {p.circuit_state}
                    </span>
                  </div>
                  <div>
                    Status:{" "}
                    <span
                      className={`font-semibold ${
                        p.is_down ? "text-rose-400" : "text-emerald-400"
                      }`}
                    >
                      {p.is_down ? "FORCED DOWN" : "Healthy"}
                    </span>
                  </div>
                </div>

                {/* Knobs */}
                <div className="space-y-4 mb-6">
                  {/* Latency Slider */}
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-gray-400 flex items-center gap-1">
                        <Sliders className="w-3 h-3" /> Injected Latency
                      </span>
                      <span className="font-mono text-gray-200 font-medium">
                        {p.base_latency_ms ?? 0} ms
                      </span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="1500"
                      step="25"
                      value={p.base_latency_ms ?? 0}
                      onChange={(e) =>
                        handleLatencyChange(p, Number(e.target.value))
                      }
                      className="w-full accent-blue-500 h-1.5 bg-gray-700 rounded-lg cursor-pointer"
                    />
                  </div>

                  {/* Error Rate Slider */}
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-gray-400 flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3" /> Injected Error Rate
                      </span>
                      <span className="font-mono text-gray-200 font-medium">
                        {Math.round((p.error_rate ?? 0) * 100)}%
                      </span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="5"
                      value={Math.round((p.error_rate ?? 0) * 100)}
                      onChange={(e) =>
                        handleErrorRateChange(p, Number(e.target.value))
                      }
                      className="w-full accent-rose-500 h-1.5 bg-gray-700 rounded-lg cursor-pointer"
                    />
                  </div>

                  {/* Fail Mid Stream */}
                  <label className="flex items-center gap-2 pt-1 text-xs text-gray-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={p.fail_after_tokens !== null && p.fail_after_tokens > 0}
                      onChange={(e) => handleFailMidStream(p, e.target.checked)}
                      className="rounded bg-gray-800 border-gray-700 text-rose-600 focus:ring-0"
                    />
                    <span>Simulate mid-stream failure after 3 tokens</span>
                  </label>
                </div>
              </div>

              {/* Action Button */}
              <button
                onClick={() => handleToggleDown(p)}
                disabled={isLoading}
                className={`w-full py-2.5 px-3 rounded-xl text-xs font-bold flex items-center justify-center gap-1.5 transition shadow-sm ${
                  p.is_down
                    ? "bg-emerald-600 hover:bg-emerald-500 text-white"
                    : "bg-rose-600 hover:bg-rose-500 text-white"
                }`}
              >
                <Power className="w-3.5 h-3.5" />
                {p.is_down ? "Restore Provider" : "Kill Provider"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

