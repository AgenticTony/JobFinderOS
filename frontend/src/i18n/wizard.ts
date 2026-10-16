// Onboarding wizard copy, both languages. Same rules as ./console.ts:
// `wizardSv` mirrors `wizardEn` exactly; interpolation is by function;
// stored values (country codes, language names, work-rights codes,
// search modes) stay English in the payload — only labels live here.

export const wizardEn = {
  title: "Set up your job hunt",
  subtitle: "Takes a minute — makes everything after it personal",
  close: "Close",
  steps: [
    "Your CV",
    "Country",
    "Location",
    "Languages",
    "Job titles",
    "Confirm",
  ],
  cv: {
    title: "First — your CV",
    body: "Every job you see is scored against your CV — it's the source of truth for your whole hunt. Have it ready as a PDF or Word (.docx).",
    ready:
      "CV on file — continue when ready (or drop a new one to replace it).",
  },
  country: {
    title: "Where are you job hunting?",
    sourceHint: {
      SE: "Platsbanken — every public listing",
      GB: "Reed.co.uk — every sector",
    } as Record<string, string>,
  },
  rights: {
    title: "Do you need visa sponsorship to work here?",
    body: "Postings that require citizenship or security clearance are hidden for sponsorship seekers; everything else is shown with an eligibility note. You can change this anytime in Profile.",
  },
  area: {
    title: "Which area should we search?",
    region: "Region",
    selectRegion: "Select a region…",
    municipalities: "Cities / municipalities",
    municipalitiesHint: (region: string | null) =>
      `(pick one or several — none selected means all of ${region ?? "the region"})`,
    selectRegionFirst: "Select a region first…",
    municipalitiesAria: "Municipalities",
    radius: (town: string) => `Commute radius around ${town}`,
    radiusHint: "(catches nearby towns without picking them)",
    radiusAria: "Commute radius",
    radiusExact: "Selected towns only",
    radiusKm: (km: number) => `+${km} km`,
    radiusNote: (km: number, town: string) =>
      `Jobs within ${km} km of ${town} — the neighbouring towns come to you.`,
    includeRemote: "Include remote jobs",
    includeRemoteHint:
      "Also search worldwide remote boards (remotive, jobicy…). Leave off to search strictly your area.",
    remoteOnly: "Remote jobs only",
    remoteOnlyHint: "Skip anything that requires being on-site",
  },
  languages: {
    title: "Which languages do you work in?",
    body: "Jobs posted in other languages are filtered out before matching — English always passes. You can change this anytime under Edit setup.",
  },
  titles: {
    title: "What roles should we hunt for?",
    modes: {
      field: { label: "Stay in my field", hint: "Job titles from my CV" },
      adjacent: {
        label: "Open to adjacent roles",
        hint: "My field, plus near-neighbours",
      },
      widen: {
        label: "Widen my options",
        hint: "My field is shrinking or I’m changing direction — use my transferable skills",
      },
    },
    current: (n: number) => `Your current search titles (${n})`,
    fresh: "Get fresh AI suggestions for this strategy →",
    suggestFailed: "Couldn't load AI suggestions.",
    tryAgain: "Try again",
    readingWiden: "Reading your CV’s transferable skills…",
    reading: "AI is reading your CV…",
    usually: "usually 10-45 seconds",
    // WO-31: the suggestion request waits in the AI queue
    queued: (about: string) =>
      `You're in a queue for AI suggestions${about} — they'll appear here by themselves.`,
    fromExperience: "From your experience",
    professions: "Your professions — official job taxonomy",
    professionsHint: "(catches jobs whose title uses other words)",
    widenHeading: "Jobs your skills open up",
    worthALook: "Worth a look — you might not have thought of these",
    addPlaceholder: "Add your own search title…",
    addAria: "Add search title",
  },
  confirm: {
    title: "Ready — here's your setup",
    country: "Country",
    area: "Area",
    allOf: (region: string) => `All of ${region}`,
    everywhere: "everywhere",
    remote: "Remote",
    remoteOnly: "remote jobs only",
    localRemote: "local + remote",
    strictlyLocal: "strictly local",
    languages: "Languages",
    workRights: "Work rights",
    strategy: "Strategy",
    jobTitles: "Job titles",
    queries: (n: number) => `${n} search ${n === 1 ? "query" : "queries"}`,
    firstHunt:
      "Your first targeted hunt starts automatically — jobs will be scraped from your country's boards, filtered to your area, and ranked against your CV.",
  },
  finishFailed: (err: string) =>
    `Couldn't save your setup — nothing was lost, try again: ${err}`,
  back: "Back",
  stepOf: (step: number, total: number) => `Step ${step} of ${total}`,
  continue: "Continue",
  start: "Start hunting",
};

export type WizardDict = typeof wizardEn;

