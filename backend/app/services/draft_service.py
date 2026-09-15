"""
Draft service — the application-preparation stage between match approval
and submission.

Flow: user approves a match -> create_draft_for_job() runs AI tailoring ->
user reviews/edits the package in the UI -> submit_draft() sends it
(email with tailored PDFs, or browser/manual queue).

INVARIANT — THE ORIGINAL CV IS NEVER TOUCHED:
profile.cv_text and the stored PDF are read-only inputs here. Tailoring
always writes to THIS draft's cover_letter / tailored_cv columns, one row
per job. The original CV stays the permanent reference for every future
match and every future draft, and is attached unmodified alongside the
tailored documents when sending — the DRAFT'S SNAPSHOT of the CV path
(P1-5b), not the profile's current path, so a re-upload mid-review can
never pair old-tailored documents with a brand-new CV.
"""

import hashlib
import logging
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import utc_now
from app.models import Application, ApplicationDraft, JobPosting, MatchResult, Profile
from app.services import pdf_service
from app.services.ai_service import ai_service_available, get_ai_service
from app.services.cv_service import build_profile_context
from app.services.matcher_service import _job_text

logger = logging.getLogger(__name__)

# WO-01 Layer C: after this many regeneration attempts a surviving
# high-confidence finding is the model REPEATEDLY asserting something
# the CV does not support — block, don't retry again.
MAX_FABRICATION_RETRIES = 2

# Function words only — enough to tie a claim to the CV LINE it conflicts
# with without pretending to do semantics. Swedish + English articles,
# pronouns, conjunctions, prepositions. WO-23: the 2026-09-10 Experis
# block quoted lines that matched ONLY the preposition "under" — the
# Swedish function words that were missing are here now.
_CLAIM_STOPWORDS = frozenset(
    "the a an and or in of to for with my me i am is are both have has "
    "was were this that och eller i på med för att som jag är av en ett "
    "till från inte även under över inom utom genom utan mot hos när då "
    "både alla sin sina deras vår era detta dessa sedan redan".split()
)


def _cv_lines_near_claims(cv_text: str, high, max_lines: int = 2) -> list:
    """CV lines sharing informative tokens with an unsupported claim,
    RANKED by overlap (WO-23 fix).

    The live case this exists for (2026-09-02, owner account): the letter
    claimed "Jag är obehindrad i både svenska och engelska" while the CV
    line says "Svenska (god nivå, daglig användning)" — the block message
    naming only the claim read as "the guard didn't see my languages".
    Quoting the conflicting line puts the answer on screen. Pure token
    overlap, casefolded, unicode-word tokens; bounded, no semantics.

    2026-09-10 (fixed in WO-23): taking the first two matches top-down let
    a one-token line ("Under studietiden…") crowd out the line that
    actually carried the conflict — matches now rank by shared-token
    count, ties broken by CV order (stable sort).
    """
    import re

    def toks(s: str) -> set:
        return {t for t in re.findall(r"[^\W_]+", s.lower()) if len(t) >= 4}

    claim_tokens: set = set()
    for c in high:
        claim_tokens |= toks(str(getattr(c, "value", ""))) - _CLAIM_STOPWORDS
    if not claim_tokens:
        return []
    scored: list = []
    for order, line in enumerate(cv_text.splitlines()):
        line = line.strip()
        if len(line) < 8:
            continue
        overlap = len(toks(line) & claim_tokens)
        if overlap:
            trimmed = line if len(line) <= 140 else line[:137] + "..."
            scored.append((-overlap, order, trimmed))
    scored.sort()
    out: list = []
    for _, _, trimmed in scored[:max_lines]:
        if trimmed not in out:
            out.append(trimmed)
    return out


def fabrication_block_error(cv_text: str, high) -> str:
    """The Layer C block message. Names the unsupported claims and, when
    the CV has lines near them, quotes those lines — the fix is on screen
    instead of a support question."""
    named = ", ".join(sorted({str(c.value).split("|")[0] for c in high}))
    msg = (
        "Blocked by the fabrication guard: the tailored document "
        f"repeatedly asserts claims your CV does not support ({named})."
    )
    near = _cv_lines_near_claims(cv_text, high)
    if near:
        quoted = "; ".join(f'"{line}"' for line in near)
        msg += f" Your CV says: {quoted}."
    return msg + " Fix or confirm them below, or regenerate."


