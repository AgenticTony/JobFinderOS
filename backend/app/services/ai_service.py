"""
AI Service for JobFinderOS using GLM (Z.ai).

Adapted from TalentHive's ai_service.py — same client setup (so an existing
GLM_API_KEY works unchanged), same robustness patterns (markdown-fence JSON
extraction, GLM-5 reasoning_content fallback, error defaults), same structured
scoring philosophy — but inverted to serve the job seeker:

  TalentHive: "Is this candidate right for this job?" (many CVs, one job)
  JobFinderOS: "Is this job right for me?"           (one CV, many jobs)

Two operations:
  1. extract_profile — CV text -> structured seeker profile
  2. match_job       — profile + CV vs one job -> fit score, tier, apply
                       recommendation, tailored cover note
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, Optional

import httpx
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Raised when the AI service cannot complete a request."""


class AIService:
    """Service for AI profile extraction and job matching using GLM via Z.ai."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GLM_API_KEY
        if not self.api_key:
            raise ValueError(
                "GLM_API_KEY environment variable is required "
                "(same key as TalentHive works)"
            )

        # Z.ai GLM endpoint (OpenAI-compatible) — TalentHive setup reused.
        # glm-4.5 here is a reasoning model: time-to-first-token scales with
        # prompt size, and full CV+JD prompts regularly take 60-120s when
        # Z.ai is loaded. Short connect timeout, generous read timeout,
        # one retry — worst case ~6 min per call, never an infinite hang.
        http_client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=60.0),
            follow_redirects=True,
        )
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=settings.GLM_BASE_URL,
            http_client=http_client,
            max_retries=1,
        )
        # glm-4.6 with thinking disabled scores a job in ~5s. If thinking is
        # enabled (slower, more deliberation), 6000 tokens lets the model
        # finish its reasoning AND emit the JSON answer — 2000 gets eaten by
        # reasoning alone and the response comes back empty.
        self.model = settings.GLM_MODEL
        self.thinking = settings.GLM_THINKING
        self.max_tokens = 2000 if self.thinking == "disabled" else 6000
        logger.info(
            "Initialized GLM AI service: model=%s thinking=%s", self.model, self.thinking
        )

    # ------------------------------------------------------------------
    # Operation 1: profile extraction
    # ------------------------------------------------------------------

    def extract_profile(self, cv_text: str) -> Dict[str, Any]:
        """Extract a structured seeker profile from CV text."""
        system_prompt = """You are an expert career coach analyzing a CV to build a structured job-seeker profile.
Extract facts ONLY — do not invent, embellish, or guess. If a field is not present in the CV, leave it empty.

LANGUAGE RULE: The CV may be written in any language (English, Swedish, French, etc.).
Translate all extracted facts to English internally. The output must be identical regardless of CV language.

Extract:
- Contact details (name, email, phone, location) if present
- Professional title / current role
- Total years of professional experience
- Skills with honest proficiency levels (only those evidenced in the CV)
- Recent roles (most recent 3-4) with title, company, period, key highlights
- Education and certifications
- 15-25 job-search keywords that best represent this person for job matching
- A 2-3 sentence professional summary, written in neutral professional style
  (e.g. "Fullstack developer focused on...") — never "the candidate" or
  "the applicant"; this summary is shown to the person themselves

