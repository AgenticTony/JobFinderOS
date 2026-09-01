'use client';

// The localized login/create-account form. The Phase 1b token layer's
// entry point: stores the JWT in localStorage; the axios interceptor
// (lib/api.ts) attaches it to every request and redirects here on 401.
// The create-account mode posts to the fastapi-users register endpoint,
// then signs straight in. The console after sign-in is English for beta.

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Radar, Loader2, ArrowLeft } from 'lucide-react';
import { track } from '@/lib/analytics';
import { api, apiErrorMessage, setAuthToken } from '@/lib/api';
import { dicts, type Locale } from '@/i18n/dict';
import { shouldRedirectToSv, switchLocale } from '@/i18n/locale';

export default function LoginView({ locale }: { locale: Locale }) {
  const t = dicts[locale].login;
  const router = useRouter();
  const [mode, setMode] = useState<'signin' | 'register'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // /login?mode=register — every "Get started" entry point lands new
  // users on the create-account form, not a sign-in wall.
  useEffect(() => {
    document.documentElement.lang = locale;
    if (new URLSearchParams(window.location.search).get('mode') === 'register') {
      setMode('register');
    }
    // First-visit detection on the EN route only — mirrors LandingView.
    if (
      locale === 'en' &&
      window.location.pathname === '/login' &&
      shouldRedirectToSv()
    ) {
      window.location.replace(`/sv/login${window.location.search}`);
    }
  }, [locale]);

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
        track('signup_completed', { locale });
      }
      await signIn(email, password);
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 400 && mode === 'signin') {
        setError(t.errWrong);
      } else if (status === 400 && mode === 'register') {
        setError(t.errExists);
      } else {
        setError(apiErrorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const homePath = locale === 'sv' ? '/sv' : '/';
  const otherLocale: Locale = locale === 'en' ? 'sv' : 'en';

  return (
    <div className="console-backdrop flex min-h-dvh items-center justify-center bg-ink px-4">
      <div className="w-full max-w-sm">
        <Link
          href={homePath}
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-low transition hover:text-hi"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          {t.backHome}
        </Link>
        <form
          onSubmit={submit}
          className="w-full rounded-2xl border border-line bg-surface p-8"
        >
          <Link href={homePath} className="mb-6 flex items-center gap-3" aria-label="JobFinderOS home">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-signal/30 bg-signal/10">
              <Radar className="h-5 w-5 text-signal" aria-hidden />
            </div>
            <div>
              <h1 className="font-semibold tracking-tight text-hi">JobFinderOS</h1>
              <p className="num text-[10px] uppercase tracking-widest text-low">
                {mode === 'signin' ? t.subtitleSignin : t.subtitleRegister}
              </p>
            </div>
          </Link>

        <label className="mb-4 block">
          <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
            {t.email}
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
            {t.password}
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
            <span className="mt-1 block text-[11px] text-low">{t.minChars}</span>
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
              ? t.busySignin
              : t.busyRegister
            : mode === 'signin'
              ? t.submitSignin
              : t.submitRegister}
        </button>

        <p className="mt-4 text-center text-xs text-low">
          {mode === 'signin' ? (
            <>
              {t.newHere}{' '}
              <button
                type="button"
                onClick={() => {
                  setMode('register');
                  setError(null);
                }}
                className="font-medium text-signal underline underline-offset-4 transition hover:text-signal/80"
              >
                {t.createAccount}
              </button>
            </>
          ) : (
            <>
              {t.alreadyHunting}{' '}
              <button
                type="button"
                onClick={() => {
                  setMode('signin');
                  setError(null);
                }}
                className="font-medium text-signal underline underline-offset-4 transition hover:text-signal/80"
              >
                {t.signIn}
              </button>
            </>
          )}
        </p>

        <p className="mt-2 text-center text-xs text-low">
          {t.footerLine} ·{' '}
          <Link href="/privacy" className="underline underline-offset-2 transition hover:text-mid">
            {t.privacy}
          </Link>
        </p>

        {/* Language toggle — same rule as the landing footer: explicit
            choice beats auto-detection and sticks. */}
        <p className="mt-3 text-center">
          <button
            type="button"
            onClick={() => switchLocale(otherLocale, window.location.pathname)}
            className="text-xs text-low underline underline-offset-2 transition hover:text-mid"
          >
            {dicts[locale].langToggle}
          </button>
        </p>
        </form>
      </div>
    </div>
  );
}