def get_ai_service_with_judge():
    """The AI service when the production judge is enabled; None when
    FABRICATION_JUDGE=off (emergency cost lever — Layer A still guards)."""
    if getattr(settings, "FABRICATION_JUDGE", "on") == "off":
        return None
    return get_ai_service()


def _attested_claims(draft: ApplicationDraft) -> list:
    """Claims the user has personally confirmed on THIS draft. They feed
    the guard source (a confirmed claim must not re-flag — that is the
    loop the 2026-09-10 Experis block had no exit from)."""
    from app.schemas.common import parse_json_list

    entries = parse_json_list(getattr(draft, "fabrication_attested", None))
    return [str(e.get("claim", "")).strip()
            for e in entries if isinstance(e, dict) and e.get("claim")]


def check_package(profile: Profile, job: JobPosting,
                  cover_letter: str, tailored_cv: str,
                  attested_claims=()) -> tuple:
    """WO-23 constraint 5: ONE implementation of 'check a package'.

    Layer A + the production judge over the GIVEN text, against the
    composed guard source: CV + user-entered profile context + this
    draft's attestations (vouched facts arrive via build_profile_context,
    which renders them outside the include_derived gate). Generation and
    re-check call this same function — duplicated thresholds are what
    dismissed 62 matcher rows on a single sample.

    Returns (high, advisory) claim lists; never mutates the draft row —
    the caller decides what a finding means (generation regenerates and
    blocks; re-check reports and resolves). Judge transport failures
    raise (fail-closed; the caller's except handles them).

    INVARIANT (2026-08-31, unchanged): the guard's source is never
    smaller than the generator's input. The CV is guarded BEFORE
    composing; the confirmed-facts block only ever ADDS to the source.
    """
    from app.services.ai_service import CV_GUARD_CHARS
    from app.services.cv_service import confirmed_facts_block
    from app.services.fabrication import split_tiers, unsupported_claims

    model_input = (
        f"{profile.cv_text[:CV_GUARD_CHARS]}\n"
        f"{build_profile_context(profile, include_derived=False)}"
        f"{confirmed_facts_block(attested_claims)}"
    )
    document = f"{cover_letter or ''}\n{tailored_cv or ''}"
    findings = unsupported_claims(
        model_input,
        document,
        allowed_names=[n for n in (job.company, job.title) if n],
    )
    high, advisory = split_tiers(findings)

    # WO-02: the production judge — semantic evidence Layer A cannot
    # see. Runs after Layer A is clean (no point judging a document
    # Layer A already rejected); a finding joins the high-confidence
    # path. Kill switch: FABRICATION_JUDGE=off.
    if not high and get_ai_service_with_judge():
        judge_claims = get_ai_service_with_judge().judge_fabrication(
            model_input, document)
        if judge_claims:
            high = [
                type("JudgeClaim", (), {
                    "value": c.get("claim", "?"),
                    "kind": "judge", "tier": "high",
                    "context": c.get("why", ""),
                })() for c in judge_claims
            ]
    return high, advisory

DRAFT_DIR = "uploads/drafts"


class DraftError(Exception):
    """Raised when a draft cannot be created or submitted."""


class DraftConflictError(DraftError):
    """Another dispatch owns the draft's submission ('sending' claim held,
    or an application already exists). The caller's package is FINE — the
    route maps this to 409, not 400."""


class EmailApplyDisabledError(DraftError):
    """Email apply is switched off (beta, 2026-09-01: it returns sending
    from the user's own Gmail). Not a conflict and not a bad package —
    the DraftError handler maps this to 400 with the beta message."""


def get_draft(db: Session, draft_id: int) -> Optional[ApplicationDraft]:
    return db.query(ApplicationDraft).filter(ApplicationDraft.id == draft_id).first()


def list_drafts(db: Session, limit: int = 100, *, user_id) -> List[ApplicationDraft]:
    return (
        db.query(ApplicationDraft)
        .filter(ApplicationDraft.user_id == user_id)
        .order_by(ApplicationDraft.updated_at.desc())
        .limit(limit)
        .all()
    )


