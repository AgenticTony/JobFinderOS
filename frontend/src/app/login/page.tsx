'use client';

// Login — the Phase 1b token layer's entry point. Stores the JWT in
// localStorage; the axios interceptor (lib/api.ts) attaches it to every
// request and redirects here on 401.

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Radar, Loader2 } from 'lucide-react';
import { api, apiErrorMessage, setAuthToken } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = new URLSearchParams({ username: email, password });
      const res = await api.post('/api/v1/auth/jwt/login', body, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });
      setAuthToken(res.data.access_token);
      router.push('/');
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      setError(status === 400 ? 'Wrong email or password' : apiErrorMessage(err));
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
              TalentHive engine
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
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            className="w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
          />
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
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="mt-4 text-center text-xs text-low">
          Hunts twice daily · scores honestly · nothing sent without you
        </p>
      </form>
    </div>
  );
}
