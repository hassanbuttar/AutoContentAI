import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useState, useCallback, useEffect } from "react";
import { api } from "./api";

export function useApi<T = any>(key: QueryKey, url: string | null, opts: { refetchInterval?: number | false; enabled?: boolean } = {}) {
  return useQuery<T>({
    queryKey: key,
    queryFn: () => api.get<T>(url as string),
    enabled: url !== null && opts.enabled !== false,
    refetchInterval: opts.refetchInterval,
  });
}

/** Mutation that invalidates the given query-key prefixes and reports via toast. */
export function useAction<TArgs = void, TResult = any>(fn: (args: TArgs) => Promise<TResult>, invalidate: QueryKey[] = [], success?: string) {
  const qc = useQueryClient();
  const { toast } = useToast();
  return useMutation<TResult, Error, TArgs>({
    mutationFn: fn,
    onSuccess: () => {
      invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k }));
      if (success) toast(success, "success");
    },
    onError: (e) => toast(e.message, "error"),
  });
}

// --- tiny global toast store (no extra dependency)
type Toast = { id: number; message: string; tone: "success" | "error" | "info" };
let toasts: Toast[] = [];
const listeners = new Set<(t: Toast[]) => void>();
let nextId = 1;
export function pushToast(message: string, tone: Toast["tone"] = "info") {
  const t = { id: nextId++, message, tone };
  toasts = [...toasts, t];
  listeners.forEach((l) => l(toasts));
  setTimeout(() => {
    toasts = toasts.filter((x) => x.id !== t.id);
    listeners.forEach((l) => l(toasts));
  }, tone === "error" ? 7000 : 3500);
}
export function useToast() {
  return { toast: useCallback((m: string, tone: Toast["tone"] = "info") => pushToast(m, tone), []) };
}
export function useToasts() {
  const [list, setList] = useState<Toast[]>(toasts);
  useEffect(() => {
    listeners.add(setList);
    return () => {
      listeners.delete(setList);
    };
  }, []);
  return list;
}
