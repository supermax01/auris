// Review state from the engine (the existing review service) and the plain
// derivations the screens need. The server decides; this only presents.

export type LaneState = "PASS" | "FAIL" | "NOT_CHECKED";

export interface Lane {
  state: LaneState;
  reason: string | null;
  issues: Array<{ severity?: string; code?: string; details?: { text?: string } | null }>;
}

export interface Item {
  id: string;
  kind: string;
  export_state: string;
  fact: string;
  receipts: string;
  label: string;
  reasons: string[];
  in_ledger: boolean;
  section: string;
  reference: string;
  dismissed: boolean;
}

export interface ReviewRow {
  id: string;
  target_sha256: string;
  state: "approve" | "dismiss" | "unreviewed";
  receipt: { reviewer: string; role: string; timestamp: string; reason?: string } | null;
  stale_message: string | null;
  allowed_actions: string[];
  editable: boolean;
  /** The correction entry an undo would revert, if any. */
  undo_edit: string | null;
  check_status: "complete" | "rechecking" | "blocked" | string;
  check_message: string | null;
}

export interface Neighbor {
  id: string;
  text: string;
  role: string;
  receipt: string;
  cited: boolean;
}

export interface Evidence {
  quote: string;
  segment_id: string | null;
  neighbors: Neighbor[];
  alignment: string | null;
  failure: string | null;
  audio_start_seconds: number | null;
  receipt: string;
}

export interface Speaker {
  role: string;
  target_sha256: string;
  /** The correction entry an undo would revert, if any. */
  undo_edit: string | null;
}

export interface ReviewState {
  items: Item[];
  review: ReviewRow[];
  lanes: Record<string, Record<string, Lane>>;
  evidence: Record<string, Evidence[]>;
  assertions: Record<string, Record<string, unknown>>;
  edit_schema: Record<string, string[]>;
  speakers: Record<string, Speaker>;
  export: { blocking_ids: string[]; exported_ids: string[]; summary: string };
  ready: boolean;
  ready_message: string | null;
  busy: boolean;
  fictional: boolean;
  /** Specific doubts per item, decided by the engine (auris.app.attention). */
  attention: Record<string, string[]>;
  /** Routine facts about the record, shown quietly. */
  notes: Record<string, string[]>;
  reason_labels: Record<string, string>;
}

export type Status = "pending" | "approved" | "flagged" | "outdated" | "failed" | "dismissed" | "checking";

export function rowFor(state: ReviewState, id: string): ReviewRow | undefined {
  return state.review.find((row) => row.id === id);
}

export function statusOf(state: ReviewState, item: Item): Status {
  const row = rowFor(state, item.id);
  if (row?.state === "dismiss") return "dismissed";
  if (row?.check_status === "rechecking") return "checking";
  if (row?.state === "approve") return row.stale_message ? "outdated" : "approved";
  if (row?.stale_message) return "outdated";
  if (Object.values(state.lanes[item.id] ?? {}).some((lane) => lane.state === "FAIL")) return "failed";
  return doubtsOf(state, item).length > 0 ? "flagged" : "pending";
}

/** Why this fact needs a closer look; empty when nothing specific is in doubt. */
export function doubtsOf(state: ReviewState, item: Item): string[] {
  return (state.attention[item.id] ?? []).map(asSentence);
}

export function notesOf(state: ReviewState, item: Item): string[] {
  return (state.notes[item.id] ?? []).map(asSentence);
}

function asSentence(value: string): string {
  return /[.!?]$/.test(value) ? value : `${value}.`;
}

export const STATUS_TEXT: Record<Status, string> = {
  pending: "Needs your approval.",
  approved: "Approved.",
  flagged: "Look closer.",
  outdated: "Approval out of date.",
  failed: "Check failed.",
  dismissed: "Dismissed.",
  checking: "Re-checking.",
};

export const SECTIONS: Record<string, string> = {
  S: "Subjective",
  O: "Objective",
  A: "Assessment",
  P: "Plan",
  D: "Also discussed",
  Q: "Could not be read",
};

const KIND_TEXT: Record<string, string> = {
  symptom: "A symptom",
  vital_sign: "A vital sign",
  patient_reported_condition: "A condition the patient reported",
  clinician_assessed_problem: "A diagnosis",
  clinician_ruled_out_problem: "A diagnosis ruled out",
  allergy_statement: "An allergy",
  allergy_denial: "No known allergy",
  reported_medication: "A medicine the patient takes",
  explicit_new_order: "A new medication order",
  family_history: "Family history",
};

