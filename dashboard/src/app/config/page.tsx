"use client";

import React, { useEffect, useState } from "react";
import {
  Check,
  Cpu,
  DollarSign,
  Layers,
  RefreshCw,
  Route,
  Shield,
  Zap,
} from "lucide-react";
import { fetchConfig, reloadConfig, RouterConfig } from "@/lib/api";

export default function ConfigPage() {
  const [config, setConfig] = useState<RouterConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const [successMsg, setSuccessMsg] = useState(false);

  const loadConfig = async () => {
    try {
      const data = await fetchConfig();
      setConfig(data);
    } catch (err) {
      console.warn("Failed to load config", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleHotReload = async () => {
    setReloading(true);
    setSuccessMsg(false);
    try {
      await reloadConfig();
      await loadConfig();
      setSuccessMsg(true);
      setTimeout(() => setSuccessMsg(false), 3000);
    } catch (err) {
      console.error("Failed to hot reload config", err);
    } finally {
      setReloading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Layers className="w-5 h-5 text-blue-400" /> Model Routing Configuration
          </h1>
          <p className="text-xs text-gray-400">
            View model aliases, failover priorities, cost schedules, and hot-reload changes without server restart.
          </p>
        </div>

        <button
          onClick={handleHotReload}
          disabled={reloading}
          className="bg-blue-600 hover:bg-blue-500 text-white font-bold px-4 py-2 rounded-xl text-xs flex items-center gap-2 transition shadow-lg shadow-blue-600/20"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${reloading ? "animate-spin" : ""}`} />
          <span>Hot Reload Config</span>
        </button>
      </div>

      {successMsg && (
        <div className="p-3 rounded-xl bg-emerald-950/30 border border-emerald-500/30 text-xs text-emerald-300 flex items-center gap-2">
          <Check className="w-4 h-4 text-emerald-400" />
          <span>Configuration hot-reloaded successfully from <code>config/models.yaml</code>!</span>
        </div>
      )}

      {/* Aliases Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {loading || !config ? (
          <div className="col-span-2 py-12 text-center text-gray-500 text-xs">
            Loading routing configuration...
          </div>
        ) : (
          Object.entries(config.aliases).map(([name, aliasCfg]) => (
            <div
              key={name}
              className="p-5 rounded-2xl bg-[#111827] border border-border flex flex-col justify-between"
            >
              <div>
                {/* Header */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-base font-bold font-mono text-white">
                      {name}
                    </span>
                    <span className="text-[10px] font-mono font-bold uppercase px-2 py-0.5 rounded-full border bg-blue-500/10 text-blue-400 border-blue-500/20">
                      {aliasCfg.strategy}
                    </span>
                  </div>
                  <span className="text-xs text-gray-500 font-mono">
                    {aliasCfg.targets.length} targets
                  </span>
                </div>

                <p className="text-xs text-gray-400 mb-4">{aliasCfg.description}</p>

                {/* Target Chain */}
                <div className="space-y-2">
                  <span className="text-[10px] font-bold uppercase text-gray-500 tracking-wider">
                    Target Execution Sequence
                  </span>
                  {aliasCfg.targets.map((t, idx) => (
                    <div
                      key={idx}
                      className="p-3 rounded-xl bg-[#0d131f] border border-gray-800 flex items-center justify-between text-xs font-mono"
                    >
                      <div className="flex items-center gap-2.5">
                        <span className="w-5 h-5 rounded-full bg-gray-800 flex items-center justify-center text-[10px] text-gray-400 font-bold">
                          {idx + 1}
                        </span>
                        <div>
                          <div className="font-bold text-gray-200">
                            {t.provider}
                          </div>
                          <div className="text-[10px] text-gray-500">
                            {t.model}
                          </div>
                        </div>
                      </div>

                      <div className="text-right text-[11px]">
                        <div className="text-amber-400">
                          ${t.cost_input_per_million.toFixed(2)} / ${t.cost_output_per_million.toFixed(2)}
                        </div>
                        <div className="text-[9px] text-gray-500">per 1M tokens</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Strategy Footer explanation */}
              <div className="mt-4 pt-3 border-t border-gray-800 text-[11px] text-gray-500 flex items-center gap-1.5">
                <Route className="w-3.5 h-3.5 text-blue-400" />
                <span>
                  {aliasCfg.strategy === "priority" && "Dispatches to Target 1; falls over to Target 2 if tripped."}
                  {aliasCfg.strategy === "lowest_latency" && "Dispatches to the target with the lowest EWMA latency."}
                  {aliasCfg.strategy === "lowest_cost" && "Dispatches to the cheapest healthy target in the chain."}
                  {aliasCfg.strategy === "round_robin" && "Balances traffic across targets using weighted intervals."}
                </span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

