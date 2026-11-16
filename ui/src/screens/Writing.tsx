import { useEffect, useState } from "react";
import { api, type Job, type Session, type Visit } from "../api";
import { Header, Orb, StatusIcon } from "../components";
import type { Navigate } from "../App";

export function Writing({ session, visit: initial, go }: { session: Session; visit: Visit; go: Navigate }) {
  const [visit, setVisit] = useState(initial);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const result = await api.visit(initial.id);
        if (stopped) return;
        setVisit(result.visit);
        setJob(result.job);
        if (result.visit.state === "writing") timer = window.setTimeout(poll, 1500);
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : "lost the engine");
      }
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [initial.id]);

  async function retry() {
    setError(null);
    try {
      const result = await api.retry(visit.id);
      setVisit(result.visit);
      setJob(result.job);
      go({ name: "writing", visitId: visit.id, refresh: Date.now() });
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not try again");
    }
  }

  const failed = visit.state === "failed";
  const done = visit.state === "ready" || visit.state === "exported";

  return (
    <>
      <Header clinician={session.clinician} demo={session.demo} crumbs={[visit.patient]} onToday={() => go({ name: "today" })} />
      <main className="wrap page-grid">
        <section className="col-main" aria-labelledby="writing-title">
          {failed ? null : <Orb mode="working" />}
          <h1 id="writing-title" className="title-xxl writing-title">
            {failed ? (
              <>
                The note could not be written. <span className="muted">Nothing was saved as a record.</span>
              </>
            ) : done ? (
              <>
                The note is written. <span className="muted">It is waiting for your review.</span>
              </>
            ) : (
              <>
                Writing the note. <span className="muted">You can see your next patient.</span>
              </>
            )}
          </h1>
          <p className="lead">
            {failed
              ? visit.failure ?? job?.error ?? "Something went wrong while writing the note."
              : done
                ? "Every fact needs your approval before anything leaves this computer."
                : `Auris keeps working if you leave this screen. ${visit.patient} will show as ready to review on Today.`}
          </p>
          <div className="actions">
            {done ? (
              <button className="button button-primary button-large" onClick={() => go({ name: "review", visitId: visit.id })}>
                Review the visit
              </button>
            ) : failed ? (
              <button className="button button-primary button-large" onClick={retry}>
                Try again
              </button>
            ) : (
              <button className="button button-primary button-large" onClick={() => go({ name: "today" })}>
                Back to today
              </button>
            )}
          </div>
          {error ? <p className="field-error" role="alert">{error}</p> : null}
        </section>

        <section className="col-side" aria-labelledby="progress-title">
          <h2 id="progress-title" className="title-l">
            Progress. <span className="muted">All of it on this computer.</span>
          </h2>
          <ol className="progress" aria-live="polite">
            {(job?.stages ?? []).map((stage) => (
              <li key={stage.key} className={`progress-row is-${stage.state}`}>
                <StatusIcon status={stage.state === "done" ? "approved" : stage.state === "busy" ? "busy" : "later"} size={20} />
                <span>{stage.label}</span>
              </li>
            ))}
          </ol>
          {job?.state === "queued" && job.position ? (
            <p className="label">Waiting for {job.position === 1 ? "one note" : `${job.position} notes`} ahead of it.</p>
          ) : null}
        </section>
      </main>
    </>
  );
}
