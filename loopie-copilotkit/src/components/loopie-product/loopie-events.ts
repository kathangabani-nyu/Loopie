"use client";

export type LoopieEvent = {
  type: string;
  data: Record<string, unknown>;
};

const listeners = new Set<(event: LoopieEvent) => void>();
let source: EventSource | null = null;

const EVENT_TYPES = [
  "run.queued",
  "run.running",
  "run.finished",
  "run.failed",
  "run.retrying",
  "correction.approved",
  "correction.proposed",
  "correction.rejected",
  "ticket.ingested",
] as const;

function notify(type: string, message: MessageEvent<string>) {
  let data: Record<string, unknown> = {};
  try {
    data = JSON.parse(message.data) as Record<string, unknown>;
  } catch {
    // A malformed optional event must not break the resource fetch path.
  }
  for (const listener of listeners) listener({ type, data });
}

function ensureSource() {
  if (source) return;
  source = new EventSource("/api/loopie/events");
  source.onmessage = message => notify("message", message);
  for (const eventType of EVENT_TYPES) {
    source.addEventListener(eventType, message => notify(eventType, message as MessageEvent<string>));
  }
}

export function subscribeLoopieEvents(listener: (event: LoopieEvent) => void): () => void {
  listeners.add(listener);
  ensureSource();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && source) {
      source.close();
      source = null;
    }
  };
}
