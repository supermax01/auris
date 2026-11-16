import { type ChangeEvent, useState } from "react";
import { api, type Session, type Visit } from "../api";
import { Header } from "../components";
import type { Navigate } from "../App";

export function Start({ session, visit, go }: { session: Session; visit: Visit; go: Navigate }) {
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function send(action: () => Promise<unknown>) {
    setSending(true);
    setError(null);
    try {
      await action();
      go({ name: "writing", visitId: visit.id });
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not start writing the note");
      setSending(false);
    }
  }

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 900_000) {
      setError("that transcript file is too large");
      return;
    }
    const json = await file.text();
    await send(() => api.sendTranscript(visit.id, json));
  }

  return (
    <>
      <Header clinician={session.clinician} demo={session.demo} crumbs={[visit.patient]} onToday={() => go({ name: "today" })} />
      <main className="wrap page-grid">
        <section className="col-main" aria-labelledby="start-title">
          <h1 id="start-title" className="title-xxl">
            {visit.patient}. <span className="muted">Ask before Auris listens.</span>
          </h1>
          <div className="tile quote-tile">
            <span className="label">You could say</span>
            <p className="quote">
              “I use a tool that listens to our conversation and drafts my notes. It runs on this
              computer, and the recording stays here. Is that all right with you?”
            </p>
          </div>
          <label className="check">
            <input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />
            <span>
              <span className="strong">{visit.patient} agreed.</span>{" "}
              <span className="muted">Auris notes the time.</span>
            </span>
          </label>
          <div className="actions">
            <button className="button button-primary button-large" disabled title="Listening is not built yet">
              Start listening
            </button>
            <span className="label">Listening is not built yet. Use a transcript for now.</span>
          </div>
          {error ? <p className="field-error" role="alert">{error}</p> : null}
        </section>

        <aside className="col-side" aria-labelledby="transcript-title">
          <h2 id="transcript-title" className="title-l">
            Use a transcript. <span className="muted">Until listening is built.</span>
          </h2>
          <p className="side-text">
            Auris writes the note from a transcript file in its own format. Everything stays on this
            computer.
          </p>
          <div className="stack">
            {session.demo && visit.fictional ? (
              <button className="button button-secondary" disabled={sending} onClick={() => send(() => api.demoTranscript(visit.id))}>
                Use the fictional demo transcript
              </button>
            ) : null}
            <label className="button button-secondary file-button">
              Choose a transcript file
              <input type="file" accept="application/json,.json" onChange={chooseFile} disabled={sending} />
            </label>
          </div>
        </aside>
      </main>
    </>
  );
}
