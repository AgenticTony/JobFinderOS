// FE-21: last-resort error surface. Every mutating action in the console
// catches its own errors, but anything that slips through (a forgotten
// await, a promise from a lib) used to die as an unhandled rejection —
// the UI just spun or did nothing, which on a cold-starting free-tier
// backend was the NORM, not the exception.
//
// This module installs one `unhandledrejection` listener that:
//   1. always console.errors the reason (dev tools never lose it), and
//   2. re-broadcasts it as a `jfos-unhandled-error` CustomEvent, which
//      the hunting console (app/app/page.tsx) listens for and routes into
//      the same action-error banner the explicit catches use — one error
//      surface, not a new one.
//
// Deliberately no `error` (window.onerror) listener: render errors are
// Next's job (app/error.tsx boundary); this catches PROMISES only.

export const UNHANDLED_ERROR_EVENT = 'jfos-unhandled-error';

function reasonToMessage(reason: unknown): string {
  // Reuse axios detail surfacing lazily — the api module owns that logic,
  // and importing it here would couple this tiny module to axios. A plain
  // Error.message covers the axios case too (its message is descriptive
  // enough for a fallback banner; the precise `detail` strings come from
  // the per-action catches, which stay the primary surface).
  if (reason instanceof Error && reason.message) return reason.message;
  if (typeof reason === 'string' && reason) return reason;
  try {
    return JSON.stringify(reason);
  } catch {
    return 'Unknown error';
  }
}

let installed = false;

/** Install the global listener (idempotent — safe under React 18 double-mount). */
export function installGlobalErrorReporter(): void {
  if (typeof window === 'undefined' || installed) return;
  installed = true;
  window.addEventListener('unhandledrejection', (event) => {
    const message = reasonToMessage(event.reason);
    // Keep dev-tools visibility — the banner is for the user, the console
    // for us. No preventDefault(): the browser keeps its own log too.
    console.error('[JobFinderOS] Unhandled promise rejection:', event.reason);
    window.dispatchEvent(
      new CustomEvent<string>(UNHANDLED_ERROR_EVENT, { detail: message })
    );
  });
}
