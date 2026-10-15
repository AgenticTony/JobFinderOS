// OPS-6: the full GDPR Art. 13 information set, in both languages.
// Rendered by components/PrivacyView.tsx at /privacy (EN) and
// /sv/privacy (SV).
//
// FACTUAL-ACCURACY RULE for this file: every user-facing claim carries a
// comment naming the code/config/doc it traces to. Nothing here may go
// beyond what those sources say — where a fact is not verifiable from the
// repo (e.g. Z.ai's exact processing region) the copy says only what IS
// verifiable ("outside the EU"). No DPO, address or phone is invented.
// The Swedish text is a translation of the English, claim for claim —
// it may not add, drop or soften a claim. Change both or neither.
//
// `**text**` = emphasis (rendered as text-hi by rich()).

export type PrivacyBlock =
  | { kind: "p"; text: string; muted?: boolean }
  | { kind: "list"; items: string[] }
  | {
      kind: "processors";
      intro: string;
      items: {
        name: string;
        where: string;
        tone: "ok" | "signal";
        body: string;
        note?: string;
      }[];
    };

export const privacyEn = {
  metaTitle: "Privacy notice · JobFinderOS",
  metaDescription:
    "What JobFinderOS collects, who processes it, where it lives, how long we keep it, and your export and deletion rights.",
  backHome: "Back to home",
  title: "Privacy notice",
  stamp: "JobFinderOS · beta · August 2026",
  intro:
    "This notice explains what we collect when you use JobFinderOS, who processes it on our behalf, where it goes, how long we keep it, and the export and deletion rights built into the app. It is shown at the points where we collect your data, and lives at **/privacy** permanently.",
  sections: [
    {
      n: "01",
      title: "Who is responsible",
      blocks: [
        // Controller presentation: owner decision 2026-09-01 — the public
        // face is "the JobFinderOS team" (repo owner Anthony Foran remains
        // the controller in fact; the contact address reaches them). No
        // legal entity is claimed.
        {
          kind: "p",
          text: "JobFinderOS is a production-grade job-search platform built and operated by **the JobFinderOS team**, the data controller for everything described here.",
        },
        // Contact: owner-published address 2026-09-01 — interim until
        // dedicated JobFinderOS mailboxes exist; in-app tools remain the
        // first-class route for data requests.
        {
          kind: "p",
          text: "Contact: **anthony@flutterhive.dev** — for anything about your data you can also sign in and use the in-app account tools in **Settings → Your data**. No data protection officer has been appointed.",
        },
      ],
    },
    {
      n: "02",
      title: "What we collect",
      blocks: [
        // Account = email + password (Supabase Auth signUp takes exactly
        // those two, components/LoginView.tsx); CV file + extracted text
        // (uploadCv -> POST /api/v1/profile/upload); profile fields incl.
        // extracted name/email/phone (console ProfileView, backend Profile
        // model); matches/drafts/applications rows (backend models);
        // analytics consent-gated (components/AnalyticsGate.tsx).
        {
          kind: "list",
          items: [
            "**Your account** — email address and password (hashed). No name is required to register.",
            "**Your CV** — the file you upload (PDF or Word), and the text the system extracts from it.",
            "**Your profile** — the setup you choose (country, region, towns, languages, job titles) plus the name, email and phone the AI reads out of your CV, which you can correct.",
            "**Matches, drafts and applications** — the scores and decisions on jobs we show you, the tailored CV and cover letter drafted for each, and a record of applications you approved and sent.",
            "**Site analytics** — Google Analytics cookies measuring which pages and features are used, so we can see what to improve. Analytics loads only after you accept it in the consent banner — decline and no analytics script runs at all. Google processes this measurement data, which can involve transfers outside the EU.",
          ],
        },
      ],
    },
    {
      n: "03",
      title: "Why we process it",
      blocks: [
        // Purpose: the app's actual pipeline stages (scrape -> match vs CV
        // -> approve -> tailor -> review -> send, app/app/page.tsx header).
        // Legal basis is a characterization (contract, GDPR Art. 6(1)(b)),
        // not a code fact — kept plain on purpose.
        {
          kind: "p",
          text: "Everything above is processed to run the service you signed up for: hunting job boards on your settings, scoring jobs against your CV, drafting applications for your approval, and sending the ones you approve. That processing is necessary to perform that service. We do not sell your data, run advertising on it, or build profiles for anything outside the app.",
        },
      ],
    },
    {
      n: "04",
      title: "Who else processes your data",
      blocks: [
        {
          kind: "processors",
          intro: "These processors act on our instructions only:",
          items: [
            // Z.ai: GLM_BASE_URL = https://api.z.ai/api/coding/paas/v4
            // (backend/app/core/config.py); CV text excerpts are sent in the
            // match, tailor, suggest and extract prompts
            // (backend/app/services/ai_service.py) and to the fabrication
            // judge (draft_service.py). Exact Z.ai region NOT verifiable —
            // hence only "outside the EU". Beta note: owner decision
            // 2026-09-01; EU endpoint plan = MIGRATION.md MIG-WO5, quoted
            // as a plan, not a completed fact.
            {
              name: "Z.ai — AI provider",
              where: "outside the EU",
              tone: "signal",
              body: "To score jobs against your CV, suggest search titles, tailor your CV and cover letter, and fact-check those drafts, parts of your CV text and the job ad are sent to Z.ai's API (api.z.ai). Z.ai processes this outside the EU. Each call is logged with the endpoint and model it used (no CV text in the log) so the transfer is auditable.",
              note: "Beta: during the current beta phase, AI processing runs on Z.ai. We are migrating AI processing to an EU-hosted endpoint of the same models, planned to be in place by the end of the beta phase — this notice will be updated when that change lands.",
            },
            // Supabase: DATABASE_URL host aws-1-eu-west-1.pooler.supabase.com
            // (docs/deploy/WO-07-runbook.md); CV files in a private Supabase
            // Storage bucket (render.yaml STORAGE_BACKEND=supabase,
            // ops/provision_supabase_storage.py).
            {
              name: "Supabase — database and file storage",
              where: "EU (eu-west-1, Frankfurt)",
              tone: "ok",
              body: "All account data and the CV file itself live in Supabase (Postgres and a private storage bucket) in the EU, Frankfurt region.",
            },
            // Render: region frankfurt for the API and the hunt cron
            // (render.yaml, both services).
            {
              name: "Render — application hosting",
              where: "EU (Frankfurt)",
              tone: "ok",
              body: "The API and the twice-daily hunt jobs run on Render in Frankfurt, in the same region as the database.",
            },
            // Email apply (owner decision 2026-09-01): applications ship from
            // the USER'S OWN connected Gmail — no platform sender. Sending is
            // off during beta (EMAIL_APPLY_ENABLED gate); browser and manual
            // apply remain available.
            {
              name: "Your own Gmail — application emails",
              where: "optional, you connect it",
              tone: "signal",
              body: "Email applications are sent from **your own Gmail account**, which you connect under Settings — they carry your address, never ours. Sending runs through Google's infrastructure. During the beta rollout of this feature, email sending is briefly offline; browser and manual apply are available throughout.",
            },
            // Onboarding emails (owner decision 2026-09-03): signup creates a
            // Resend contact and fires user.created; a Resend automation
            // sends ~6 daily emails (ONBOARDING_EMAILS_ENABLED gate).
            // Resend domain region eu-west-1 (Ireland). Unsubscribe link in
            // every email; account deletion calls remove_contact.
            {
              name: "Resend — onboarding emails",
              where: "EU (eu-west-1, Ireland)",
              tone: "ok",
              body: "When you sign up we send a short series of onboarding emails (about six, one per day) explaining each part of the platform. Your email address is stored with our email provider, Resend, for this purpose. Every email has an **unsubscribe link** — using it stops the series and nothing else about your account. Deleting your account removes the contact entirely.",
            },
            // Sentry: init_sentry() is a no-op without SENTRY_DSN
            // (backend/app/main.py); render.yaml notes "EU region project
            // when set". Composio: optional, user-initiated Gmail connection
            // (console SettingsView / getIntegrations).
            {
              name: "Only if you turn them on",
              where: "",
              tone: "ok",
              body: "Error tracking (Sentry, an EU-region project when enabled) may receive error reports. Connecting your Gmail under Settings uses Composio as the integration layer — that link is created only if you click Connect. With Gmail connected, the platform can also **read your inbox to spot employer replies** to applications you sent: it looks at senders and full message text from employers you applied to, and stores only the outcome it spotted plus a reference to the message (sender, subject, arrival time) — never the message body. Every spotted outcome is shown to you for confirmation before it is recorded.",
            },
          ],
        },
      ],
    },
    {
      n: "05",
      title: "Transfers outside the EU",
      blocks: [
        // Only what the setup verifies is stated — the Z.ai transfer
        // (config.py GLM_BASE_URL). Delivery by Google / the employer's mail
        // system is named as a flow outside our control, not claimed as EU.
        {
          kind: "p",
          text: "The processing outside the EU that our setup verifies is the CV text and job-ad text sent to **Z.ai** for matching, tailoring and fact-checking, as described above. Your stored account data and CV file remain in the EU (Frankfurt). Applications you send go out from your own Gmail account and are delivered by Google and the employer's mail system, whose locations are outside our control — and once delivered, we cannot recall an application you have approved and sent.",
        },
        // Beta scope: same MIG-WO5 note as section 04 (owner timeline
        // 2026-09-01).
        {
          kind: "p",
          muted: true,
          text: "The Z.ai transfer above applies during the beta phase: we are moving AI processing to an EU-hosted endpoint of the same models, planned to be live by the end of beta, after which CV and job-ad text no longer leave the EU for AI processing.",
        },
      ],
    },
    {
      n: "06",
      title: "How long we keep it",
      blocks: [
        // Deliberately non-technical (owner decision 2026-09-01): WHAT is
        // kept and THAT it ends with account deletion; engineering detail
        // stays in the repo.
        {
          kind: "list",
          items: [
            "**Job ads** — kept only while they are plausibly still open; we do not build an archive of postings.",
            "**Everything personal** — your account, CV, profile, matches, drafts, applications and processing logs — is kept until you delete your account. Deleting it removes all of it, permanently.",
          ],
        },
      ],
    },
    {
      n: "07",
      title: "Your rights",
      blocks: [
        // Export: GET /api/v1/account/export returns account, profile,
        // matches, drafts (cover_letter, tailored_cv, changes_summary) and
        // applications incl. subject/body/target_email
        // (backend/app/api/v1/account.py).
        {
          kind: "p",
          text: "**Export.** In **Settings → Your data** you can download what we hold about you — your account details, profile, matches, drafts (including each tailored CV and cover letter) and applications (including the subject, body and recipient address of every application you sent) — as a JSON file.",
        },
        // Erasure: DELETE /api/v1/account/delete removes applications,
        // drafts, matches, profiles, ai_usage rows and the user, then the CV
        // file from storage (backend/app/api/v1/account.py). Honest limits
        // from the same file: job postings stay (shared scraped data) and
        // Composio connections "become orphaned on Composio's side".
        {
          kind: "p",
          text: "**Erasure.** The same screen deletes your account and all personal data with it: your profile, the CV file itself, every match, draft and application, your AI usage logs, and the account. Two honest limits: job postings stay (they are shared scraped data, not personal to you), and a Composio integration you connected is left orphaned on Composio's side rather than disconnected.",
        },
        {
          kind: "p",
          text: "You also have the rights to access, rectify and object to processing, and to lodge a complaint with a supervisory authority. Use the contact in section 01 for any of these.",
        },
      ],
    },
    {
      n: "08",
      title: "Security",
      blocks: [
        // Auth: Supabase Auth owns identities and stores passwords hashed;
        // the API verifies per-account ES256 access tokens (app/users.py,
        // MIG-WO2). Per-user isolation: required keyword-only user_id
        // scoping + RLS (CLAUDE.md "Per-user data isolation", MIG-WO3). CV
        // bucket is private (ops/provision_supabase_storage.py). No
        // over-claiming beyond this.
        {
          kind: "p",
          text: "Your password is stored hashed, sign-in uses per-account tokens, and every query for profiles, matches, drafts and applications is scoped to your account. The CV storage bucket is private — files are not publicly reachable.",
        },
      ],
    },
  ] as { n: string; title: string; blocks: PrivacyBlock[] }[],
  footer:
    "JobFinderOS is in beta. If this notice and what the app actually does ever disagree, treat that as a bug: the in-app export and deletion in Settings always operate on the real data.",
};

