// Dates are calendar days ("2026-11-02") from the engine; format them without
// time-zone shifts by building a local date from the parts.
export function longDay(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(year ?? 1970, (month ?? 1) - 1, day ?? 1);
  return date.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
}

export function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];

export function countWord(count: number): string {
  return WORDS[count] ?? String(count);
}
