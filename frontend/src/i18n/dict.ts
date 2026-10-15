// The platform's two languages. Landing + login + reset (owner decision
// 2026-09-01) and — since 2026-10-12 — the console, the onboarding
// wizard and the privacy notice. `sv` is typed as the exact shape of
// `en`, so a missing key is a type error — adding a string means adding
// both translations. The console/wizard/privacy copy lives in sibling
// modules (./console.ts, ./wizard.ts, ./privacy.ts) to keep this file
// navigable; tests/i18n.test.ts checks deep key parity, including the
// Record-typed maps the compiler can't.

import { consoleEn, consoleSv } from './console.ts';
import { privacyEn, privacySv } from './privacy.ts';
import { wizardEn, wizardSv } from './wizard.ts';

export type Dict = typeof en;

const en = {
  meta: {
    title: 'JobFinderOS · Stop refreshing job boards',
    description:
      "Sweden's and the UK's job markets, hunted twice daily and scored against your CV. Applications you approve, drafts that never invent facts.",
  },
  nav: {
    sections: 'Landing sections',
    home: 'JobFinderOS home',
    how: 'How it works',
    sources: 'Sources',
    guard: 'Fact guard',
    faq: 'FAQ',
    signIn: 'Sign in',
    getStarted: 'Get started',
  },
  hero: {
    badge: 'Sweden + UK · hunts twice daily',
    // WO-30 hero (owner pick 2026-10-15, evidence: REA copy teardown
    // §4 — feeling-led H1, understanding in the sub): "Get your
    // evenings back" is the offload outcome reviewers describe.
    h1a: 'Job hunting is stressful enough.',
    h1b: 'Get your evenings back.',
    sub: 'JobFinderOS scans both markets twice a day and scores every listing against your CV — you approve what gets sent. No more scrolling job boards at midnight.',
    cta: 'Upload your CV — free in beta',
    how: 'See how it works',
    proof: 'No card · no credits · nothing sent without you',
    // Claims audit (WO-28): the backend caps uploads at 5 MB
    // (file_service.py validate_size) and accepts .pdf/.docx only —
    // the redesign bundle's "up to 10 MB" was false.
    formats: 'PDF or Word · up to 5 MB',
    pulseAlt:
      "The hunt pulse: a real run's ads, funnelled from hunted to matched, each scored against your CV",
  },
  stats: [
    ['2', 'countries hunted'],
    ['6', 'sources per hunt'],
    ['2×', 'runs every day'],
    ['0', 'facts invented'],
  ] as [string, string][],
  steps: [
    {
      title: 'Upload once',
      body: 'Your CV, your cities and regions, your minimum score. One city, or a whole region.',
    },
    {
      title: 'The hunt runs twice a day',
      body: 'New ads scraped, agency cross-posts merged into the direct ad, every survivor scored. The rest never reach you.',
    },
    {
      title: 'Approve, tailor, send',
      body: 'One click and the AI writes the CV and cover letter shaped to the ad. The guard rejects anything your history cannot back.',
    },
  ],
  verdict: {
    h2a: 'You upload once.',
    h2b: 'It hunts twice a day.',
    h2: 'Every match explains itself.',
    body: 'Not a black-box score. Each verdict shows the skills you have, the gaps the ad demands, and what transfers — so you apply to the right jobs, and skip the rest on purpose.',
    alt: 'A match verdict: the job, its score, and three columns — what you have, what they want, what transfers',
    caption: 'A real verdict, unedited',
    pulseCaption: 'The hunt pulse, from a real run',
  },
  volume: {
    h2: 'Volume is not a strategy.',
    // Claims audit (WO-28): the "two thirds of hiring managers" figure
    // was never verified (and the old copy contradicted itself — the
    // body said two thirds, the stat line 65%). The numeric claim is
    // OUT of the body; statClaim renders only when the owner pastes a
    // checked citation into VolumeArgument's VOLUME_STAT_CITATION.
    body: 'The mass-apply era made recruiters numb to applications. More applications, less signal. We send fewer matches, and we tell you when a job is not worth applying for.',
    statClaim: 'Two thirds of hiring managers say AI-optimised CVs make skills harder to verify.',
  },
  demo: {
    badge: 'Illustrative example',
    // Owner feedback 2026-10-15: no slider — the console has no live
    // feed-filtering, and the landing must not demo interactions the
    // product lacks. The cards mirror the real MatchCard verdict
    // columns; the toggle mirrors the real approval gate.
    h2: 'The verdict, card by card',
    sub: 'Three sample matches, scored against a sample CV: the skills you have, the gaps the ad asks for, what carries over. Nothing is sent without your approval.',
    showDrafts: 'Show what gets sent',
    hideDrafts: 'Hide what gets sent',
    draftLabel: 'Sample cover-letter opening — nothing is sent without your approval',
    draftSample:
      'For eight years I have built and run payment flows that clear four million transactions a day — the same reliability this role asks of its next backend engineer.',
    scoreWord: 'match score',
    verdicts: {
      apply: 'Worth applying',
      marginal: 'Marginal',
      skip: 'Skip',
    },
    chips: {
      inCv: 'In your CV',
      gap: 'Gap',
      carries: 'Carries over',
    },
    cards: [
      {
        title: 'Backend Developer · fintech',
        score: 84,
        verdict: 'apply' as const,
        chips: [
          { kind: 'inCv' as const, text: '5 yrs Python' },
          { kind: 'inCv' as const, text: 'REST APIs' },
          { kind: 'gap' as const, text: 'Kubernetes' },
        ],
      },
      {
        title: 'Data Analyst · retail',
        score: 71,
        verdict: 'marginal' as const,
        chips: [
          { kind: 'inCv' as const, text: 'SQL' },
          { kind: 'carries' as const, text: 'Excel → dashboards' },
          { kind: 'gap' as const, text: 'Swedish B2 not shown' },
        ],
      },
      {
        title: 'Senior Java Developer · bank',
        score: 58,
        verdict: 'skip' as const,
        chips: [
          { kind: 'inCv' as const, text: 'OOP depth' },
          { kind: 'gap' as const, text: '8 yrs Java' },
          { kind: 'gap' as const, text: 'Banking domain' },
        ],
      },
    ],
  },
  comparison: {
    h2: 'Different by design.',
    sub: 'The typical auto-apply tool optimises for volume. We optimise for the applications worth sending.',
    colFeature: 'Feature',
    colThem: 'Typical auto-apply tool',
    colUs: 'JobFinderOS',
    rows: [
      ['What gets sent', 'Hundreds of applications a day', 'Only jobs you approve'],
      ['Duplicate listings', 'Same job applied to via 3–9 agencies', 'Reposts merged into the original'],
      ['Why a job matched', 'A score, or nothing', 'Skills you have, gaps, what carries over'],
      ['Claims in your CV', 'Rewritten to hit keywords', 'Checked line by line against your real CV'],
      ['Job sources', 'Often scraped behind logins', 'Official, public feeds only'],
    ] as [string, string, string][],
  },
  sourcesSec: {
    h2: 'Where we hunt.',
    ariaLabel: 'Job sources',
    cards: [
      {
        tag: 'Sweden',
        title: 'The whole national feed.',
        body: 'Every public listing in the country, straight from the official source. If it is posted, it is hunted.',
      },
      {
        tag: 'United Kingdom',
        title: 'Every sector, daily.',
        body: 'One of the largest job feeds on the market, scored by the same engine as the Swedish hunt.',
      },
      {
        tag: 'Precision',
        title: 'Your cities and regions.',
        body: 'Scoped by official region codes, not fuzzy text matching. Your commute, your rules.',
      },
    ],
    privacyA:
      // Claims audit (WO-28): city dropped — storage is Render Frankfurt
      // + Supabase eu-west-1 (Dublin); our own comments conflate them,
      // so the defensible claim is "in the EU", no city.
      // WO-30 follow-up (owner 2026-10-15): the outside-EU processing
      // is BETA-ONLY — EU-resident AI is the stated end state — so the
      // claim is scoped to the beta and the commitment stated, instead
      // of reading like a permanent arrangement.
      'Official public data only. No logins, no grey scraping. Your account data and CV file are stored in the EU. During the beta, CV text sent to our AI is processed outside the EU — when the beta ends, all AI processing moves inside the EU.',
    privacyLink: 'Read the privacy notice',
  },
  guardSec: {
    h2a: 'Nothing invented.',
    h2b: 'Ever.',
    body: "Every claim in your tailored CV is checked against your real CV before it's sent. If a claim isn't in your history, it doesn't ship unless you confirm it.",
    receipt: {
      ariaLabel: 'Example of the draft guard checking claims against your CV (illustrative, not a real audit)',
      label: 'Draft guard · cover letter',
      example: 'Example',
      summary: '2 verified · 1 rejected',
      rows: [
        { ok: true, claim: 'Five years of bakery production', note: 'In your CV' },
        { ok: true, claim: 'Food-safety certificate', note: 'In your CV' },
        { ok: false, claim: 'PMP certification', note: 'Not in your CV — draft regenerated' },
      ],
      caption:
        'Illustrative example with sample claims — not a real audit. Every real draft gets this check before you see it: a claim your CV cannot back is rejected and the draft regenerated; one that survives blocks the draft.',
    },
  },
  terms: {
    h2a: 'No credits.',
    h2b: 'No surprises.',
    note: 'In beta it is free. Whenever we charge, these are the terms.',
    items: [
      'No credits and no surprise charges. The price is the price.',
      'The trial never auto-renews.',
      'Cancel in one click.',
      'Get hired, and we refund the months you have not used.',
    ],
  },
  proof: {
    kicker: 'From the beta',
    placeholderQuote: 'Ask us for one result, in a real user\u2019s own words.',
    placeholderAttr: 'Name · role · city · with permission',
    note: 'We will not invent a testimonial. When a beta user gives us a result in their own words — with permission — it goes here, word for word.',
  },
  faq: {
    h2: 'Questions, answered.',
    items: [
      {
        q: 'Does it apply to jobs without asking me?',
        a: 'No. Nothing is sent until you approve it. The hunt finds and scores jobs; you decide which ones get an application, and you see every draft before it goes anywhere.',
      },
      {
        q: 'Where do the jobs come from?',
        a: "Official, public sources only: Sweden's national job feed and one of the UK's largest job feeds. No logins, no grey-zone scraping. Agency reposts are merged into the original listing so you see each job once.",
      },
      {
        q: 'Will employers know I used AI?',
        a: 'Your drafts only contain claims your CV can back up, written for that specific listing. That is the opposite of the generic, keyword-stuffed CVs recruiters have learned to spot.',
      },
      {
        q: 'What happens to my CV and data?',
        a: 'Your account data and CV file are stored in the EU. During the beta, the CV text sent to our AI is processed outside the EU — when the beta ends, all AI processing moves inside the EU. The privacy notice lists every processor.',
      },
      {
        q: 'What does it cost?',
        a: 'Nothing during the beta, and no card is needed. When paid plans arrive there are no credits, no auto-renewing trials, one-click cancellation — and if you get the job, we refund the months you have not used.',
      },
      {
        q: 'Does it work in Swedish and English?',
        a: 'Yes. The hunt reads listings in both languages and writes your CV and cover letter in the language of each listing.',
      },
    ],
  },
  final: {
    h2a: 'Your next job is',
    h2b: 'already posted.',
    // Claims audit (WO-28): "tomorrow morning" is conservative-true —
    // the hunt runs twice daily; the worst case for a new signup is the
    // next morning run, and an earlier signup sees matches same evening.
    sub: "Somewhere in tonight's hunt — see your first matches tomorrow morning.",
    cta: 'Upload your CV',
  },
  sticky: {
    cta: 'Upload your CV — free',
    note: 'No card · nothing sent without you',
  },
  footer: {
    privacy: 'Privacy notice',
    made: 'Made in Malmö',
  },
  langToggle: 'Svenska',
  radar: {
    chipRole: 'Backend developer · fintech',
    match: 'match',
    // [label, score] — order MUST match RadarScope's FIELD coordinates.
    titles: [
      ['Nurse', 82], ['Teacher', 76], ['Chef', 73], ['Pilot', 84],
      ['Vet nurse', 76], ['Pharmacist', 81], ['Accountant', 79], ['Optician', 79],
      ['Postman', 68], ['Electrician', 71], ['IT support', 78], ['Physio', 83],
      ['Midwife', 81],
      ['Police officer', 70], ['Firefighter', 65], ['Bus driver', 64],
      ['Journalist', 67], ['Social worker', 74],
      ['Barista', 57], ['Plumber', 62], ['Welder', 59], ['Hairdresser', 56],
      ['Gardener', 55], ['Courier', 63],
    ] as [string, number][],
  },
  login: {
    backHome: 'Back to home',
    subtitleSignin: 'Sign in to the console',
    subtitleRegister: 'Create your account',
    subtitleForgot: 'Reset your password',
    email: 'Email',
    password: 'Password',
    minChars: 'At least 8 characters.',
    busySignin: 'Signing in…',
    busyRegister: 'Creating account…',
    busyForgot: 'Sending reset link…',
    submitSignin: 'Sign in',
    submitRegister: 'Create account and start hunting',
    submitForgot: 'Send reset link',
    forgotLink: 'Forgot your password?',
    checkEmailTitle: 'Check your email',
    checkEmailBody:
      'We sent you a link — click it to continue. No email? Check spam, or try again.',
    backToSignin: 'Back to sign in',
    newHere: 'New here?',
    createAccount: 'Create an account',
    alreadyHunting: 'Already hunting with us?',
    signIn: 'Sign in',
    footerLine: 'Hunts twice daily · scores honestly · nothing sent without you',
    privacy: 'Privacy notice',
    errWrong: 'Wrong email or password',
    errExists: 'That email already has an account, or the password is too short',
    errWeakPassword: 'Password must be at least 8 characters',
    errUnconfirmed: 'Confirm your email first — check your inbox for the link',
  },
  resetPassword: {
    backHome: 'Back to home',
    subtitle: 'Set a new password',
    waiting: 'Opening your reset link…',
    newPassword: 'New password',
    confirmPassword: 'Repeat the new password',
    minChars: 'At least 8 characters.',
    submit: 'Save new password',
    busy: 'Saving…',
    done: 'Password updated. Everything else stays signed out.',
    toConsole: 'Go to the console',
    errLink:
      'This link is invalid, expired — or was opened on a different device than the one you requested it from. Open it on that device, or request a new link here.',
    requestNew: 'Request a new reset link',
    errMismatch: 'The two passwords do not match',
    errShort: 'Password must be at least 8 characters',
    errGeneric: 'Could not update the password — try again',
  },
  console: consoleEn,
  wizard: wizardEn,
  privacy: privacyEn,
};

