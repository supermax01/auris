import type { ReviewState } from "./review";

// The engine hands the window a URL ending in #token=…; keep the token in
// memory and session storage only, and take it out of the address bar.
const STORAGE_KEY = "auris-session";

function readToken(): string {
  const match = /(?:^|[#&])token=([A-Za-z0-9_-]+)/.exec(window.location.hash);
  if (match?.[1]) {
    try {
      sessionStorage.setItem(STORAGE_KEY, match[1]);
    } catch {
      // Storage can be unavailable; the in-memory token still works.
    }
    history.replaceState(null, "", window.location.pathname);
    return match[1];
  }
  try {
    return sessionStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

const token = readToken();

export type VisitState = "scheduled" | "listening" | "writing" | "failed" | "ready" | "exported";

export interface Visit {
  id: string;
  patient: string;
  reason: string;
  day: string;
  time: string;
  state: VisitState;
  fictional: boolean;
  failure: string | null;
  /** Every record is decided, so the visit can be exported (from the visit list only). */
  review_complete?: boolean;
}

export interface Stage {
  key: string;
  label: string;
  state: "done" | "busy" | "waiting";
}

export interface Job {
  state: "queued" | "running" | "done" | "failed";
  error: string | null;
  stages: Stage[];
  attempt: string;
  position: number | null;
}

export interface Session {
  clinician: string | null;
  today: string;
  demo: boolean;
}

export interface Day {
  day: string;
  visits: Visit[];
  counts: Record<VisitState, number>;
}

export interface Outputs {
  "note.md": string;
  "bundle.json": string;
  "validation.json": string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown, raw?: string): Promise<T> {
  const payload = raw ?? (body === undefined ? undefined : JSON.stringify(body));
  const response = await fetch(path, {
    method,
    headers: {
      "X-Auris-Token": token,
      ...(payload === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: payload,
    cache: "no-store",
    credentials: "omit",
  });
  const data = (await response.json().catch(() => ({}))) as { error?: string };
  if (!response.ok) {
    throw new ApiError(data.error ?? `request failed (${response.status})`, response.status);
  }
  return data as T;
}

const visitPath = (id: string, rest = "") => `/api/visits/${encodeURIComponent(id)}${rest}`;

export const api = {
  hasToken: () => token.length > 0,
  session: () => call<Session>("GET", "/api/session"),
  setClinician: (name: string) => call<{ clinician: string }>("POST", "/api/clinician", { name }),
  visits: (day?: string) =>
    call<Day>("GET", day ? `/api/visits?day=${encodeURIComponent(day)}` : "/api/visits"),
  addVisit: (visit: { patient: string; reason: string; time: string }) =>
    call<{ visit: Visit }>("POST", "/api/visits", visit),
  visit: (id: string) => call<{ visit: Visit; job: Job | null }>("GET", visitPath(id)),
  sendTranscript: (id: string, json: string) =>
    call<{ visit: Visit; job: Job }>("POST", visitPath(id, "/transcript"), undefined, json),
  demoTranscript: (id: string) =>
    call<{ visit: Visit; job: Job }>("POST", visitPath(id, "/demo-transcript")),
  retry: (id: string) => call<{ visit: Visit; job: Job }>("POST", visitPath(id, "/retry")),
  review: (id: string) => call<ReviewState>("GET", visitPath(id, "/review/state")),
  act: (id: string, body: { target_id: string; target_sha256: string; action: string; reason?: string }) =>
    call<unknown>("POST", visitPath(id, "/review/action"), body),
  correct: (id: string, body: { target_id: string; target_sha256: string; fields: Record<string, string | null> }) =>
    call<unknown>("POST", visitPath(id, "/review/fact"), body),
  speaker: (id: string, body: { target_id: string; target_sha256: string; speaker_role: string }) =>
    call<unknown>("POST", visitPath(id, "/review/speaker"), body),
  undoEdit: (id: string, body: { target_id: string; target_sha256: string }) =>
    call<unknown>("POST", visitPath(id, "/review/undo-edit"), body),
  outputs: (id: string) => call<Outputs>("GET", visitPath(id, "/review/outputs")),
  exportVisit: (id: string) => call<{ visit: Visit; outputs: Outputs }>("POST", visitPath(id, "/export")),
  reopen: (id: string) => call<{ visit: Visit }>("POST", visitPath(id, "/reopen")),
};
