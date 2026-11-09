"use client";

import React, { useState } from "react";
import {
  AlertTriangle,
  Flame,
  Power,
  RotateCcw,
  Sliders,
  X,
  Zap,
} from "lucide-react";
import { ProviderInfo, resetChaos, updateChaos } from "@/lib/api";

interface ChaosDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  providers: ProviderInfo[];
  onRefresh: () => void;
}

export function ChaosDrawer({
  isOpen,
  onClose,
  providers,
  onRefresh,
}: ChaosDrawerProps) {
  const [loadingMap, setLoadingMap] = useState<Record<string, boolean>>({});
  const [globalResetting, setGlobalResetting] = useState(false);

  if (!isOpen) return null;

  const handleToggleDown = async (p: ProviderInfo) => {
    const nextState = !p.is_down;
    setLoadingMap((prev) => ({ ...prev, [p.name]: true }));
    try {
      await updateChaos(p.name, { is_down: nextState });
      await onRefresh();
    } catch (err) {
      console.error("Failed to toggle provider status", err);
    } finally {
      setLoadingMap((prev) => ({ ...prev, [p.name]: false }));
    }
  };

  const handleLatencyChange = async (p: ProviderInfo, latencyMs: number) => {
    try {
      await updateChaos(p.name, { base_latency_ms: latencyMs });
      await onRefresh();
    } catch (err) {
      console.error("Failed to update latency", err);
    }
  };

  const handleErrorRateChange = async (p: ProviderInfo, ratePercent: number) => {
    try {
      await updateChaos(p.name, { error_rate: ratePercent / 100.0 });
      await onRefresh();
    } catch (err) {
      console.error("Failed to update error rate", err);
    }
  };

  const handleFailMidStream = async (p: ProviderInfo, checked: boolean) => {
    try {
      await updateChaos(p.name, { fail_after_tokens: checked ? 3 : null });
      await onRefresh();
    } catch (err) {
      console.error("Failed to update fail after tokens", err);
    }
  };

  const handleResetAll = async () => {
    setGlobalResetting(true);
    try {
      await resetChaos();
      await onRefresh();
    } catch (err) {
      console.error("Failed to reset chaos", err);
    } finally {
      setGlobalResetting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm transition-opacity">
      <div className="w-full max-w-lg bg-[#0d131f] border-l border-border h-full flex flex-col shadow-2xl p-6 overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-border">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400">
              <Flame className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-100 flex items-center gap-2">
                Chaos Injection Panel
              </h2>
              <p className="text-xs text-gray-400">
                Simulate upstream outages, latency, & stream errors in real-time
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Presentation Demo Banner */}
        <div className="my-4 p-3.5 rounded-xl bg-blue-500/10 border border-blue-500/20 text-xs text-blue-300 leading-relaxed">
          <div className="font-semibold text-blue-200 mb-1 flex items-center gap-1.5">
            <Zap className="w-3.5 h-3.5 text-blue-400" /> Demo Moment Instructions:
          </div>
          Click <span className="font-mono text-rose-300 bg-rose-950/60 px-1 py-0.5 rounded">Kill</span> on Provider A. Watch its circuit trip <span className="font-mono text-rose-300">OPEN</span> on the dashboard while traffic seamlessly fails over to Provider B with 0 client errors. Click <span className="font-mono text-emerald-300 bg-emerald-950/60 px-1 py-0.5 rounded">Restore</span> to recover.
        </div>

        {/* Global Reset */}
        <div className="flex justify-end mb-4">
          <button
            onClick={handleResetAll}
            disabled={globalResetting}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 transition"
          >
            <RotateCcw className={`w-3.5 h-3.5 ${globalResetting ? "animate-spin" : ""}`} />
            Reset All Knobs to Healthy
          </button>
        </div>

        {/* Provider Cards */}
        <div className="space-y-4 flex-1">
          {providers.map((p) => {
            const isLoading = !!loadingMap[p.name];
            return (
              <div
                key={p.name}
                className={`p-4 rounded-xl border transition ${
                  p.is_down
                    ? "bg-rose-950/20 border-rose-500/40"
                    : p.circuit_state === "OPEN"
                    ? "bg-amber-950/20 border-amber-500/40"
                    : "bg-[#111827] border-border"
                }`}
              >
                {/* Header Row */}
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <span className="font-mono font-bold text-gray-200 text-sm">
                      {p.name}
                    </span>
                    <span className="ml-2 text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full border bg-gray-800 text-gray-300 border-gray-700">
                      {p.type || "mock"}
                    </span>
                  </div>

                  <button
                    onClick={() => handleToggleDown(p)}
                    disabled={isLoading}
                    className={`flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg transition shadow-sm ${
                      p.is_down
                        ? "bg-emerald-600 hover:bg-emerald-500 text-white"
                        : "bg-rose-600 hover:bg-rose-500 text-white"
                    }`}
                  >
                    <Power className="w-3.5 h-3.5" />
                    {p.is_down ? "Restore Provider" : "Kill Provider"}
                  </button>
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
                    Outage Mode:{" "}
                    <span
                      className={`font-semibold ${
                        p.is_down ? "text-rose-400" : "text-gray-300"
                      }`}
                    >
                      {p.is_down ? "FORCED DOWN" : "Healthy"}
                    </span>
                  </div>
                </div>

                {/* Knobs */}
                <div className="space-y-3">
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
            );
          })}
        </div>
      </div>
    </div>
  );
}