def create_draft_for_job(
    db: Session,
    job: JobPosting,
    *,
    profile: Profile,
    force: bool = False,
    user_id,
) -> ApplicationDraft:
    """
    Generate (or regenerate) the tailored application package for an approved job.

    TENANCY LAYER 1: the profile arrives as a required parameter — this
    function never resolves identity itself. The three cross-tenant P0
    leaks all came from services fetching "the" profile internally; the
    route resolves the caller's profile and hands it in.

    The AI tailoring call is synchronous here — the API endpoint wraps this in
    a threadpool. Typical latency ~5-20s on glm-4.6 with thinking disabled.
    """
    existing = (
        db.query(ApplicationDraft)
        .filter(
            ApplicationDraft.job_id == job.id,
            ApplicationDraft.user_id == user_id,
            ApplicationDraft.status != "submitted",
        )
        .first()
    )
    if existing and existing.status == "ready" and not force:
        return existing  # already prepared — user should review, not regenerate

    if not profile or not profile.cv_text:
        raise DraftError("Upload your CV before preparing applications")

    if not ai_service_available():
        raise DraftError("GLM_API_KEY not configured — cannot tailor applications")

    match: Optional[MatchResult] = (
        db.query(MatchResult)
        .filter(MatchResult.job_id == job.id, MatchResult.user_id == user_id)
        .first()
    )

    # Reuse the existing row when regenerating a failed draft
    draft = existing or ApplicationDraft(
        user_id=user_id, job_id=job.id, match_id=match.id if match else None
    )
    draft.status = "drafting"
    draft.error = None
    # Review fix R6 (2026-09-15): a regeneration REPLACES the text, so
    # the previous text's attestations die with it — they must not
    # suppress flags on documents they never saw. Their durable channel
    # is profile.vouched_facts (saved at attest time by choice). The
    # block itself is NEVER cleared (constraint 4): regeneration is a
    # recovery, and the fabrication-rate data keeps the block.
    draft.fabrication_attested = None
    draft.fabrication_resolved_at = None
    # P1-5b: snapshot the CV reference this tailoring runs against. The
    # profile's path moves on re-upload; without the snapshot the send
    # path cannot know which CV the package was built from, and a
    # CV-old-tailored package emails CV-new as its "original CV".
    # Refreshed on every (re)generation — the package now being built IS
    # against the current CV. The early-return reuse of an untouched
    # 'ready' draft above keeps its original snapshot (package unchanged).
    draft.cv_file_path = profile.cv_file_path
    draft.cv_hash = hashlib.sha256(
        (profile.cv_text or "").encode("utf-8")
    ).hexdigest()
    db.add(draft)
    db.commit()

    try:
        # FABRICATION GUARD (WO-01 Layer C): every tailored output is
        # checked against the source CV before anything reaches the
        # review screen. High-confidence findings REGENERATE (never strip
        # — a silently mutilated document is worse than a blocked one),
        # up to MAX_FABRICATION_RETRIES; a survivor BLOCKS the draft and
        # names the untraceable claim. Advisory findings persist for the
        # review UI and never auto-act.
        # WO-23: the check itself (source composition, Layer A, judge)
        # lives in check_package — ONE implementation shared with
        # re-check. Attestations on this draft ride along: a claim the
        # user already confirmed must not re-block a regeneration.
        from app.services.fabrication import findings_as_json

        correction: Optional[str] = None
        retries = 0
        while True:
            kwargs = {}
            if correction:
                kwargs["correction"] = correction
            result = get_ai_service().tailor_application(
                profile_context=build_profile_context(profile),
                cv_text=profile.cv_text,
                job_description=_job_text(job),
                **kwargs,
            )
            draft.cover_letter = result["cover_letter"]
            draft.tailored_cv = result["tailored_cv"]
            from app.schemas.common import dump_json_list

            draft.changes_summary = dump_json_list(result["changes_summary"])

            high, advisory = check_package(
                profile, job,
                result.get("cover_letter", ""), result.get("tailored_cv", ""),
                attested_claims=_attested_claims(draft),
            )

            if not high:
                from app.schemas.common import dump_json_list as _dumps

                draft.fabrication_findings = _dumps(findings_as_json(advisory))
                draft.fabrication_retries = retries
                # Constraint 4 (review fix R6): fabrication_blocked is
                # never cleared on ANY recovery path — a draft that
                # blocked once keeps the block in the fabrication-rate
                # data even after a clean regeneration. It is False only
                # for drafts that never blocked (column default).
                draft.status = "ready"
                db.add(draft)
                db.commit()
                db.refresh(draft)
                logger.info(
                    "Draft %s prepared for job %s (fabrication retries=%d, "
                    "advisory=%d)", draft.id, job.id, retries, len(advisory),
                )
                return draft

            if retries >= MAX_FABRICATION_RETRIES:
                # WO-23: the findings PERSIST (high + advisory) — they are
                # the recovery UI's content ("Why this was blocked"), and
                # the block itself stays recorded forever.
                from app.schemas.common import dump_json_list as _dumps

                draft.fabrication_findings = _dumps(findings_as_json(
                    high + advisory))
                draft.fabrication_retries = retries
                draft.fabrication_blocked = True
                draft.status = "failed"
                named = ", ".join(
                    sorted({c.value.split("|")[0] for c in high})
                )
                draft.error = fabrication_block_error(profile.cv_text, high)
                db.add(draft)
                db.commit()
                logger.warning(
                    "Draft %s BLOCKED after %d retries: %s",
                    draft.id, retries, named,
                )
                return draft

            retries += 1
            correction = (
                "Your previous output contained claims that cannot be traced "
                "to your CV and may be fabrications: "
                + ", ".join(sorted({c.value.split("|")[0] for c in high}))
                + ". Regenerate the documents WITHOUT these claims — every "
                "employer, date, credential and metric must exist in the "
                "original CV."
            )
            logger.info(
                "Draft %s: %d high-confidence fabrication findings — "
                "regenerating (attempt %d)", draft.id, len(high), retries,
            )
    except Exception as e:  # noqa: BLE001 — surface the failure on the draft row
        db.rollback()
        draft.status = "failed"
        draft.error = f"Tailoring failed: {type(e).__name__}: {e}"
        db.add(draft)
        db.commit()
        db.refresh(draft)
        logger.error("Draft tailoring failed for job %s: %s", job.id, e)
        return draft


