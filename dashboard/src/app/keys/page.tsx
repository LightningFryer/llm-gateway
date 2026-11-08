"use client";

import React, { useEffect, useState } from "react";
import {
  Check,
  Copy,
  KeyRound,
  Plus,
  RefreshCw,
  Shield,
  ShieldAlert,
  Sliders,
  Trash2,
  X,
} from "lucide-react";
import {
  createKey,
  fetchKeys,
  rotateKey,
  toggleKeyStatus,
  ApiKey,
} from "@/lib/api";

export default function KeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [newKeyRevealed, setNewKeyRevealed] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Form fields
  const [name, setName] = useState("");
  const [rpm, setRpm] = useState(60);
  const [tpm, setTpm] = useState(100000);
  const [budget, setBudget] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  const loadKeys = async () => {
    try {
      const data = await fetchKeys();
      setKeys(data);
    } catch (err) {
      console.warn("Failed to fetch keys", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadKeys();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setSubmitting(true);
    try {
      const budgetNum = budget ? parseFloat(budget) : null;
      const res = await createKey({
        name,
        rate_limit_rpm: rpm,
        rate_limit_tpm: tpm,
        monthly_budget_usd: budgetNum,
      });
      setNewKeyRevealed(res.raw_key);
      await loadKeys();
      setName("");
      setBudget("");
    } catch (err) {
      console.error("Failed to create key", err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRotate = async (k: ApiKey) => {
    if (!confirm(`Are you sure you want to rotate "${k.name}"? The existing secret will stop working immediately.`)) {
      return;
    }
    try {
      const res = await rotateKey(k.id);
      setNewKeyRevealed(res.raw_key);
      await loadKeys();
    } catch (err) {
      console.error("Failed to rotate key", err);
    }
  };

  const handleToggleActive = async (k: ApiKey) => {
    try {
      await toggleKeyStatus(k.id, !k.is_active);
      await loadKeys();
    } catch (err) {
      console.error("Failed to toggle key status", err);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <KeyRound className="w-5 h-5 text-blue-400" /> Gateway API Keys
          </h1>
          <p className="text-xs text-gray-400">
            Create, rotate, rate limit, and enforce monthly budgets on client API keys.
          </p>
        </div>

        <button
          onClick={() => {
            setNewKeyRevealed(null);
            setCreateModalOpen(true);
          }}
          className="bg-blue-600 hover:bg-blue-500 text-white font-bold px-4 py-2 rounded-xl text-xs flex items-center gap-1.5 transition shadow-lg shadow-blue-600/20"
        >
          <Plus className="w-4 h-4" /> Create API Key
        </button>
      </div>

      {/* Keys Table */}
      <div className="p-4 rounded-xl bg-[#111827] border border-border">
        {loading ? (
          <div className="py-12 text-center text-gray-500 text-xs">
            Loading keys...
          </div>
        ) : keys.length === 0 ? (
          <div className="py-12 text-center text-gray-500 text-xs">
            No API keys configured yet. Click &quot;Create API Key&quot; above to generate one.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800 pb-2">
                  <th className="py-2.5 px-3">Name</th>
                  <th className="py-2.5 px-3">Prefix</th>
                  <th className="py-2.5 px-3">RPM Limit</th>
                  <th className="py-2.5 px-3">TPM Limit</th>
                  <th className="py-2.5 px-3">Monthly Budget</th>
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {keys.map((k) => (
                  <tr key={k.id} className="hover:bg-gray-800/40 transition">
                    <td className="py-3 px-3 font-semibold text-gray-200">
                      {k.name}
                    </td>
                    <td className="py-3 px-3">
                      <span className="bg-gray-900 border border-gray-800 text-gray-400 px-2 py-0.5 rounded text-[11px]">
                        {k.key_prefix}...
                      </span>
                    </td>
                    <td className="py-3 px-3 text-gray-300">
                      {k.rate_limit_rpm} req/min
                    </td>
                    <td className="py-3 px-3 text-gray-300">
                      {k.rate_limit_tpm.toLocaleString()} tok/min
                    </td>
                    <td className="py-3 px-3 text-amber-400">
                      {k.monthly_budget_usd !== null
                        ? `$${k.monthly_budget_usd.toFixed(2)}`
                        : "Unlimited"}
                    </td>
                    <td className="py-3 px-3">
                      <button
                        onClick={() => handleToggleActive(k)}
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-full border transition ${
                          k.is_active
                            ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20"
                            : "bg-gray-800 text-gray-400 border-gray-700 hover:bg-gray-700"
                        }`}
                      >
                        {k.is_active ? "Active" : "Disabled"}
                      </button>
                    </td>
                    <td className="py-3 px-3 text-right">
                      <button
                        onClick={() => handleRotate(k)}
                        title="Rotate key secret"
                        className="text-gray-400 hover:text-blue-400 p-1.5 rounded transition inline-flex items-center gap-1 text-[11px]"
                      >
                        <RefreshCw className="w-3.5 h-3.5" /> Rotate
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create / Reveal Modal */}
      {createModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-md bg-[#0d131f] border border-border rounded-2xl shadow-2xl p-6">
            <div className="flex items-center justify-between pb-3 border-b border-gray-800 mb-4">
              <h2 className="text-sm font-bold text-gray-100 flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-blue-400" />
                {newKeyRevealed ? "Key Generated Successfully" : "Create New API Key"}
              </h2>
              <button
                onClick={() => setCreateModalOpen(false)}
                className="text-gray-400 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {newKeyRevealed ? (
              <div className="space-y-4">
                <div className="p-3.5 rounded-xl bg-amber-950/20 border border-amber-500/30 text-xs text-amber-300">
                  <div className="font-bold flex items-center gap-1.5 text-amber-200 mb-1">
                    <ShieldAlert className="w-4 h-4 text-amber-400" /> Save your secret key
                  </div>
                  Please copy your raw API key now. For security, it is stored as a SHA-256 hash in SQLite and will not be displayed again.
                </div>

                <div className="p-3 rounded-xl bg-black/60 border border-gray-800 flex items-center justify-between">
                  <span className="font-mono text-xs text-emerald-400 select-all break-all">
                    {newKeyRevealed}
                  </span>
                  <button
                    onClick={() => copyToClipboard(newKeyRevealed)}
                    className="ml-3 p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 transition"
                  >
                    {copied ? (
                      <Check className="w-4 h-4 text-emerald-400" />
                    ) : (
                      <Copy className="w-4 h-4" />
                    )}
                  </button>
                </div>

                <button
                  onClick={() => setCreateModalOpen(false)}
                  className="w-full py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold transition"
                >
                  Done
                </button>
              </div>
            ) : (
              <form onSubmit={handleCreate} className="space-y-4 text-xs">
                <div>
                  <label className="block text-gray-300 mb-1">Key Name / Description</label>
                  <input
                    type="text"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. mobile-app-production"
                    className="w-full bg-[#111827] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-blue-500 font-mono"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-gray-300 mb-1">RPM Limit</label>
                    <input
                      type="number"
                      min="1"
                      value={rpm}
                      onChange={(e) => setRpm(parseInt(e.target.value) || 60)}
                      className="w-full bg-[#111827] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-blue-500 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-gray-300 mb-1">TPM Limit</label>
                    <input
                      type="number"
                      min="100"
                      step="1000"
                      value={tpm}
                      onChange={(e) => setTpm(parseInt(e.target.value) || 100000)}
                      className="w-full bg-[#111827] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-blue-500 font-mono"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-gray-300 mb-1">
                    Monthly Budget Limit ($ USD, optional)
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="e.g. 50.00 (leave empty for unlimited)"
                    value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                    className="w-full bg-[#111827] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-blue-500 font-mono"
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setCreateModalOpen(false)}
                    className="px-4 py-2 rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 font-medium"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-bold transition"
                  >
                    {submitting ? "Generating..." : "Generate Key"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