export type PrivacyDict = typeof privacyEn;

// Swedish: claim-for-claim translation of privacyEn (see the trace
// comments there). GDPR terms follow the Swedish text of the regulation:
// personuppgiftsansvarig (controller), personuppgiftsbiträde (processor),
// dataskyddsombud (DPO), tillsynsmyndighet (supervisory authority).
export const privacySv: PrivacyDict = {
  metaTitle: "Integritetsmeddelande · JobFinderOS",
  metaDescription:
    "Vad JobFinderOS samlar in, vem som behandlar det, var det lagras, hur länge vi sparar det och dina rättigheter att exportera och radera.",
  backHome: "Tillbaka till startsidan",
  title: "Integritetsmeddelande",
  stamp: "JobFinderOS · beta · augusti 2026",
  intro:
    "Det här meddelandet förklarar vad vi samlar in när du använder JobFinderOS, vem som behandlar det åt oss, vart det tar vägen, hur länge vi sparar det och de rättigheter att exportera och radera som finns inbyggda i appen. Det visas där vi samlar in dina uppgifter och finns permanent på **/sv/privacy**.",
  sections: [
    {
      n: "01",
      title: "Vem som är ansvarig",
      blocks: [
        {
          kind: "p",
          text: "JobFinderOS är en jobbsökningsplattform byggd för skarp drift, utvecklad och driven av **JobFinderOS-teamet**, som är personuppgiftsansvarigt för allt som beskrivs här.",
        },
        {
          kind: "p",
          text: "Kontakt: **anthony@flutterhive.dev** — för allt som rör dina uppgifter kan du också logga in och använda kontoverktygen i appen under **Inställningar → Din data**. Inget dataskyddsombud har utsetts.",
        },
      ],
    },
    {
      n: "02",
      title: "Vad vi samlar in",
      blocks: [
        {
          kind: "list",
          items: [
            "**Ditt konto** — e-postadress och lösenord (hashat). Inget namn krävs för att registrera sig.",
            "**Ditt CV** — filen du laddar upp (PDF eller Word) och texten som systemet läser ut ur den.",
            "**Din profil** — inställningarna du väljer (land, region, orter, språk, jobbtitlar) samt namnet, e-postadressen och telefonnumret som AI:n läser ut ur ditt CV, och som du kan rätta.",
            "**Matchningar, utkast och ansökningar** — poängen och besluten för jobben vi visar dig, det anpassade CV och personliga brev som skrivs för vart och ett, och en historik över ansökningar du har godkänt och skickat.",
            "**Webbanalys** — Google Analytics-cookies som mäter vilka sidor och funktioner som används, så att vi ser vad som behöver förbättras. Analysen laddas först när du har godkänt den i samtyckesrutan — tackar du nej körs inget analysskript alls. Google behandlar mätdatan, vilket kan innebära överföringar utanför EU.",
          ],
        },
      ],
    },
    {
      n: "03",
      title: "Varför vi behandlar uppgifterna",
      blocks: [
        {
          kind: "p",
          text: "Allt ovan behandlas för att driva tjänsten du har registrerat dig för: att jaga på jobbsajter utifrån dina inställningar, poängsätta jobb mot ditt CV, skriva ansökningsutkast för ditt godkännande och skicka dem du godkänner. Behandlingen är nödvändig för att kunna tillhandahålla tjänsten. Vi säljer inte dina uppgifter, använder dem inte för annonsering och bygger inga profiler för något utanför appen.",
        },
      ],
    },
    {
      n: "04",
      title: "Vilka andra som behandlar dina uppgifter",
      blocks: [
        {
          kind: "processors",
          intro:
            "De här personuppgiftsbiträdena agerar endast på våra instruktioner:",
          items: [
            {
              name: "Z.ai — AI-leverantör",
              where: "utanför EU",
              tone: "signal",
              body: "För att poängsätta jobb mot ditt CV, föreslå söktitlar, anpassa ditt CV och personliga brev och faktakontrollera utkasten skickas delar av din CV-text och jobbannonsen till Z.ai:s API (api.z.ai). Z.ai behandlar detta utanför EU. Varje anrop loggas med vilken endpoint och modell som användes (ingen CV-text i loggen) så att överföringen går att granska.",
              note: "Beta: under den nuvarande betafasen körs AI-behandlingen hos Z.ai. Vi flyttar AI-behandlingen till en EU-baserad endpoint för samma modeller, planerad att vara på plats i slutet av betafasen — meddelandet uppdateras när ändringen är genomförd.",
            },
            {
              name: "Supabase — databas och fillagring",
              where: "EU (eu-west-1, Frankfurt)",
              tone: "ok",
              body: "All kontodata och själva CV-filen lagras hos Supabase (Postgres och en privat lagringsbucket) i EU, i regionen Frankfurt.",
            },
            {
              name: "Render — drift av applikationen",
              where: "EU (Frankfurt)",
              tone: "ok",
              body: "API:et och jakterna som körs två gånger om dagen körs hos Render i Frankfurt, i samma region som databasen.",
            },
            {
              name: "Ditt eget Gmail — ansökningsmejl",
              where: "valfritt, du ansluter det själv",
              tone: "signal",
              body: "Ansökningar via e-post skickas från **ditt eget Gmail-konto**, som du ansluter under Inställningar — de bär din adress, aldrig vår. Utskicket går via Googles infrastruktur. Medan funktionen rullas ut under betan är e-postutskick tillfälligt avstängt; ansökan via webbläsaren och manuell ansökan finns tillgängliga hela tiden.",
            },
            {
              name: "Resend — introduktionsmejl",
              where: "EU (eu-west-1, Irland)",
              tone: "ok",
              body: "När du registrerar dig skickar vi en kort serie introduktionsmejl (ungefär sex, ett per dag) som förklarar varje del av plattformen. Din e-postadress sparas hos vår e-postleverantör Resend för det ändamålet. Varje mejl har en **länk för att avregistrera dig** — den stoppar serien och påverkar inget annat på ditt konto. Raderar du ditt konto tas kontakten bort helt.",
            },
            {
              name: "Bara om du slår på dem",
              where: "",
              tone: "ok",
              body: "Felspårning (Sentry, ett projekt i EU-regionen när det är påslaget) kan ta emot felrapporter. Ansluter du ditt Gmail under Inställningar används Composio som integrationslager — den kopplingen skapas bara om du klickar på Anslut. Med Gmail anslutet kan plattformen även **läsa din inkorg för att upptäcka svar från arbetsgivare** på ansökningar du skickat: den tittar på avsändare och hela meddelandetexten från arbetsgivare du sökt hos, och lagrar bara utfallet den upptäckte plus en hänvisning till meddelandet (avsändare, ämne, ankomsttid) — aldrig meddelandetexten. Varje upptäckt utfall visas för dig för bekräftelse innan det registreras.",
            },
          ],
        },
      ],
    },
    {
      n: "05",
      title: "Överföringar utanför EU",
      blocks: [
        {
          kind: "p",
          text: "Den behandling utanför EU som vår uppsättning kan belägga är CV-texten och annonstexten som skickas till **Z.ai** för matchning, anpassning och faktakontroll, enligt ovan. Din lagrade kontodata och CV-fil stannar i EU (Frankfurt). Ansökningar du skickar går ut från ditt eget Gmail-konto och levereras av Google och arbetsgivarens e-postsystem, vars placering ligger utanför vår kontroll — och när en ansökan som du har godkänt och skickat väl är levererad kan vi inte återkalla den.",
        },
        {
          kind: "p",
          muted: true,
          text: "Överföringen till Z.ai ovan gäller under betafasen: vi flyttar AI-behandlingen till en EU-baserad endpoint för samma modeller, planerad att vara i drift i slutet av betan, varefter CV-text och annonstext inte längre lämnar EU för AI-behandling.",
        },
      ],
    },
    {
      n: "06",
      title: "Hur länge vi sparar uppgifterna",
      blocks: [
        {
          kind: "list",
          items: [
            "**Jobbannonser** — sparas bara så länge de rimligen fortfarande är öppna; vi bygger inget arkiv av annonser.",
            "**Allt personligt** — ditt konto, CV, profil, matchningar, utkast, ansökningar och behandlingsloggar — sparas tills du raderar ditt konto. Raderingen tar bort allt, permanent.",
          ],
        },
      ],
    },
    {
      n: "07",
      title: "Dina rättigheter",
      blocks: [
        {
          kind: "p",
          text: "**Export.** Under **Inställningar → Din data** kan du ladda ner det vi har om dig — dina kontouppgifter, profil, matchningar, utkast (inklusive varje anpassat CV och personligt brev) och ansökningar (inklusive ämnesrad, brödtext och mottagaradress för varje ansökan du har skickat) — som en JSON-fil.",
        },
        {
          kind: "p",
          text: "**Radering.** På samma ställe raderar du ditt konto och all personlig data med det: din profil, själva CV-filen, varje matchning, utkast och ansökan, dina AI-användningsloggar och kontot. Två ärliga begränsningar: jobbannonser finns kvar (de är gemensam skrapad data, inte personlig för dig), och en Composio-integration som du har anslutit lämnas kvar utan ägare hos Composio i stället för att kopplas från.",
        },
        {
          kind: "p",
          text: "Du har också rätt till tillgång, rättelse och att invända mot behandling, samt rätt att lämna klagomål till en tillsynsmyndighet. Använd kontaktuppgiften i avsnitt 01 för något av detta.",
        },
      ],
    },
    {
      n: "08",
      title: "Säkerhet",
      blocks: [
        {
          kind: "p",
          text: "Ditt lösenord lagras hashat, inloggningen använder token per konto och varje sökning efter profiler, matchningar, utkast och ansökningar är avgränsad till ditt konto. Lagringsbucketen för CV:n är privat — filerna går inte att nå publikt.",
        },
      ],
    },
  ],
  footer:
    "JobFinderOS är i beta. Om det här meddelandet och det appen faktiskt gör någon gång inte stämmer överens, behandla det som en bugg: exporten och raderingen under Inställningar arbetar alltid på den riktiga datan.",
};
