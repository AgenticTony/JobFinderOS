// The message a failed PDF download shows the user (J-5).
//
// api.ts fetches the cover-letter and CV PDFs as blobs, so when the
// download fails, the response body the catch receives is STILL a Blob —
// the HTTP client only parses JSON for JSON responses. Running that
// error through apiErrorMessage saw no `detail` in a Blob, and the
// banner ended up showing the client's own "Request failed with status
// code 500" — burying the backend's blocked-check message, which names
// the exact characters to fix and, since J-5, where to fix them.
//
// This module reads the body (a Blob has to be awaited as text before it
// can be parsed) and returns the backend's `detail` word for word, or a
// fixed sentence when there is nothing to surface. It is kept free of
// the HTTP client, the auth SDK and any path-alias import so node --test
// can import it directly — the same discipline as aiJobPoll.ts.

/** Shown when the failed response carries no message to surface (a
 *  transport failure, or a body that is not JSON with a string `detail`)
 *  — a real sentence, never the client's status-code string. */
export const DOWNLOAD_FAILED_MESSAGE = 'The download could not be completed.';

type JsonBody = { detail?: unknown };

/** A Blob (or anything shaped like one) — the unparsed body of a failed
 *  blob download. */
function isBlobLike(value: unknown): value is { text(): Promise<string> } {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { text?: unknown }).text === 'function'
  );
}

/** The `detail` of a body's JSON text, or undefined when it is not JSON
 *  (an HTML error page, an empty body) or has no detail. */
function detailOf(bodyText: string): unknown {
  try {
    const parsed: unknown = JSON.parse(bodyText);
    if (parsed && typeof parsed === 'object') return (parsed as JsonBody).detail;
  } catch {
    // not JSON — nothing to surface
  }
  return undefined;
}

/** The user-facing message for a failed PDF download: the backend's
 *  `detail` word for word when the response body carries one (whatever
 *  shape the body arrived in), else the fixed non-empty sentence. */
export async function downloadErrorMessage(error: unknown): Promise<string> {
  const body = (error as { response?: { data?: unknown } })?.response?.data;
  let detail: unknown;
  if (typeof body === 'string') {
    detail = detailOf(body);
  } else if (isBlobLike(body)) {
    detail = detailOf(await body.text());
  } else if (body && typeof body === 'object') {
    detail = (body as JsonBody).detail;
  }
  if (typeof detail === 'string' && detail.length > 0) return detail;
  return DOWNLOAD_FAILED_MESSAGE;
}
