// The onboarding chime: one soft bell per arch as it rises, then a low note
// under the name. Synthesised, so there is no audio file to ship or fetch.
const NOTES: ReadonlyArray<[frequency: number, at: number, peak: number, length: number]> = [
  [523.25, 0.45, 0.07, 1.4],
  [659.25, 0.6, 0.07, 1.4],
  [783.99, 0.75, 0.07, 1.4],
  [1046.5, 0.9, 0.06, 1.6],
  [261.63, 2.1, 0.05, 2.4],
];

type Ctor = typeof AudioContext;

/** Plays the chime. Returns false when the browser has not allowed sound yet. */
export async function playChime(requireRunning = false): Promise<boolean> {
  const Context: Ctor | undefined =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: Ctor }).webkitAudioContext;
  if (!Context) return false;
  const ctx = new Context();
  if (ctx.state !== "running") {
    await ctx.resume().catch(() => undefined);
  }
  if (requireRunning && ctx.state !== "running") {
    await ctx.close();
    return false;
  }
  const t0 = ctx.currentTime + 0.05;
  for (const [frequency, at, peak, length] of NOTES) {
    for (const [harmonic, level] of [
      [1, peak],
      [2, peak * 0.25],
    ] as const) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const start = t0 + at;
      osc.type = "sine";
      osc.frequency.value = frequency * harmonic;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(level, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + length);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + length + 0.05);
    }
  }
  window.setTimeout(() => void ctx.close(), 5000);
  return true;
}
