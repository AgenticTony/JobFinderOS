'use client';

// The localized login/create-account/forgot-password form (MIG-WO2:
// Supabase Auth hosts the flows; this form calls the SDK, never the
// backend — the backend only ever sees the session's access token).
//
// - signin: signInWithPassword → /app
// - register: signUp → with confirmation emails ON (the project's
//   mailer_autoconfirm=false) the account needs an email click first;
//   the form says so instead of pretending to sign in.
// - forgot: resetPasswordForEmail → the reset link lands on
//   /reset-password (ResetPasswordView), which is where the new
//   password is set. Supabase's anti-enumeration contract: unknown
//   emails still "succeed" — the form always says check your inbox.

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Radar, Loader2, ArrowLeft, MailCheck } from 'lucide-react';
import { track } from '@/lib/analytics';
import { apiErrorMessage } from '@/lib/api';
import { supabase } from '@/lib/supabase';
import { dicts, type Locale } from '@/i18n/dict';
import { shouldRedirectToSv, switchLocale } from '@/i18n/locale';

type Mode = 'signin' | 'register' | 'forgot';

export default function LoginView({ locale }: { locale: Locale }) {
  const t = dicts[locale].login;
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentMail, setSentMail] = useState(false);

  // /login?mode=register — every "Get started" entry point lands new
  // users on the create-account form, not a sign-in wall.
  useEffect(() => {
    document.documentElement.lang = locale;
    const wanted = new URLSearchParams(window.location.search).get('mode');
    if (wanted === 'register' || wanted === 'forgot') {
      setMode(wanted);
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

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === 'forgot') {
        const redirectTo =
          locale === 'sv'
            ? `${window.location.origin}/sv/reset-password`
            : `${window.location.origin}/reset-password`;
        const { error: resetError } = await supabase.auth.resetPasswordForEmail(
          email,
          { redirectTo }
        );
        if (resetError) throw resetError;
        setSentMail(true); // unknown accounts "succeed" too — no enumeration
        return;
      }

      if (mode === 'register') {
        const { data, error: signUpError } = await supabase.auth.signUp({
          email,
          password,
        });
        if (signUpError) throw signUpError;
        track('signup_completed', { locale });
        // Confirmation emails are ON: no session until the email click.
        // Tell the user instead of falling through to a failed sign-in.
        if (!data.session) {
          setSentMail(true);
          return;
        }
        router.push('/app');
        return;
      }

      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (signInError) throw signInError;
      router.push('/app');
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? translateAuthError(err.message, t)
          : apiErrorMessage(err)
      );
    } finally {
      setBusy(false);
    }
  };

  const homePath = locale === 'sv' ? '/sv' : '/';
  const otherLocale: Locale = locale === 'en' ? 'sv' : 'en';

  if (sentMail) {
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
          <div className='w-full rounded-2xl border border-line bg-surface p-8 text-center'>
            <div className='mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-signal/30 bg-signal/10'>
              <MailCheck className='h-6 w-6 text-signal' aria-hidden />
            </div>
            <h1 className='mb-2 font-semibold tracking-tight text-hi'>
              {t.checkEmailTitle}
            </h1>
            <p className='mb-6 text-sm text-mid'>{t.checkEmailBody}</p>
            <button
              type='button'
              onClick={() => {
                setSentMail(false);
                setMode('signin');
                setPassword('');
              }}
              className='text-sm font-medium text-signal underline underline-offset-4 transition hover:text-signal/80'
            >
              {t.backToSignin}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const subtitle =
    mode === 'signin'
      ? t.subtitleSignin
      : mode === 'register'
        ? t.subtitleRegister
        : t.subtitleForgot;

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
        <form
          onSubmit={submit}
          className='w-full rounded-2xl border border-line bg-surface p-8'
        >
          <Link href={homePath} className='mb-6 flex items-center gap-3' aria-label='JobFinderOS home'>
            <div className='flex h-10 w-10 items-center justify-center rounded-lg border border-signal/30 bg-signal/10'>
              <Radar className='h-5 w-5 text-signal' aria-hidden />
            </div>
            <div>
              <h1 className='font-semibold tracking-tight text-hi'>JobFinderOS</h1>
              <p className='num text-[10px] uppercase tracking-widest text-low'>
                {subtitle}
              </p>
            </div>
          </Link>

        <label className='mb-4 block'>
          <span className='mb-1 block text-[10px] uppercase tracking-[0.14em] text-low'>
            {t.email}
          </span>
          <input
            type='email'
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete='email'
            className='w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal'
          />
        </label>

        {mode !== 'forgot' && (
          <label className='mb-4 block'>
            <span className='mb-1 block text-[10px] uppercase tracking-[0.14em] text-low'>
              {t.password}
            </span>
            <input
              type='password'
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              className='w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal'
            />
            {mode === 'register' && (
              <span className='mt-1 block text-[11px] text-low'>{t.minChars}</span>
            )}
          </label>
        )}

        {error && (
          <p className='mb-4 rounded-lg bg-bad/10 p-3 text-sm text-hi' role='alert'>
            {error}
          </p>
        )}

        <button
          type='submit'
          disabled={busy}
          className='inline-flex w-full items-center justify-center gap-2 rounded-lg bg-signal px-4 py-2.5 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50'
        >
          {busy ? <Loader2 className='h-4 w-4 animate-spin' /> : null}
          {busy
            ? mode === 'signin'
              ? t.busySignin
              : mode === 'register'
                ? t.busyRegister
                : t.busyForgot
            : mode === 'signin'
              ? t.submitSignin
              : mode === 'register'
                ? t.submitRegister
                : t.submitForgot}
        </button>

        {mode === 'signin' && (
          <p className='mt-3 text-center'>
            <button
              type='button'
              onClick={() => {
                setMode('forgot');
                setError(null);
              }}
              className='text-xs text-low underline underline-offset-2 transition hover:text-mid'
            >
              {t.forgotLink}
            </button>
          </p>
        )}

        <p className='mt-4 text-center text-xs text-low'>
          {mode === 'signin' ? (
            <>
              {t.newHere}{' '}
              <button
                type='button'
                onClick={() => {
                  setMode('register');
                  setError(null);
                }}
                className='font-medium text-signal underline underline-offset-4 transition hover:text-signal/80'
              >
                {t.createAccount}
              </button>
            </>
          ) : (
            <>
              {t.alreadyHunting}{' '}
              <button
                type='button'
                onClick={() => {
                  setMode('signin');
                  setError(null);
                }}
                className='font-medium text-signal underline underline-offset-4 transition hover:text-signal/80'
              >
                {t.signIn}
              </button>
            </>
          )}
        </p>

        <p className='mt-2 text-center text-xs text-low'>
          {t.footerLine} ·{' '}
          <Link href='/privacy' className='underline underline-offset-2 transition hover:text-mid'>
            {t.privacy}
          </Link>
        </p>

        {/* Language toggle — same rule as the landing footer: explicit
            choice beats auto-detection and sticks. */}
        <p className='mt-3 text-center'>
          <button
            type='button'
            onClick={() => switchLocale(otherLocale, window.location.pathname)}
            className='text-xs text-low underline underline-offset-2 transition hover:text-mid'
          >
            {dicts[locale].langToggle}
          </button>
        </p>
        </form>
      </div>
    </div>
  );
}

// Supabase error messages are English/technical — map the ones a user
// can actually trigger to the dictionary; anything unknown passes
// through (the message is still the honest answer).
function translateAuthError(message: string, t: (typeof dicts)['en']['login']): string {
  const m = message.toLowerCase();
  if (m.includes('invalid login credentials')) return t.errWrong;
  if (m.includes('already registered') || m.includes('already been registered')) {
    return t.errExists;
  }
  if (m.includes('password should be') || m.includes('at least')) return t.errWeakPassword;
  if (m.includes('email not confirmed')) return t.errUnconfirmed;
  return message;
}
