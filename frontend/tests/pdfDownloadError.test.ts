// J-5 — AC-2: a failed cover-letter/CV PDF download shows the backend's
// own `detail` message, word for word.
//
// api.ts fetches those PDFs with responseType 'blob', so a failed
// download hands the catch a response body that is STILL a Blob — axios
// only parses JSON for JSON responses. apiErrorMessage's
// response.data.detail lookup saw nothing in a Blob, and the banner
// showed axios's "Request failed with status code 500" instead of the
// backend's blocked-check message (J-3's characters-lost text, which
// names the exact characters to fix and, since J-5, where to fix them).
//
// The message-extraction logic lives in src/lib/pdfDownload.ts, which is
// deliberately free of axios, Supabase and '@/' alias imports so this
// file can import it under node --test. The wiring half reads api.ts's
// source: the download path must import that module and use it on
// failure, so it cannot ship unwired.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { downloadErrorMessage, DOWNLOAD_FAILED_MESSAGE } from '../src/lib/pdfDownload.ts';

const HERE = dirname(fileURLToPath(import.meta.url));

// The backend's J-5 blocked-check message, verbatim (the download
// surface's outcome sentence) — a realistic body for a refused download.
const BACKEND_DETAIL =
  'characters lost — cover letter: its text layer lost these characters ' +
  'during rendering: from your profile name: Ł (U+0141). ' +
  'the download was stopped. Please check your name in your profile.';

// What axios puts in err.message for a 500 — the string the user used to
// see instead of the backend's message.
const AXIOS_GENERIC = 'Request failed with status code 500';

// The error shape axios rejects a blob download with: response.data is
// the UNPARSED body.
function failedDownload(body: Blob, status = 500): unknown {
  return { response: { status, data: body }, message: AXIOS_GENERIC };
}

const jsonBlob = (payload: string) => new Blob([payload], { type: 'application/json' });

test('a Blob error body carrying the backend detail is shown word for word', async () => {
  const message = await downloadErrorMessage(
    failedDownload(jsonBlob(JSON.stringify({ detail: BACKEND_DETAIL }))),
  );
  assert.equal(message, BACKEND_DETAIL);
  assert.notEqual(message, AXIOS_GENERIC);
  assert.ok(!message.startsWith('Request failed with status code'), message);
});

test('a body that is not JSON with a string detail still shows a non-empty message', async () => {
  // A proxy's HTML error page (not JSON at all).
  const html = await downloadErrorMessage(
    failedDownload(new Blob(['<html><body>504 Gateway Timeout</body></html>'], { type: 'text/html' }), 504),
  );
  assert.ok(typeof html === 'string' && html.length > 0, `got ${JSON.stringify(html)}`);
  assert.notEqual(html, AXIOS_GENERIC);

  // JSON, but the detail is not a string.
  const objectDetail = await downloadErrorMessage(failedDownload(jsonBlob('{"detail":{"msg":"x"}}')));
  assert.ok(objectDetail.length > 0, `got ${JSON.stringify(objectDetail)}`);

  // JSON with no detail at all.
  const noDetail = await downloadErrorMessage(failedDownload(jsonBlob('{"error":"gone"}')));
  assert.ok(noDetail.length > 0, `got ${JSON.stringify(noDetail)}`);

  // An empty-string detail is not a message.
  const emptyDetail = await downloadErrorMessage(failedDownload(jsonBlob('{"detail":""}')));
  assert.ok(emptyDetail.length > 0, `got ${JSON.stringify(emptyDetail)}`);

  // A transport failure never got a response body to read.
  const offline = await downloadErrorMessage(new Error('Network Error'));
  assert.ok(offline.length > 0, `got ${JSON.stringify(offline)}`);

  // The fixed fallback is the documented sentence, never axios's.
  assert.equal(html, DOWNLOAD_FAILED_MESSAGE);
});

// ---------- the wiring: api.ts's download path uses the module ----------

/** The source of one function in a module, by brace matching — so the
 *  checks below read the download path itself, not its neighbourhood. */
function functionSource(source: string, name: string): string {
  const start = source.indexOf(`async function ${name}`);
  assert.ok(start >= 0, `api.ts no longer defines async function ${name}`);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  assert.fail(`unbalanced braces reading ${name} from api.ts`);
}

test("api.ts's download path imports the module and uses it on failure", async () => {
  const apiSource = readFileSync(join(HERE, '../src/lib/api.ts'), 'utf8');
  const moduleSource = readFileSync(join(HERE, '../src/lib/pdfDownload.ts'), 'utf8');

  // The module stays importable by node --test: no '@/' alias, no axios,
  // no Supabase (checked as substrings, so even a comment mentioning one
  // counts — the constraint is about the module's dependencies).
  assert.ok(!moduleSource.includes('@/'), 'pdfDownload.ts uses a "@/..." alias import');
  assert.ok(!moduleSource.includes('axios'), 'pdfDownload.ts depends on axios');
  assert.ok(!moduleSource.includes('supabase'), 'pdfDownload.ts depends on Supabase');

  // api.ts imports the helper from the module.
  assert.match(apiSource, /import\s*\{[^}]*downloadErrorMessage[^}]*\}\s*from\s*'\.\/pdfDownload'/);

  const download = functionSource(apiSource, 'downloadPdfBlob');
  // The success path is unchanged: still a blob fetch, still handed to
  // the browser via a blob URL and an anchor.
  assert.ok(download.includes("api.get<Blob>(path, { responseType: 'blob' })"), download);
  assert.ok(download.includes('URL.createObjectURL(response.data)'), download);
  // The failure path reads the body through the module's helper — and
  // only there, so a successful download never touches it.
  const catchAt = download.indexOf('catch');
  assert.ok(catchAt >= 0, 'the download path no longer catches failures');
  const helperAt = download.indexOf('downloadErrorMessage(');
  assert.ok(helperAt > catchAt, `the helper is not used on failure:\n${download}`);
  // What the user is shown is the helper's message, not axios's: the
  // catch rethrows an Error carrying it.
  assert.match(download.slice(catchAt), /new Error\(await downloadErrorMessage\(err\)\)/, download);
});

test('a successful download is unchanged: the helper is not on that path', async () => {
  const apiSource = readFileSync(join(HERE, '../src/lib/api.ts'), 'utf8');
  const download = functionSource(apiSource, 'downloadPdfBlob');
  // The fetch (and everything after it) happens before the catch, so the
  // anchor-click success path runs exactly as it did before J-5.
  assert.ok(download.indexOf('anchor.download') > download.indexOf('catch'), download);
});