def save_draft_edits(
    db: Session,
    draft: ApplicationDraft,
    cover_letter: Optional[str] = None,
    tailored_cv: Optional[str] = None,
) -> ApplicationDraft:
    """Persist the user's manual edits to the package."""
    if draft.status == "submitted":
        raise DraftError("This application was already submitted")
    if cover_letter is not None:
        draft.cover_letter = cover_letter
    if tailored_cv is not None:
        draft.tailored_cv = tailored_cv
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def recheck_draft(db: Session, draft: ApplicationDraft, *, profile: Profile) -> ApplicationDraft:
    """WO-23: run the shared guard over the draft's CURRENT text.

    Clean -> 'ready' (fabrication_resolved_at set; fabrication_blocked
    STAYS true — constraint 4). Still flagged -> stays 'failed' with the
    remaining findings persisted for the recovery panel; each resolves by
    fix-in-text (edit + re-check again) or by attestation.

    The re-check is for drafts that failed the guard FIRST time round
    (owner decision 2026-09-11): edits on a draft that never failed stay
    un-checked — the guard polices AI output; what the user writes is
    theirs.
    """
    if not draft.fabrication_blocked:
        raise DraftError(
            "Only drafts blocked by the fabrication guard can be re-checked"
        )
    if draft.status != "failed":
        # Review fix R3 (2026-09-15): recovery never clears
        # fabrication_blocked, so the flag alone let a SUBMITTED draft
        # recheck back to 'ready' — and submit again, a second employer
        # email. 'sending' and 'drafting' are excluded for the same
        # reason: their owners finish first.
        raise DraftError(
            f"Only failed drafts can be re-checked — this one is "
            f"'{draft.status}'"
        )
    if not (draft.cover_letter or draft.tailored_cv):
        raise DraftError("No documents to check — regenerate the draft first")

    job: Optional[JobPosting] = draft.job
    if job is None:
        job = db.query(JobPosting).filter(JobPosting.id == draft.job_id).first()
    try:
        high, advisory = check_package(
            profile, job, draft.cover_letter, draft.tailored_cv,
            attested_claims=_attested_claims(draft),
        )
    except Exception as e:  # noqa: BLE001 — transport failure, never a verdict
        # The user's text is already saved (save_draft_edits); only this
        # check failed. Record it, keep the draft failed, re-raise for 400.
        draft.error = f"Re-check failed: {type(e).__name__}: {e}"
        db.add(draft)
        db.commit()
        raise DraftError(draft.error) from e

    from app.schemas.common import dump_json_list
    from app.services.fabrication import findings_as_json

    draft.fabrication_findings = dump_json_list(findings_as_json(high + advisory))
    if not high:
        draft.status = "ready"
        draft.fabrication_resolved_at = utc_now()
        draft.error = None
        logger.info("Draft %s RECOVERED by re-check", draft.id)
    else:
        # stays failed; the panel shows only what remains
        draft.status = "failed"
        draft.error = fabrication_block_error(profile.cv_text, high)
        logger.info(
            "Draft %s re-check: %d claims still unresolved",
            draft.id, len(high),
        )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def _norm_claim(s: str) -> str:
    return (s or "").strip().casefold()


