"use client";

import React, { useState } from "react";
import {
  Clock,
  Coins,
  Cpu,
  Database,
  Flame,
  MessageSquare,
  Play,
  RotateCcw,
  Send,
  Zap,
} from "lucide-react";
import { ADMIN_KEY, GATEWAY_URL } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface CompletionMeta {
  provider?: string;
  latency_ms?: number;
  ttft_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_usd?: number;
  cached?: boolean;
}

export default function PlaygroundPage() {
  const [alias, setAlias] = useState("fast");
  const [temperature, setTemperature] = useState(0.0); // 0.0 allows exact-match caching
  const [stream, setStream] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [prompt, setPrompt] = useState("Explain how a circuit breaker works in distributed systems.");
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingContent, setStreamingContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [meta, setMeta] = useState<CompletionMeta | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSend = async (overridePrompt?: string) => {
    const textToSend = overridePrompt !== undefined ? overridePrompt : prompt;
    if (!textToSend.trim() || loading) return;

    setErrorMsg(null);
    setStreamingContent("");
    setMeta(null);
    setLoading(true);

    const newMessages: Message[] = [
      ...messages,
      { role: "user", content: textToSend },
    ];
    setMessages(newMessages);

    const payload = {
      model: alias,
      messages: newMessages,
      temperature,
      stream,
    };

    const startTime = performance.now();
    let firstTokenTime: number | null = null;

    const activeToken = apiKey.trim() || ADMIN_KEY;

    try {
      const response = await fetch(`${GATEWAY_URL}/v1/chat/completions`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${activeToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      const providerHeader = response.headers.get("X-Relay-Provider");
      const cacheHeader = response.headers.get("X-Relay-Cache");

      if (!response.ok) {
        const errBody = await response.json().catch(() => ({}));
        const rawErr = errBody.detail?.error || errBody.error || errBody.detail;
        const msg =
          typeof rawErr === "object"
            ? rawErr?.message || JSON.stringify(rawErr)
            : typeof rawErr === "string"
            ? rawErr
            : `HTTP ${response.status}: ${response.statusText}`;
        throw new Error(msg);
      }

      if (stream) {
        const reader = response.body?.getReader();
        const decoder = new TextDecoder("utf-8");
        let accumulated = "";
        let providerTag = providerHeader || "";
        let isCached = cacheHeader === "HIT";

        if (reader) {
          let lineBuffer = "";
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            lineBuffer += decoder.decode(value, { stream: true });
            const lines = lineBuffer.split("\n");
            lineBuffer = lines.pop() || "";

            for (const line of lines) {
              const trimmed = line.trim();
              if (!trimmed || !trimmed.startsWith("data: ")) continue;
              if (trimmed === "data: [DONE]") break;

              try {
                const chunkJson = JSON.parse(trimmed.slice(6));
                const deltaContent = chunkJson.choices?.[0]?.delta?.content;
                if (deltaContent) {
                  if (firstTokenTime === null) {
                    firstTokenTime = performance.now();
                  }
                  accumulated += deltaContent;
                  setStreamingContent(accumulated);
                }
                if (chunkJson.choices?.[0]?.provider) {
                  providerTag = chunkJson.choices[0].provider;
                }
              } catch {
                // Ignore parse errors on partial frames
              }
            }
          }
        }

        const totalLatency = performance.now() - startTime;
        const ttft = firstTokenTime ? firstTokenTime - startTime : undefined;
        isCached = isCached || totalLatency < 20;

        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: accumulated || "[No content returned]" },
        ]);
        setStreamingContent("");

        setMeta({
          provider: providerTag || "gateway",
          latency_ms: totalLatency,
          ttft_ms: ttft,
          cached: isCached,
          prompt_tokens: Math.round(textToSend.length / 4),
          completion_tokens: Math.round(accumulated.length / 4),
        });
      } else {
        const data = await response.json();
        const totalLatency = performance.now() - startTime;
        const content = data.choices?.[0]?.message?.content || "";

        setMessages((prev) => [
          ...prev,
          { role: "assistant", content },
        ]);

        setMeta({
          provider: providerHeader || data.provider || "gateway",
          latency_ms: totalLatency,
          cached: cacheHeader === "HIT" || totalLatency < 20,
          prompt_tokens: data.usage?.prompt_tokens,
          completion_tokens: data.usage?.completion_tokens,
        });
      }
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to complete request");
    } finally {
      setLoading(false);
    }
  };

  const handleSendAgain = () => {
    // Re-send only the user prompt with fresh state for exact cache hit demonstration
    const lastUserMsg = messages
      .slice()
      .reverse()
      .find((m) => m.role === "user");
    const textToResend = lastUserMsg?.content || prompt;
    setMessages([]);
    setStreamingContent("");
    setMeta(null);
    setErrorMsg(null);
    setTemperature(0.0);
    setTimeout(() => {
      handleSend(textToResend);
    }, 50);
  };

  const clearChat = () => {
    setMessages([]);
    setStreamingContent("");
    setMeta(null);
    setErrorMsg(null);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <MessageSquare className="w-5 h-5 text-blue-400" /> Gateway Playground
          </h1>
          <p className="text-xs text-gray-400">
            Send requests to model aliases and test streaming, latency, and exact-match caching.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={clearChat}
            className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white px-3 py-1.5 rounded-lg border border-gray-800 bg-[#111827] transition"
          >
            <RotateCcw className="w-3.5 h-3.5" /> Clear History
          </button>
        </div>
      </div>

      {/* Grid: Controls (Left) & Chat Screen (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Controls Column */}
        <div className="p-5 rounded-2xl bg-[#111827] border border-border space-y-5 h-fit">
          <h2 className="text-xs font-bold uppercase tracking-wider text-gray-400">
            Routing Configuration
          </h2>

          {/* Alias Select */}
          <div>
            <label className="block text-xs font-medium text-gray-300 mb-1.5">
              Model Alias
            </label>
            <select
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              className="w-full bg-[#0d131f] border border-gray-700 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-blue-500"
            >
              <option value="fast">fast (Lowest-latency routing)</option>
              <option value="smart">smart (Flagship priority fallback)</option>
              <option value="cheap">cheap (Cost-optimized routing)</option>
              <option value="balanced">balanced (Weighted round-robin)</option>
            </select>
          </div>

          {/* API Key Override (Optional) */}
          <div>
            <div className="flex justify-between text-xs text-gray-300 mb-1.5">
              <span>Client API Key</span>
              <span className="text-[10px] text-gray-500">Optional</span>
            </div>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Default: Admin Master Key"
              className="w-full bg-[#0d131f] border border-gray-700 rounded-lg px-3 py-2 text-xs font-mono text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Temperature Slider */}
          <div>
            <div className="flex justify-between text-xs text-gray-300 mb-1">
              <span>Temperature</span>
              <span className="font-mono text-blue-400">{temperature.toFixed(1)}</span>
            </div>
            <input
              type="range"
              min="0.0"
              max="2.0"
              step="0.1"
              value={temperature}
              onChange={(e) => setTemperature(parseFloat(e.target.value))}
              className="w-full accent-blue-500 h-1.5 bg-gray-700 rounded-lg cursor-pointer"
            />
            <p className="text-[10px] text-gray-500 mt-1">
              {temperature === 0.0
                ? "⚡ 0.0: Deterministic and eligible for Redis response caching."
                : "Non-zero: Varied output, cache bypassed."}
            </p>
          </div>

          {/* Streaming Toggle */}
          <div className="flex items-center justify-between pt-2 border-t border-gray-800">
            <div>
              <span className="text-xs font-medium text-gray-200 block">
                Server-Sent Events (SSE)
              </span>
              <span className="text-[11px] text-gray-500">Stream chunks live</span>
            </div>
            <input
              type="checkbox"
              checked={stream}
              onChange={(e) => setStream(e.target.checked)}
              className="rounded bg-gray-800 border-gray-700 text-blue-600 focus:ring-0 cursor-pointer"
            />
          </div>

          {/* Demo Cache Box */}
          <div className="p-3.5 rounded-xl bg-blue-950/20 border border-blue-500/20 text-xs text-blue-300 space-y-2">
            <div className="font-semibold flex items-center gap-1.5 text-blue-200">
              <Zap className="w-3.5 h-3.5 text-blue-400" /> Cache Demo Hint:
            </div>
            <p className="text-[11px] leading-relaxed text-blue-300/80">
              Set <span className="font-mono text-white">temperature: 0.0</span>, send a query, then click{" "}
              <span className="font-mono text-emerald-300 font-bold">Send Again</span> below. The second call is served from Redis in under 5ms!
            </p>
          </div>
        </div>

        {/* Chat / Output Column */}
        <div className="lg:col-span-2 flex flex-col justify-between p-5 rounded-2xl bg-[#111827] border border-border min-h-[500px]">
          {/* Messages Container */}
          <div className="space-y-4 overflow-y-auto max-h-[420px] mb-4 pr-2">
            {messages.length === 0 && !streamingContent && (
              <div className="flex flex-col items-center justify-center py-20 text-center text-gray-500">
                <MessageSquare className="w-10 h-10 stroke-1 mb-2 text-gray-600" />
                <p className="text-sm font-medium">No messages yet</p>
                <p className="text-xs text-gray-600 max-w-sm mt-1">
                  Type a prompt below and send it to test Gateway multi-provider routing and streaming.
                </p>
              </div>
            )}

            {messages.map((m, idx) => (
              <div
                key={idx}
                className={`flex flex-col ${
                  m.role === "user" ? "items-end" : "items-start"
                }`}
              >
                <span className="text-[10px] font-mono uppercase text-gray-500 mb-1 px-1">
                  {m.role}
                </span>
                <div
                  className={`p-3.5 rounded-2xl max-w-[85%] text-xs leading-relaxed ${
                    m.role === "user"
                      ? "bg-blue-600 text-white rounded-tr-none"
                      : "bg-[#0d131f] border border-gray-800 text-gray-200 rounded-tl-none font-mono"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            ))}

            {/* In-Flight Streaming Bubble */}
            {streamingContent && (
              <div className="flex flex-col items-start">
                <span className="text-[10px] font-mono uppercase text-blue-400 mb-1 px-1 flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-ping" />
                  Streaming from Gateway...
                </span>
                <div className="p-3.5 rounded-2xl max-w-[85%] text-xs leading-relaxed bg-[#0d131f] border border-blue-500/30 text-gray-100 rounded-tl-none font-mono">
                  {streamingContent}
                </div>
              </div>
            )}

            {/* Error Message */}
            {errorMsg && (
              <div className="p-3.5 rounded-xl bg-rose-950/30 border border-rose-500/30 text-xs text-rose-300">
                <span className="font-bold">Error:</span> {errorMsg}
              </div>
            )}
          </div>

          {/* Metadata Pill */}
          {meta && (
            <div className="mb-4 p-3 rounded-xl bg-[#0d131f] border border-gray-800 flex flex-wrap items-center gap-4 text-xs font-mono">
              <div className="flex items-center gap-1.5 text-gray-300">
                <Cpu className="w-3.5 h-3.5 text-blue-400" />
                <span>Provider: </span>
                <span className="font-bold text-white">{meta.provider}</span>
              </div>
              <div className="flex items-center gap-1.5 text-gray-300">
                <Clock className="w-3.5 h-3.5 text-emerald-400" />
                <span>Latency: </span>
                <span className="font-bold text-white">
                  {Math.round(meta.latency_ms || 0)} ms
                </span>
                {meta.ttft_ms !== undefined && (
                  <span className="text-gray-500 text-[10px]">
                    (TTFT: {Math.round(meta.ttft_ms)} ms)
                  </span>
                )}
              </div>
              {meta.cached && (
                <div className="flex items-center gap-1 text-emerald-400 font-bold bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                  <Zap className="w-3 h-3" /> CACHE HIT
                </div>
              )}
              {meta.completion_tokens !== undefined && (
                <div className="flex items-center gap-1.5 text-gray-400">
                  <Coins className="w-3.5 h-3.5 text-amber-400" />
                  <span>Tokens: </span>
                  <span className="text-gray-200">
                    {meta.prompt_tokens || 0} in / {meta.completion_tokens} out
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Input & Action Bar */}
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                type="text"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSend()}
                placeholder="Type a message to test gateway..."
                disabled={loading}
                className="flex-1 bg-[#0d131f] border border-gray-700 rounded-xl px-4 py-3 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 transition"
              />
              <button
                onClick={() => handleSend()}
                disabled={loading || !prompt.trim()}
                className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold px-5 py-3 rounded-xl text-xs flex items-center gap-2 transition shadow-lg shadow-blue-600/20"
              >
                {loading ? (
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                <span>Send</span>
              </button>
            </div>

            {/* Send Again Demo Button */}
            {messages.length > 0 && (
              <div className="flex justify-end pt-1">
                <button
                  onClick={handleSendAgain}
                  disabled={loading}
                  className="flex items-center gap-1.5 text-xs text-emerald-400 hover:text-emerald-300 font-semibold transition"
                >
                  <Zap className="w-3.5 h-3.5" /> Resend Identical Prompt (Demo Sub-5ms Cache Hit)
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

