// MIG-WO2: the Supabase Auth client. The browser talks to Supabase
// directly — signUp, signInWithPassword, resetPasswordForEmail and the
// PKCE email-confirmation exchange all happen here; the backend never
// sees a password. All it wants per request is the session's access
// token (an ES256 Supabase JWT it verifies against its JWKS).
//
// Env vars are PUBLIC by design (NEXT_PUBLIC_ prefix) — they name the
// project, they are not secrets. The ANON key is a publishable key;
// the service-role key must NEVER appear in this frontend.

import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  // Fail at USE time, not import time — `next build` must succeed on
  // machines without the env (CI builds the static export without
  // secrets; the login page renders a clear error instead of the build
  // breaking).
  console.error(
    'Supabase auth is not configured: set NEXT_PUBLIC_SUPABASE_URL and ' +
      'NEXT_PUBLIC_SUPABASE_ANON_KEY (frontend/.env.local locally, Pages ' +
      'env vars in production).'
  );
}

export const supabase = createClient(
  SUPABASE_URL ?? 'https://unset.supabase.co',
  SUPABASE_ANON_KEY ?? 'unset-anon-key',
  {
    auth: {
      // PKCE: the confirmation/reset email links carry a code the SDK
      // exchanges on page load (detectSessionInUrl) — no implicit-flow
      // tokens in URLs, and it works under the static export (no server
      // routes needed).
      flowType: 'pkce',
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  }
);

/** The session's access token for the API's Authorization header, or
 * null when signed out. The SDK refreshes short-lived tokens ahead of
 * expiry (autoRefreshToken) — this is the only token source now; the
 * old jfos-token localStorage key is dead. */
export async function getSessionToken(): Promise<string | null> {
  const { data, error } = await supabase.auth.getSession();
  if (error) return null;
  return data.session?.access_token ?? null;
}
