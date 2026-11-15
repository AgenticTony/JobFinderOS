// Wizard titles step: fresh suggestions replace the previous strategy's
// picks, but never a title the user typed and still has ticked — the
// manual input stays usable while suggestions load (PR #153 review,
// 2026-11-11: a title added during the wait was silently unticked).

export function mergeSuggestedSelection(
  prev: Set<string>,
  customTitles: string[],
  suggested: string[],
): Set<string> {
  const keptCustom = [...prev].filter((q) => customTitles.includes(q));
  return new Set([...suggested, ...keptCustom]);
}
