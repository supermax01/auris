// The Auris mark (docs/brand): arches of sound resting on lines of writing.
const ARCHES = [
  "M138 318V228A90 90 0 0 1 318 228V318H298V228A70 70 0 0 0 158 228V318Z",
  "M92 318V228A136 136 0 0 1 364 228V318H344V228A116 116 0 0 0 112 228V318Z",
  "M46 318V228A182 182 0 0 1 410 228V318H390V228A162 162 0 0 0 66 228V318Z",
  "M0 318V228A228 228 0 0 1 456 228V318H436V228A208 208 0 0 0 20 228V318Z",
];
const LINES = [298, 344, 390, 436];
const MEDIUM =
  "M8 16V12A4 4 0 0 1 16 12V16H14V12A2 2 0 0 0 10 12V16ZM4 16V12A8 8 0 0 1 20 12V16H18V12A6 6 0 0 0 6 12V16ZM0 16V12A12 12 0 0 1 24 12V16H22V12A10 10 0 0 0 2 12V16ZM0 14H24V16H0ZM0 18H24V20H0ZM0 22H24V24H0Z";

export const TERRACOTTA = "#C7522B";

export function HeaderMark() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
      <path fill={TERRACOTTA} d={MEDIUM} />
    </svg>
  );
}

/** The mark building itself: base line, four arches rising, then the lines written. */
export function AnimatedMark({ size = 168 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 456 456" role="img" aria-label="Auris">
      <rect className="ob-line d0" fill={TERRACOTTA} x="0" y={LINES[0]} width="456" height="20" />
      {ARCHES.map((d, i) => (
        <path key={d} className={`ob-arch d${i + 1}`} fill={TERRACOTTA} d={d} />
      ))}
      {LINES.slice(1).map((y, i) => (
        <rect key={y} className={`ob-line d${i + 5}`} fill={TERRACOTTA} x="0" y={y} width="456" height="20" />
      ))}
    </svg>
  );
}
