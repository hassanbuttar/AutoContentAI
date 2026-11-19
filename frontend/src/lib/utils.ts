import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtDate(iso?: string | null, opts: Intl.DateTimeFormatOptions = {}) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", ...opts });
}

export function fmtRelative(iso?: string | null) {
  if (!iso) return "—";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function fmtBytes(n?: number | null) {
  if (!n) return "—";
  if (n > 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n > 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  return `${(n / 1e3).toFixed(0)} KB`;
}

export function fmtNum(n?: number | null) {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat().format(n);
}

export function fmtUsd(n?: number | null) {
  if (n === null || n === undefined) return "—";
  return `$${n.toFixed(n < 1 ? 4 : 2)}`;
}

export const PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube Shorts",
  instagram: "Instagram Reels",
  tiktok: "TikTok",
  export: "Manual export",
};

export const PLATFORM_COLOR: Record<string, string> = {
  youtube: "bg-red-500",
  instagram: "bg-pink-500",
  tiktok: "bg-slate-900 dark:bg-slate-200",
  export: "bg-slate-400",
};

export const CONTENT_TYPES = [
  "feature_announcement",
  "walkthrough",
  "product_demo",
  "problem_solution",
  "tips_tutorial",
  "benefits_use_cases",
  "release_highlights",
  "educational",
  "productivity_tip",
  "comparison",
  "evergreen",
];

export const label = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