const sv: Dict = {
  meta: {
    title: 'JobFinderOS · Sluta uppdatera jobbsajter',
    description:
      'Sveriges och Storbritanniens jobbmarknader, bevakade två gånger om dagen och poängsatta mot ditt CV. Ansökningar du godkänner — utkast som aldrig hittar på fakta.',
  },
  nav: {
    sections: 'Landningssidans sektioner',
    home: 'JobFinderOS startsida',
    how: 'Så fungerar det',
    sources: 'Källor',
    guard: 'Faktavakten',
    faq: 'FAQ',
    signIn: 'Logga in',
    getStarted: 'Kom igång',
  },
  hero: {
    // WO-30 hero (owner pick 2026-10-15) + the de-jakt rule: Swedish
    // landing copy uses bevakar/bevakning, never jakt/jagar — the
    // hunt metaphor stays English-only (product names untouched).
    badge: 'Sverige + Storbritannien · bevakning två gånger om dagen',
    h1a: 'Jobbsökandet är stressigt nog.',
    h1b: 'Få tillbaka dina kvällar.',
    sub: 'JobFinderOS bevakar marknaden två gånger om dagen och poängsätter varje annons mot ditt CV — du godkänner det som skickas. Inget mer kvällsskrollande efter jobb.',
    cta: 'Ladda upp ditt CV — gratis i beta',
    how: 'Se hur det fungerar',
    proof: 'Inget kort · inga krediter · inget skickas utan dig',
    formats: 'PDF eller Word · max 5 MB',
    pulseAlt:
      'Bevakningsflödet: annonser från en riktig körning, varje annons poängsatt mot ditt CV',
  },
  stats: [
    ['2', 'länder bevakas'],
    ['6', 'källor per körning'],
    ['2×', 'körningar varje dag'],
    ['0', 'påhittade fakta'],
  ],
  steps: [
    {
      title: 'Ladda upp en gång',
      body: 'Ditt CV, dina städer och regioner, din minimipoäng. En stad eller en hel region.',
    },
    {
      title: 'Bevakningen körs två gånger om dagen',
      body: 'Nya annonser skrapas, bemanningsföretagens korspostningar slås samman med direktannonsen och varje överlevande poängsätts. Resten når aldrig dig.',
    },
    {
      title: 'Godkänn, anpassa, skicka',
      body: 'Ett klick — och AI:n skriver CV och personligt brev format efter annonsen. Vakten avvisar allt som din historik inte kan belägga.',
    },
  ],
  verdict: {
    h2a: 'Du laddar upp en gång.',
    h2b: 'Den bevakar två gånger om dagen.',
    h2: 'Varje match förklarar sig själv.',
    body: 'Inte en poäng i en svart låda. Varje bedömning visar kompetenserna du har, det annonsen kräver som du saknar, och vad som överförs — så att du söker rätt jobb och medvetet hoppar över resten.',
    alt: 'En matchbedömning: jobbet, poängen och tre kolumner — vad du har, vad de vill ha, vad som överförs',
    caption: 'En riktig bedömning, oredigerad',
    pulseCaption: 'Bevakningsflödet, från en riktig körning',
  },
  volume: {
    h2: 'Volym är ingen strategi.',
    body: 'Era av massansökningar gjorde rekryterare immuna mot ansökningar. Fler ansökningar, mindre signal. Vi skickar färre matchningar — och säger till när ett jobb inte är värt att söka.',
    statClaim: 'Två tredjedelar av rekryterande chefer säger att AI-optimerade CV:n gör kompetenser svårare att verifiera.',
  },
  demo: {
    badge: 'Illustrativt exempel',
    h2: 'Bedömningen, kort för kort',
    sub: 'Tre exempel på matchningar mot ett exempel-CV: kompetensen du har, det annonsen kräver som saknas, vad som överförs. Inget skickas utan ditt godkännande.',
    showDrafts: 'Visa vad som skickas',
    hideDrafts: 'Dölj vad som skickas',
    draftLabel: 'Exempel på inledning av personligt brev — inget skickas utan ditt godkännande',
    draftSample:
      'I åtta år har jag byggt och drivit betalflöden som klarar fyra miljoner transaktioner om dagen — samma driftsäkerhet som den här rollen kräver av sin nästa backendutvecklare.',
    scoreWord: 'matchpoäng',
    verdicts: {
      apply: 'Värd att söka',
      marginal: 'Gränsfall',
      skip: 'Hoppa över',
    },
    chips: {
      inCv: 'I ditt CV',
      gap: 'Saknas',
      carries: 'Överförs',
    },
    cards: [
      {
        title: 'Backendutvecklare · fintech',
        score: 84,
        verdict: 'apply',
        chips: [
          { kind: 'inCv', text: '5 års Python' },
          { kind: 'inCv', text: 'REST-API:er' },
          { kind: 'gap', text: 'Kubernetes' },
        ],
      },
      {
        title: 'Dataanalytiker · detaljhandel',
        score: 71,
        verdict: 'marginal',
        chips: [
          { kind: 'inCv', text: 'SQL' },
          { kind: 'carries', text: 'Excel → dashboards' },
          { kind: 'gap', text: 'Svenska B2 visas ej' },
        ],
      },
      {
        title: 'Senior Java-utvecklare · bank',
        score: 58,
        verdict: 'skip',
        chips: [
          { kind: 'inCv', text: 'OOP-djup' },
          { kind: 'gap', text: '8 års Java' },
          { kind: 'gap', text: 'Bankdomän' },
        ],
      },
    ],
  },
  comparison: {
    h2: 'Medvetet annorlunda.',
    sub: 'Det typiska autoapply-verktyget optimerar för volym. Vi optimerar för ansökningar värda att skicka.',
    colFeature: 'Funktion',
    colThem: 'Typiskt autoapply-verktyg',
    colUs: 'JobFinderOS',
    rows: [
      ['Vad som skickas', 'Hundratals ansökningar om dagen', 'Bara jobb du godkänner'],
      ['Dubbletannonser', 'Samma jobb sökt via 3–9 bemanningsföretag', 'Korspostningar slås samman med originalet'],
      ['Varför ett jobb matchade', 'En poäng — eller inget', 'Kompetens du har, luckor, vad som överförs'],
      ['Påståenden i ditt CV', 'Omskrivna för att träffa nyckelord', 'Kontrollerade rad för rad mot ditt riktiga CV'],
      ['Jobbkällor', 'Ofta skrapade bakom inloggningar', 'Endast officiella, offentliga flöden'],
    ] as [string, string, string][],
  },
  sourcesSec: {
    h2: 'Var vi bevakar.',
    ariaLabel: 'Jobbkällor',
    cards: [
      {
        tag: 'Sverige',
        title: 'Hela det nationella flödet.',
        body: 'Varje offentlig annons i landet, direkt från den officiella källan. Är den utlagd finns den med.',
      },
      {
        tag: 'Storbritannien',
        title: 'Alla sektorer, varje dag.',
        body: 'Ett av marknadens största jobbflöden, poängsatt av samma motor som den svenska bevakningen.',
      },
      {
        tag: 'Precision',
        title: 'Dina städer och regioner.',
        body: 'Avgränsat med officiella regionkoder, inte luddig textmatchning. Din pendling, dina regler.',
      },
    ],
    privacyA:
      'Endast officiell, offentlig data. Inga inloggningar, ingen grå zon-skrapning. Din kontodata och din CV-fil lagras i EU. Under betan behandlas CV-texten som skickas till vår AI utanför EU — när betan är över sker all AI-behandling inom EU.',
    privacyLink: 'Läs integritetsmeddelandet',
  },
  guardSec: {
    h2a: 'Inget påhittat.',
    h2b: 'Aldrig.',
    body: 'Varje påstående i ditt anpassade CV kontrolleras mot ditt riktiga CV innan det skickas. Finns påståendet inte i din historik skickas det inte förrän du bekräftar det.',
    receipt: {
      ariaLabel:
        'Exempel på hur utkastvakten kontrollerar påståenden mot ditt CV (illustrativt, ingen riktig granskning)',
      label: 'Utkastvakten · personligt brev',
      example: 'Exempel',
      summary: '2 verifierade · 1 avvisat',
      rows: [
        { ok: true, claim: 'Fem års produktionserfarenhet från bageri', note: 'I ditt CV' },
        { ok: true, claim: 'Intyg i livsmedelssäkerhet', note: 'I ditt CV' },
        { ok: false, claim: 'PMP-certifiering', note: 'Inte i ditt CV — utkastet gjordes om' },
      ],
      caption:
        'Illustrativt exempel med exempel-påståenden — ingen riktig granskning. Varje riktigt utkast får den här kontrollen innan du ser det: ett påstående ditt CV inte kan belägga avvisas och utkastet görs om; ett som överlever blockerar utkastet.',
    },
  },
  terms: {
    h2a: 'Inga krediter.',
    h2b: 'Inga överraskningar.',
    note: 'I beta är det gratis. När vi någon gång tar betalt är det här villkoren.',
    items: [
      'Inga krediter och inga överraskande avgifter. Priset är priset.',
      'Testperioden förnyas aldrig automatiskt.',
      'Avsluta med ett klick.',
      'Får du jobbet återbetalar vi månaderna du inte har använt.',
    ],
  },
  proof: {
    kicker: 'Från betan',
    placeholderQuote: 'Be oss om ett resultat, i en riktig användares egna ord.',
    placeholderAttr: 'Namn · roll · stad · med tillstånd',
    note: 'Vi hittar inte på ett omdöme. När en betaanvändare ger oss ett resultat med egna ord — med tillstånd — hamnar det här, ord för ord.',
  },
  faq: {
    h2: 'Frågor, besvarade.',
    items: [
      {
        q: 'Söker den jobb utan att fråga mig?',
        a: 'Nej. Inget skickas förrän du godkänner det. Jakten hittar och poängsätter jobb; du avgör vilka som får en ansökan — och du ser varje utkast innan det lämnar huset.',
      },
      {
        q: 'Var kommer jobben ifrån?',
        a: 'Endast officiella, offentliga källor: Sveriges nationella jobbflöde och ett av Storbritanniens största. Inga inloggningar, ingen skrapning i gråzonen. Bemanningsföretagens korspostningar slås samman med originalannonsen så att du ser varje jobb en gång.',
      },
      {
        q: 'Märker arbetsgivare att jag använt AI?',
        a: 'Dina utkast innehåller bara påståenden ditt CV kan belägga, skrivna för just den annonsen. Det är motsatsen till de generiska, nyckelordsstoppade CV:n rekryterare har lärt sig känna igen.',
      },
      {
        q: 'Vad händer med mitt CV och min data?',
        a: 'Din kontodata och din CV-fil lagras i EU. Under betan behandlas CV-texten som skickas till vår AI utanför EU — när betan är över sker all AI-behandling inom EU. Integritetsmeddelandet listar alla behandlare.',
      },
      {
        q: 'Vad kostar det?',
        a: 'Inget under betan, och inget kort behövs. När betalda planer kommer: inga krediter, inga automatiskt förnyade provperioder, uppsägning med ett klick — och får du jobbet återbetalar vi månaderna du inte har använt.',
      },
      {
        q: 'Fungerar det på svenska och engelska?',
        a: 'Ja. Bevakningen läser annonser på båda språken och skriver ditt CV och personliga brev på annonsens språk.',
      },
    ],
  },
  final: {
    h2a: 'Ditt nästa jobb är',
    h2b: 'redan utlagt.',
    sub: 'Någonstans i kvällens bevakning — se dina första matchningar imorgon bitti.',
    cta: 'Ladda upp ditt CV',
  },
  sticky: {
    cta: 'Ladda upp ditt CV — gratis',
    note: 'Inget kort · inget skickas utan dig',
  },
  footer: {
    privacy: 'Integritetsmeddelande',
    made: 'Tillverkat i Malmö',
  },
  langToggle: 'English',
  radar: {
    chipRole: 'Backendutvecklare · fintech',
    match: 'match',
    // Same coordinates as EN — labels picked to stay pill-width safe
    // (Djurvårdare/Odlare instead of the 15-17 char literals).
    titles: [
      ['Sjuksköterska', 82], ['Lärare', 76], ['Kock', 73], ['Pilot', 84],
      ['Djurvårdare', 76], ['Apotekare', 81], ['Bokförare', 79], ['Optiker', 79],
      ['Postbud', 68], ['Elektriker', 71], ['IT-support', 78], ['Fysioterapeut', 83],
      ['Barnmorska', 81],
      ['Polis', 70], ['Brandman', 65], ['Bussförare', 64],
      ['Journalist', 67], ['Kurator', 74],
      ['Barista', 57], ['Rörmokare', 62], ['Svetsare', 59], ['Frisör', 56],
      ['Odlare', 55], ['Budbärare', 63],
    ],
  },
  login: {
    backHome: 'Tillbaka till startsidan',
    subtitleSignin: 'Logga in på konsolen',
    subtitleRegister: 'Skapa ditt konto',
    subtitleForgot: 'Återställ ditt lösenord',
    email: 'E-post',
    password: 'Lösenord',
    minChars: 'Minst 8 tecken.',
    busySignin: 'Loggar in…',
    busyRegister: 'Skapar konto…',
    busyForgot: 'Skickar återställningslänk…',
    submitSignin: 'Logga in',
    submitRegister: 'Skapa konto och kom igång',
    submitForgot: 'Skicka återställningslänk',
    forgotLink: 'Glömt ditt lösenord?',
    checkEmailTitle: 'Kolla din inkorg',
    checkEmailBody:
      'Vi har skickat en länk — klicka på den för att fortsätta. Inget mejl? Kolla skräpposten, eller försök igen.',
    backToSignin: 'Tillbaka till inloggningen',
    newHere: 'Ny här?',
    createAccount: 'Skapa ett konto',
    alreadyHunting: 'Jagar du redan med oss?',
    signIn: 'Logga in',
    footerLine: 'Bevakar två gånger om dagen · poängsätter ärligt · inget skickas utan dig',
    privacy: 'Integritetsmeddelande',
    errWrong: 'Fel e-post eller lösenord',
    errExists: 'Den e-postadressen har redan ett konto, eller så är lösenordet för kort',
    errWeakPassword: 'Lösenordet måste vara minst 8 tecken',
    errUnconfirmed: 'Bekräfta din e-post först — länken ligger i inkorgen',
  },
  resetPassword: {
    backHome: 'Tillbaka till startsidan',
    subtitle: 'Välj ett nytt lösenord',
    waiting: 'Öppnar din återställningslänk…',
    newPassword: 'Nytt lösenord',
    confirmPassword: 'Upprepa det nya lösenordet',
    minChars: 'Minst 8 tecken.',
    submit: 'Spara nytt lösenord',
    busy: 'Sparar…',
    done: 'Lösenordet är uppdaterat. Allt annat är fortfarande utloggat.',
    toConsole: 'Till konsolen',
    errLink:
      'Länken är ogiltig, har gått ut — eller öppnades på en annan enhet än den du begärde den från. Öppna den på den enheten, eller begär en ny länk här.',
    requestNew: 'Be om en ny återställningslänk',
    errMismatch: 'Lösenorden stämmer inte överens',
    errShort: 'Lösenordet måste vara minst 8 tecken',
    errGeneric: 'Kunde inte uppdatera lösenordet — försök igen',
  },
  console: consoleSv,
  wizard: wizardSv,
  privacy: privacySv,
};

export const dicts = { en, sv } as const;
export type Locale = keyof typeof dicts;