Respond with ONLY valid JSON (no markdown):
{
  "full_name": "string or empty",
  "email": "string or empty",
  "phone": "string or empty",
  "location": "string or empty",
  "professional_title": "string or empty",
  "experience_years": 0,
  "skills": [{"name": "skill", "level": "expert|advanced|intermediate|basic"}],
  "recent_roles": [{"title": "", "company": "", "period": "", "highlights": ""}],
  "education": [{"degree": "", "field": "", "institution": "", "year": ""}],
  "certifications": ["..."],
  "keywords": ["..."],
  "summary": "2-3 sentence professional summary"
}"""

        result = self._complete(system_prompt, f"## CV\n{cv_text}\n\nExtract the structured profile as JSON.")
        return self._parse_json(result)

    # ------------------------------------------------------------------
    # Operation 2: job matching (the inverted TalentHive screening)
    # ------------------------------------------------------------------

    def match_job(
        self,
        profile_context: str,
        cv_text: str,
        job_description: str,
    ) -> Dict[str, Any]:
        """
        Assess one job against the seeker's profile.

        Args:
            profile_context: Compact text summary of the structured profile + preferences
            cv_text: Full CV text (evidence base)
            job_description: The job posting text

        Returns:
            Dict with score, tier, reasoning, matched/missing skills,
            recommendation (apply/maybe/skip), confidence
        """
        system_prompt = self._build_matching_prompt()

        user_message = f"""
## My Profile & Preferences
{profile_context}

## My CV (evidence)
{cv_text[:5000]}

## Job Posting
{job_description[:5000]}

