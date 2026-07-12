"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { LoopieEvent, subscribeLoopieEvents } from "./loopie-events";

const REQUEST_TIMEOUT_MS = 12_000;

function eventMatchesPath(path: string, event: LoopieEvent): boolean {
  const resource = path.split("?", 1)[0];
  if (resource.startsWith("runs/")) {
    return String(event.data.run_id ?? "") === resource.slice("runs/".length);
  }
  if (resource === "runs") return event.type.startsWith("run.");
  if (resource === "tickets") return event.type === "ticket.ingested";
  if (resource === "corrections") return event.type.startsWith("correction.");
  return true;
}

export function useResource<T>(path: string, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const inFlight = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`/api/loopie/v1/${path}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string; error?: string };
        throw new Error(payload.detail ?? payload.error ?? `request failed (${response.status})`);
      }
      setData(await response.json() as T);
      setError(null);
    } catch (cause) {
      setError(
        controller.signal.aborted
          ? "Loopie API did not respond within 12 seconds. Check the local API process."
          : cause instanceof Error ? cause.message : "request failed",
      );
    } finally {
      window.clearTimeout(timeout);
      if (inFlight.current === controller) inFlight.current = null;
      setLoading(false);
    }
  }, [path]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => subscribeLoopieEvents(event => {
    if (eventMatchesPath(path, event)) void refresh();
  }), [path, refresh]);
  useEffect(() => () => inFlight.current?.abort(), []);
  return { data, error, loading, refresh };
}