def _resolves_finding(attested_entry: dict, finding: dict) -> bool:
    """Does a recorded attestation resolve THIS flagged finding?

    Exact value match, or — for Layer A's variant findings from one
    credential sentence ("AWS Certified Solutions Architect" also yields
    "aws certified" + "certified solutions architect") — the SAME
    extraction sentence with containment overlap. Containment WITHOUT
    the shared sentence is the whole-draft-override hole: a one-letter
    claim ('e') or a whole pasted document matches every finding
    (review fix R2, 2026-09-15).
    """
    av = _norm_claim(str(attested_entry.get("claim", "")))
    fv = _norm_claim(str(finding.get("value", "")))
    if not av or not fv:
        return False
    if av == fv:
        return True
    ac = attested_entry.get("context") or ""
    fc = finding.get("context") or ""
    return bool(ac) and ac == fc and (av in fv or fv in av)


def attest_claim(db: Session, draft: ApplicationDraft, claim: str,
                 save_to_profile: bool, *, profile: Profile,
                 profile_fact: Optional[str] = None) -> ApplicationDraft:
    """WO-23: record the user's 'This is true — keep it' for ONE flagged
    claim. Per-claim resolution — never a whole-draft override.

    Review fixes (2026-09-15):
    - R3: only status 'failed' drafts accept attestations — a submitted
      draft must never be confirmable back to 'ready' (second send).
    - R2: the claim must EXACTLY match a currently-flagged value
      (casefolded). Variants resolve via the shared extraction sentence,
      not global containment.
    - R1: when the last unresolved claim is confirmed, the guard runs
      AGAIN on the current text before 'ready'. A draft blocked by
      Layer A was never judged (the judge only runs on a Layer-A-clean
      document), and text edited after the block was never re-checked —
      attest-ready without a final check shipped exactly that.
    - R5 (round-2 N1): the profile save takes ONLY the explicit
      profile_fact the client sends (the UI displays it in full next to
      the opt-in), bounded by the SAME 200-char bound as the profile
      editor (N2 — an attest-saved fact must never block a later
      preferences save). The backend derives nothing: confirming one
      atom of a sentence ("40%") must not vouch the sentence's other
      claims ("team of 12") into guard truth.
    - N3: no blanket claim length cap — judge claim values are free
      text; EXACT match to a stored flagged value is the override gate,
      and rejecting what the guard itself listed recreates WO-23's
      original dead end.

    Per-draft attestations stay value-based: they die with this text
    (regeneration clears them) and only suppress flags on THIS draft.
    fabrication_blocked is never cleared on any recovery path.
    """
    from app.schemas.common import dump_json_list, parse_json_list
    from app.services.cv_service import (
        VOUCHED_FACTS_MAX_CHARS,
        VOUCHED_FACTS_MAX_ITEMS,
    )

    if not draft.fabrication_blocked:
        raise DraftError(
            "Only drafts blocked by the fabrication guard can be attested"
        )
    attested = [a for a in parse_json_list(
        getattr(draft, "fabrication_attested", None)) if isinstance(a, dict)]
    if any(_norm_claim(str(a.get("claim", ""))) == _norm_claim(claim)
           for a in attested):
        # Replayed click — idempotent no-op BEFORE the status gate: the
        # first click may have recovered the draft to 'ready' (or it may
        # since have been submitted); a replay must not error and must
        # not mutate anything.
        return draft
    if draft.status != "failed":
        raise DraftError(
            "Only failed drafts can be confirmed — this one is "
            f"'{draft.status}'"
        )
    claim = (claim or "").strip()
    if not claim:
        raise DraftError("Empty claim")

    findings = parse_json_list(draft.fabrication_findings)
    unresolved_high = [f for f in findings
                       if isinstance(f, dict) and f.get("tier") == "high"]
    matched = next((f for f in unresolved_high
                    if _norm_claim(str(f.get("value", ""))) == _norm_claim(claim)),
                   None)
    if matched is None:
        raise DraftError(
            "That claim is not currently flagged — confirm the claims the "
            "guard listed, or fix them in the text"
        )

    saved = False
    if save_to_profile:
        fact = (profile_fact or "").strip()
        # Round-3 N1: the per-claim opt-in saves ONLY the confirmed claim
        # itself — a longer text (the finding's sentence, what the old UI
        # sent by default) carries claims the user did NOT confirm and
        # must not reach the profile or the final check's source. The
        # full sentence is a separate explicit action through the
        # profile editor, where the user reads what gets saved.
        # ONE bound with the profile editor (N2): the Profile form
        # resends the whole vouched list on every save — anything this
        # path writes must survive normalize_vouched_facts verbatim.
        if (0 < len(fact) <= VOUCHED_FACTS_MAX_CHARS
                and _norm_claim(fact) == _norm_claim(claim)):
            vouched = [str(v).strip() for v in parse_json_list(
                getattr(profile, "vouched_facts", None))]
            if (fact not in vouched
                    and len(vouched) < VOUCHED_FACTS_MAX_ITEMS):
                vouched.append(fact)
                profile.vouched_facts = dump_json_list(vouched)
                db.add(profile)
                saved = True

    attested.append({"claim": claim,
                     "context": matched.get("context") or "",
                     "at": utc_now().isoformat(),
                     "saved_to_profile": saved})
    draft.fabrication_attested = dump_json_list(attested)

    remaining = [f for f in unresolved_high
                 if not any(_resolves_finding(a, f) for a in attested)]
    if remaining:
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft  # still failed — the panel lists what is left

    # R1: the last claim is confirmed — verify the CURRENT text before
    # 'ready'. The attested claims ride in the guard's source, so they
    # cannot re-flag; anything ELSE the guard finds surfaces here.
    job: Optional[JobPosting] = draft.job
    if job is None:
        job = db.query(JobPosting).filter(JobPosting.id == draft.job_id).first()
    try:
        high, advisory = check_package(
            profile, job, draft.cover_letter, draft.tailored_cv,
            attested_claims=[a["claim"] for a in attested],
        )
    except Exception as e:  # noqa: BLE001 — transport failure, never a verdict
        draft.error = f"Re-check failed: {type(e).__name__}: {e}"
        db.add(draft)
        db.commit()
        raise DraftError(draft.error) from e

    from app.services.fabrication import findings_as_json

    draft.fabrication_findings = dump_json_list(findings_as_json(high + advisory))
    if not high:
        draft.status = "ready"
        draft.fabrication_resolved_at = utc_now()
        draft.error = None
        logger.info("Draft %s RECOVERED by attestation (%d claims, "
                    "guard re-run clean)", draft.id, len(attested))
    else:
        # New findings on the current text — the loop continues; the
        # user confirms or fixes these the same way.
        draft.status = "failed"
        draft.error = fabrication_block_error(profile.cv_text, high)
        logger.info("Draft %s attest re-check surfaced %d new findings",
                    draft.id, len(high))
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def _probe_apply_portal(apply_url: Optional[str]) -> Optional[str]:
    """One HEAD request against the hand-off URL (6s, redirects followed).

    Warn, never block: a transient hiccup must not stop a manual apply
    the user can still complete. Only a DEFINITE HTTP >= 400 warns — 405
    aside (HEAD unsupported, but the server answered, so it is alive).
    401/403 get their own message: they refused the CHECK's client, not
    the user — bank, insurer and recruiter domains routinely 403
    non-browser clients while serving browsers the same page (verified
    externally, ai-job-search's 09-web-research: Barclays et al.), so
    reporting them as expiry is exactly the false alarm this probe
    promises never to raise. Timeouts and connection errors stay
    silent: 'unknown' is not 'dead', and probe noise must never cry
    wolf."""
    if not apply_url:
        return None
    try:
        import httpx

        response = httpx.head(apply_url, follow_redirects=True, timeout=6.0)
        if response.status_code in (401, 403):
            return (
                f"Apply portal blocked our automated check (HTTP "
                f"{response.status_code}) — many employer and recruiter "
                "sites block bots while serving browsers normally. The link "
                "will likely open fine; check the page if it does not."
            )
        if response.status_code != 405 and response.status_code >= 400:
            return (
                f"Apply portal returned HTTP {response.status_code} at hand-off — "
                "this posting may have expired. Check the page loads before applying."
            )
    except Exception:  # noqa: BLE001 — a probe failure is context, never a submit failure
        return None
    return None


