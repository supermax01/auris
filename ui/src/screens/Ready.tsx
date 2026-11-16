import { useEffect, useState } from "react";
import { api, type Outputs, type Session, type Visit } from "../api";
import { download, Header, StatusIcon } from "../components";
import type { Navigate } from "../App";
import { longDay } from "../format";
import { type ReviewState, statusOf, titleOf } from "../review";

function fileStem(visit: Visit): string {
  return `auris-${visit.day}-${visit.time.replace(":", "")}`;
}

export function Ready({ session, visit: initial, go }: { session: Session; visit: Visit; go: Navigate }) {
  const [visit, setVisit] = useState(initial);
  const [state, setState] = useState<ReviewState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    api.review(visit.id).then(setState, (err: Error) => setError(err.message));
  }, [visit.id]);

  function save(outputs: Outputs, which: "bundle" | "note") {
    if (which === "bundle") download(`${fileStem(visit)}-bundle.json`, outputs["bundle.json"], "application/fhir+json");
    else download(`${fileStem(visit)}-note.md`, outputs["note.md"], "text/markdown");
  }

  async function exportFile(which: "bundle" | "note") {
    setWorking(true);
    setError(null);
    try {
      if (visit.state === "exported") {
        save(await api.outputs(visit.id), which);
      } else {
        const result = await api.exportVisit(visit.id);
        setVisit(result.visit);
        save(result.outputs, which);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "the export did not finish");
    } finally {
      setWorking(false);
    }
  }

  async function reopen() {
    try {
      const result = await api.reopen(visit.id);
      setVisit(result.visit);
      go({ name: "review", visitId: visit.id });
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not reopen the visit");
    }
  }

  const exported = new Set(state?.export.exported_ids ?? []);
  const inFile = state?.items.filter((item) => exported.has(item.id)) ?? [];
  const outOfFile = state?.items.filter((item) => !exported.has(item.id)) ?? [];

  return (
    <>
      <Header clinician={session.clinician} demo={session.demo} crumbs={[visit.patient]} onToday={() => go({ name: "today" })} />
      <section className="wrap visit-head" aria-label="Visit">
        <h1 className="title-xl">
          {visit.patient}. <span className="muted">Seen by {session.clinician} on {longDay(visit.day)}.</span>
        </h1>
      </section>
      <main className="wrap ready-grid">
        {state && !state.ready ? (
          <section className="col-main">
            <h2 className="title-xxl">
              Export is blocked. <span className="muted">{state.export.blocking_ids.length} items still need a decision.</span>
            </h2>
            <div className="actions">
              <button className="button button-primary button-large" onClick={() => go({ name: "review", visitId: visit.id })}>
                Back to the facts
              </button>
            </div>
          </section>
        ) : state ? (
          <section className="col-main" aria-labelledby="ready-title">
            <h2 id="ready-title" className="title-xxl">
              {inFile.length} {inFile.length === 1 ? "record is" : "records are"} ready to leave this computer.{" "}
              <span className="muted">{outOfFile.length} more stay out.</span>
            </h2>
            <p className="lead">
              Each record is the exact version you approved. The visit note keeps the facts that are
              not exported as records.
            </p>
            <div className="actions">
              <button className="button button-primary button-large" disabled={working} onClick={() => exportFile("bundle")}>
                Save the export file
              </button>
              <button className="button button-secondary button-large" disabled={working} onClick={() => exportFile("note")}>
                Save the visit note
              </button>
            </div>
            <p className="label">
              {visit.state === "exported"
                ? "Exported. Nothing is sent anywhere; each file goes only where you save it."
                : "Nothing has been sent anywhere. The file is FHIR R4 and checks against US Core 6.1."}
            </p>
            {visit.state === "exported" ? (
              <p className="after">
                Need to change something? <button type="button" className="link-button" onClick={reopen}>Reopen the visit</button>
              </p>
            ) : (
              <p className="after">
                Changed your mind? <button type="button" className="link-button" onClick={() => go({ name: "review", visitId: visit.id })}>Back to the facts</button>
              </p>
            )}
            <p className="after">
              Done with this visit? <button type="button" className="link-button" onClick={() => go({ name: "today" })}>Back to today</button>
            </p>
          </section>
        ) : null}

        {state ? (
          <aside className="col-side manifest" aria-label="What the export file holds">
            <h3 className="title-l">
              In the export file. <span className="muted">{inFile.length} records.</span>
            </h3>
            <ul className="manifest-list">
              {inFile.map((item) => (
                <li key={item.id}>
                  <StatusIcon status="approved" />
                  <span>{titleOf(state, item)}</span>
                </li>
              ))}
            </ul>
            <p className="label">With the patient, the visit and the clinician.</p>
            <h3 className="title-l manifest-gap">
              Not in the file. <span className="muted">{outOfFile.length} items.</span>
            </h3>
            <ul className="manifest-list">
              {outOfFile.map((item) => {
                const status = statusOf(state, item);
                return (
                  <li key={item.id} className={status === "dismissed" ? "is-dismissed" : undefined}>
                    <StatusIcon status={status === "dismissed" ? "dismissed" : "approved"} />
                    <span>{titleOf(state, item)}</span>
                    <span className="muted">{status === "dismissed" ? "dismissed" : "visit note only"}</span>
                  </li>
                );
              })}
            </ul>
          </aside>
        ) : null}
        {error ? <p className="field-error" role="alert">{error}</p> : null}
      </main>
    </>
  );
}
