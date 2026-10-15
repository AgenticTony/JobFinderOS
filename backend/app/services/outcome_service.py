"""WO-20 outcome layer — what the EMPLOYER did.

Three lifecycles meet here and must never be conflated:

- Send-side `applications.status` (queued/sent/failed/manual_pending) —
  OUR dispatch machinery. Outcome writes never touch it (tested).
- `applications.outcome_status` — the employer-side vocabulary below.
- `outcome_proposals` — reply detection and the quiet sweep NEVER write
  applications directly; they file a proposal citing evidence, and only
  an explicit user approval applies it (propose-then-confirm — the
  approval invariant applied to the inbox direction).

Classification reads the FULL message body, never subject/snippet alone:
snippets truncate the exact phrase that distinguishes "we'd like to
schedule a call" from "thanks for applying". Uncertain is surfaced,
never guessed.

PII: scanning reads the user's own inbox through their own entity-scoped
Composio connection. Stored: the classification + an evidence POINTER
(message id, sender, subject, arrival time). Never a message body.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, List, Optional
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import utc_now
from app.models import (
    Application,
    ApplicationDraft,
    GmailScanState,
    JobPosting,
    OutcomeProposal,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vocabulary — single source of truth (tests assert the partition)
# ---------------------------------------------------------------------------

OUTCOME_STATUSES = [
    "drafted",
    "applied",
    "interview",
    "offer",
    "hired",
    "rejected",
    "no_response",
    "offer_declined",
    "withdrawn",
]
# Final = the application is closed. Everything else is open.
FINAL_OUTCOMES = {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}
OPEN_OUTCOMES = set(OUTCOME_STATUSES) - FINAL_OUTCOMES

OUTCOME_SOURCES = ["manual", "reply_detection", "sweep"]

# Outcome statuses only a human may set — a classifier can never infer
# them from a reply (they are the user's own actions/decisions).
MANUAL_ONLY_OUTCOMES = {"drafted", "hired", "offer_declined", "withdrawn", "applied"}

# Employer signals for stats: a reply that proves a human looked. `applied`
# is the user confirming their own send — not an employer signal.
EMPLOYER_SIGNAL_OUTCOMES = {"interview", "offer", "hired", "rejected", "offer_declined"}

# ATS/notification sender domains worth matching beyond the employer's own.
ATS_SENDER_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "myworkday.com",
    "workday.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "icims.com",
    "bamboohr.com",
    "teamtailor.com",
]

# Hosts that mean "job board / aggregator", never "this employer". A
# scraped posting's URL usually points at the BOARD — matching mail from
# that host would attach board notifications to every application scraped
# from that board (review round 1, finding 2). Derived from the live
# source set (services/scrapers/*), VERIFIED against the live pool
# 2026-10-15: careerjet postings live on jobviewtrack.com and arbeitnow
# splits across arbeitnow.com AND arbeitnow.co.uk (review round 2,
# finding 5 — both were matching as "the employer").
AGGREGATOR_HOSTS = {
    "reed.co.uk",
    "adzuna.com",
    "adzuna.co.uk",
    "arbeitnow.com",
    "arbeitnow.co.uk",
    "remotive.com",
    "jobicy.com",
    "workingnomads.com",
    "careerjet.com",
    "careerjet.co.uk",
    "jobviewtrack.com",
    "platsbanken.se",
    "arbetsformedlingen.se",
    "jobtech.dev",
}

# Nudge thresholds (WO-20 §3; tests assert these exact numbers)
DEADLINE_URGENT_DAYS = 7        # 🔥 deadline within 7 days
FOLLOW_UP_QUIET_DAYS = 10       # sent 10+ days quiet …
FOLLOW_UP_MAX = 2               # … with fewer than two follow-ups
QUIET_SWEEP_DAYS = 60           # 60+ days quiet → propose no_response


class OutcomeError(Exception):
    """Invalid outcome transition/use — surfaces as 400 at the route."""


# ---------------------------------------------------------------------------
# Manual marker + the single writer
# ---------------------------------------------------------------------------

def set_outcome(
    db: Session,
    application: Application,
    status: str,
    source: str,
    note: Optional[str] = None,
    evidence: Optional[str] = None,
    resolved_at: Optional[datetime] = None,
) -> Application:
    """The ONLY writer of outcome columns. Send-side `status` is never
    touched here — two lifecycles, two columns, tested separately."""
    if status not in OUTCOME_STATUSES:
        raise OutcomeError(
            f"unknown outcome '{status}' — must be one of {OUTCOME_STATUSES}"
        )
    if source not in OUTCOME_SOURCES:
        raise OutcomeError(f"unknown outcome source '{source}'")
    application.outcome_status = status
    application.outcome_source = source
    application.outcome_note = note
    application.outcome_evidence = evidence
    # Event-time semantics: WHEN this outcome became established — the
    # reply's arrival time when evidence provides one (approved
    # proposals), the recording time otherwise. FIRST-RESPONSE RULE
    # (review round 1, finding 5): once an employer signal has stamped
    # a time, a later employer signal (interview → rejected) does not
    # move it — the stat measures the first reply, and a late-arriving
    # proposal for an EARLIER reply moves it back (min), never forward.
    new_time = resolved_at or utc_now()
    prev_was_signal = (
        application.outcome_status in EMPLOYER_SIGNAL_OUTCOMES
        and application.resolved_at is not None
    )
    if status in EMPLOYER_SIGNAL_OUTCOMES and prev_was_signal:
        application.resolved_at = min(application.resolved_at, new_time)
    else:
        application.resolved_at = new_time
    db.add(application)
    db.commit()
    return application


# ---------------------------------------------------------------------------
# Reply classification — full body, or refuse
# ---------------------------------------------------------------------------

# Class evidence FIRST, acknowledgment phrases never override it: the
# single most common rejection letter opens "Thank you for applying…"
# before the verdict — an ack pre-check silently swallowed exactly the
# messages this layer exists to catch (found red by
# test_moving_forward_with_others_is_rejected). Pure acknowledgments
# ("we received your application") match no class pattern and classify
# to None on their own.
_INTERVIEW_PATTERNS = [
    r"(we'?d|we would) like to (invite|schedule|arrange|set up)",
    r"invite you (to|for) an? (interview|call|chat)",
    r"schedule (a|an) (call|interview|phone screen|video interview)",
    r"book (a|an) (call|interview|meeting)",
    r"(first|second|final)[- ]round interview",
    r"phone screen",
    r"assessment center",
    r"(would|will) (you|like) (to|for) (a|an)? ?(short)? ?(call|interview)",
    r"intervju",                      # SV — broad but single-class
    r"kallelse till intervju",
    r"boka (en|ett) (samtal|möte|intervju)",
    r"skulle vilja boka",
]
_OFFER_PATTERNS = [
    r"pleased to offer",
    r"(we'?d|we would) like to offer you",
    r"offer (you|for) the position",
    r"(your|the) job offer",
    r"congratulations.{0,40}offer",
    r"vill erbjuda (dig|tjänsten)",
    r"erbjuder dig (tjänsten|positionen|anställning)",
    r"grattis.{0,40}erbjudande",
]
_REJECTION_PATTERNS = [
    r"(have|has)? ?decided to (move|proceed|go) forward with (other|another)",
    r"not (to )?(be )?moving forward",
    r"(have|will) (decided|decide) not to proceed",
    r"position (has been|is) filled",
    r"regret to inform",
    r"your application (was|has) not been successful",
    r"unfortunately.{0,60}(other candidates|not proceeding|not successful|filled)",
    r"tyvärr.{0,80}(andra kandidater|tillsatt|inte gå vidare)",
    r"gått vidare med (andra|en annan) kandidat",
    r"tjänsten (är|har) tillsatt",
]

_CLASS_RULES = [
    ("interview", _INTERVIEW_PATTERNS),
    ("offer", _OFFER_PATTERNS),
    ("rejected", _REJECTION_PATTERNS),
]


@dataclass
class Classification:
    status: str
    matched_phrase: str


def classify_message(subject: Optional[str], body: Optional[str]) -> Optional[Classification]:
    """Classify one employer reply to the outcome vocabulary.

    FULL BODY ONLY — the source repo's rule, and ours (acceptance #2):
    a subject/snippet truncates the exact phrase that distinguishes
    "we'd like to schedule a call" from "thanks for applying". Refuses
    (raises) rather than classifies without a body. Returns None for
    acknowledgments (no class evidence), ambiguity (multiple classes
    matched), or no signal — callers surface, never guess.
    """
    if not body or not body.strip():
        raise ValueError(
            "classification requires the FULL message body — "
            "subject/snippet-only classification is forbidden (WO-20)"
        )
    text = f"{subject or ''}\n{body}".lower()

    hits: List[tuple] = []
    for status, patterns in _CLASS_RULES:
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                hits.append((status, m.group(0)))
    classes = {h[0] for h in hits}
    if len(classes) > 1:
        return None  # conflicting signals — surfaced, not guessed
    if classes:
        status = hits[0][0]
        if status in MANUAL_ONLY_OUTCOMES:
            return None  # classifier may never set a manual-only status
        return Classification(status=status, matched_phrase=hits[0][1])
    return None


# ---------------------------------------------------------------------------
# Sender matching — which application is this reply about?
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)


def extract_email(from_header: Optional[str]) -> Optional[str]:
    if not from_header:
        return None
    m = _EMAIL_RE.search(from_header)
    return m.group(0).lower() if m else None


def _domain(email_or_url: Optional[str], is_url: bool = False) -> Optional[str]:
    if not email_or_url:
        return None
    if is_url:
        host = urlparse(email_or_url if "//" in email_or_url else f"https://{email_or_url}").hostname
    else:
        host = email_or_url.rsplit("@", 1)[-1] if "@" in email_or_url else None
    return host.lower().strip(".") if host else None


def _domains_match(a: Optional[str], b: Optional[str]) -> bool:
    """Two hosts match on exact host OR shared parent (company.com vs
    careers.company.com match both ways; greenhouse.io only matches
    itself — registrable-domain guessing stops at one level)."""
    if not a or not b or a == b:
        return bool(a and b and a == b)
    return a.endswith("." + b) or b.endswith("." + a)


def _is_aggregator_host(host: Optional[str]) -> bool:
    """The host belongs to a job board, not the employer. Aggregator
    links are where scraped postings LIVE — mail from the board says
    nothing about which application it concerns."""
    if not host:
        return False
    return any(
        host == agg or host.endswith("." + agg) for agg in AGGREGATOR_HOSTS
    )


def sender_match_is_specific(
    from_email: Optional[str], application: Application, job: Optional[JobPosting]
) -> bool:
    """A SPECIFIC match names THIS application: the target email we sent
    to, its domain, or the employer's own (non-aggregator) posting host.
    A specific match alone is evidence enough to attach a reply. An
    ATS-domain sender is never specific — the same notification domain
    serves every employer on that ATS, including jobs the user applied
    to OFF-platform (review round 2, finding 2)."""
    if not from_email:
        return False
    sender_domain = _domain(from_email)
    if application.target_email and from_email == application.target_email.lower():
        return True
    target_domain = _domain(application.target_email)
    if sender_domain and target_domain and _domains_match(sender_domain, target_domain):
        return True
    if sender_domain:
        for host_source in (job.url if job else None, application.apply_url):
            host = _domain(host_source, is_url=True)
            if (
                host
                and not _is_aggregator_host(host)
                # An ATS-hosted posting (boards.greenhouse.io/…) is the
                # ATS's domain, not the employer's own — exactly as
                # generic as the ATS sender domain itself (round 2,
                # finding 2, caught by the lone-match red test).
                and not any(
                    host == d or host.endswith("." + d)
                    for d in ATS_SENDER_DOMAINS
                )
                and _domains_match(sender_domain, host)
            ):
                return True
    return False


def sender_matches_application(
    from_email: Optional[str], application: Application, job: Optional[JobPosting]
) -> bool:
    """Does this sender plausibly relate to this application? Matches:
    the application's own target email, its domain, the posting URL's
    host (parent-aware, but NEVER a board/aggregator host), and the
    common ATS notification domains. A shared ATS domain matching
    several applications is resolved by the scan's ambiguity logic —
    this predicate is only 'plausibly related', never 'certainly this
    one'."""
    if not from_email:
        return False
    sender_domain = _domain(from_email)
    if application.target_email and from_email == application.target_email.lower():
        return True
    target_domain = _domain(application.target_email)
    if sender_domain and target_domain and _domains_match(sender_domain, target_domain):
        return True
    if sender_domain and any(
        sender_domain == d or sender_domain.endswith("." + d) for d in ATS_SENDER_DOMAINS
    ):
        return True
    if job is not None and sender_domain:
        job_host = _domain(job.url, is_url=True)
        if (
            job_host
            and not _is_aggregator_host(job_host)
            and _domains_match(sender_domain, job_host)
        ):
            return True
        app_url_host = _domain(application.apply_url, is_url=True)
        if (
            app_url_host
            and not _is_aggregator_host(app_url_host)
            and _domains_match(sender_domain, app_url_host)
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Propose-then-confirm — scan + sweep file proposals; approval applies
# ---------------------------------------------------------------------------

def _proposal_exists_for_message(
    db: Session, user_id, evidence_message_id: Optional[str]
) -> Optional[OutcomeProposal]:
    """Pre-check for an identical filed proposal, scoped to the USER +
    message — one email files at most ONE proposal across ALL the
    user's applications (review round 2, finding 6: the per-application
    key let a later scan re-file the same message against a second
    application after the first closed). A concurrent uncommitted
    insert is INVISIBLE here — the unique index at flush is the real
    backstop (review round 1, finding 3)."""
    if not evidence_message_id:
        return None
    return (
        db.query(OutcomeProposal)
        .filter(
            OutcomeProposal.user_id == user_id,
            OutcomeProposal.evidence_message_id == evidence_message_id,
        )
        .first()
    )


def _file_proposal(
    db: Session,
    application: Application,
    proposed_status: str,
    source: str,
    *,
    evidence_message_id: Optional[str] = None,
    evidence_from: Optional[str] = None,
    evidence_subject: Optional[str] = None,
    evidence_received_at: Optional[datetime] = None,
    reason: Optional[str] = None,
) -> Optional[OutcomeProposal]:
    """Insert one pending proposal, deduped on (application, message).
    Returns None when an identical proposal already exists — a replayed
    scan must be a no-op (acceptance #4). The insert runs under a
    SAVEPOINT: a unique-index collision rolls back ONLY the duplicate —
    a session-wide rollback here dropped every earlier uncommitted
    proposal of the same scan (review round 1, finding 3)."""
    if proposed_status not in OUTCOME_STATUSES or proposed_status in MANUAL_ONLY_OUTCOMES:
        raise OutcomeError(f"'{proposed_status}' cannot be proposed by {source}")
    existing = _proposal_exists_for_message(db, application.user_id, evidence_message_id)
    if existing is not None:
        return None
    proposal = OutcomeProposal(
        user_id=application.user_id,
        application_id=application.id,
        proposed_status=proposed_status,
        source=source,
        evidence_message_id=evidence_message_id,
        evidence_from=evidence_from,
        evidence_subject=evidence_subject,
        evidence_received_at=evidence_received_at,
        reason=reason,
    )
    try:
        with db.begin_nested():  # SAVEPOINT — collision rolls back only this
            db.add(proposal)
            db.flush()
    except IntegrityError:
        # begin_nested's context manager already rolled the SAVEPOINT
        # back; a db.rollback() HERE would be the session-wide rollback
        # this fix exists to remove. The proposal object dies with the
        # savepoint; everything flushed earlier in this scan survives.
        return None
    return proposal


def list_pending_proposals(db: Session, *, user_id) -> List[OutcomeProposal]:
    return (
        db.query(OutcomeProposal)
        .filter(OutcomeProposal.user_id == user_id, OutcomeProposal.decision.is_(None))
        .order_by(OutcomeProposal.created_at.asc())
        .all()
    )


def decide_proposal(
    db: Session, proposal: OutcomeProposal, decision: str
) -> Application:
    """Approve or dismiss ONE proposal. Approve = the only path from a
    proposal to an applications row (acceptance #3); manual outcomes are
    never overwritten by detected ones — the user's word outranks the
    classifier's guess. Dismiss = keep the row as audit + re-scan dedupe,
    touch nothing."""
    if decision not in ("approved", "dismissed"):
        raise OutcomeError("decision must be 'approved' or 'dismissed'")
    if proposal.decision is not None:
        raise OutcomeError(f"proposal already {proposal.decision}")

    application = (
        db.query(Application).filter(Application.id == proposal.application_id).first()
    )
    if application is None:
        raise OutcomeError("proposal's application no longer exists")

    if decision == "approved":
        if application.outcome_source == "manual":
            # A manual marker is the user's own statement — a detected
            # proposal never overwrites it. Record the decision, skip the write.
            proposal.decision = "approved"
            proposal.decided_at = utc_now()
            proposal.reason = (proposal.reason or "") + " [superseded by manual outcome]"
            db.add(proposal)
            db.commit()
            return application
        if (
            proposal.proposed_status == "no_response"
            and application.outcome_status in EMPLOYER_SIGNAL_OUTCOMES
        ):
            # Silence never beats a recorded employer signal (review
            # round 1, finding 1): a stale 60-day sweep must not turn an
            # interview into a false no_response in the stats.
            proposal.decision = "approved"
            proposal.decided_at = utc_now()
            proposal.reason = (proposal.reason or "") + " [superseded: employer signal on record]"
            db.add(proposal)
            db.commit()
            return application
        if (
            application.outcome_status in FINAL_OUTCOMES
            and not (
                proposal.evidence_received_at is not None
                and application.resolved_at is not None
                and proposal.evidence_received_at > application.resolved_at
            )
        ):
            # A stale or untimestamped proposal cannot regress a CLOSED
            # application (review round 2, finding 3): newest-first
            # scanning proposes the newer rejection before the older
            # interview invite, and approve-all in created_at order
            # would otherwise land the application back at 'interview' —
            # re-entering nudges and the sweep pool with the real
            # rejection lost. A proposal with NEWER evidence than the
            # recorded outcome is a genuine progression (a later offer
            # rescuing a rejection) and stays allowed. The age test uses
            # evidence_received_at, NOT the open/final partition —
            # 'offer' is an open status, and blocking on the partition
            # would reject exactly that rescue.
            proposal.decision = "approved"
            proposal.decided_at = utc_now()
            proposal.reason = (proposal.reason or "") + " [superseded: final outcome on record]"
            db.add(proposal)
            db.commit()
            return application
        evidence = (
            f"msg:{proposal.evidence_message_id}"
            if proposal.evidence_message_id
            else f"{proposal.source}:quiet>{QUIET_SWEEP_DAYS}d"
        )
        set_outcome(
            db,
            application,
            proposal.proposed_status,
            proposal.source,
            note=proposal.reason,
            evidence=evidence,
            # Honest resolution time: when the reply ARRIVED, not when we
            # scanned or approved it (stats measure the employer, not us).
            resolved_at=proposal.evidence_received_at,
        )

    proposal.decision = decision
    proposal.decided_at = utc_now()
    db.add(proposal)
    db.commit()
    return application


def approve_all_pending(db: Session, *, user_id) -> int:
    """Confirm the batch: approve every pending proposal for this user.
    One confirmation, many writes — each still routes decide_proposal."""
    pending = list_pending_proposals(db, user_id=user_id)
    count = 0
    for p in pending:
        decide_proposal(db, p, "approved")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Scan orchestration — fetcher injected, watermark per user
# ---------------------------------------------------------------------------

# fetch_messages(user_id, after_date, sender_terms) -> {"messages":
# [...], "truncated": bool} — messages ordered oldest→newest, each
# {id, from, subject, body, received_at}. truncated=True means the
# remote query returned MORE than the adapter's page cap: the scan must
# NOT advance its cursor past what it processed, or everything beyond
# the cap is never scanned again (review round 1, finding 8). The
# Composio/Gmail adapter in gmail_scan.py satisfies it; tests inject
# fakes. sender_terms ("a@b.com" / "@b.com") constrain the remote
# query; the service still re-verifies every sender itself (adapter
# results are hints, sender_matches_application is the authority).
# after_date lets the adapter widen the Gmail `after:` window; the
# dedupe backstop is the unique proposal index, so overlap is safe.
FetchMessages = Callable[[str, Optional[date], List[str]], Awaitable[dict]]


def _naive_utc(value):
    """Normalize a possibly tz-AWARE datetime to naive UTC — the app's
    timestamp convention (utc_now). Gmail arrival times arrive aware;
    every column and every subtraction here is naive (review round 1,
    finding 9)."""
    if value is None or not isinstance(value, datetime):
        return value
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _discriminate_by_subject(
    subject: Optional[str], direct: List[Application], jobs: dict
) -> Optional[Application]:
    """Among several plausibly-matching applications (a shared ATS
    domain), pick the one whose company or full job title appears in
    the subject. Exactly one wins → it; zero or several → None
    (uncertain, surfaced — never the first by query order)."""
    if not subject:
        return None
    subject_l = subject.lower()
    hits = []
    for app in direct:
        job = jobs.get(app.job_id)
        if job is None:
            continue
        if job.company and job.company.lower() in subject_l:
            hits.append(app)
            continue
        if job.title and job.title.lower() in subject_l:
            hits.append(app)
    return hits[0] if len(hits) == 1 else None


async def scan_replies(
    db: Session, user_id, fetch_messages: FetchMessages
) -> dict:
    """One incremental inbox pass for one user. Files proposals; NEVER
    writes applications (acceptance #3). Advances the scan cursor ONLY
    over ground actually covered."""
    state = db.query(GmailScanState).filter(GmailScanState.user_id == user_id).first()
    if state is None:
        state = GmailScanState(user_id=user_id)
        db.add(state)

    # Denominator-style candidates: actually out the door (sent by us or
    # user-confirmed applied), not yet closed, and NOT follow-up rows —
    # a reply to the thread answers the parent application (review
    # round 1, finding 4).
    candidates = (
        db.query(Application)
        .filter(
            Application.user_id == user_id,
            Application.follow_up_of_id.is_(None),
            (Application.sent_at.isnot(None))
            | (Application.outcome_status == "applied"),
        )
        .all()
    )
    open_candidates = [
        a for a in candidates if a.outcome_status not in FINAL_OUTCOMES
    ]
    jobs = {
        j.id: j
        for j in db.query(JobPosting).filter(
            JobPosting.id.in_([a.job_id for a in open_candidates])
        ).all()
    } if open_candidates else {}

    after_date = (
        date.fromisoformat(state.scan_cursor) - timedelta(days=1)
        if state.scan_cursor
        else None  # adapter default: a bounded recent window
    )
    # Sender terms for the remote query: target emails, their domains,
    # non-aggregator posting-URL hosts, and the ATS notification domains.
    sender_terms: List[str] = []
    for app in open_candidates:
        if app.target_email:
            sender_terms.append(app.target_email.lower())
            d = _domain(app.target_email)
            if d:
                sender_terms.append("@" + d)
        job = jobs.get(app.job_id)
        if job is not None:
            h = _domain(job.url, is_url=True)
            if h and not _is_aggregator_host(h):
                sender_terms.append("@" + h)
        h2 = _domain(app.apply_url, is_url=True)
        if h2 and not _is_aggregator_host(h2):
            sender_terms.append("@" + h2)
    for d in ATS_SENDER_DOMAINS:
        sender_terms.append("@" + d)
    # de-dup, keep order, cap the query length (Gmail limits)
    seen = set()
    sender_terms = [t for t in sender_terms if not (t in seen or seen.add(t))][:20]

    summary = {"scanned": 0, "matched": 0, "proposed": 0, "uncertain": 0,
               "truncated": False}
    try:
        result = await fetch_messages(user_id, after_date, sender_terms)
        # Tolerate a bare list from older fakes — the contract is the dict
        if isinstance(result, dict):
            messages = result.get("messages") or []
            truncated = bool(result.get("truncated"))
        else:
            messages = result or []
            truncated = False
        last_received = None
        for msg in messages:
            summary["scanned"] += 1
            received = _naive_utc(msg.get("received_at"))
            if received is not None and (last_received is None or received > last_received):
                last_received = received
            from_email = extract_email(msg.get("from"))
            body = msg.get("body")

            # Matching (review rounds 1-2): SPECIFIC matches (this
            # application's own email/domain/employer host) attach alone
            # when unique, discriminate by company/title when several.
            # ATS-domain senders are never specific — the same
            # notification domain serves every employer on that ATS, so
            # an ATS match ALWAYS requires the subject discriminator,
            # even when only one application matches (round 2, finding 2).
            direct = [
                app for app in open_candidates
                if sender_matches_application(from_email, app, jobs.get(app.job_id))
            ]
            if not direct:
                continue
            target = None
            if any(
                sender_match_is_specific(from_email, app, jobs.get(app.job_id))
                for app in direct
            ):
                specifics = [
                    app for app in direct
                    if sender_match_is_specific(from_email, app, jobs.get(app.job_id))
                ]
                if len(specifics) == 1:
                    target = specifics[0]
                elif len(specifics) > 1:
                    target = _discriminate_by_subject(msg.get("subject"), specifics, jobs)
            else:
                # ATS-only senders: subject must name exactly one candidate
                target = _discriminate_by_subject(msg.get("subject"), direct, jobs)
            if target is None:
                summary["uncertain"] += 1
                continue

            summary["matched"] += 1
            if not body or not str(body).strip():
                # No full body → cannot classify (never a snippet).
                # Surfaced, not guessed; the scan never wedges on one
                # malformed message.
                summary["uncertain"] += 1
                continue
            classification = classify_message(msg.get("subject"), body)
            if classification is None:
                summary["uncertain"] += 1
                continue  # matched, unclassifiable — this message is done
            proposal = _file_proposal(
                db,
                target,
                classification.status,
                "reply_detection",
                evidence_message_id=str(msg.get("id")),
                evidence_from=from_email,
                evidence_subject=(msg.get("subject") or "")[:500],
                evidence_received_at=received,
                reason=f"matched: '{classification.matched_phrase}'",
            )
            if proposal is not None:
                summary["proposed"] += 1
        # Cursor safety (review round 1, finding 8): advance only over
        # covered ground. Truncated → the day of the last message we
        # actually saw (the one-day overlap + unique index handle the
        # seam); complete → today.
        summary["truncated"] = truncated
        if truncated:
            if last_received is not None:
                state.scan_cursor = last_received.date().isoformat()
            # no messages + truncated → window unknown; keep the old cursor
        else:
            state.scan_cursor = date.today().isoformat()
        state.last_scan_at = utc_now()
        state.last_error = None
        db.add(state)
        db.commit()
    except Exception as e:  # noqa: BLE001 — record, never wedge the scan
        db.rollback()
        state.last_error = f"{type(e).__name__}: {e}"
        state.last_scan_at = utc_now()
        db.add(state)
        db.commit()
        raise
    return summary


# ---------------------------------------------------------------------------
# Quiet sweep — silence must land in the denominator
# ---------------------------------------------------------------------------

def propose_no_response_sweep(db: Session, *, user_id, days: int = QUIET_SWEEP_DAYS) -> int:
    """60+ days quiet → propose batch no_response (one confirmation each,
    cited, undoable via a later manual marker). The quiet clock needs a
    KNOWN send time: user-confirmed portal applies without sent_at have
    no honest "quiet since" date, so they are not swept. Follow-up rows
    are pings of their parent, not applications — never swept. An
    application with a recorded employer signal (interview/offer/…) is
    not silent whatever the clock says (review round 1, finding 1). A
    dismissed sweep proposal is never re-filed — dismissal is a
    decision, not a pending state."""
    cutoff = utc_now() - timedelta(days=days)
    rows = (
        db.query(Application)
        .filter(
            Application.user_id == user_id,
            Application.sent_at.isnot(None),
            Application.sent_at < cutoff,
            Application.follow_up_of_id.is_(None),
        )
        .all()
    )
    count = 0
    for app in rows:
        if (
            app.outcome_status in FINAL_OUTCOMES
            or app.outcome_status in EMPLOYER_SIGNAL_OUTCOMES
        ):
            continue
        already = (
            db.query(OutcomeProposal)
            .filter(
                OutcomeProposal.application_id == app.id,
                OutcomeProposal.source == "sweep",
            )
            .first()
        )
        if already is not None:
            continue
        _file_proposal(
            db,
            app,
            "no_response",
            "sweep",
            reason=f"no employer reply recorded {days}+ days after send",
        )
        count += 1
    db.commit()
    return count


# ---------------------------------------------------------------------------
# Nudges — read-only offers; every send still routes the draft gates
# ---------------------------------------------------------------------------

def _in_the_door(a: Application) -> bool:
    """The stats/nudge denominator: sent by us, or the user confirmed
    they applied (beta reality: email apply is off, browser/manual
    applies need the user's own confirmation to count). Follow-up rows
    are pings of a parent, never their own applications (review round
    1, finding 4)."""
    if a.follow_up_of_id is not None:
        return False
    return a.sent_at is not None or a.outcome_status == "applied"


def compute_nudges(db: Session, *, user_id) -> dict:
    """Deadline urgency (Finish-applying cliff), follow-up offers, and
    the confirm-applied prompt. Read-only — creating a follow-up or
    confirming a send is an explicit user action."""
    now = utc_now()
    horizon = timedelta(days=settings.MAX_POSTING_AGE_DAYS)

    # --- Deadline urgency: documents written, never sent ---------------
    # ready drafts with no application row, plus manual_pending hand-offs
    # still unconfirmed after 3 days (both are the same cliff).
    deadline_items = []
    drafts = (
        db.query(ApplicationDraft)
        .filter(
            ApplicationDraft.user_id == user_id,
            ApplicationDraft.status == "ready",
            # Follow-up drafts are NOT first-contact packages awaiting a
            # send — they ping a job already applied to, and their
            # posting window is usually long past. Showing them under
            # 'Finish applying' as missed deadlines is noise (review
            # round 2, finding 4).
            ApplicationDraft.follow_up_application_id.is_(None),
        )
        .all()
    )
    # Scoped to THIS user (review round 1, finding 7 — the unscoped
    # variant read every tenant's applications on the service session
    # where RLS is not enforced; the same shape drove the 2026-09-11
    # egress incident).
    sent_draft_ids = {
        d_id for (d_id,) in db.query(Application.draft_id).filter(
            Application.user_id == user_id,
            Application.draft_id.isnot(None),
            Application.status.in_(["sent", "manual_pending"]),
        ).all()
    } if drafts else set()
    unsent = [d for d in drafts if d.id not in sent_draft_ids]
    # one batched job fetch — the per-draft lazy query was N+1
    jobs_by_id = {
        j.id: j
        for j in db.query(JobPosting).filter(
            JobPosting.id.in_({d.job_id for d in unsent})
        ).all()
    } if unsent else {}
    for draft in unsent:
        job = jobs_by_id.get(draft.job_id)
        if job is None:
            continue
        anchor = job.published_at or job.scraped_at
        deadline = anchor + horizon
        days_left = (deadline - now).days
        if days_left > DEADLINE_URGENT_DAYS:
            continue
        deadline_items.append({
            "draft_id": draft.id,
            "job_id": job.id,
            "title": job.title,
            "company": job.company,
            "days_left": days_left,
            "level": "urgent" if days_left >= 0 else "passed",
        })

    # --- Follow-up offers: 10+ days quiet, <2 follow-ups ---------------
    apps = db.query(Application).filter(Application.user_id == user_id).all()
    follow_up_counts: dict = {}
    for a in apps:
        if a.follow_up_of_id is not None:
            follow_up_counts[a.follow_up_of_id] = (
                follow_up_counts.get(a.follow_up_of_id, 0) + 1
            )
    follow_ups = []
    confirm_applied = []
    for a in apps:
        if a.outcome_status in FINAL_OUTCOMES:
            continue
        if a.follow_up_of_id is not None:
            # a follow-up ping is never offered its own follow-up, never
            # asked to confirm the parent's portal hand-off (finding 4)
            continue
        if a.sent_at is not None and a.status == "sent":
            quiet_days = (now - a.sent_at).days
            if (
                quiet_days >= FOLLOW_UP_QUIET_DAYS
                and follow_up_counts.get(a.id, 0) < FOLLOW_UP_MAX
            ):
                follow_ups.append({
                    "application_id": a.id,
                    "job_id": a.job_id,
                    "days_quiet": quiet_days,
                    "follow_up_count": follow_up_counts.get(a.id, 0),
                })
        elif a.status == "manual_pending" and a.outcome_status is None:
            # Handed off 3+ days ago and never confirmed — the honest
            # stats need the user's own "I applied".
            if (now - a.created_at).days >= 3:
                confirm_applied.append({"application_id": a.id, "job_id": a.job_id})

    pending_sweep = (
        db.query(OutcomeProposal)
        .filter(
            OutcomeProposal.user_id == user_id,
            OutcomeProposal.decision.is_(None),
        )
        .count()
    )

    deadline_items.sort(key=lambda d: d["days_left"])
    return {
        "deadlines": deadline_items,
        "follow_ups": follow_ups,
        "confirm_applied": confirm_applied,
        "pending_proposals": pending_sweep,
        "thresholds": {
            "deadline_urgent_days": DEADLINE_URGENT_DAYS,
            "follow_up_quiet_days": FOLLOW_UP_QUIET_DAYS,
            "follow_up_max": FOLLOW_UP_MAX,
            "quiet_sweep_days": QUIET_SWEEP_DAYS,
        },
    }


# ---------------------------------------------------------------------------
# Stats — one honest denominator
# ---------------------------------------------------------------------------

def outcome_stats(db: Session, *, user_id) -> dict:
    """Response/interview rates over the applications that are actually
    OUT THE DOOR: sent by us (sent_at) or user-confirmed applied.
    manual_pending hand-offs without confirmation are unknown, not sent —
    they are NOT in the denominator (an honest small number beats an
    inflated guess). no_response (incl. sweep results) counts in the
    denominator — silence is data."""
    apps = db.query(Application).filter(Application.user_id == user_id).all()
    door = [a for a in apps if _in_the_door(a)]
    responded = [a for a in door if a.outcome_status in EMPLOYER_SIGNAL_OUTCOMES]
    interviews = [
        a for a in door if a.outcome_status in {"interview", "offer", "hired"}
    ]
    # time-to-first-response from the honest timestamps: reply arrival
    # (evidence_received_at carried into resolved_at) minus the send.
    times = [
        (a.resolved_at - a.sent_at).days
        for a in responded
        if a.resolved_at and a.sent_at and (a.resolved_at - a.sent_at).days >= 0
    ]
    n = len(door)
    return {
        "sent_total": n,
        "responded_total": len(responded),
        "response_rate": round(len(responded) / n, 4) if n else None,
        "interview_total": len(interviews),
        "interview_rate": round(len(interviews) / n, 4) if n else None,
        "no_response_total": sum(1 for a in door if a.outcome_status == "no_response"),
        "avg_days_to_first_response": round(sum(times) / len(times), 1) if times else None,
        "outcome_counts": {
            s: sum(1 for a in door if a.outcome_status == s)
            for s in OUTCOME_STATUSES
        },
    }


# ---------------------------------------------------------------------------
# Follow-up draft — same gates, seeded from the original package
# ---------------------------------------------------------------------------

_FOLLOW_UP_EN = (
    "Hi {team},\n\n"
    "I'm following up on my application for the {title} position at "
    "{company}, sent on {sent_date}. I remain very interested in the "
    "role and would be glad to answer any questions or provide more "
    "detail. My CV and cover letter are attached again for convenience.\n\n"
    "Thank you for your time,\n{name}\n\n"
    "--- Original application below ---\n\n{original}"
)
_FOLLOW_UP_SV = (
    "Hej {team},\n\n"
    "Jag hör av mig igen angående min ansökan till {title} på {company}, "
    "som jag skickade {sent_date}. Jag är fortfarande mycket intresserad "
    "av tjänsten och svarar gärna på frågor eller fyller i mer. CV och "
    "personligt brev bifogas igen för enkelhetens skull.\n\n"
    "Tack för din tid,\n{name}\n\n"
    "--- Ursprunglig ansökan nedan ---\n\n{original}"
)


def create_follow_up_draft(db: Session, application: Application, profile) -> ApplicationDraft:
    """Draft a short follow-up in the user's voice, grounded ONLY in the
    original application (title, company, send date) — nothing new is
    claimed. Seeded with the original package so the send path is
    unchanged; routed through the SAME review-approve gates (it is a
    normal draft). Max 2 follow-ups per application — drafts in review
    count too (two drafted follow-ups in the queue IS the cap reached;
    failed sends don't)."""
    existing_apps = (
        db.query(Application)
        .filter(
            Application.follow_up_of_id == application.id,
            Application.status != "failed",
        )
        .count()
    )
    existing_drafts = (
        db.query(ApplicationDraft)
        .filter(
            ApplicationDraft.follow_up_application_id == application.id,
            ApplicationDraft.status.in_(["drafting", "ready", "sending"]),
        )
        .count()
    )
    if existing_apps + existing_drafts >= FOLLOW_UP_MAX:
        raise OutcomeError(
            f"already {existing_apps + existing_drafts} follow-ups (max {FOLLOW_UP_MAX})"
        )
    if application.sent_at is None:
        raise OutcomeError("follow-ups are for applications that were sent")
    if application.outcome_status in FINAL_OUTCOMES:
        raise OutcomeError("this application is closed — no follow-up")

    job = db.query(JobPosting).filter(JobPosting.id == application.job_id).first()
    original = (
        db.query(ApplicationDraft)
        .filter(ApplicationDraft.id == application.draft_id)
        .first()
        if application.draft_id
        else None
    )
    template = (
        _FOLLOW_UP_SV
        if (getattr(profile, "country", None) or "").upper() == "SE"
        else _FOLLOW_UP_EN
    )
    team = (job.company if job and job.company else "hiring team")
    cover = template.format(
        team=team,
        title=job.title if job else "role",
        company=team,
        sent_date=application.sent_at.date().isoformat(),
        name=(profile.full_name or "Applicant"),
        original=(original.cover_letter if original else application.body or ""),
    )
    draft = ApplicationDraft(
        user_id=application.user_id,
        job_id=application.job_id,
        match_id=application.match_id,
        cover_letter=cover,
        tailored_cv=(original.tailored_cv if original else None),
        changes_summary=None,
        cv_file_path=(original.cv_file_path if original else profile.cv_file_path),
        cv_hash=(original.cv_hash if original else None),
        status="ready",
        follow_up_application_id=application.id,
    )
    db.add(draft)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise OutcomeError("follow-up draft already exists for this application") from e
    db.commit()
    return draft
