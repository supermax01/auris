import { useEffect, useState } from "react";
import { api, type Session, type Visit } from "./api";
import { Correct } from "./screens/Correct";
import { Dismiss } from "./screens/Dismiss";
import { Onboarding } from "./screens/Onboarding";
import { Ready } from "./screens/Ready";
import { Review } from "./screens/Review";
import { Start } from "./screens/Start";
import { Today } from "./screens/Today";
import { Writing } from "./screens/Writing";

export type Route =
  | { name: "today" }
  | { name: "start"; visitId: string }
  | { name: "writing"; visitId: string; refresh?: number }
  | { name: "review"; visitId: string; factId?: string }
  | { name: "correct"; visitId: string; factId: string }
  | { name: "dismiss"; visitId: string; factId: string }
  | { name: "ready"; visitId: string };

export type Navigate = (route: Route) => void;

function VisitScreen({ session, route, go }: { session: Session; route: Exclude<Route, { name: "today" }>; go: Navigate }) {
  const [visit, setVisit] = useState<Visit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = route.name === "writing" ? route.refresh : undefined;

  useEffect(() => {
    setVisit(null);
    api.visit(route.visitId).then((result) => setVisit(result.visit), (err: Error) => setError(err.message));
  }, [route.visitId, route.name, refresh]);

  if (error) {
    return (
      <main className="wrap page" role="alert">
        <h1 className="title-xl">This visit could not be opened. <span className="muted">{error}</span></h1>
        <button className="button button-secondary" onClick={() => go({ name: "today" })}>Back to today</button>
      </main>
    );
  }
  if (!visit) return null;
  switch (route.name) {
    case "start":
      return <Start session={session} visit={visit} go={go} />;
    case "writing":
      return <Writing key={refresh} session={session} visit={visit} go={go} />;
    case "review":
      return <Review session={session} visit={visit} go={go} focus={route.factId} />;
    case "correct":
      return <Correct session={session} visit={visit} factId={route.factId} go={go} />;
    case "dismiss":
      return <Dismiss session={session} visit={visit} factId={route.factId} go={go} />;
    case "ready":
      return <Ready session={session} visit={visit} go={go} />;
  }
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>({ name: "today" });

  useEffect(() => {
    if (!api.hasToken()) {
      setError("Open Auris from its app window or from the link the engine printed.");
      return;
    }
    api.session().then(setSession, (err: Error) => setError(err.message));
  }, []);

  const go: Navigate = (next) => {
    setRoute(next);
    window.scrollTo({ top: 0 });
  };

  if (error) {
    return (
      <main className="wrap page" role="alert">
        <h1 className="title-xl">
          Auris can’t reach its engine. <span className="muted">{error}</span>
        </h1>
      </main>
    );
  }
  if (!session) return null;
  if (!session.clinician) {
    return <Onboarding onDone={(clinician) => setSession({ ...session, clinician })} />;
  }
  if (route.name === "today") return <Today session={session} go={go} />;
  return <VisitScreen session={session} route={route} go={go} />;
}
