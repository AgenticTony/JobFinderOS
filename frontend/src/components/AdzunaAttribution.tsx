// Adzuna API attribution — required by their Terms of Service wherever
// Adzuna-sourced job listings are displayed ("Jobs by Adzuna", linked).

export default function AdzunaAttribution() {
  return (
    <a
      href="https://www.adzuna.co.uk"
      target="_blank"
      rel="noopener noreferrer"
      className="ml-1.5 inline-flex items-center gap-0.5 text-[10px] text-zinc-600 hover:text-zinc-400"
      title="Jobs by Adzuna"
    >
      Jobs by <span className="font-semibold">Adzuna</span>
    </a>
  );
}
