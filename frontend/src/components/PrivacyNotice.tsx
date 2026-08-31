import Link from 'next/link';

// OPS-6: the point-of-collection privacy disclosure (GDPR Art. 13).
// Informational only — no checkbox, nothing blocking. Two placements:
//   1. Under the CV upload dropzone (CvUpload) — the moment the CV is
//      collected.
//   2. Below the login/register form — the moment the account is created.
// Both link to /privacy for the full Art. 13 set.
//
// Every statement here must stay in sync with /privacy, whose claims are
// individually source-annotated in src/app/privacy/page.tsx:
//   - Z.ai processes CV text outside the EU   (backend/app/core/config.py GLM_BASE_URL)
//   - Supabase DB + Storage in eu-west-1      (docs/deploy/WO-07-runbook.md, render.yaml)
//   - Resend sends outbound applications      (backend/app/services/apply_service.py)
//   - Export + erasure exist                  (backend/app/api/v1/account.py)

const PROCESSORS = [
  {
    name: 'Z.ai',
    role: 'AI matching, tailoring and fact-checking — your CV text is sent to it',
    where: 'outside the EU',
  },
  {
    name: 'Supabase',
    role: 'database and CV file storage',
    where: 'EU (Frankfurt)',
  },
  {
    name: 'Resend',
    role: 'delivers the applications you approve',
    where: '',
  },
] as const;

export default function PrivacyNotice({ context }: { context: 'cv' | 'account' }) {
  return (
    <aside
      aria-label="How your data is used"
      className="mt-3 rounded-xl border border-line bg-surface/60 p-4"
    >
      <p className="num text-[10px] uppercase tracking-[0.14em] text-low">
        Where your data goes
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-mid">
        {context === 'cv' ? (
          <>
            Your CV&apos;s text is stored with your account and sent to our AI
            provider to match, tailor and fact-check applications.{' '}
          </>
        ) : (
          <>
            We store your email and password, and once you upload a CV its text
            is used to match and tailor applications.{' '}
          </>
        )}
        <span className="text-hi">Nothing is sent to an employer without your approval.</span>
      </p>
      <ul className="mt-2.5 space-y-1">
        {PROCESSORS.map((p) => (
          <li key={p.name} className="flex flex-wrap items-baseline gap-x-1.5 text-xs text-low">
            <span className="font-medium text-mid">{p.name}</span>
            <span>— {p.role}</span>
            {p.where && <span className="text-mid">({p.where})</span>}
          </li>
        ))}
      </ul>
      <p className="mt-2.5 text-xs text-low">
        You can export or delete everything (including your CV) in{' '}
        <span className="text-mid">Settings → Your data</span>. Full details:{' '}
        <Link
          href="/privacy"
          className="font-medium text-signal underline underline-offset-4 transition hover:text-signal/80"
        >
          Privacy notice
        </Link>
        .
      </p>
    </aside>
  );
}