export const wizardSv: WizardDict = {
  title: "Ställ in din jobbjakt",
  subtitle: "Tar en minut — gör allt som kommer efter personligt",
  close: "Stäng",
  steps: ["Ditt CV", "Land", "Område", "Språk", "Jobbtitlar", "Bekräfta"],
  cv: {
    title: "Först — ditt CV",
    body: "Varje jobb du ser poängsätts mot ditt CV — det är sanningskällan för hela din jakt. Ha det redo som PDF eller Word (.docx).",
    ready:
      "CV uppladdat — fortsätt när du är redo (eller släpp ett nytt för att byta ut det).",
  },
  country: {
    title: "Var söker du jobb?",
    sourceHint: {
      SE: "Platsbanken — varje offentlig annons",
      GB: "Reed.co.uk — alla sektorer",
    },
  },
  rights: {
    title: "Behöver du visumsponsring för att arbeta här?",
    body: "Annonser som kräver medborgarskap eller säkerhetsprövning döljs för den som behöver sponsring; allt annat visas med en notering om behörighet. Du kan ändra det när som helst under Profil.",
  },
  area: {
    title: "Vilket område ska vi söka i?",
    region: "Region",
    selectRegion: "Välj en region…",
    municipalities: "Städer / kommuner",
    municipalitiesHint: (region) =>
      `(välj en eller flera — inga valda betyder hela ${region ?? "regionen"})`,
    selectRegionFirst: "Välj en region först…",
    municipalitiesAria: "Kommuner",
    radius: (town) => `Pendlingsradie runt ${town}`,
    radiusHint: "(fångar orter i närheten utan att du behöver välja dem)",
    radiusAria: "Pendlingsradie",
    radiusExact: "Bara valda orter",
    radiusKm: (km) => `+${km} km`,
    radiusNote: (km, town) =>
      `Jobb inom ${km} km från ${town} — grannorterna kommer till dig.`,
    includeRemote: "Ta med distansjobb",
    includeRemoteHint:
      "Sök även globala distansjobbsajter (remotive, jobicy…). Låt vara avstängt för att bara söka i ditt område.",
    remoteOnly: "Bara distansjobb",
    remoteOnlyHint: "Hoppa över allt som kräver att du är på plats",
  },
  languages: {
    title: "Vilka språk arbetar du på?",
    body: "Jobb som annonseras på andra språk filtreras bort innan matchningen — engelska släpps alltid igenom. Du kan ändra det när som helst under Ändra sökningen.",
  },
  titles: {
    title: "Vilka roller ska vi jaga efter?",
    modes: {
      field: {
        label: "Stanna inom mitt område",
        hint: "Jobbtitlar från mitt CV",
      },
      adjacent: {
        label: "Öppen för närliggande roller",
        hint: "Mitt område, plus närliggande",
      },
      widen: {
        label: "Bredda mina möjligheter",
        hint: "Mitt område krymper eller jag byter riktning — använd mina överförbara kompetenser",
      },
    },
    current: (n) => `Dina nuvarande söktitlar (${n})`,
    fresh: "Hämta nya AI-förslag för den här strategin →",
    suggestFailed: "Kunde inte hämta AI-förslag.",
    tryAgain: "Försök igen",
    readingWiden: "Läser de överförbara kompetenserna i ditt CV…",
    reading: "AI:n läser ditt CV…",
    usually: "brukar ta 10–45 sekunder",
    queued: (about) =>
      `Du står i kö för AI-förslag${about} — de dyker upp här av sig själva.`,
    fromExperience: "Från din erfarenhet",
    professions: "Dina yrken — officiell yrkestaxonomi",
    professionsHint: "(fångar jobb vars titel använder andra ord)",
    widenHeading: "Jobb som dina kompetenser öppnar",
    worthALook: "Värda en titt — de här kanske du inte har tänkt på",
    addPlaceholder: "Lägg till en egen söktitel…",
    addAria: "Lägg till söktitel",
  },
  confirm: {
    title: "Klart — så här ser din sökning ut",
    country: "Land",
    area: "Område",
    allOf: (region) => `Hela ${region}`,
    everywhere: "överallt",
    remote: "Distans",
    remoteOnly: "bara distansjobb",
    localRemote: "lokalt + distans",
    strictlyLocal: "bara lokalt",
    languages: "Språk",
    workRights: "Arbetstillstånd",
    strategy: "Strategi",
    jobTitles: "Jobbtitlar",
    queries: (n) => `${n} ${n === 1 ? "sökfråga" : "sökfrågor"}`,
    firstHunt:
      "Din första riktade jakt startar automatiskt — jobb skrapas från ditt lands jobbsajter, filtreras till ditt område och poängsätts mot ditt CV.",
  },
  finishFailed: (err) =>
    `Kunde inte spara dina inställningar — inget har gått förlorat, försök igen: ${err}`,
  back: "Tillbaka",
  stepOf: (step, total) => `Steg ${step} av ${total}`,
  continue: "Fortsätt",
  start: "Börja jaga",
};
