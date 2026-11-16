import { type FormEvent, useEffect, useState } from "react";
import { api, type Session, type Visit } from "../api";
import { Header } from "../components";
import type { Navigate } from "../App";
import { highlight, highlightTerms, type ReviewState, receiptTime, rowFor, titleOf } from "../review";

const REASONS: Array<[string, string]> = [
  ["It’s about someone else.", "Family history or another person."],
  ["It wasn’t said in this visit.", ""],
  ["It’s already in the patient’s record.", ""],
  ["It isn’t needed in this record.", ""],
  ["Something else.", "Say why in the note."],
];

export function Dismiss({ session, visit, factId, go }: { session: Session; visit: Visit; factId: string; go: Navigate }) {
  const [state, setState] = useState<ReviewState | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.review(visit.id).then(setState, (err: Error) => setError(err.message));
  }, [visit.id]);

  const item = state?.items.find((i) => i.id === factId);
  const evidence = state?.evidence[factId]?.[0];
  const other = reason === REASONS[REASONS.length - 1]?.[0];
  const text = reason ? [reason, note.trim()].filter(Boolean).join(" ") : "";

  async function dismiss(event: FormEvent) {
    event.preventDefault();
    if (!state || !item || !reason) return;
    if (other && !note.trim()) {
      setError("say why in the note");
      return;
    }
    const row = rowFor(state, item.id);
    if (!row) return;
    setSaving(true);
    setError(null);
    try {
      await api.act(visit.id, { target_id: item.id, target_sha256: row.target_sha256, action: "dismiss", reason: text });
      go({ name: "review", visitId: visit.id, factId: item.id });
    } catch (err) {
      setError(err instanceof Error ? err.message : "the fact was not dismissed");
      setSaving(false);
    }
  }

  const title = state && item ? titleOf(state, item) : "";

  return (
    <>
      <Header clinician={session.clinician} demo={session.demo} crumbs={[visit.patient, title]} onToday={() => go({ name: "today" })} />
      <main className="wrap form-page">
        {state && item ? (
          <form onSubmit={dismiss} className="form-column" aria-labelledby="dismiss-title">
            <h1 id="dismiss-title" className="title-fact">
              Dismiss “{title}”. <span className="muted">It stays out of the export file.</span>
            </h1>
            {evidence ? (
              <figure className="tile quote-tile">
                <figcaption className="label">{receiptTime(evidence.receipt)}</figcaption>
                <blockquote className="quote">
                  “
                  {highlight(evidence.quote, highlightTerms(state, item)).map((part, i) =>
                    part.strong ? <strong key={i}>{part.text}</strong> : <span key={i}>{part.text}</span>,
                  )}
                  ”
                </blockquote>
              </figure>
            ) : null}
            <fieldset className="reason-list">
              <legend className="title-l">Why are you dismissing it?</legend>
              {REASONS.map(([value, hint]) => (
                <label key={value} className="reason-row">
                  <input type="radio" name="reason" checked={reason === value} onChange={() => setReason(value)} />
                  <span>
                    <span className={reason === value ? "strong" : undefined}>{value}</span>{" "}
                    {hint ? <span className="muted">{hint}</span> : null}
                  </span>
                </label>
              ))}
            </fieldset>
            <label className="field">
              <span className="label">
                A note for the history {other ? null : <span className="muted">(optional)</span>}
              </span>
              <textarea rows={3} maxLength={3000} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
            {error ? <p className="field-error" role="alert">{error}</p> : null}
            <div className="fact-actions">
              <button type="submit" className="button button-primary" disabled={!reason || saving}>Dismiss the fact</button>
              <button type="button" className="button button-quiet" onClick={() => go({ name: "review", visitId: visit.id, factId })}>
                Cancel
              </button>
              <span className="spacer" />
              <span className="label">You can undo this until you export.</span>
            </div>
          </form>
        ) : error ? (
          <p className="field-error" role="alert">{error}</p>
        ) : null}
      </main>
    </>
  );
}
