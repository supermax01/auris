import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, type Day, type Session, type Visit } from "../api";
import { Header, StatusIcon } from "../components";
import type { Navigate } from "../App";
import { countWord, longDay } from "../format";

function describe(visit: Visit, isNext: boolean) {
  switch (visit.state) {
    case "exported":
      return { icon: "approved" as const, text: "Exported", note: "" };
    case "ready":
      return visit.review_complete
        ? { icon: "approved" as const, text: "Ready to export.", note: "Every fact is decided." }
        : { icon: "flagged" as const, text: "Ready to review.", note: "Every fact needs your approval." };
    case "writing":
      return { icon: "busy" as const, text: "Writing the note", note: "" };
    case "listening":
      return { icon: "busy" as const, text: "Listening", note: "" };
    case "failed":
      return { icon: "failed" as const, text: "The note could not be written.", note: "" };
    default:
      return isNext
        ? { icon: "next" as const, text: "Next", note: "" }
        : { icon: "later" as const, text: "Not started", note: "" };
  }
}

export function Today({ session, go }: { session: Session; go: Navigate }) {
  const [day, setDay] = useState<Day | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ patient: "", reason: "", time: "" });
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.visits().then(setDay, (err: Error) => setError(err.message));
  }, []);
  useEffect(load, [load]);

  // Keep the list current while a note is being written.
  useEffect(() => {
    if (!day?.visits.some((visit) => visit.state === "writing")) return;
    const timer = window.setTimeout(load, 2500);
    return () => window.clearTimeout(timer);
  }, [day, load]);

  async function add(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    try {
      await api.addVisit(form);
      setForm({ patient: "", reason: "", time: "" });
      setAdding(false);
      load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "could not add the visit");
    }
  }

  const visits = day?.visits ?? [];
  const next = visits.find((visit) => visit.state === "scheduled");
  const counts = day?.counts;
  const toExport = visits.filter((visit) => visit.state === "ready" && visit.review_complete).length;
  const toReview = visits.filter((visit) => visit.state === "ready" && !visit.review_complete).length;
  const progress = [
    counts?.exported ? `${counts.exported} exported` : "",
    toExport ? `${toExport} to export` : "",
    toReview ? `${toReview} to review` : "",
    counts?.writing ? `${counts.writing} being written` : "",
  ].filter(Boolean);

  return (
    <>
      <Header clinician={session.clinician} demo={session.demo} crumbs={[]} />
      <main className="wrap today">
        <div className="today-head">
          <h1 className="title-xl">
            {longDay(session.today)}.{" "}
            <span className="muted">
              {visits.length === 0
                ? "No visits yet."
                : `${countWord(visits.length)} ${visits.length === 1 ? "visit" : "visits"}${next ? `, the next at ${next.time}.` : "."}`}
            </span>
          </h1>
          {counts ? (
            <p>
              <span className="strong">
                {progress.length ? `${progress.join(", ")}.` : "Nothing exported yet."}
              </span>
            </p>
          ) : null}
        </div>
        {error ? <p className="field-error" role="alert">{error}</p> : null}

        <div role="table" aria-label="Today’s visits" className="visits">
          <div role="row" className="visit-row is-head label">
            <span role="columnheader">Time</span>
            <span role="columnheader">Patient</span>
            <span role="columnheader">State</span>
            <span role="columnheader" className="align-end">Next step</span>
          </div>
          {visits.map((visit) => {
            const isNext = visit.id === next?.id;
            const state = describe(visit, isNext);
            return (
              <div role="row" key={visit.id} className={`visit-row${isNext ? " is-next" : ""}`}>
                <span role="cell" className="time">{visit.time}</span>
                <span role="cell">
                  <span className="strong">{visit.patient}.</span> <span className="muted">{visit.reason}</span>
                </span>
                <span role="cell" className="state">
                  <StatusIcon status={state.icon} />
                  <span>
                    <span className={state.icon === "later" ? "muted" : undefined}>{state.text}</span>{" "}
                    {state.note ? <span className="muted">{state.note}</span> : null}
                  </span>
                </span>
                <span role="cell" className="action">
                  {visit.state === "scheduled" ? (
                    <button className={`button ${isNext ? "button-primary" : "button-secondary"}`} onClick={() => go({ name: "start", visitId: visit.id })}>
                      Start the visit
                    </button>
                  ) : visit.state === "writing" ? (
                    <button className="button button-quiet" onClick={() => go({ name: "writing", visitId: visit.id })}>See progress</button>
                  ) : visit.state === "failed" ? (
                    <button className="button button-secondary" onClick={() => go({ name: "writing", visitId: visit.id })}>See what went wrong</button>
                  ) : visit.state === "ready" && visit.review_complete ? (
                    <button className="button button-secondary" onClick={() => go({ name: "ready", visitId: visit.id })}>Export the visit</button>
                  ) : visit.state === "ready" ? (
                    <button className="button button-secondary" onClick={() => go({ name: "review", visitId: visit.id })}>Review the visit</button>
                  ) : visit.state === "exported" ? (
                    <button className="link-button" onClick={() => go({ name: "ready", visitId: visit.id })}>Open the export</button>
                  ) : null}
                </span>
              </div>
            );
          })}
        </div>

        <section className="add-visit" aria-label="Add a visit">
          {adding ? (
            <form onSubmit={add}>
              <h2 className="title-l">
                Add a visit. <span className="muted">Until the clinic schedule is connected.</span>
              </h2>
              <div className="form-row">
                <label className="field">
                  <span className="label">Patient</span>
                  <input required maxLength={120} value={form.patient} onChange={(e) => setForm({ ...form, patient: e.target.value })} />
                </label>
                <label className="field">
                  <span className="label">Reason</span>
                  <input maxLength={200} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
                </label>
                <label className="field">
                  <span className="label">Time</span>
                  <input required type="time" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} />
                </label>
                <div className="inline-actions">
                  <button className="button button-primary" type="submit">Add the visit</button>
                  <button className="button button-quiet" type="button" onClick={() => setAdding(false)}>Cancel</button>
                </div>
              </div>
              {formError ? <p className="field-error" role="alert">{formError}</p> : null}
            </form>
          ) : (
            <button className="button button-secondary" onClick={() => setAdding(true)}>Add a visit</button>
          )}
        </section>
      </main>
    </>
  );
}
