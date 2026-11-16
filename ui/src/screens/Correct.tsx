import { type FormEvent, useEffect, useMemo, useState } from "react";
import { api, type Session, type Visit } from "../api";
import { Header, StatusIcon } from "../components";
import type { Navigate } from "../App";
import {
  CHOICES,
  fieldLabel,
  formValue,
  highlight,
  highlightTerms,
  numbersNotIn,
  type ReviewState,
  receiptTime,
  rowFor,
  titleOf,
} from "../review";

const HIDDEN = new Set(["kind"]);

export function Correct({ session, visit, factId, go }: { session: Session; visit: Visit; factId: string; go: Navigate }) {
  const [state, setState] = useState<ReviewState | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [speaker, setSpeaker] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.review(visit.id).then(
      (next) => {
        setState(next);
        const assertion = next.assertions[factId] ?? {};
        setValues(
          Object.fromEntries(
            (next.edit_schema[next.items.find((i) => i.id === factId)?.kind ?? ""] ?? [])
              .filter((field) => !HIDDEN.has(field))
              .map((field) => [field, formValue(field, assertion[field])]),
          ),
        );
      },
      (err: Error) => setError(err.message),
    );
  }, [visit.id, factId]);

  const item = state?.items.find((i) => i.id === factId);
  const evidence = state?.evidence[factId]?.[0];
  const segmentId = evidence?.segment_id ?? null;
  const currentSpeaker = segmentId ? state?.speakers[segmentId]?.role ?? null : null;
  const original = useMemo(() => (state ? state.assertions[factId] ?? {} : {}), [state, factId]);
  // A placeholder such as "not stated" shows as empty and is not a change when left empty.
  const changed = Object.entries(values).filter(([field, value]) => value.trim() !== formValue(field, original[field]));
  const quote = (state?.evidence[factId] ?? []).map((e) => e.quote).join(" ");
  const invented = changed.flatMap(([field, value]) =>
    field.endsWith("_text") && field !== "concept_text" ? numbersNotIn(value, quote).map((n) => [field, n] as const) : [],
  );

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!state || !item) return;
    setSaving(true);
    setError(null);
    try {
      const row = rowFor(state, item.id);
      if (changed.length && row) {
        await api.correct(visit.id, {
          target_id: item.id,
          target_sha256: row.target_sha256,
          fields: Object.fromEntries(changed.map(([field, value]) => [field, value.trim() === "" ? null : value])),
        });
      }
      if (speaker && segmentId && speaker !== currentSpeaker) {
        const latest = await api.review(visit.id);
        const target = latest.speakers[segmentId];
        if (target) {
          await api.speaker(visit.id, { target_id: segmentId, target_sha256: target.target_sha256, speaker_role: speaker });
        }
      }
      go({ name: "review", visitId: visit.id, factId: item.id });
    } catch (err) {
      setError(err instanceof Error ? err.message : "the correction was not saved");
      setSaving(false);
    }
  }

  const title = state && item ? titleOf(state, item) : "";
  const back = () => go({ name: "review", visitId: visit.id, factId });

  return (
    <>
      <Header clinician={session.clinician} demo={session.demo} crumbs={[visit.patient, title]} onToday={() => go({ name: "today" })} />
      <main className="wrap form-page">
        {state && item ? (
          <form onSubmit={save} className="form-column" aria-labelledby="correct-title">
            <h1 id="correct-title" className="title-fact">
              Correct {title.toLowerCase()}. <span className="muted">The words stay exactly as they were said.</span>
            </h1>
            {evidence ? (
              <figure className="tile quote-tile">
                <figcaption className="label">
                  {receiptTime(evidence.receipt)}, the {currentSpeaker ?? "speaker"}
                </figcaption>
                <blockquote className="quote">
                  “
                  {highlight(evidence.quote, highlightTerms(state, item)).map((part, i) =>
                    part.strong ? <strong key={i}>{part.text}</strong> : <span key={i}>{part.text}</span>,
                  )}
                  ”
                </blockquote>
              </figure>
            ) : null}

            <div className="form-fields">
              {Object.entries(values).map(([field, value]) => {
                const choices = CHOICES[field];
                const bad = invented.some(([f]) => f === field);
                return (
                  <label key={field} className="field">
                    <span className="label">{fieldLabel(field)}</span>
                    {choices ? (
                      <select value={value} onChange={(e) => setValues({ ...values, [field]: e.target.value })}>
                        {choices.map(([code, text]) => (
                          <option key={code} value={code}>{text}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={value}
                        maxLength={4000}
                        aria-invalid={bad || undefined}
                        className={bad ? "is-invalid" : undefined}
                        onChange={(e) => setValues({ ...values, [field]: e.target.value })}
                      />
                    )}
                  </label>
                );
              })}
            </div>

            {invented.length ? (
              <p className="field-error with-icon" role="alert">
                <StatusIcon status="failed" size={18} />
                <span>
                  <span className="strong">{invented.map(([, n]) => n).join(", ")} is not in the words.</span> If you save it,
                  the check fails and the fact can’t be approved.
                </span>
              </p>
            ) : null}

            {segmentId && currentSpeaker ? (
              <fieldset className="radios">
                <legend className="label">Who said it</legend>
                {[["clinician", "The clinician"], ["patient", "The patient"], ["other", "Someone else"]].map(([code, text]) => (
                  <label key={code} className="radio">
                    <input
                      type="radio"
                      name="speaker"
                      checked={(speaker ?? currentSpeaker) === code}
                      onChange={() => setSpeaker(code ?? null)}
                    />
                    {text}
                  </label>
                ))}
              </fieldset>
            ) : null}

            {error ? <p className="field-error" role="alert">{error}</p> : null}
            <div className="fact-actions">
              <button type="submit" className="button button-primary" disabled={saving || (!changed.length && (!speaker || speaker === currentSpeaker))}>
                Save the correction
              </button>
              <button type="button" className="button button-quiet" onClick={back}>Cancel</button>
              <span className="spacer" />
              <span className="label">Saving runs every check again and keeps the old version in the history.</span>
            </div>
          </form>
        ) : error ? (
          <p className="field-error" role="alert">{error}</p>
        ) : null}
      </main>
    </>
  );
}
