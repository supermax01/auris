import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Session, type Visit } from "../api";
import { Header, Notice, StatusIcon } from "../components";
import type { Navigate } from "../App";
import { longDay } from "../format";
import {
  checksSummary,
  doubtsOf,
  destination,
  highlight,
  highlightTerms,
  type Item,
  kindText,
  receiptRole,
  receiptTime,
  notesOf,
  recordFields,
  type ReviewState,
  rowFor,
  SECTIONS,
  STATUS_TEXT,
  type Status,
  statusOf,
  titleOf,
} from "../review";

const DECIDED: Status[] = ["approved", "dismissed"];

export function Review({
  session,
  visit,
  go,
  focus,
}: {
  session: Session;
  visit: Visit;
  go: Navigate;
  focus?: string;
}) {
  const [state, setState] = useState<ReviewState | null>(null);
  const [selected, setSelected] = useState<string | undefined>(focus);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.review(visit.id);
      setState(next);
      setError(null);
      return next;
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not load the review");
      return null;
    }
  }, [visit.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while a correction is being re-checked.
  useEffect(() => {
    if (!state?.busy) return;
    const timer = window.setTimeout(() => void load(), 1500);
    return () => window.clearTimeout(timer);
  }, [state, load]);

  const items = useMemo(() => {
    if (!state) return [];
    const order = Object.keys(SECTIONS);
    return [...state.items].sort(
      (a, b) => order.indexOf(a.section) - order.indexOf(b.section) || a.reference.localeCompare(b.reference, undefined, { numeric: true }),
    );
  }, [state]);

  const current = items.find((item) => item.id === selected) ?? items.find((item) => state && !DECIDED.includes(statusOf(state, item))) ?? items[0];

  function nextUndecided(after: ReviewState, fromId: string): string | undefined {
    const index = items.findIndex((item) => item.id === fromId);
    const rotated = [...items.slice(index + 1), ...items.slice(0, index + 1)];
    return rotated.find((item) => {
      const status = statusOf(after, item);
      return !DECIDED.includes(status) && status !== "failed";
    })?.id;
  }

  async function act(item: Item, action: "approve" | "undo") {
    if (!state) return;
    const row = rowFor(state, item.id);
    if (!row) return;
    setWorking(true);
    try {
      await api.act(visit.id, { target_id: item.id, target_sha256: row.target_sha256, action });
      const after = await load();
      if (after && action === "approve") setSelected(nextUndecided(after, item.id) ?? item.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "the action did not go through");
    } finally {
      setWorking(false);
    }
  }

  async function undoEdit(item: Item) {
    if (!state) return;
    const row = rowFor(state, item.id);
    if (!row?.undo_edit) return;
    setWorking(true);
    try {
      await api.undoEdit(visit.id, { target_id: item.id, target_sha256: row.target_sha256 });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not undo the change");
    } finally {
      setWorking(false);
    }
  }

  if (!state) {
    return (
      <>
        <Header clinician={session.clinician} demo={session.demo} crumbs={[visit.patient]} onToday={() => go({ name: "today" })} />
        <main className="wrap page">{error ? <p className="field-error" role="alert">{error}</p> : null}</main>
      </>
    );
  }

  const statuses = items.map((item) => statusOf(state, item));
  const decided = statuses.filter((s) => DECIDED.includes(s)).length;
  const sections = Object.keys(SECTIONS)
    .map((code) => ({ code, name: SECTIONS[code] ?? code, items: items.filter((item) => item.section === code) }))
    .filter((section) => section.items.length);
  const other = items.filter((item) => !(item.section in SECTIONS));
  if (other.length) sections.push({ code: "other", name: "Other", items: other });

  return (
    <>
      <Header clinician={session.clinician} demo={session.demo} crumbs={[visit.patient]} onToday={() => go({ name: "today" })} />
      <section className="wrap visit-head" aria-label="Visit">
        <h1 className="title-xl">
          {visit.patient}. <span className="muted">Seen by {session.clinician} on {longDay(visit.day)}.</span>
        </h1>
        <div className="tally-block">
          <div className="tally" role="img" aria-label={`${decided} of ${items.length} facts decided`}>
            {statuses.map((status, index) => (
              <StatusIcon key={items[index]?.id ?? index} status={status} />
            ))}
          </div>
          <span>
            <span className="strong">{decided} of {items.length} decided.</span>{" "}
            <span className="muted">
              {state.ready ? "Ready to export." : `Export waits for the other ${items.length - decided}.`}
            </span>
          </span>
        </div>
      </section>

      {state.ready ? (
        <div className="wrap">
          <Notice>
            <span className="strong">Every record is decided.</span>{" "}
            <button type="button" className="button button-primary" onClick={() => go({ name: "ready", visitId: visit.id })}>
              Go to export
            </button>
          </Notice>
        </div>
      ) : null}
      {error ? (
        <div className="wrap">
          <p className="field-error" role="alert">{error}</p>
        </div>
      ) : null}

      <div className="wrap review-grid">
        <nav aria-label="Facts to review" className="queue">
          {sections.map((section) => (
            <div key={section.code} className="queue-section">
              <div className="queue-head label">
                <span>{section.name}</span>
                <span>{section.items.length}</span>
              </div>
              {section.items.map((item) => {
                const status = statusOf(state, item);
                const isCurrent = item.id === current?.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`queue-row${isCurrent ? " is-current" : ""}${status === "dismissed" ? " is-dismissed" : ""}`}
                    aria-current={isCurrent ? "true" : undefined}
                    onClick={() => setSelected(item.id)}
                  >
                    <StatusIcon status={status} />
                    <span className="queue-title">
                      {titleOf(state, item)}
                      <span className="visually-hidden">, {STATUS_TEXT[status]}</span>
                    </span>
                    <span className="label">{receiptTime(state.evidence[item.id]?.[0]?.receipt ?? item.receipts)}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        {current ? (
          <Fact
            key={current.id}
            state={state}
            item={current}
            working={working || state.busy}
            onApprove={() => act(current, "approve")}
            onUndo={() => act(current, "undo")}
            onUndoEdit={() => undoEdit(current)}
            onCorrect={() => go({ name: "correct", visitId: visit.id, factId: current.id })}
            onDismiss={() => go({ name: "dismiss", visitId: visit.id, factId: current.id })}
            onStep={(delta) => {
              const index = items.findIndex((item) => item.id === current.id);
              const next = items[(index + delta + items.length) % items.length];
              if (next) setSelected(next.id);
            }}
          />
        ) : (
          <p>No facts were found in this visit.</p>
        )}
      </div>
    </>
  );
}

function Fact({
  state,
  item,
  working,
  onApprove,
  onUndo,
  onUndoEdit,
  onCorrect,
  onDismiss,
  onStep,
}: {
  state: ReviewState;
  item: Item;
  working: boolean;
  onApprove: () => void;
  onUndo: () => void;
  onUndoEdit: () => void;
  onCorrect: () => void;
  onDismiss: () => void;
  onStep: (delta: number) => void;
}) {
  const status = statusOf(state, item);
  const row = rowFor(state, item.id);
  const title = titleOf(state, item);
  const evidence = state.evidence[item.id] ?? [];
  const terms = highlightTerms(state, item);
  const where = destination(item);
  const checks = checksSummary(state.lanes[item.id], item, state.reason_labels);
  const doubts = doubtsOf(state, item);
  const notes = notesOf(state, item);
  const fields = recordFields(state, item);
  const canApprove = !!row?.allowed_actions.includes("approve") && status !== "failed";
  const canDismiss = !!row?.allowed_actions.includes("dismiss");
  const decided = status === "approved" || status === "dismissed";
  const statusNote =
    status === "flagged"
      ? doubts.length === 1
        ? doubts[0]
        : `${doubts.length} things to check.`
      : status === "failed"
        ? "It can’t be approved."
        : status === "outdated"
          ? "You changed it after approving it."
          : status === "approved" && row?.receipt
            ? `By ${row.receipt.reviewer}.`
            : status === "dismissed" && row?.receipt?.reason
              ? row.receipt.reason
              : status === "checking"
                ? "Every check is running again on your change."
                : "Nothing about it looks wrong.";

  return (
    <article className="fact" aria-labelledby="fact-title">
      <div className="fact-head">
        <h2 id="fact-title" className="title-fact">
          {title}. <span className="muted">{kindText(item.kind)}.</span>
        </h2>
        <div className="stepper">
          <button type="button" className="icon-button" aria-label="Previous fact" onClick={() => onStep(-1)}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3.5L5.5 8l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
          <button type="button" className="icon-button" aria-label="Next fact" onClick={() => onStep(1)}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
        </div>
      </div>

      <div className={`status-line is-${status}`}>
        <StatusIcon status={status} size={18} />
        <span>
          <span className="strong status-word">{STATUS_TEXT[status]}</span> <span className="muted">{statusNote}</span>
        </span>
      </div>

      {status === "flagged" && doubts.length > 1 ? (
        <ul className="reasons">
          {doubts.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}

      {status === "outdated" && row?.stale_message ? (
        <section className="tile change-tile" aria-label="What changed">
          <p>{row.stale_message}</p>
          {row.undo_edit ? (
            <button type="button" className="link-button" onClick={onUndoEdit} disabled={working}>
              Undo the change
            </button>
          ) : null}
        </section>
      ) : null}

      {status === "failed" && checks.failures.length ? (
        <section className="tile fail-tile" role="alert">
          {checks.failures.map((failure) => (
            <p key={failure}>{failure}</p>
          ))}
          {row?.undo_edit ? (
            <button type="button" className="link-button" onClick={onUndoEdit} disabled={working}>
              Undo the change
            </button>
          ) : null}
        </section>
      ) : null}

      {evidence.map((quote, index) => {
        const cited = quote.neighbors.find((n) => n.cited);
        const around = quote.neighbors.filter((n) => !n.cited);
        return (
          <section key={index} className="evidence">
            <figure className="tile quote-tile">
              <figcaption className="label">
                {receiptTime(quote.receipt)}, the {cited?.role ?? receiptRole(quote.receipt)}
              </figcaption>
              <blockquote className="quote">
                “
                {highlight(quote.quote, terms).map((part, i) =>
                  part.strong ? <strong key={i}>{part.text}</strong> : <span key={i}>{part.text}</span>,
                )}
                ”
              </blockquote>
              <p className="label quote-note">
                {quote.failure
                  ? `Not found word for word: ${quote.failure.replaceAll("_", " ")}`
                  : quote.alignment?.startsWith("disfluency")
                    ? "Matched, ignoring fillers like “um”"
                    : "Word for word in the transcript"}
              </p>
            </figure>
            {around.length ? (
              <div className="context">
                {around.map((line) => (
                  <p key={line.id} className="context-line">
                    <span className="label">{receiptTime(line.receipt)}</span>
                    <span>
                      <span className="role">{line.role.charAt(0).toUpperCase() + line.role.slice(1)}:</span>{" "}
                      <span className="context-text">{line.text}</span>
                    </span>
                  </p>
                ))}
              </div>
            ) : null}
          </section>
        );
      })}

      <section className="record" aria-labelledby="record-title">
        <h3 id="record-title" className="title-l">
          {where.title} <span className="muted">{where.detail}</span>
        </h3>
        {fields.length ? (
          <dl className="fields">
            {fields.map(([label, value]) => (
              <div key={label} className="field-row">
                <dt className="label">{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        <p className="checks">
          {checks.sentence} {checks.note ? <span className="muted">{checks.note}</span> : null}
        </p>
        {notes.length ? (
          <p className="checks muted">{notes.join(" ")}</p>
        ) : null}
      </section>

      <div className="fact-actions">
        {decided ? (
          <button type="button" className="button button-secondary" onClick={onUndo} disabled={working}>
            {status === "approved" ? "Undo the approval" : "Undo the dismissal"}
          </button>
        ) : (
          <>
            {canApprove ? (
              <button type="button" className="button button-primary" onClick={onApprove} disabled={working}>
                {status === "outdated" ? "Approve the new version" : `Approve ${title.length > 28 ? "this fact" : title.toLowerCase()}`}
              </button>
            ) : null}
            {row?.editable ? (
              <button type="button" className={`button ${canApprove ? "button-secondary" : "button-primary"}`} onClick={onCorrect} disabled={working}>
                Correct the fact
              </button>
            ) : null}
            {canDismiss ? (
              <button type="button" className="button button-quiet" onClick={onDismiss} disabled={working}>
                Dismiss the fact
              </button>
            ) : null}
          </>
        )}
        <span className="spacer" />
        <span className="label">
          {status === "failed"
            ? "A fact has to match the words it came from."
            : decided
              ? "You can change your mind until you export."
              : item.in_ledger
                ? "Approving keeps it in the visit note. You can undo it."
                : "Approving puts it in the export file. You can undo it."}
        </span>
      </div>
    </article>
  );
}