Evaluate this job for me and respond with ONLY valid JSON in the required format.
"""

        raw = self._complete(system_prompt, user_message, temperature=0.0)  # scoring: deterministic
        parsed = self._parse_json(raw)
        if not parsed:
            # Unparseable output is a TRANSPORT/FORMAT failure, not a score.
            # Raise so the matcher leaves the job 'new' for retry — never
            # treat it as a 0-point match (which would dismiss it forever).
            raise ValueError("Unparseable JSON from model (truncated/malformed response)")

        return {
            "score": self._clamp_score(parsed.get("score", 0)),
            "tier": self._tier_for_score(parsed.get("score", 0), parsed.get("tier")),
            "reasoning": parsed.get("reasoning", ""),
            "matched_skills": parsed.get("matched_skills", []),
            "missing_skills": parsed.get("missing_skills", []),
            "transferable_skills": parsed.get("transferable_skills", []),
            "recommendation": parsed.get("recommendation", "maybe"),
            "confidence": parsed.get("confidence", "medium"),
        }

    # ------------------------------------------------------------------
    # Operation 3: application tailoring (post-approval stage)
    # ------------------------------------------------------------------

    def tailor_application(
        self,
        profile_context: str,
        cv_text: str,
        job_description: str,
        correction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build the tailored application package for one approved job:
        a rewritten cover letter (first person) and a restructured CV
        (same facts, re-emphasized for this role), plus a summary of the
        changes addressed to the job seeker.
        """
        system_prompt = """You are an expert career coach preparing a job application package.
The person reading your output IS the job seeker (never say "the candidate").

You will be given a job seeker's CV, their profile summary, and ONE job posting they
have approved. Produce a tailored application package.

═══════════════════════════════════════
RULES — READ BEFORE WRITING ANYTHING
═══════════════════════════════════════
- ZERO FABRICATION: every employer, date, skill, title, achievement and metric in
  the tailored CV and cover letter must be traceable to the original CV. Never
  invent, upgrade, or embellish anything.
- The tailored CV keeps the SAME facts but re-presents them for THIS job:
  * A professional summary line tuned to the role
  * Skills reordered to front-load what this job asks for
  * Experience bullets from the original CV most relevant to this role kept
    and made prominent; clearly irrelevant material trimmed or shortened
  * Keep the original chronology and job titles — do not rename roles
- The cover letter is in FIRST PERSON ("I..."), under 220 words, addressed to
  the employer, referencing 1-2 concrete, real pieces of experience that fit
  THIS job's requirements. Warm, direct, no fluff, no generic filler.
- changes_summary: 3-5 short bullets addressed to the job seeker in second
  person ("Moved your Azure experience to the top because this role...").
- Write both documents in the language of the job posting (a German posting
  gets German documents; English posting gets English). If the posting mixes
  languages or is ambiguous, use its dominant language; if still unclear,
  use the first of my working languages listed in My Profile. The
  changes_summary always matches the CV's original language.

Output plain text with clear section headers (e.g. "PROFESSIONAL SUMMARY",
"SKILLS", "EXPERIENCE") — no markdown asterisks or hashes.

Respond with ONLY valid JSON (no markdown):
{
  "cover_letter": "full cover letter text, first person, with greeting and sign-off",
  "tailored_cv": "full tailored CV text with section headers",
  "changes_summary": ["Moved ... because ...", "Trimmed ... because ..."]
}"""

        user_message = f"""
## My Profile & Preferences
{profile_context}

## My CV (source of truth — every fact must come from here)
{cv_text[:9000]}

## The Job I Approved
{job_description[:6000]}

Prepare my tailored application package.
"""
        if correction:
            # Fabrication-guard regeneration instruction (WO-01 Layer C):
            # the named untraceable claims, so the retry targets the defect
            user_message += f"\n\n--- CORRECTION ---\n{correction}"

        raw = self._complete(system_prompt, user_message)
        parsed = self._parse_json(raw)

        return {
            "cover_letter": parsed.get("cover_letter", ""),
            "tailored_cv": parsed.get("tailored_cv", ""),
            "changes_summary": parsed.get("changes_summary", []),
        }

    # ------------------------------------------------------------------
    # Operation 4: onboarding — country-aware search query suggestions
    # ------------------------------------------------------------------

    def suggest_search_queries(self, cv_text: str, country: str, mode: str = "field") -> Dict[str, Any]:
        """
        Suggest job-search queries for THIS user's CV in THIS country, shaped
        by their chosen search strategy:

          field    — stay in my field (titles from the CV; default)
          adjacent — open to adjacent roles too
          widen    — my field is shrinking / changing direction: derive queries
                     from underlying SKILLS, mapping to job families the CV
                     never mentions (the career-changer / dying-occupation path)

        Returns two labeled clusters; the user approves/edits in the wizard.
        The mode is ALWAYS the user's own choice — never inferred from age
        or any protected characteristic (design decision, see CLAUDE.md).
        """
        from app.data.geo import COUNTRIES

        country_info = COUNTRIES.get(country.upper())
        language_hint = (
            country_info["query_language"] if country_info else "the country's main language"
        )
        country_name = country_info["name"] if country_info else country

        mode_rules = {
            "field": """STRATEGY = STAY IN MY FIELD:
- 5-8 queries, all direct titles for the person's actual profession and level
- worth_a_look: at most 1-2 gentle adjacent variants, clearly same field""",
            "adjacent": """STRATEGY = OPEN TO ADJACENT ROLES:
- 4-6 direct titles for the person's actual profession and level
- worth_a_look: 3-5 adjacent-role variants they could plausibly win at the
  same level, where their skills transfer (same family, different seat)""",
            "widen": """STRATEGY = WIDEN MY OPTIONS (field shrinking or career change):
- from_your_experience: only 2-3 anchor titles (their realistic direct shots)
- worth_a_look is the MAIN event: 5-7 queries from job families the CV NEVER
  names, derived by decomposing the CV into underlying capabilities
  (e.g. cash/chip handling -> cash integrity; floor conflict de-escalation ->
  customer service under pressure; regulatory compliance -> regulated
  environments; staff scheduling -> operations coordination) and mapping them
  to concrete local job titles (kassabiträde, lagermedarbetare, kontorsassistent,
  kundtjänst, säkerhet, bank, production, office admin...)
- Each worth_a_look query MUST carry a one-sentence 'why' addressed to the
  person in second person, naming the specific CV evidence that maps to it
- Favour fields with genuine labour demand; respect the person's level —
  entry-adjacent, not fantasy jumps""",
        }.get(mode, "")

        system_prompt = f"""You are an expert career advisor configuring a job-search tool for one user.
Based on their CV, suggest SEARCH QUERIES for job boards in {country_name}.

RULES:
- Queries are 1-4 words — the exact words a recruiter puts in the job title
- Write them in {language_hint}, matching how jobs are actually titled there
- Derive from the person's ACTUAL experience and level — never assume tech
  if the CV is a nurse's, never assume senior if the CV is junior
- No location or salary words — titles and role keywords only

{mode_rules}

Respond with ONLY valid JSON (no markdown):
{{
  "from_your_experience": ["query1", "query2"],
  "worth_a_look": [{{"query": "query", "why": "one sentence, second person, citing their CV evidence"}}]
}}"""

        raw = self._complete(system_prompt, f"## CV\n{cv_text[:6000]}\n\nSuggest search queries.")
        parsed = self._parse_json(raw)

        direct = [str(q).strip() for q in parsed.get("from_your_experience", []) if str(q).strip()][:8]
        pivot = []
        for item in parsed.get("worth_a_look", []):
            if isinstance(item, dict) and item.get("query"):
                pivot.append(
                    {"query": str(item["query"]).strip(), "why": str(item.get("why", "")).strip()}
                )
            elif isinstance(item, str) and item.strip():
                pivot.append({"query": item.strip(), "why": ""})
        return {"from_your_experience": direct, "worth_a_look": pivot[:7]}

    def _build_matching_prompt(self) -> str:
        """Job-seeker-direction adaptation of TalentHive's structured screening prompt."""
        return """You are an expert career advisor performing structured job matching.
Your job is to assess whether a SPECIFIC JOB is worth applying to for a SPECIFIC JOB SEEKER,
producing consistent, evidence-based recommendations.

CRITICAL: You must follow this exact process in order. Do not skip steps.

═══════════════════════════════════════
VOICE — YOU ARE TALKING TO THE JOB SEEKER
═══════════════════════════════════════
The person reading your output IS the job seeker — this is not a recruiter tool.
- The "reasoning" field must address them directly in second person:
  "Your tech stack (Next.js, Python) matches what this job asks for...",
  "You have 5 years of...", "This role requires... which you haven't shown yet."
- NEVER use third-person recruiter language such as "the candidate",
  "the applicant", or "their experience". Always "you/your".
- Frame everything as: what YOU bring vs what THIS JOB asks for.

═══════════════════════════════════════
STEP 1 — EXTRACT JOB FACTS (before any scoring)
═══════════════════════════════════════
Regardless of the job posting's language, extract:
- Required skills, technologies, frameworks
- Required years of experience and seniority level
- Required education/certifications
- Location constraints, remote policy
- Employment type and salary if stated
- Nice-to-have skills (not core requirements)

═══════════════════════════════════════
STEP 2 — SCORE (per requirement)
═══════════════════════════════════════
For each job requirement, score it against the seeker's CV:
- Fully met with clear CV evidence: +12 points
- Partially met or transferable evidence: +6 points
- Not present in CV: 0 points (do not subtract unless critical)
- Critical hard requirement completely missing (e.g. required license, required language, on-site when seeker needs remote): -8 points

Also weigh the seeker's STATED PREFERENCES:
- Job matches preferred roles/locations: no change
- Violates a stated hard preference (e.g. location, on-site vs remote): -8 points

Add up the points, normalize to 0-100. This is your score.

═══════════════════════════════════════
STEP 3 — TIER (assign based on score)
═══════════════════════════════════════
- excellent_match: 80-100 — meets 80%+ of requirements with clear evidence. Apply immediately.
- good_match: 50-79 — meets core requirements or has strong transferable skills. Worth applying.
- stretch: 30-49 — notable gaps but plausible; a learning move or growth bet.
- poor_match: 0-29 — fails core requirements. Do not apply.

CALIBRATION ANCHORS — score against these reference cases:
- ~90: hits essentially every listed requirement with named CV evidence, right
  location, right language, right seniority. Nothing material is missing.
- ~75: strong on the core stack (most requirements evidenced), but ONE real
  gap — a missing certification, less seniority than asked, or adjacent-not-
  exact domain experience.
- ~55: transferable core (can plausibly do the job) but multiple clear gaps —
  some required tools unproven, or a significant seniority/domain jump.
- ~35: the direction is plausible but the job's core requirement rests mainly
  on experience the CV does not show. A growth bet at best.
- ~15: core requirements are absent; only generic soft skills overlap.
Score relative to these anchors. Reserve 80+ for the ~90 anchor's level.

STRICT RULE: The tier MUST match the score. No exceptions.

═══════════════════════════════════════
STEP 4 — TRANSFERABLE SKILLS
═══════════════════════════════════════
Actively look for seeker experience that maps to this job even if the industry or
job title differs (TalentHive's differentiator — kept, direction inverted).
Do not penalize the JOB because the seeker comes from a different industry when
the underlying skills are demonstrably transferable.

═══════════════════════════════════════
STEP 5 — COVER NOTE
═══════════════════════════════════════
Write a short, tailored application cover note (under 120 words), in FIRST PERSON
("I...") — it is sent by the job seeker to the employer:
- Open with the specific value they bring for THIS job
- Reference 1-2 concrete pieces of their actual experience
- Close with genuine interest in the role
- Professional, warm, no fluff, no fabricated experience
- Never invent skills or experience that are not in the CV

═══════════════════════════════════════
CONSISTENCY CHECK (run before outputting)
═══════════════════════════════════════
✓ Score matches tier definition
✓ Matched skills are backed by actual CV content
✓ Missing skills are actual job requirements, not assumptions
✓ Cover note only references evidenced experience
✓ Reasoning uses "you/your" throughout — no "the candidate"

═══════════════════════════════════════
RULES
═══════════════════════════════════════
- Judge skills and evidence only — ignore the seeker's name, age, gender,
  nationality, and CV language when scoring.
- Respect stated preferences as constraints where they are explicit.
- If the job posting content is too thin to assess, set confidence to "low".

Respond with ONLY valid JSON (no markdown):
{
  "score": 0-100,
  "tier": "excellent_match|good_match|stretch|poor_match",
  "reasoning": "Why this score — addressed directly to the job seeker in second person (e.g. 'Your tech stack (Next.js, Python) matches what this job asks for. You have not yet shown X, which they require.')",
  "matched_skills": ["skill1", "skill2"],
  "missing_skills": ["skill1"],
  "transferable_skills": ["skill1"],
  "recommendation": "apply|maybe|skip",
  "confidence": "high|medium|low"
}"""

    def judge_fabrication(self, source_of_truth: str,
                          tailored_text: str) -> list:
        """WO-02: the fabrication judge, IN PRODUCTION on every draft.

        A FRESH call with no tailoring context — asking the same
        conversation to grade its own output measures agreeableness,
        not fidelity. The judge is the only mechanism with demonstrated
        catches on real output (WO-01's live runs: every real fabrication
        was semantic — invented work authority, duties, practices —
        invisible to deterministic atom checks). Returns a list of
        {"claim", "why"} dicts; empty list = faithful.
        """
        system_prompt = """You are a strict fact-checker for job applications. You are
given a candidate's SOURCE OF TRUTH (CV plus their own stated preferences)
and a TAILORED document derived from it. List every claim about this
person in the tailored document that the source does not support —
invented employers, shifted dates, upgraded titles, invented credentials,
inflated metrics, invented work authorization, technologies or duties they
have not had. Translation is legitimate (an English CV may be tailored
into Swedish); fabrication is not. Respond with ONLY valid JSON:
{"unsupported": [{"claim": "...", "why": "..."}]}
An empty list means the document is faithful."""
        user_message = (
            f"## SOURCE OF TRUTH\n{source_of_truth[:9000]}\n\n"
            f"## TAILORED DOCUMENT\n{tailored_text[:9000]}\n\n"
            "List every unsupported claim."
        )
        raw = self._complete(system_prompt, user_message, temperature=0.0)
        parsed = self._parse_json(raw)
        if "unsupported" not in parsed:
            # FAIL CLOSED (review finding): _parse_json returns {} on any
            # decode failure, which read as 'faithful' and shipped the
            # document. Worse, truncation CORRELATES with guilt — a
            # document with many fabrications produces a long unsupported
            # array, hits max_tokens mid-JSON, parses to {}, and passes.
            # Same rule as match_job: unparseable output is a
            # transport/format failure, never a verdict. The caller's
            # except marks the draft failed.
            raise ValueError(
                "Unparseable JSON from fabrication judge "
                "(truncated/malformed response)"
            )
        unsupported = parsed["unsupported"]
        return unsupported if isinstance(unsupported, list) else []

    # ------------------------------------------------------------------
    # Prompt versioning
    # ------------------------------------------------------------------

    #: Bump when the scoring RUBRIC changes meaning (new anchors, new tier
    #: bands). The hash suffix is derived from the prompt text itself, so an
    #: accidental edit changes the version even if this constant is not
    #: touched — tests/test_calibration.py fails loudly when that happens.
    MATCHING_PROMPT_MAJOR = "m2"

    @classmethod
    def matching_prompt_version(cls) -> str:
        """Stable id for the scoring prompt that produced a score.

        Scores from different versions are NOT comparable: re-running the
        SAME model on the SAME job across a prompt change moved scores by
        up to 26 points. Stored on match_results.prompt_version so a stale
        backlog is detectable instead of silently mis-ranked.
        """
        body = cls._build_matching_prompt(cls.__new__(cls))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
        return f"{cls.MATCHING_PROMPT_MAJOR}-{digest}"

    # ------------------------------------------------------------------
    # Shared plumbing (TalentHive patterns)
    # ------------------------------------------------------------------

    def _complete(self, system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
        """Run a chat completion and return raw content text."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            # Scoring calls pass 0.0. NOTE: temperature 0 makes sampling
            # greedy, NOT deterministic — MoE routing and batched GPU
            # reduction still vary. Measured on glm-5.1, same CV + same job,
            # 5 runs: spread 6-10 points at 0.0 (vs 12-16 at 0.3). Treat
            # scores as noisy to about +/-7, never as reproducible values.
            temperature=temperature,
            max_tokens=self.max_tokens,
            extra_body={"thinking": {"type": self.thinking}},
        )

        result_text = response.choices[0].message.content

        # GLM-5 puts reasoning in reasoning_content; recover JSON from it if
        # the final content came back empty (TalentHive workaround kept)
        if not result_text and hasattr(response.choices[0].message, "reasoning_content"):
            reasoning = response.choices[0].message.reasoning_content
            if reasoning and "{" in reasoning:
                json_match = re.search(r"\{[\s\S]*\}", reasoning)
                if json_match:
                    result_text = json_match.group(0)

        if not result_text:
            raise AIServiceError("GLM returned empty content")

        return result_text

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        """Parse the AI response, handling markdown code blocks (TalentHive pattern)."""
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            text = text[start:end].strip()

        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError as e:
            logger.error("Failed to parse GLM response as JSON: %s", e)
            # Last resort: outermost braces
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return {}
            return {}

    @staticmethod
    def _clamp_score(score) -> int:
        try:
            return max(0, min(100, int(round(float(score)))))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _tier_for_score(score, tier_hint=None) -> str:
        """Enforce the strict tier-from-score rule regardless of what the model said."""
        score = AIService._clamp_score(score)
        if score >= 80:
            return "excellent_match"
        if score >= 50:
            return "good_match"
        if score >= 30:
            return "stretch"
        return "poor_match"


# Module-level cache — TalentHive pattern
_ai_service: Optional[AIService] = None


def get_ai_service() -> AIService:
    """Get or create the AI service instance. Raises if GLM_API_KEY missing."""
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service


def ai_service_available() -> bool:
    """True if the AI service can be constructed (key configured)."""
    return bool(settings.GLM_API_KEY)
