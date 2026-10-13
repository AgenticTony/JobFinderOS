// The dictionaries' `**emphasis**` convention, parsed. Pure (no JSX) so
// node --test can load it; the React renderer is ./rich.tsx.
export function splitRich(text: string): { text: string; strong: boolean }[] {
  return text
    .split(/\*\*(.+?)\*\*/g)
    .map((part, i) => ({ text: part, strong: i % 2 === 1 }))
    .filter((p) => p.text !== '');
}
