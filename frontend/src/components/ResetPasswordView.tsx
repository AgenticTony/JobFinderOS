'use client';

// MIG-WO2: the password-reset landing page. The reset email links here
// (resetPasswordForEmail's redirectTo); with the PKCE flow the link
// carries ?code=, which the Supabase SDK exchanges for a recovery
// session on load (detectSessionInUrl) — that session is exactly the
// "authenticated to change the password" state, so the form only needs
// updateUser({ password }).
//
// States: waiting (session exchange still running), ready (form),
// done (password set; session survives — straight to the console),
// and the error shapes that can reach a user (link expired/used,
// Supabase unreachable).

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Radar, Loader2, ArrowLeft, ShieldCheck } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import { dicts, type Locale } from '@/i18n/dict';

export default function ResetPasswordView({ locale }: { locale: Locale }) {
  const t = dicts[locale].resetPassword;
  const router = useRouter();
  const [phase, setPhase] = useState<'waiting' | 'ready' | 'done'>('waiting');
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.documentElement.lang = locale;
    let alive = true;
    // The SDK exchanges ?code= on client boot; by the time getSession
    // answers, the recovery session is either there or the link was
    // bad/expired. onAuthStateChange covers the case where the exchange
    // completes slightly after the first getSession read.
    supabase.auth.getSession().then(({ data }) => {
      if (!alive) return;
      if (data.session) setPhase('ready');
      else {
        const timer = setTimeout(() => {
          supabase.auth.getSession().then(({ data: second }) => {
            if (!alive) return;
            if (second.session) setPhase('ready');
            else setError(t.errLink);
          });
        }, 1500);
        return () => clearTimeout(timer);
      }
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locale]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError(t.errMismatch);
      return;
    }
    if (password.length < 8) {
      setError(t.errShort);
      return;
    }
    setBusy(true);
    try {
      // Changing the password terminates all OTHER sessions (Supabase
      // doc behavior); the current one survives to land in /app.
      const { error: updateError } = await supabase.auth.updateUser({ password });
      if (updateError) throw updateError;
      setPhase('done');
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : t.errGeneric);
    } finally {
      setBusy(false);
    }
  };

  const homePath = locale === 'sv' ? '/sv' : '/';

  return (
    <div className='console-backdrop flex min-h-dvh items-center justify-center bg-ink px-4'>
      <div className='w-full max-w-sm'>
        <Link
          href={homePath}
          className='mb-3 inline-flex items-center gap-1.5 text-sm text-low transition hover:text-hi'
        >
          <ArrowLeft className='h-3.5 w-3.5' aria-hidden />
          {t.backHome}
        </Link>
        <div className='w-full rounded-2xl border border-line bg-surface p-8'>
          <Link href={homePath} className='mb-6 flex items-center gap-3' aria-label='JobFinderOS home'>
            <div className='flex h-10 w-10 items-center justify-center rounded-lg border border-signal/30 bg-signal/10'>
              <Radar className='h-5 w-5 text-signal' aria-hidden />
            </div>
            <div>
              <h1 className='font-semibold tracking-tight text-hi'>JobFinderOS</h1>
              <p className='num text-[10px] uppercase tracking-widest text-low'>
                {t.subtitle}
              </p>
            </div>
          </Link>

          {phase === 'waiting' && !error && (
            <p className='flex items-center gap-2 text-sm text-mid'>
              <Loader2 className='h-4 w-4 animate-spin' aria-hidden />
              {t.waiting}
            </p>
          )}

          {error && (
            <div className='mb-4'>
              <p className='rounded-lg bg-bad/10 p-3 text-sm text-hi' role='alert'>
                {error}
              </p>
              <p className='mt-3 text-center text-xs text-low'>
                <Link
                  href={locale === 'sv' ? '/sv/login?mode=forgot' : '/login?mode=forgot'}
                  className='underline underline-offset-2 transition hover:text-mid'
                >
                  {t.requestNew}
                </Link>
              </p>
            </div>
          )}

          {phase === 'ready' && !error && (
            <form onSubmit={submit}>
              <label className='mb-4 block'>
                <span className='mb-1 block text-[10px] uppercase tracking-[0.14em] text-low'>
                  {t.newPassword}
                </span>
                <input
                  type='password'
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete='new-password'
                  className='w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal'
                />
              </label>
              <label className='mb-4 block'>
                <span className='mb-1 block text-[10px] uppercase tracking-[0.14em] text-low'>
                  {t.confirmPassword}
                </span>
                <input
                  type='password'
                  required
                  minLength={8}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  autoComplete='new-password'
                  className='w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal'
                />
              </label>
              <span className='mb-4 block text-[11px] text-low'>{t.minChars}</span>
              <button
                type='submit'
                disabled={busy}
                className='inline-flex w-full items-center justify-center gap-2 rounded-lg bg-signal px-4 py-2.5 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50'
              >
                {busy ? <Loader2 className='h-4 w-4 animate-spin' /> : null}
                {busy ? t.busy : t.submit}
              </button>
            </form>
          )}

          {phase === 'done' && (
            <div className='text-center'>
              <div className='mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-signal/30 bg-signal/10'>
                <ShieldCheck className='h-6 w-6 text-signal' aria-hidden />
              </div>
              <p className='mb-6 text-sm text-mid'>{t.done}</p>
              <button
                type='button'
                onClick={() => router.push('/app')}
                className='inline-flex items-center justify-center rounded-lg bg-signal px-4 py-2.5 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98]'
              >
                {t.toConsole}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
