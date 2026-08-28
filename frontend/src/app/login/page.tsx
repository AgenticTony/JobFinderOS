'use client';

// Login — the Phase 1b token layer's entry point. Stores the JWT in
// localStorage; the axios interceptor (lib/api.ts) attaches it to every
// request and redirects here on 401. The create-account mode posts to the
// fastapi-users register endpoint, then signs straight in.

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Radar, Loader2 } from 'lucide-react';
import { api, apiErrorMessage, setAuthToken } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<'signin' | 'register'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signIn = async (username: string, pwd: string) => {
    const body = new URLSearchParams({ username, password: pwd });
    const res = await api.post('/api/v1/auth/jwt/login', body, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    setAuthToken(res.data.access_token);
    router.push('/app');
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === 'register') {
        await api.post('/api/v1/auth/register', { email, password });
      }
      await signIn(email, password);
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 400 && mode === 'signin') {
        setError('Wrong email or password');
      } else if (status === 400 && mode === 'register') {
        setError('That email already has an account, or the password is too short');
      } else {
        setError(apiErrorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="console-backdrop flex min-h-dvh items-center justify-center bg-ink px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-line bg-surface p-8"
      >
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-signal/30 bg-signal/10">
            <Radar className="h-5 w-5 text-signal" aria-hidden />
          </div>
          <div>
            <h1 className="font-semibold tracking-tight text-hi">JobFinderOS</h1>
            <p className="num text-[10px] uppercase tracking-widest text-low">
              {mode === 'signin' ? 'Sign in to the console' : 'Create your account'}
            </p>
          </div>
        </div>

        <label className="mb-4 block">
          <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
            Email
          </span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            className="w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
          />
        </label>

        <label className="mb-4 block">
          <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
            Password
          </span>
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            className="w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
          />
          {mode === 'register' && (
            <span className="mt-1 block text-[11px] text-low">
              At least 8 characters.
            </span>
          )}
        </label>

        {error && (
          <p className="mb-4 rounded-lg bg-bad/10 p-3 text-sm text-hi" role="alert">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-signal px-4 py-2.5 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {busy
            ? mode === 'signin'
              ? 'Signing in…'
              : 'Creating account…'
            : mode === 'signin'
              ? 'Sign in'
              : 'Create account and start hunting'}
        </button>

        <p className="mt-4 text-center text-xs text-low">
          {mode === 'signin' ? (
            <>
              New here?{' '}
              <button
                type="button"
                onClick={() => {
                  setMode('register');
                  setError(null);
                }}
                className="font-medium text-signal underline underline-offset-4 transition hover:text-signal/80"
              >
                Create an account
              </button>
            </>
          ) : (
            <>
              Already hunting with us?{' '}
              <button
                type="button"
                onClick={() => {
                  setMode('signin');
                  setError(null);
                }}
                className="font-medium text-signal underline underline-offset-4 transition hover:text-signal/80"
              >
                Sign in
              </button>
            </>
          )}
        </p>

        <p className="mt-2 text-center text-xs text-low">
          Hunts hourly · scores honestly · nothing sent without you
        </p>
      </form>
    </div>
  );
}
