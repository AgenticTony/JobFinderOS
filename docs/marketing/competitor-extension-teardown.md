# Competitor autofill engines — extension teardown

> 2026-08-31. Companion to `aiapply-architecture-teardown.md`. Where that one
> answered *how do they match*, this answers *how do they apply* — the part of
> the product our roadmap still has as a TODO ("Playwright ATS drivers for
> structured portal applies").
>
> **Source:** Chrome extension packages, downloaded from Google's public CRX
> endpoint and unzipped. Extension code ships to every user's disk and is
> reviewable by design; reading it is the same act as reading a site's served
> JavaScript. No authentication, no probing, no paid account.
>
> **The line on use:** the *architecture* below is the lesson and it is free to
> learn from. Their selector tables are their work product, built over years of
> maintenance — understanding the shape is legitimate, copying the contents is
> not, and would also be a maintenance trap since they update theirs remotely.

## 1. What is actually available to look at

| source | yield | status |
|---|---|---|
| **Chrome extensions** | **very high** — full client source | 4 competitors ship one; 2 torn down below |
| Public GitHub org | high, when it exists | Only AIApply has code; Jobright's 36 repos are curated job lists, not code |
| JS sourcemaps | would be total | **All stripped** — checked Simplify, Jobright, JobCopilot. Good practice on their part |
| DNS TXT / headers | vendor inventory | Done in `competitor-tech-stacks.md` |
| npm / PyPI | none | No first-party packages published |
| Hiring take-homes | very high | AIApply only |

Extensions are the richest remaining vector by a wide margin, and every
auto-apply competitor has one:

| product | extension id | torn down |
|---|---|---|
| Simplify Copilot | `pbanhockgagggenencehbnadejlgchfc` | ✅ |
| LazyApply | `pgnfaifdbfoiehcndkoeemaifhhbgkmm` | ✅ |
| Jobright Autofill | `odcnpipkhjegpefkfplmedhmkmmhmoko` | not yet |
| JobCopilot | `bnnacanndojemikeabbdejlamlecikcn` | not yet |

## 2. Two opposite architectures

### Simplify Copilot v3.1.3 — generic engine + declarative remote config

```
manifest v3
  host_permissions : ["*://*/*"]          ← runs everywhere
  content_scripts  : 1, matches *://*/*    ← ONE script, not per-site
  permissions      : cookies, webRequest, offscreen, webNavigation, alarms

background.js          2.58 MB   the engine
contentScriptMain.js   1.99 MB   the engine, page side
pdf.worker.js          1.53 MB   resume parsed CLIENT-SIDE
remoteConfig.json                57 ATS definitions, updatable without a store review
```

`remoteConfig.json` is the crown jewel and the whole architectural idea: the
engine is generic, and **every site-specific fact is data, not code**. Per ATS:

| key | coverage | what it does |
|---|---|---|
| `inputSelectors` | 55/57 | field selectors (ADP alone has 26) |
| `submittedSuccessPaths` | **54/57** | how to *prove* the submission landed |
| `trackedObjExtractors` | 56/57 | pull job/company back out for tracking |
| `submitButtonPaths` | 48/57 | |
| `applyButtonPaths` | 25/57 | |
| `proxySubmitButtons`, `continueButtonPaths` | 18/57 each | multi-step wizards |
| `embeddedPaths`, `containerPath` | iframe/embedded portals | |
| `defaultMethod` | 7/57 | widget-framework quirks (ADP is `dijit`) |

**57 ATS covered**, including the direct employer portals nobody thinks about:

```
ADP  ADP2  Amazon  Apple  AshbyHQ  Avature  BambooHR  BrassRing  BreezyHR
ByteDance  Comeet  Cursor  DayforceHCM  Dover  Eightfold  FreshTeam  Google
GovernmentJobs  Greenhouse  Gusto  Homerun  IBM  ICIMS  Indeed  JazzHR
JobScore  Jobvite  Lever  LinkedIn  Mechanize  Meta  Naukri  Netflix  Okta
OracleCloud  Paylocity  PhenomPeople  PinpointHQ  Polymer  Recruitee  Rippling
Roblox  SEEK  SmartRecruiters  SuccessFactors  TalNet  Taleo  Teamtailor
Tesla  TikTok  TriNetHire  Uber  Ultipro  Waymo  Workable  Workday
```

**The field ontology is the genuinely hard part**, and they solved it as data too
— `fieldCategories` (9 groups), `fieldNameAliases`, `fieldDependencies`,
`trackedInputProfileKeyCorrections`, `countryAbbreviationsToNames`,
`stateAbbreviationsToNames`:

```
Personal Information · Location · Documents · Education · Experience
EEO (gender, ethnicity, veteran, disability, LGBT, age bands)
Work Authorization → US / UK / Canada / Sponsorship
Social & Links · Other
```

Note **`Work Authorization (UK)` is first-class**, with
`workAuthLocationPatterns` enumerating regions per country. The UK is on their
map. Sweden is not.

**Where AI enters — and where it does not.** Two remote flags are on:
`fullAIAutofillEnabled: true`, `copilotAnswerUnifiedEnabled: true`. But
`inlineAnswerMinSize: {minWidth: 400, minHeight: 80}` gives the split away:
**AI is offered only on large free-text boxes** — the "why do you want to work
here" essays. Structured fields go through deterministic selectors. They spend
inference exactly where deterministic rules cannot reach, and nowhere else.

All traffic goes to `simplify.jobs` and `storage.googleapis.com`. **No model
provider is called from the client and no provider key ships in the bundle** —
inference is proxied through their own backend. Correct, and worth noting as the
baseline any client-side feature of ours must also meet.

### LazyApply v0.8.93 — one hardcoded script per board

```
manifest v3
  host_permissions : 53 explicit hosts (indeed, linkedin, glassdoor ×9 TLDs,
                     dice, careerbuilder, ziprecruiter, simplyhired, seek…)
  content_scripts  : 49                  ← per-site, not generic

linkedin.bundle.js  indeed.bundle.js  glassdoor.bundle.js  dice.bundle.js
monster.bundle.js   ziprecruiter.bundle.js  careerBuilder.bundle.js
seek.bundle.js      foundit.bundle.js   atsAutomation.bundle.js (394 KB)
jquery.min.js       bootstrap.min.js    ← in 2026
```

Everything points at `backend.lazyapply.com` (218 references). There is a
generic `atsAutomation.bundle.js` covering Greenhouse, Lever, AshbyHQ, Rippling
and VivaHR — but the core is 49 bespoke scripts.

**This is the architecture their reviews are complaining about.** A hardcoded
script per board breaks every time a board ships a DOM change, and a fix
requires a new build and a store review. It is the direct mechanical explanation
for a 2.1 Trustpilot score with 56% one-star, and it is a caution rather than a
pattern.

## 3. What we should take from this

Our roadmap item is *"Playwright ATS drivers for structured portal applies
(staged, human-confirmed)."* Two competitors have now shown us the fork:

1. **Build the engine generic and the sites declarative.** Simplify's split —
   one engine, 57 ATS definitions as data — is the correct shape, and it is the
   shape a solo maintainer needs most, because adding a portal becomes writing
   a config entry rather than shipping code. Ship the config from the server so
   a broken selector is a deploy, not a release.
2. **`submittedSuccessPaths` is the primitive our design is missing.** 54 of 57
   of their definitions carry an explicit way to *prove* a submission landed.
   Our invariant is "nothing is sent without explicit user approval" and our
   applications table records what we sent — but a staged, human-confirmed apply
   needs a machine-checkable success signal, or "sent" is an assumption. This is
   the single most portable idea in the teardown.
3. **Spend inference only where rules cannot reach.** Their `inlineAnswerMinSize`
   gate is the same instinct as our cheap gates: deterministic first, model only
   for the genuinely open-ended. It also tells us free-text screening questions
   are where the fabrication guard will next be needed — an essay answer is
   exactly where an invented claim would appear.
4. **The EEO and work-authorisation ontology is a real body of work.** Nine
   field categories with per-country work-auth patterns is not something to
   improvise at apply time. If we build portal applies, this is a design
   artifact to draft deliberately — and ours must be EU-shaped, where their
   US-centric EEO block largely does not apply and GDPR does.

## 4. Jobright and JobCopilot — the other two

Both are **generic** (`<all_urls>`), like Simplify. Neither uses LazyApply's
per-site approach. But they sit at opposite ends of the config-versus-inference
spectrum.

### Jobright Autofill v1.21.0 — AI-first field mapping

```
manifest v3 · built with Plasmo (React extension framework)
  host_permissions : http://*/*, https://*/*, <all_urls>
  content_scripts  : 2 — one generic, one scoped to jobright.ai/jobs/info/*
  14 files, 7.5 MB of JS
```

The manifest leaks their internal environments:
`preprod.jobright.ai`, `beta.jobright-internal.com`, `dev.jobright-internal.com`.

**The architecture is in one call.** The content script gathers the form's
elements and hands them off:

```js
sendToBackground({ name: "getGptResults",
                   body: { params: { elements: […], token: … } } })
```

Rather than resolving fields against a shipped selector table, they send the
**form structure itself** to a GPT-backed endpoint and let the model do the
mapping. That matches the `openai-domain-verification` TXT record found in
`competitor-tech-stacks.md`. Selector and xpath machinery is still present in
volume (5,354 / 547 references, with Workday, iCIMS, Greenhouse and Ashby
special-cased) — but the config is **compiled into the bundle**, so a broken
portal needs a new release and a store review, unlike Simplify's remote JSON.

> **A verification note worth recording.** A first pass counted 129 `llm` and
> 6 `openai` hits and nearly became a finding. Both were false positives:
> `llm` was `DiffieHellman` from a crypto polyfill, `openai` was Ant Design's
> `OpenAIFilled` / `OpenAIOutlined` icon components. Only `getGptResults`
> survived inspection. Substring counts are a hypothesis, not evidence.

**They automate ATS account creation.** Constants including
`PRE_AUTOFILL_ACCOUNT_SETUP_MISSING_STEPS_KEY`,
`…_PROGRESS_TITLE` and `…_PROMPT_DELAY_MS` show a flow that registers the user
on the portal *before* filling. Worth naming explicitly as something for us to
decide about rather than drift into: creating accounts on a third party's system
on a user's behalf is a materially different act from filling a form they opened,
and it sits badly beside our two-gate approval invariant.

### JobCopilot v1.5.27 — config-lite, AI-assisted, and it learns

The leanest of the four: **441 KB**, 45 files, and the most restrained
permissions of any (`webNavigation`, `storage`, `activeTab` — no `cookies`).

Its message-handler names are effectively an architecture diagram:

```
PROCESS_FORM            GET_AI_ANSWER           SHOW_FILLED_FIELDS
AUTOFILL_PROGRESS_TICK  CANCEL_AUTOFILL         SHOW_AUTOFILL_ERROR
UPDATE_TRAINING_ANSWER  JC_NEW_ANSWER_ADDED     SAVE_APPLICATION_TO_TRACKER
GET_API_ENDPOINT        GET_FRONTEND_URL        FETCH_PARTNER_LOGO
field types: date · free_text · multiple_choice
```

Four things stand out:

1. **An answer bank that learns.** `UPDATE_TRAINING_ANSWER` carries
   `{question, update_only}`, and `JC_NEW_ANSWER_ADDED` tags each with
   `question_type` and `category`. Answers are stored against the *question*, so
   the same screening question is answered consistently next time — and each
   application makes the next one cheaper. This is the best product idea in the
   whole teardown.
2. **A typed field taxonomy** (`date` / `free_text` / `multiple_choice`) that
   decides how a field is handled before AI is involved.
3. **Config-lite, not config-heavy.** A small `{hostname, selector}` table
   (Personio, Zoho Recruit, Bullhorn, Greenhouse, Workday, Lever, Ashby,
   Workable, Jobvite, SuccessFactors, SmartRecruiters, Recruitee) — a fraction
   of Simplify's 57, leaning on `GET_AI_ANSWER` for the rest. One entry has a
   visible copy-paste bug (`hostname: "personio"` paired with
   `selector: "form#ZohoRecruitForm"`), which is its own comment on the
   maintenance burden.
4. **Built for white-label.** `GET_API_ENDPOINT`, `GET_FRONTEND_URL` and
   `FETCH_PARTNER_LOGO` are exactly the parameterisation a resold deployment
   needs — consistent with the white-label B2B product on their site.

**Personio matters to us.** It is a German ATS, and JobCopilot is the only one of
the four that carries a European portal at all. The rest of the field —
Simplify's 57 ATS, Jobright, LazyApply — is US-shaped.

### The spectrum, in one table

| | site strategy | AI's role | config lives | failure mode |
|---|---|---|---|---|
| **Simplify** | generic engine | free-text only, gated at 400×80px | **remote JSON, 57 ATS** | biggest maintenance surface — but updates need no release |
| **JobCopilot** | generic engine | answers + a **learning bank** | small in-bundle table | thin coverage; leans on the model |
| **Jobright** | generic engine | **maps the fields themselves** | compiled in | inference cost per form; fixes need a store review |
| **LazyApply** | **49 per-site scripts** | none | hardcoded | breaks constantly — 2.1 Trustpilot, 56% one-star |

Read down that table and the trade is clear: **declarative is robust but heavy;
inference is adaptive but recurring.** Simplify buys robustness and pays in
maintenance, which they made cheap by moving config off the release cycle.
Jobright buys adaptability and pays per form, forever. JobCopilot splits the
difference and — uniquely — turns each user's own answers into the config.

## 5. Method

```bash
ID=pbanhockgagggenencehbnadejlgchfc
curl -sSL -o ext.crx \
  "https://clients2.google.com/service/update2/crx?response=redirect&prodversion=120&acceptformat=crx2,crx3&x=id%3D${ID}%26uc"
# CRX3 = 'Cr24' header then a zip; seek to the first PK\x03\x04 and unzip
python3 -c "import sys,zipfile,io;b=open('ext.crx','rb').read();zipfile.ZipFile(io.BytesIO(b[b.find(b'PK\x03\x04'):])).extractall('ext')"
```

Then read `manifest.json` first — `host_permissions` and `content_scripts`
alone separate a generic engine from a per-site one before any bundle is opened.
