// The marketing surface's two languages (owner decision 2026-09-01):
// landing + login are localized; the app console stays English for beta.
// `sv` is typed as the exact shape of `en`, so a missing key is a
// type error — adding a string means adding both translations.

export type Dict = typeof en;

const en = {
  meta: {
    title: 'JobFinderOS · Stop refreshing job boards',
    description:
      "Sweden's and the UK's job markets, hunted twice daily and scored against your CV. Applications you approve, drafts that never invent facts.",
  },
  nav: {
    sections: 'Landing sections',
    how: 'How it works',
    sources: 'Sources',
    guard: 'Fact guard',
    signIn: 'Sign in',
    getStarted: 'Get started',
  },
  hero: {
    badge: 'Sweden + UK · hunts twice daily',
    h1a: 'The jobs worth applying to,',
    h1b: 'found for you.',
    sub: "Sweden's and the UK's job markets, hunted, cleaned and scored against your CV. You approve everything.",
    cta: 'Upload your CV — free in beta',
    how: 'See how it works',
    proof: 'No card · no credits · nothing sent without you',
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
    body: 'The mass-apply era made recruiters numb: two thirds of hiring managers say AI-written CVs make your skills harder to verify. More applications, less signal. We send fewer matches, and we tell you when a job is not worth applying for.',
    stat: '65% of hiring managers say AI-optimized CVs make skills harder to verify. Forbes, March 2026.',
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
      'Official public data only. No logins, no grey scraping. Your account data and CV file are stored in the EU (Frankfurt); the CV text sent to our AI for matching and tailoring is processed outside the EU.',
    privacyLink: 'Read the privacy notice',
  },
  guardSec: {
    h2a: 'Nothing invented.',
    h2b: 'Ever.',
    body: "Every claim in your tailored CV is checked against your real CV before it's sent. If a claim is not in your history, it does not ship.",
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
  final: {
    h2a: 'Your next job is',
    h2b: 'already posted.',
    sub: "Somewhere in tonight's hunt.",
    cta: 'Upload your CV',
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
    email: 'Email',
    password: 'Password',
    minChars: 'At least 8 characters.',
    busySignin: 'Signing in…',
    busyRegister: 'Creating account…',
    submitSignin: 'Sign in',
    submitRegister: 'Create account and start hunting',
    newHere: 'New here?',
    createAccount: 'Create an account',
    alreadyHunting: 'Already hunting with us?',
    signIn: 'Sign in',
    footerLine: 'Hunts twice daily · scores honestly · nothing sent without you',
    privacy: 'Privacy notice',
    errWrong: 'Wrong email or password',
    errExists: 'That email already has an account, or the password is too short',
  },
};

const sv: Dict = {
  meta: {
    title: 'JobFinderOS · Sluta uppdatera jobbsajter',
    description:
      'Sveriges och Storbritanniens jobbmarknader, bevakade två gånger om dagen och poängsatta mot ditt CV. Ansökningar du godkänner — utkast som aldrig hittar på fakta.',
  },
  nav: {
    sections: 'Landningssidans sektioner',
    how: 'Så fungerar det',
    sources: 'Källor',
    guard: 'Faktavakten',
    signIn: 'Logga in',
    getStarted: 'Kom igång',
  },
  hero: {
    badge: 'Sverige + Storbritannien · jakt två gånger om dagen',
    h1a: 'Jobben värda att söka,',
    h1b: 'hittade åt dig.',
    sub: 'Sveriges och Storbritanniens jobbmarknader — jagade, rensade och poängsatta mot ditt CV. Du godkänner allt.',
    cta: 'Ladda upp ditt CV — gratis i beta',
    how: 'Se hur det fungerar',
    proof: 'Inget kort · inga krediter · inget skickas utan dig',
    pulseAlt:
      'Jaktpulsen: annonser från en riktig körning, från jagade till matchade, varje annons poängsatt mot ditt CV',
  },
  stats: [
    ['2', 'länder bevakas'],
    ['6', 'källor per jakt'],
    ['2×', 'körningar varje dag'],
    ['0', 'påhittade fakta'],
  ],
  steps: [
    {
      title: 'Ladda upp en gång',
      body: 'Ditt CV, dina städer och regioner, din minimipoäng. En stad eller en hel region.',
    },
    {
      title: 'Jakten körs två gånger om dagen',
      body: 'Nya annonser skrapas, bemanningsföretagens korspostningar slås samman med direktannonsen och varje överlevande poängsätts. Resten når aldrig dig.',
    },
    {
      title: 'Godkänn, anpassa, skicka',
      body: 'Ett klick — och AI:n skriver CV och personligt brev format efter annonsen. Vakten avvisar allt som din historik inte kan belägga.',
    },
  ],
  verdict: {
    h2a: 'Du laddar upp en gång.',
    h2b: 'Den jagar två gånger om dagen.',
    h2: 'Varje match förklarar sig själv.',
    body: 'Inte en poäng i en svart låda. Varje bedömning visar kompetenserna du har, det annonsen kräver som du saknar, och vad som överförs — så att du söker rätt jobb och medvetet hoppar över resten.',
    alt: 'En matchbedömning: jobbet, poängen och tre kolumner — vad du har, vad de vill ha, vad som överförs',
    caption: 'En riktig bedömning, oredigerad',
    pulseCaption: 'Jaktpulsen, från en riktig körning',
  },
  volume: {
    h2: 'Volym är ingen strategi.',
    body: 'Era av massansökningar gjorde rekryterare immuna: två tredjedelar av rekryterande chefer säger att AI-skrivna CV:n gör dina kompetenser svårare att verifiera. Fler ansökningar, mindre signal. Vi skickar färre matchningar — och säger till när ett jobb inte är värt att söka.',
    stat: '65 % av rekryterande chefer säger att AI-optimerade CV:n gör kompetenser svårare att verifiera. Forbes, mars 2026.',
  },
  sourcesSec: {
    h2: 'Var vi jagar.',
    ariaLabel: 'Jobbkällor',
    cards: [
      {
        tag: 'Sverige',
        title: 'Hela det nationella flödet.',
        body: 'Varje offentlig annons i landet, direkt från den officiella källan. Är den utlagd är den jagad.',
      },
      {
        tag: 'Storbritannien',
        title: 'Alla sektorer, varje dag.',
        body: 'Ett av marknadens största jobbflöden, poängsatt av samma motor som den svenska jakten.',
      },
      {
        tag: 'Precision',
        title: 'Dina städer och regioner.',
        body: 'Avgränsat med officiella regionkoder, inte luddig textmatchning. Din pendling, dina regler.',
      },
    ],
    privacyA:
      'Endast officiell, offentlig data. Inga inloggningar, ingen grå zon-skrapning. Din kontodata och din CV-fil lagras i EU (Frankfurt); CV-texten som skickas till vår AI för matchning och anpassning behandlas utanför EU.',
    privacyLink: 'Läs integritetsmeddelandet',
  },
  guardSec: {
    h2a: 'Inget påhittat.',
    h2b: 'Aldrig.',
    body: 'Varje påstående i ditt anpassade CV kontrolleras mot ditt riktiga CV innan det skickas. Finns påståendet inte i din historik skickas det inte.',
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
  final: {
    h2a: 'Ditt nästa jobb är',
    h2b: 'redan utlagt.',
    sub: 'Någonstans i kvällens jakt.',
    cta: 'Ladda upp ditt CV',
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
    email: 'E-post',
    password: 'Lösenord',
    minChars: 'Minst 8 tecken.',
    busySignin: 'Loggar in…',
    busyRegister: 'Skapar konto…',
    submitSignin: 'Logga in',
    submitRegister: 'Skapa konto och börja jaga',
    newHere: 'Ny här?',
    createAccount: 'Skapa ett konto',
    alreadyHunting: 'Jagar du redan med oss?',
    signIn: 'Logga in',
    footerLine: 'Jagar två gånger om dagen · poängsätter ärligt · inget skickas utan dig',
    privacy: 'Integritetsmeddelande',
    errWrong: 'Fel e-post eller lösenord',
    errExists: 'Den e-postadressen har redan ett konto, eller så är lösenordet för kort',
  },
};

export const dicts = { en, sv } as const;
export type Locale = keyof typeof dicts;
