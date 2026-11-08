"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Flame,
  KeyRound,
  Layers,
  MessageSquare,
  ShieldAlert,
} from "lucide-react";
import { fetchHealth } from "@/lib/api";

interface NavbarProps {
  onOpenChaos: () => void;
  chaosCount?: number;
}

export function Navbar({ onOpenChaos, chaosCount = 0 }: NavbarProps) {
  const pathname = usePathname();
  const [isOnline, setIsOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let mounted = true;
    const check = async () => {
      try {
        await fetchHealth();
        if (mounted) setIsOnline(true);
      } catch {
        if (mounted) setIsOnline(false);
      }
    };
    check();
    const interval = setInterval(check, 5000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const navLinks = [
    { href: "/", label: "Live Overview", icon: Activity },
    { href: "/playground", label: "Playground", icon: MessageSquare },
    { href: "/analytics", label: "Analytics", icon: BarChart3 },
    { href: "/keys", label: "API Keys", icon: KeyRound },
    { href: "/config", label: "Config", icon: Layers },
  ];

  return (
    <header className="sticky top-0 z-40 bg-[#090d16]/85 backdrop-blur-md border-b border-border/80 px-4 lg:px-8 py-3 flex items-center justify-between">
      {/* Brand & Status */}
      <div className="flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2.5 group">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center font-black text-white text-base shadow-lg shadow-blue-500/20 group-hover:scale-105 transition">
            R
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-base tracking-tight text-white group-hover:text-blue-400 transition">
                Relay
              </span>
              <span className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
                v1.0
              </span>
            </div>
            <p className="text-[10px] text-gray-400">Resilient LLM Gateway</p>
          </div>
        </Link>

        {/* Gateway Connection Pill */}
        <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-gray-900 border border-gray-800 text-xs">
          <div
            className={`w-2 h-2 rounded-full ${
              isOnline === true
                ? "bg-emerald-400 shadow-sm shadow-emerald-400 animate-pulse"
                : isOnline === false
                ? "bg-rose-500 shadow-sm shadow-rose-500"
                : "bg-gray-500"
            }`}
          />
          <span className="text-gray-300 font-mono text-[11px]">
            {isOnline === true
              ? "Gateway Online"
              : isOnline === false
              ? "Gateway Offline"
              : "Checking..."}
          </span>
        </div>
      </div>

      {/* Navigation Links */}
      <nav className="hidden md:flex items-center gap-1 bg-[#111827]/60 p-1 rounded-xl border border-gray-800">
        {navLinks.map((link) => {
          const Icon = link.icon;
          const isActive = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-medium transition ${
                isActive
                  ? "bg-blue-600 text-white shadow-sm"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/60"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {link.label}
            </Link>
          );
        })}
      </nav>

      {/* Right Controls: Chaos Button */}
      <div className="flex items-center gap-3">
        <button
          onClick={onOpenChaos}
          className={`flex items-center gap-2 px-3.5 py-1.5 rounded-xl text-xs font-bold transition shadow-lg ${
            chaosCount > 0
              ? "bg-gradient-to-r from-rose-600 to-amber-600 hover:from-rose-500 hover:to-amber-500 text-white animate-pulse"
              : "bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border border-rose-500/30"
          }`}
        >
          <Flame className="w-4 h-4 text-rose-400" />
          <span>Chaos Panel</span>
          {chaosCount > 0 && (
            <span className="bg-black/40 text-rose-200 text-[10px] font-mono px-1.5 py-0.2 rounded-full">
              {chaosCount} ACTIVE
            </span>
          )}
        </button>
      </div>
    </header>
  );
}
