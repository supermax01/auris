import { type FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { playChime } from "../chime";
import { AnimatedMark } from "../Mark";

export function Onboarding({ onDone }: { onDone: (clinician: string) => void }) {
  const [run, setRun] = useState(0);
  const [step, setStep] = useState<"welcome" | "name">("welcome");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Play the chime with the first run if the window allows sound without a click.
  useEffect(() => {
    void playChime(true);
  }, []);

  function replay() {
    setRun((n) => n + 1);
    void playChime();
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const result = await api.setClinician(name);
      onDone(result.clinician);
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not save your name");
      setSaving(false);
    }
  }

  return (
    <main className="onboarding">
      <div key={run} style={{ display: "contents" }}>
        <AnimatedMark />
        <h1 className="ob-up d8">Auris</h1>
        <p className="tagline ob-up d9">It listens. You decide.</p>
        {step === "welcome" ? (
          <div className="next ob-up d10">
            <button className="button button-primary button-large" onClick={() => setStep("name")}>
              Get started
            </button>
            <span className="label">Runs on this computer. Nothing leaves it unless you export it.</span>
          </div>
        ) : (
          <form className="name-form" onSubmit={save}>
            <label className="field">
              <span className="title-l">
                What should Auris call you? <span className="muted">It appears next to every approval.</span>
              </span>
              <input
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="For example, Dr Tobin Hale"
                maxLength={80}
                aria-invalid={error ? true : undefined}
              />
            </label>
            {error ? <p className="field-error" role="alert">{error}</p> : null}
            <p className="label">This is a name, not a sign-in. Sign-in comes before any clinic use.</p>
            <div>
              <button className="button button-primary" type="submit" disabled={saving || !name.trim()}>
                Continue to today
              </button>
            </div>
          </form>
        )}
      </div>
      <button type="button" className="replay" onClick={replay}>
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M3 6h2.5L9 3v10L5.5 10H3Z" fill="currentColor" />
          <path d="M11 5.5a3.5 3.5 0 0 1 0 5M12.8 3.8a6 6 0 0 1 0 8.4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        Replay with sound
      </button>
    </main>
  );
}