def submit_draft(
    db: Session,
    draft: ApplicationDraft,
    method: str,
    profile: Profile,
    *,
    user_id,
) -> Application:
    """
    Submit the reviewed package.

    - email:   sends cover letter + tailored CV PDFs (plus the original CV) via
               Resend when the job published an application email
    - browser / manual: queues as manual_pending with the apply URL; the UI
               opens the posting with the package ready to paste

    SUBMIT (double-send window): the ready->sending claim below is a
    conditional UPDATE — the rowcount is the verdict, the same atomic
    pattern as claim_hunt. Two rapid submits both read 'ready', but only
    ONE transition wins; the loser gets DraftConflictError (409 at the
    route) BEFORE any email is dispatched. The partial unique index on
    applications(draft_id) is the DB backstop for anything the claim
    cannot see.
    """
    if draft.status == "submitted":
        raise DraftConflictError("This application was already submitted")
    if draft.status == "sending":
        raise DraftConflictError("This application is already being submitted")
    if draft.status != "ready" or not draft.cover_letter:
        raise DraftError("Draft is not ready — prepare or fix it first")

    # Atomic claim: exactly one caller moves this row ready -> sending.
    # (In-memory status above is only a fast path — it can be stale in the
    # racing session; the UPDATE's WHERE clause cannot.)
    from sqlalchemy import update

    claimed = db.execute(
        update(ApplicationDraft)
        .where(ApplicationDraft.id == draft.id, ApplicationDraft.status == "ready")
        .values(status="sending")
    )
    db.commit()
    if claimed.rowcount != 1:
        db.rollback()
        db.refresh(draft)
        if draft.status == "submitted":
            # the other dispatch already finished
            raise DraftConflictError("This application was already submitted")
        raise DraftConflictError("This application is already being submitted")

    job: JobPosting = draft.job
    if job is None:
        job = db.query(JobPosting).filter(JobPosting.id == draft.job_id).first()
    # TENANCY LAYER 1: profile is a required parameter — the outbound
    # identity (sender name, attached CVs) comes from exactly the profile
    # the route resolved for the caller. This function never looks one up;
    # the optional-lookup version here is where a wrong user's CV got
    # emailed to an employer.

    target_email = job.application_email
    apply_url = job.application_url or job.url
    if method == "email" and not target_email:
        # No published email — degrade to the browser flow
        method = "browser"
    # Owner decision 2026-09-01 (beta): email apply ships from the USER'S
    # OWN connected Gmail, not a platform sender. Composio's Gmail actions
    # cannot carry our PDF attachments (s3key-only), so sending is OFF
    # until the first-party Google OAuth build lands post-beta. The gate
    # lives HERE so every caller — route, retry, script — inherits it.
    if method == "email" and not settings.EMAIL_APPLY_ENABLED:
        raise EmailApplyDisabledError(
            "Email apply is moving to your own Gmail account and returns "
            "at the end of beta — use browser or manual apply for now"
        )

    applicant = profile.full_name if profile else None
    subject = f"Application: {job.title}" + (f" — {applicant}" if applicant else "")

    # One application row per draft (unique index). A FAILED application
    # leaves the draft 'ready'; resubmitting REUSES that row — a fresh
    # insert would trip the constraint, and a duplicate history for one
    # reviewed package is a lie anyway.
    application = (
        db.query(Application).filter(Application.draft_id == draft.id).first()
    )
    if application is not None and application.status != "failed":
        _release_send_claim(db, draft, status="submitted")
        raise DraftConflictError("This application was already submitted")
    if application is None:
        application = Application(
            user_id=user_id if user_id is not None else draft.user_id,
            job_id=job.id,
            match_id=draft.match_id,
            draft_id=draft.id,
        )
        db.add(application)
    application.method = method
    application.status = "queued"
    application.error = None
    application.subject = subject
    application.body = draft.cover_letter
    application.target_email = target_email
    application.apply_url = apply_url
    try:
        db.flush()
    except IntegrityError:
        # The unique(draft_id) backstop fired: another dispatch won the
        # race (its claim preceded ours in a path the conditional UPDATE
        # could not observe). Nothing of ours was sent — the INSERT is the
        # first write of this attempt.
        db.rollback()
        db.refresh(draft)
        raise DraftConflictError("This application was already submitted")

    try:
        if method == "email":
            _send_with_pdfs(db, application, draft, job, profile)
        else:
            application.status = "manual_pending"
            # Liveness probe at hand-off (2026-08-31 incident: a
            # careerjet redirect 502'd while the original posting
            # lived at its source — the user was handed a dead link
            # under a 'sent' label). The warning rides the application
            # row: the Applications card renders `error` right beside
            # the "Open posting" link. It never blocks the hand-off.
            warning = _probe_apply_portal(apply_url)
            if warning:
                logger.warning("Browser hand-off %s: %s", apply_url, warning)
                application.error = warning
    except Exception:
        # _send_with_pdfs swallows its own failures onto the application
        # row; anything raised PAST it must not strand the 'sending'
        # claim (the sweep is the last resort, not the plan).
        db.rollback()
        draft.status = "ready"
        db.add(draft)
        db.commit()
        raise

    # State follows the outcome: a FAILED email send leaves the draft
    # editable, so "Finish applying" still shows it. Applied-ness is DERIVED
    # from the applications table per user — job.status is never written
    # here (it's shared across users).
    if application.status != "failed":
        draft.status = "submitted"
    else:
        draft.status = "ready"
    db.add(draft)
    db.commit()
    db.refresh(application)
    return application


