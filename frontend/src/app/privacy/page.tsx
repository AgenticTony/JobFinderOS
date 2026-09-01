import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowLeft, Radar } from 'lucide-react';

// OPS-6: the full GDPR Art. 13 information set, as a static route that
// ships in the Cloudflare Pages export (next.config.ts `output: 'export'`
// emits every route under app/ as static HTML — no server needed, this
// page is pure markup).
//
// FACTUAL-ACCURACY RULE for this file: every user-facing claim carries an
// HTML comment naming the code/config/doc it traces to. Nothing here may
// go beyond what those sources say — where a fact is not verifiable from
// the repo (e.g. Z.ai's exact processing region) the copy says only what
// IS verifiable ("outside the EU"). No DPO, address or phone is invented.

export const metadata: Metadata = {
  title: 'Privacy notice · JobFinderOS',
  description:
    'What JobFinderOS collects, who processes it (Z.ai, Supabase, Resend), where it lives, how long we keep it, and your export and deletion rights.',
};

function Section({
  n,
  title,
  children,
}: {
  n: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-line py-8 first:border-t-0 first:pt-0">
      <h2 className="flex items-baseline gap-3 font-display text-xl font-semibold tracking-tight text-hi">
        <span className="num text-sm text-signal">{n}</span>
        {title}
      </h2>
      <div className="mt-3 space-y-3 text-[15px] leading-relaxed text-mid">{children}</div>
    </section>
  );
}

