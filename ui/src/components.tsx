import type { ReactNode } from "react";
import { HeaderMark } from "./Mark";
import type { Status } from "./review";

export function initialsOf(name: string | null): string {
  return (name ?? "")
    .split(" ")
    .filter((part) => part && !/^(dr|prof|mr|mrs|ms|mx)\.?$/i.test(part))
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export function Header({
  clinician,
  demo,
  crumbs,
  onToday,
}: {
  clinician: string | null;
  demo: boolean;
  crumbs: string[];
  onToday?: () => void;
}) {
  return (
    <header className="app-header">
      <div className="wrap">
        <button type="button" className="brand" aria-label="Auris, back to today" onClick={onToday}>
          <HeaderMark />
          <span>Auris</span>
        </button>
        <span className="divider" />
        <nav aria-label="Breadcrumb" className="crumbs">
          {onToday && crumbs.length ? (
            <button type="button" className="crumb-link" onClick={onToday}>Today</button>
          ) : (
            <span aria-current={crumbs.length ? undefined : "page"}>Today</span>
          )}
          {crumbs.map((crumb, index) => (
            <span key={crumb + index} className="crumb">
              <span aria-hidden="true" className="crumb-sep">/</span>
              <span aria-current={index === crumbs.length - 1 ? "page" : undefined}>{crumb}</span>
            </span>
          ))}
        </nav>
        <span className="spacer" />
        {demo ? <span className="label">Fictional demo data</span> : null}
        <span className="person">
          <span className="initials" aria-hidden="true">{initialsOf(clinician)}</span>
          <span>{clinician}</span>
        </span>
      </div>
    </header>
  );
}

const RING = "#8A8A8A";

/** One symbol per state (design system sheet). */
export function StatusIcon({ status, size = 16 }: { status: Status | "busy" | "next" | "later"; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" style={{ flex: "none" }}>
      {status === "approved" ? (
        <>
          <circle cx="8" cy="8" r="6.25" fill="#111111" stroke="#111111" strokeWidth="1.5" />
          <path d="M5.3 8.2l1.9 1.9 3.6-3.9" fill="none" stroke="#FFFFFF" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
        </>
      ) : status === "flagged" ? (
        <>
          <circle cx="8" cy="8" r="6.25" fill="none" stroke="#111111" strokeWidth="1.5" />
          <path d="M8 4.8v3.7" stroke="#111111" strokeWidth="1.7" strokeLinecap="round" />
          <circle cx="8" cy="11.1" r="1" fill="#111111" />
        </>
      ) : status === "failed" ? (
        <>
          <circle cx="8" cy="8" r="6.25" fill="#A1123C" stroke="#A1123C" strokeWidth="1.5" />
          <path d="M5.9 5.9l4.2 4.2M10.1 5.9l-4.2 4.2" stroke="#FFFFFF" strokeWidth="1.7" strokeLinecap="round" />
        </>
      ) : status === "dismissed" ? (
        <>
          <circle cx="8" cy="8" r="6.25" fill="none" stroke="#9A9A9A" strokeWidth="1.5" />
          <path d="M4.8 11.2l6.4-6.4" stroke="#9A9A9A" strokeWidth="1.5" strokeLinecap="round" />
        </>
      ) : status === "outdated" || status === "busy" || status === "checking" ? (
        <path
          className={status === "outdated" ? undefined : "spin"}
          d="M13.5 8A5.5 5.5 0 1 1 11.9 4.1M9.5 4.1H11.9V1.7"
          fill="none"
          stroke="#111111"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : status === "next" ? (
        <>
          <circle cx="8" cy="8" r="6.25" fill="none" stroke="#C7522B" strokeWidth="1.5" />
          <circle cx="8" cy="8" r="2.5" fill="#C7522B" />
        </>
      ) : status === "later" ? (
        <circle cx="8" cy="8" r="6.25" fill="none" stroke="#C8C8C8" strokeWidth="1.5" />
      ) : (
        <circle cx="8" cy="8" r="6.25" fill="none" stroke={RING} strokeWidth="1.5" />
      )}
    </svg>
  );
}

/** The calm "working" orb (Writing the note), in the logo's terracotta. */
export function Orb({ mode }: { mode: "listening" | "working" }) {
  return (
    <div className={`orb orb-${mode}`} role="img" aria-label={mode === "listening" ? "Auris is listening" : "Auris is writing the note"}>
      {mode === "listening" ? (
        <>
          <span className="orb-ripple" />
          <span className="orb-ripple orb-ripple-late" />
        </>
      ) : null}
      <span className="orb-body">
        <span className="orb-cloud orb-a" />
        <span className="orb-cloud orb-b" />
        <span className="orb-cloud orb-c" />
      </span>
    </div>
  );
}

export function Notice({ children, onClose }: { children: ReactNode; onClose?: () => void }) {
  return (
    <div className="notice" role="status">
      <span>{children}</span>
      {onClose ? (
        <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
      ) : null}
    </div>
  );
}

export function download(name: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