export function kindText(kind: string): string {
  return KIND_TEXT[kind] ?? kind.replaceAll("_", " ");
}

// Words a model writes instead of leaving a field empty (as in auris.consistency).
const PLACEHOLDERS = new Set([
  "null",
  "none",
  "n/a",
  "na",
  "unknown",
  "not stated",
  "not mentioned",
  "not specified",
  "not given",
  "not provided",
  "unspecified",
  "not applicable",
  "none stated",
]);

/** A free-text field's value, or "" when it is empty or a placeholder. */
function text(value: unknown): string {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  return PLACEHOLDERS.has(trimmed.toLowerCase()) ? "" : trimmed;
}

/** The value a form shows for a field: coded values as they are, free text without placeholders. */
export function formValue(field: string, value: unknown): string {
  if (field in CHOICES) return typeof value === "string" ? value : "";
  return text(value);
}

// The note's fact strings are Markdown-escaped; remove escapes for display.
export function unescapeFact(value: string): string {
  return value
    .replace(/\\([\\`*_{}[\]()#+\-.!|>~<])/g, "$1")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&amp;", "&");
}

/** A short clinical title built from the extracted fields, e.g. "Heart rate 84 /min". */
export function titleOf(state: ReviewState, item: Item): string {
  const a = state.assertions[item.id] ?? {};
  const concept = text(a.concept_text) || text(a.allergen_text);
  const negated = a.polarity === "negated";
  let title = "";
  switch (item.kind) {
    case "vital_sign":
      title = [concept, text(a.value_text), text(a.unit_text)].filter(Boolean).join(" ");
      break;
    case "allergy_statement":
      title = concept ? `${negated ? "No " : ""}${concept} allergy` : "";
      break;
    case "allergy_denial":
      title = concept ? `No ${concept} allergy` : "No known allergies";
      break;
    case "explicit_new_order":
    case "reported_medication":
      title = [concept, text(a.dose_text)].filter(Boolean).join(" ");
      break;
    case "clinician_ruled_out_problem":
      title = concept ? `${concept} ruled out` : "";
      break;
    default:
      title = concept ? `${negated ? "No " : ""}${concept}` : "";
  }
  if (title && a.experiencer === "family_member") title = `${title} in the family`;
  else if (title && a.experiencer === "other") title = `${title}, someone else`;
  title = title || unescapeFact(item.fact);
  return title.charAt(0).toUpperCase() + title.slice(1);
}

const FIELD_LABELS: Record<string, string> = {
  concept_text: "What",
  allergen_text: "Allergen",
  reaction_manifestation_text: "Reaction",
  stated_resolution_text: "Resolved",
  value_text: "Value",
  unit_text: "Unit",
  dose_text: "Dose",
  route_text: "Route",
  frequency_text: "How often",
  stated_time_text: "When",
  polarity: "Said as",
  experiencer: "About",
  temporality: "Timing",
  medication_intent: "Use",
};

/** Plain words for the coded fields; the Correct screen offers the same choices. */
export const CHOICES: Record<string, Array<[string, string]>> = {
  polarity: [["affirmed", "Present"], ["negated", "Absent"]],
  experiencer: [["patient", "The patient"], ["family_member", "A family member"], ["other", "Someone else"], ["unknown", "Not clear"]],
  temporality: [["current", "Now"], ["past", "In the past"], ["resolved", "Resolved"], ["future", "In the future"], ["unclear", "Not clear"]],
  medication_intent: [["new_order", "A new order"], ["continuing", "Continuing"], ["historical", "In the past"], ["discussed", "Only discussed"], ["unclear", "Not clear"]],
};

function shown(field: string, value: unknown): string {
  const choices = CHOICES[field];
  if (choices && typeof value === "string") return choices.find(([code]) => code === value)?.[1] ?? value;
  return text(value);
}

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

/** The extracted fields worth showing, in a stable order. */
export function recordFields(state: ReviewState, item: Item): Array<[string, string]> {
  const a = state.assertions[item.id] ?? {};
  const allergen = text(a.allergen_text).toLowerCase();
  return Object.keys(FIELD_LABELS)
    .filter((field) => !(field === "concept_text" && allergen && text(a[field]).toLowerCase() === allergen))
    .map((field): [string, string] => [fieldLabel(field), shown(field, a[field])])
    .filter(([, value]) => value !== "");
}

/** Where the fact goes, in plain words. */
export function destination(item: Item): { title: string; detail: string } {
  if (item.in_ledger) {
    return { title: "Stays in the visit note.", detail: "It is not exported as a record." };
  }
  switch (item.kind) {
    case "vital_sign":
      return { title: "Goes into the export file.", detail: "As a vital-sign record." };
    case "allergy_statement":
      return { title: "Goes into the export file.", detail: "As an allergy record." };
    case "explicit_new_order":
      return { title: "Goes into the export file.", detail: "As a draft order, never sent to a pharmacy." };
    default:
      return { title: "Goes into the export file.", detail: "As a condition record." };
  }
}

const LANE_TEXT: Record<string, string> = {
  reduced_schema: "Complete",
  evidence: "Matches the words",
  base_r4: "Valid FHIR record",
  us_core_profile: "US Core profile",
  terminology: "Medical codes",
};

// Lanes that check the exported record; they do not apply to a fact that stays in the note.
const RECORD_LANES = new Set(["base_r4", "us_core_profile", "terminology"]);

export function checksSummary(
  lanes: Record<string, Lane> | undefined,
  item: Item,
  labels: Record<string, string> = {},
): {
  sentence: string;
  note: string | null;
  failures: string[];
} {
  const entries = Object.entries(lanes ?? {}).filter(
    ([name, lane]) => !(item.in_ledger && RECORD_LANES.has(name) && lane.state === "NOT_CHECKED"),
  );
  const checked = entries.filter(([, lane]) => lane.state !== "NOT_CHECKED");
  const failed = entries.filter(([, lane]) => lane.state === "FAIL");
  const failures = failed.flatMap(([name, lane]) => {
    const messages = lane.issues
      .filter((issue) => issue.severity === "error" || issue.severity === "fatal")
      .map((issue) => issue.details?.text ?? "")
      .filter(Boolean)
      .map((code) => asSentence(labels[code] ?? code));
    return messages.length ? [...new Set(messages)] : [`${LANE_TEXT[name] ?? name} failed${lane.reason ? `: ${lane.reason}` : ""}.`];
  });
  const notChecked = entries
    .filter(([, lane]) => lane.state === "NOT_CHECKED")
    .map(([name]) => LANE_TEXT[name] ?? name);
  const sentence = failed.length
    ? `${failed.length} of ${checked.length} checks failed.`
    : checked.length
      ? `${checked.length === entries.length ? "All" : checked.length} ${checked.length === 1 ? "check" : "checks"} passed.`
      : "No checks apply.";
  const note = notChecked.length ? `Not checked: ${notChecked.join(", ")}.` : null;
  return { sentence, note, failures };
}

/** Split a quote so the fact's own words can be set in semibold. */
export function highlight(quote: string, terms: string[]): Array<{ text: string; strong: boolean }> {
  const wanted = terms.map((t) => t.trim()).filter((t) => t.length > 1);
  if (!wanted.length) return [{ text: quote, strong: false }];
  const lower = quote.toLowerCase();
  const marks = new Array<boolean>(quote.length).fill(false);
  for (const term of wanted) {
    let from = 0;
    const needle = term.toLowerCase();
    for (;;) {
      const at = lower.indexOf(needle, from);
      if (at < 0) break;
      for (let i = at; i < at + needle.length; i++) marks[i] = true;
      from = at + needle.length;
    }
  }
  const parts: Array<{ text: string; strong: boolean }> = [];
  for (let i = 0; i < quote.length; i++) {
    const strong = marks[i] ?? false;
    const last = parts[parts.length - 1];
    if (last && last.strong === strong) last.text += quote[i];
    else parts.push({ text: quote[i] ?? "", strong });
  }
  return parts;
}

export function highlightTerms(state: ReviewState, item: Item): string[] {
  const a = state.assertions[item.id] ?? {};
  return ["concept_text", "allergen_text", "value_text", "unit_text", "dose_text", "reaction_manifestation_text"]
    .map((field) => text(a[field]))
    .filter(Boolean);
}

/** Numbers in a value that do not appear in the quote (the "invented number" check). */
export function numbersNotIn(value: string, quote: string): string[] {
  const numbers = value.match(/\d+(?:[.,]\d+)?/g) ?? [];
  const said = new Set(quote.match(/\d+(?:[.,]\d+)?/g) ?? []);
  return numbers.filter((n) => !said.has(n));
}

export function receiptTime(receipt: string): string {
  const match = /\[?(\d{2}:\d{2}|turn \d+)/.exec(receipt);
  const value = match?.[1] ?? receipt;
  return value.replace(/^0(\d):/, "$1:");
}

export function receiptRole(receipt: string): string {
  const match = /·\s*([a-z]+)/.exec(receipt);
  return match?.[1] ?? "speaker";
}