export default function PrivacyPage() {
  return (
    <div className="console-backdrop min-h-dvh bg-ink text-hi">
      <main className="mx-auto max-w-2xl px-6 py-14">
        <Link
          href="/"
          className="mb-2 inline-flex items-center gap-1.5 text-sm text-low transition hover:text-hi"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          Back to home
        </Link>

        <div className="mb-10 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-signal/30 bg-signal/10">
            <Radar className="h-5 w-5 text-signal" aria-hidden />
          </div>
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight">Privacy notice</h1>
            <p className="num text-[10px] uppercase tracking-widest text-low">
              JobFinderOS · beta · August 2026
            </p>
          </div>
        </div>

        <p className="rounded-xl border border-line bg-surface/60 p-4 text-sm leading-relaxed text-mid">
          This notice explains what we collect when you use JobFinderOS, who
          processes it on our behalf, where it goes, how long we keep it, and
          the export and deletion rights built into the app. It is shown at the
          points where we collect your data, and lives at{' '}
          <span className="text-hi">/privacy</span> permanently.
        </p>

        <div className="mt-8">
          {/* Controller identity: owner named in README.md ("Author: Anthony
              Foran"), PRD.md ("Owner: Anthony Foran"). Framing per owner
              decision 2026-09-01: production-grade product, not "personal
              project". No legal entity is claimed — the named individual
              remains the controller (that part is a fact, not tone). */}
          <Section n="01" title="Who is responsible">
            <p>
              JobFinderOS is a production-grade job-search platform built and
              operated by <span className="text-hi">Anthony Foran</span>, who
              is the data controller for everything described here.
            </p>
            {/* Contact: owner-published address 2026-09-01
                (anthony@flutterhive.dev) — interim until dedicated
                JobFinderOS mailboxes exist; in-app tools remain the
                first-class route for data requests. */}
            <p>
              Contact:{' '}
              <span className="text-hi">anthony@flutterhive.dev</span> — for
              anything about your data you can also sign in and use the
              in-app account tools in{' '}
              <span className="text-hi">Settings → Your data</span>. No data
              protection officer has been appointed.
            </p>
          </Section>

          {/* What is collected: account = email + password (register endpoint
              takes exactly those two fields, frontend/src/app/login/page.tsx);
              CV PDF + extracted text (uploadCv -> POST /api/v1/profile/upload);
              profile fields incl. extracted name/email/phone
              (frontend/src/app/app/page.tsx ProfileView, backend Profile
              model); matches/drafts/applications rows (backend models). */}
          <Section n="02" title="What we collect">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <span className="text-hi">Your account</span> — email address
                and password (hashed). No name is required to register.
              </li>
              <li>
                <span className="text-hi">Your CV</span> — the PDF file you
                upload, and the text the system extracts from it.
              </li>
              <li>
                <span className="text-hi">Your profile</span> — the setup you
                choose (country, region, towns, languages, job titles) plus the
                name, email and phone the AI reads out of your CV, which you can
                correct.
              </li>
              <li>
                <span className="text-hi">Matches, drafts and applications</span>{' '}
                — the scores and decisions on jobs we show you, the tailored CV
                and cover letter drafted for each, and a record of applications
                you approved and sent.
              </li>
            </ul>
          </Section>

          <Section n="03" title="Why we process it">
            {/* Purpose: these are the app's actual pipeline stages
                (scrape -> match vs CV -> approve -> tailor -> review -> send,
                frontend/src/app/app/page.tsx header comment). Legal basis is a
                characterization (service you signed up for = contract,
                GDPR Art. 6(1)(b)), not a code fact — kept plain on purpose. */}
            <p>
              Everything above is processed to run the service you signed up
              for: hunting job boards on your settings, scoring jobs against
              your CV, drafting applications for your approval, and sending the
              ones you approve. That processing is necessary to perform that
              service. We do not sell your data, run advertising on it, or build
              profiles for anything outside the app.
            </p>
          </Section>

          <Section n="04" title="Who else processes your data">
            <p>These processors act on our instructions only:</p>
            <ul className="space-y-2.5">
              {/* Z.ai: GLM_BASE_URL = https://api.z.ai/api/coding/paas/v4
                  (backend/app/core/config.py:33); CV text excerpts are sent in
                  match (5,000 chars), tailor (9,000), suggest (6,000) and
                  extract prompts (backend/app/services/ai_service.py:208,297,396)
                  and to the fabrication judge (draft_service.py:171). Exact Z.ai
                  processing region is NOT verifiable from the repo — hence only
                  "outside the EU" is claimed. */}
              <li className="rounded-lg border border-line bg-surface/60 p-3">
                <p className="text-sm font-medium text-hi">
                  Z.ai — AI provider{' '}
                  <span className="font-normal text-signal">· outside the EU</span>
                </p>
                <p className="mt-1 text-sm">
                  To score jobs against your CV, suggest search titles, tailor
                  your CV and cover letter, and fact-check those drafts, parts
                  of your CV text and the job ad are sent to Z.ai&apos;s API
                  (api.z.ai). Z.ai processes this outside the EU. Each call is
                  logged with the endpoint and model it used (no CV text in the
                  log) so the transfer is auditable.
                </p>
                {/* Beta scope + EU migration: owner decision 2026-09-01;
                    the EU endpoint plan is MIGRATION.md MIG-WO5 (Mistral EU
                    regional endpoint hosting the same GLM models, switch
                    armed in config). "By end of beta" is the owner's stated
                    timeline — quoted as a plan, not a completed fact. */}
                <p className="mt-1.5 text-xs leading-relaxed text-low">
                  Beta: during the current beta phase, AI processing runs on
                  Z.ai. We are migrating AI processing to an EU-hosted
                  endpoint of the same models, planned to be in place by the
                  end of the beta phase — this notice will be updated when
                  that change lands.
                </p>
              </li>
              {/* Supabase: DATABASE_URL host ...aws-1-eu-west-1.pooler.supabase.com
                  (docs/deploy/WO-07-runbook.md:70) and "keeps the API→Supabase
                  (eu-west-1) data path intra-EU" (:43). CV files go to a private
                  Supabase Storage bucket (render.yaml STORAGE_BACKEND=supabase,
                  ops/provision_supabase_storage.py). */}
              <li className="rounded-lg border border-line bg-surface/60 p-3">
                <p className="text-sm font-medium text-hi">
                  Supabase — database and file storage{' '}
                  <span className="font-normal text-ok">· EU (eu-west-1, Frankfurt)</span>
                </p>
                <p className="mt-1 text-sm">
                  All account data and the CV file itself live in Supabase
                  (Postgres and a private storage bucket) in the EU, Frankfurt
                  region.
                </p>
              </li>
              {/* Render: region frankfurt for the API web service and the hunt
                  cron (render.yaml, both services). */}
              <li className="rounded-lg border border-line bg-surface/60 p-3">
                <p className="text-sm font-medium text-hi">
                  Render — application hosting{' '}
                  <span className="font-normal text-ok">· EU (Frankfurt)</span>
                </p>
                <p className="mt-1 text-sm">
                  The API and the twice-daily hunt jobs run on Render in
                  Frankfurt, in the same region as the database.
                </p>
              </li>
              {/* Resend: application emails are sent through resend with the
                  tailored CV + cover letter as attachments
                  (backend/app/services/apply_service.py:88-118). */}
              <li className="rounded-lg border border-line bg-surface/60 p-3">
                <p className="text-sm font-medium text-hi">Resend — email delivery</p>
                <p className="mt-1 text-sm">
                  When you approve and send an application by email, it is
                  delivered through Resend to the employer&apos;s published
                  application address, with your tailored CV and cover letter
                  attached.
                </p>
              </li>
              {/* Sentry: init_sentry() is a no-op without SENTRY_DSN
                  (backend/app/main.py:72); render.yaml notes "EU region project
                  when set". Composio: optional, user-initiated Gmail connection
                  (frontend SettingsView / getIntegrations). */}
              <li className="rounded-lg border border-line bg-surface/60 p-3">
                <p className="text-sm font-medium text-hi">Only if you turn them on</p>
                <p className="mt-1 text-sm">
                  Error tracking (Sentry, an EU-region project when enabled) may
                  receive error reports. Connecting your Gmail under Settings
                  uses Composio as the integration layer — that link is created
                  only if you click Connect.
                </p>
              </li>
            </ul>
          </Section>

          <Section n="05" title="Transfers outside the EU">
            {/* Exclusive claims are unsupportable: nothing in the repo
                documents Resend's (or the employer mail system's) region, and
                the optional Composio integration also processes data. Only
                what the setup verifies is stated — the Z.ai transfer
                (config.py GLM_BASE_URL); Resend delivery is named as a flow
                whose location is outside our control, not claimed as EU. */}
            <p>
              The processing outside the EU that our setup verifies is the CV
              text and job-ad text sent to{' '}
              <span className="text-hi">Z.ai</span> for matching, tailoring and
              fact-checking, as described above. Your stored account data and
              CV file remain in the EU (Frankfurt). Applications you approve
              are delivered by Resend and the employer&apos;s own mail system,
              whose locations are outside our control — and once delivered, we
              cannot recall an application you have approved and sent.
            </p>
            {/* Beta scope: same MIG-WO5 migration note as section 04 —
                the Z.ai transfer is a beta-phase state, moving to an EU
                endpoint by end of beta (owner timeline, 2026-09-01). */}
            <p className="text-sm text-low">
              The Z.ai transfer above applies during the beta phase: we are
              moving AI processing to an EU-hosted endpoint of the same models,
              planned to be live by the end of beta, after which CV and job-ad
              text no longer leave the EU for AI processing.
            </p>
          </Section>

          <Section n="06" title="How long we keep it">
            <ul className="list-disc space-y-1.5 pl-5">
              {/* Job pool: MAX_POSTING_AGE_DAYS = 30, "postings older than this
                  are never stored" (backend/app/core/config.py:84), purged in
                  backend/app/services/pipeline.py:581. */}
              <li>
                <span className="text-hi">Job postings</span> — a shared pool of
                scraped ads; nothing older than 30 days is stored.
              </li>
              {/* Account data: kept until the user deletes the account — the
                  delete endpoint is the only removal path
                  (backend/app/api/v1/account.py:17). */}
              <li>
                <span className="text-hi">Your account, CV and applications</span>{' '}
                — kept until you delete your account. Deleting it removes the
                profile, the CV file from storage, matches, drafts,
                applications, your AI usage logs and the account itself.
              </li>
              {/* ai_usage rows (kind/model/endpoint/tokens/cost — no CV text,
                  backend/app/models/ai_usage.py) are written by
                  ai_service.record_ai_usage and are DELETED with the account:
                  the erasure path removes them alongside applications, drafts,
                  matches and profiles (backend/app/api/v1/account.py, PR #3
                  fix/p0-2-gdpr-erasure, commit 3b6d06a — "ai_usage rows are
                  user-linked telemetry ... account death takes its telemetry"). */}
              <li>
                <span className="text-hi">AI usage logs</span> — one row per AI
                call recording the model, endpoint, token counts and cost. These
                rows contain no CV text and are kept for cost and audit purposes
                only while your account exists; they are deleted together with
                your account when you delete it.
              </li>
            </ul>
          </Section>

          <Section n="07" title="Your rights">
            {/* Export: GET /api/v1/account/export returns account, profile,
                matches, drafts (cover_letter, tailored_cv, changes_summary)
                and applications incl. subject/body/target_email
                (backend/app/api/v1/account.py, PR #3 fix/p0-2-gdpr-erasure,
                commit 3b6d06a — draft_row/app_row field lists). */}
            <p>
              <span className="text-hi">Export.</span> In{' '}
              <span className="text-hi">Settings → Your data</span> you can
              download what we hold about you — your account details, profile,
              matches, drafts (including each tailored CV and cover letter) and
              applications (including the subject, body and recipient address of
              every application you sent) — as a JSON file.
            </p>
            {/* Erasure: DELETE /api/v1/account/delete removes applications,
                drafts, matches, profiles, ai_usage rows and the user, then the
                CV file from storage (incl. the Supabase Storage key) after the
                commit (backend/app/api/v1/account.py, PR #3
                fix/p0-2-gdpr-erasure, commit 3b6d06a). Honest limits from the
                same file: job postings stay (shared scraped data) and Composio
                connections "become orphaned on Composio's side" (docstring). */}
            <p>
              <span className="text-hi">Erasure.</span> The same screen deletes
              your account and all personal data with it: your profile, the CV
              file itself, every match, draft and application, your AI usage
              logs, and the account. Two honest limits: job postings stay (they
              are shared scraped data, not personal to you), and a Composio
              integration you connected is left orphaned on Composio&apos;s
              side rather than disconnected.
            </p>
            <p>
              You also have the rights to access, rectify and object to
              processing, and to lodge a complaint with a supervisory authority.
              Use the contact in section 01 for any of these.
            </p>
          </Section>

          <Section n="08" title="Security">
            {/* Auth: JWT bearer tokens, passwords handled by fastapi-users
              (login page + api.ts token layer); per-user isolation is enforced
              with required keyword-only scoping (CLAUDE.md "Per-user data
              isolation"). CV bucket is private (render.yaml comment +
              ops/provision_supabase_storage.py). No over-claiming beyond this. */}
            <p>
              Your password is stored hashed, sign-in uses per-account tokens,
              and every query for profiles, matches, drafts and applications is
              scoped to your account. The CV storage bucket is private — files
              are not publicly reachable.
            </p>
          </Section>
        </div>

        <p className="mt-10 border-t border-line pt-6 text-xs leading-relaxed text-low">
          JobFinderOS is in beta. If this notice and what the app actually does
          ever disagree, treat that as a bug: the in-app export and deletion in
          Settings always operate on the real data.
        </p>
      </main>
    </div>
  );
}
