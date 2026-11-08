"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ChaosDrawer } from "./ChaosDrawer";
import { Navbar } from "./Navbar";
import { fetchProviders, ProviderInfo } from "@/lib/api";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [chaosOpen, setChaosOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);

  const loadProviders = useCallback(async () => {
    try {
      const data = await fetchProviders();
      setProviders(data);
    } catch (err) {
      console.warn("Could not fetch providers in AppShell", err);
    }
  }, []);

  useEffect(() => {
    loadProviders();
    const interval = setInterval(loadProviders, 3000);
    return () => clearInterval(interval);
  }, [loadProviders]);

  const activeChaosCount = providers.filter(
    (p) => p.is_down || p.error_rate > 0 || (p.fail_after_tokens !== null && p.fail_after_tokens > 0)
  ).length;

  return (
    <div className="min-h-screen flex flex-col bg-background text-gray-100 selection:bg-blue-600 selection:text-white">
      <Navbar onOpenChaos={() => setChaosOpen(true)} chaosCount={activeChaosCount} />
      <main className="flex-1 p-4 lg:p-8 max-w-7xl mx-auto w-full">{children}</main>
      <ChaosDrawer
        isOpen={chaosOpen}
        onClose={() => setChaosOpen(false)}
        providers={providers}
        onRefresh={loadProviders}
      />
    </div>
  );
}