def _release_send_claim(db: Session, draft: ApplicationDraft, *, status: str) -> None:
    """Leave the draft in a truthful state when a submit aborts after the
    claim (never leave 'sending' behind on a path we control)."""
    draft.status = status
    db.add(draft)
    db.commit()


def _send_with_pdfs(
    db: Session,
    application: Application,
    draft: ApplicationDraft,
    job: JobPosting,
    profile: Optional[Profile],
) -> None:
    """Attach tailored cover letter + CV PDFs (and the original CV file) and send."""
    from app.core.config import settings

    if not settings.RESEND_API_KEY or not settings.APPLY_FROM_EMAIL:
        application.status = "failed"
        # P0-6: environment-neutral — "edit backend/.env" was a dead end
        # in the Render container, exactly where this error fires.
        application.error = (
            "Email apply is not configured on this deployment "
            "(RESEND_API_KEY / APPLY_FROM_EMAIL missing) — contact the "
            "operator, or use 'Apply in browser'"
        )
        return

    # P1-3(c): the per-account daily ceiling on employer emails — checked
    # BEFORE any attachment work or dispatch. The shared APPLY_FROM_EMAIL
    # domain carries every user's deliverability; one runaway account
    # must not burn it.
    from app.core.ratelimit import enforce

    enforce(application.user_id, "send_daily")

    import base64
    import os

    try:
        import resend

        applicant = profile.full_name if profile else None
        attachments = []
        os.makedirs(DRAFT_DIR, exist_ok=True)
        for filename, blob in (
            (f"Cover Letter - {applicant or 'Applicant'}.pdf",
             pdf_service.cover_letter_pdf(draft.cover_letter or "", applicant)),
            (f"CV - {applicant or 'Applicant'} (tailored).pdf",
             pdf_service.tailored_cv_pdf(draft.tailored_cv or "", applicant)),
        ):
            attachments.append(
                {"filename": filename, "content": base64.b64encode(blob).decode("utf-8")}
            )

        # Also attach the original CV PDF when available (storage-aware).
        # P1-5b: the DRAFT's snapshot decides which CV is "original" — this
        # package was tailored against THAT file. Reading the profile's
        # CURRENT path here is how a re-upload mid-review produced
        # CV-old-tailored documents mailed with CV-new attached. Legacy
        # drafts (NULL snapshot) fall back to the current path.
        from app.services.storage import read_cv_at_path

        original_cv_path = draft.cv_file_path or (
            profile.cv_file_path if profile else None
        )
        original_cv = read_cv_at_path(original_cv_path)
        if original_cv:
            attachments.append(
                {
                    "filename": (profile.cv_file_name if profile else None) or "CV.pdf",
                    "content": base64.b64encode(original_cv).decode("utf-8"),
                }
            )

        resend.api_key = settings.RESEND_API_KEY
        from_email = (
            f"{applicant} <{settings.APPLY_FROM_EMAIL}>" if applicant else settings.APPLY_FROM_EMAIL
        )
        params: dict = {
            "from": from_email,
            "to": [application.target_email],
            "subject": application.subject,
            "text": draft.cover_letter or "",
            "attachments": attachments,
        }
        # P1-1: applications go out from the SHARED APPLY_FROM_EMAIL —
        # without reply_to an employer's answer (the interview invitation)
        # lands in the operator's inbox and never reaches the applicant.
        if profile is not None and getattr(profile, "email", None):
            params["reply_to"] = profile.email
        email = resend.Emails.send(params)
        application.status = "sent"
        application.sent_at = utc_now()
        logger.info("Tailored application sent to %s (id=%s)", application.target_email, email.get("id"))
    except Exception as e:  # noqa: BLE001
        application.status = "failed"
        application.error = f"Email send failed: {e}"
        logger.error("Tailored application send failed: %s", e)
