"""WO-31: the durable AI job queue and its single drainer.

Owner requirement (2026-10-16): provider load must never fail a user's
request. User-waiting AI work (tailor, recheck, extract, suggest) is a
row in ai_jobs; the request enqueues and returns, ONE drainer loop makes
the provider calls, and a load error (429 / timeout / 5xx / quota)
re-queues the row with next_attempt_at — there is no attempt ceiling for
load. 'failed' is reserved for non-load causes, each with a user-facing
error naming what the user can do.

Lifecycle of a row: queued -> running (lease) -> done | failed, or back
to queued on load. A drainer that dies mid-call (deploy, crash) leaves a
lease that expires; the next claim takes the row again.

Exactly-once result, at-least-once provider call (honest boundary):
the result transaction flips the row with a compare-and-set on
lease_token, so a drainer whose lease was taken over drops its result
(rollback) instead of writing a second one. A crash between the
provider's reply and that commit re-runs the call — one duplicate
charge, never a duplicate result.

Where it runs: settings.AI_DRAINER — 'api' (a thread in the API, beta),
'worker' (the dedicated worker, launch), 'inline' (the enqueuing request
drains synchronously — tests/local dev), 'off' (tests drive drain_once).
"""

import json
import logging
import math
import random
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Dict, Optional

from sqlalchemy import and_, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import utc_now
from app.models import AIJob

logger = logging.getLogger(__name__)

# Lower claims first. Interactive (a user is looking at a spinner) beats
# every background class; the match classes land with WO-31 step 3.
PRIORITY_INTERACTIVE = 0
PRIORITY_ONBOARDING = 10
PRIORITY_MANUAL = 20
PRIORITY_SCHEDULED = 30
PRIORITY_BACKLOG = 40
PRIORITY_DEEP_BACKLOG = 50

INTERACTIVE_KINDS = frozenset({"tailor", "recheck", "extract", "suggest"})

LOAD_BACKOFF_BASE_S = 2.0
LOAD_BACKOFF_MAX_S = 120.0
# Finished rows are kept this long (status/result reads, debugging),
# then purged by the drainer's housekeeping.
FINISHED_RETENTION = timedelta(days=7)
# ETA fallback before any job has finished in the window: about one
# interactive job per minute per concurrency slot.
_FALLBACK_JOBS_PER_MIN_PER_SLOT = 1.0
_RATE_WINDOW = timedelta(minutes=15)


@dataclass
class Outcome:
    """What a handler produced. status: done | failed | dropped
    (dropped = the subject is gone or no longer the job's target; the
    row finishes 'done' with last_error_code='dropped', no result)."""

    status: str
    error: Optional[str] = None
    result: Optional[dict] = None
    after_commit: Optional[Callable[[], None]] = None


class Requeue(Exception):
    """A handler asks to run again at once — its subject changed under
    the call (e.g. the CV was re-uploaded while extract ran)."""


@dataclass
class Handler:
    # run(db, job) stages its writes on db WITHOUT committing — the
    # drainer commits them together with the job's CAS, or rolls back.
    run: Callable[[Session, AIJob], Outcome]
    # fail(db, job, exc) stages the user-facing failure for a NON-load
    # exception (after a rollback) and returns the failed Outcome.
    fail: Callable[[Session, AIJob, Exception], Outcome]


_HANDLERS: Dict[str, Handler] = {}


def register(kind: str, handler: Handler) -> None:
    _HANDLERS[kind] = handler


def _handler(kind: str) -> Handler:
    if kind not in _HANDLERS:
        # Lazy: the handlers import draft/cv services, which import this
        # module to enqueue — registration at first use breaks the cycle.
        import app.services.ai_job_handlers  # noqa: F401
    return _HANDLERS[kind]


def _session_factory():
    from app.core.database import SessionLocal

    return SessionLocal


# ------------------------------------------------------------------
# Enqueue + status (request side)
# ------------------------------------------------------------------

_wake = threading.Event()


def wake() -> None:
    """Interactive pickup without polling: set on every enqueue."""
    _wake.set()


def active_job(db: Session, *, user_id, kind: str, subject_id: int) -> Optional[AIJob]:
    return (
        db.query(AIJob)
        .filter(
            AIJob.user_id == user_id,
            AIJob.kind == kind,
            AIJob.subject_id == subject_id,
            AIJob.status.in_(("queued", "running")),
        )
        .first()
    )


def latest_job(db: Session, *, user_id, kind: str, subject_id: int) -> Optional[AIJob]:
    return (
        db.query(AIJob)
        .filter(
            AIJob.user_id == user_id,
            AIJob.kind == kind,
            AIJob.subject_id == subject_id,
        )
        .order_by(AIJob.id.desc())
        .first()
    )


def active_jobs_by_subject(db: Session, *, user_id, kinds) -> Dict[int, AIJob]:
    """{subject_id: active job} for one user — one query for a list view."""
    rows = (
        db.query(AIJob)
        .filter(
            AIJob.user_id == user_id,
            AIJob.kind.in_(tuple(kinds)),
            AIJob.status.in_(("queued", "running")),
        )
        .all()
    )
    return {j.subject_id: j for j in rows}


def draft_queue_view(db: Session, draft) -> Optional[dict]:
    """The draft's active tailor/recheck job as a view, or None."""
    jobs = active_jobs_by_subject(db, user_id=draft.user_id, kinds=("tailor", "recheck"))
    return job_view(jobs.get(draft.id))


def extraction_view(db: Session, profile) -> Optional[dict]:
    """The profile's extract job while it waits, or its failure in the
    last day (the action to take); None once fields have landed."""
    if profile is None:
        return None
    job = latest_job(db, user_id=profile.user_id, kind="extract", subject_id=profile.id)
    if job is None or job.status == "done":
        return None
    if job.status == "failed" and (
        job.finished_at is None or job.finished_at < utc_now() - timedelta(days=1)
    ):
        return None
    return job_view(job)


def enqueue(
    db: Session,
    *,
    user_id,
    kind: str,
    subject_id: int,
    params: Optional[dict] = None,
    priority: int = PRIORITY_INTERACTIVE,
) -> AIJob:
    """Idempotent: one active job per (kind, user, subject). A second
    press returns the job already queued/running — never a second row.

    The caller must have COMMITTED its own work first: a lost race on
    the partial unique rolls this session back.
    """
    payload = json.dumps(params, sort_keys=True) if params is not None else None
    existing = active_job(db, user_id=user_id, kind=kind, subject_id=subject_id)
    if existing is not None:
        if payload is not None and existing.status == "queued" and existing.params != payload:
            # Not started yet: the newest request's parameters win
            # (the user switched search mode before it ran).
            existing.params = payload
            db.commit()
        return existing
    now = utc_now()
    job = AIJob(
        user_id=user_id,
        kind=kind,
        subject_id=subject_id,
        priority_class=priority,
        status="queued",
        attempts=0,
        next_attempt_at=now,
        enqueued_at=now,
        params=payload,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # Lost the race to a concurrent press: the partial unique is the
        # idempotency backstop. Return the winner's row.
        db.rollback()
        existing = active_job(db, user_id=user_id, kind=kind, subject_id=subject_id)
        if existing is None:
            raise
        return existing
    db.refresh(job)
    wake()
    return job


def drain_if_inline() -> None:
    """AI_DRAINER=inline: the enqueuing request drains synchronously
    (tests, local dev). Every other mode returns at once."""
    if settings.AI_DRAINER == "inline":
        AIDrainer().drain_once()


def _drain_rate_per_min(db: Session, now) -> float:
    done = (
        db.query(func.count(AIJob.id))
        .filter(
            AIJob.status.in_(("done", "failed")),
            AIJob.finished_at >= now - _RATE_WINDOW,
            AIJob.priority_class == PRIORITY_INTERACTIVE,
        )
        .scalar()
        or 0
    )
    observed = done / (_RATE_WINDOW.total_seconds() / 60.0)
    if observed > 0:
        return observed
    return _FALLBACK_JOBS_PER_MIN_PER_SLOT * max(1, settings.AI_CONCURRENCY_START)


def job_view(job: Optional[AIJob], *, include_result: bool = False) -> Optional[dict]:
    """What the UI needs to show an honest queued/working state.

    position = queued jobs ahead in claim order (0 = next); eta_minutes
    = (position + 1) / observed drain rate, at least the remaining
    Retry-After wait — an estimate, never a promise. paused_until is set
    while the row waits out a provider quota window.

    Position and rate count EVERY user's rows, so they run on the
    service session (aggregates only — no other user's data leaves).
    """
    if job is None:
        return None
    now = utc_now()
    view = {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "position": 0,
        "eta_minutes": None,
        "paused_until": None,
        "error": job.error if job.status == "failed" else None,
    }
    if job.status == "queued":
        sdb = _session_factory()()
        try:
            ahead = (
                sdb.query(func.count(AIJob.id))
                .filter(
                    AIJob.status == "queued",
                    AIJob.id != job.id,
                    or_(
                        AIJob.priority_class < job.priority_class,
                        and_(
                            AIJob.priority_class == job.priority_class,
                            or_(
                                AIJob.enqueued_at < job.enqueued_at,
                                and_(AIJob.enqueued_at == job.enqueued_at, AIJob.id < job.id),
                            ),
                        ),
                    ),
                )
                .scalar()
                or 0
            )
            rate = _drain_rate_per_min(sdb, now)
        finally:
            sdb.close()
        eta = math.ceil((ahead + 1) / rate) if rate > 0 else None
        wait_s = (job.next_attempt_at - now).total_seconds() if job.next_attempt_at else 0
        if wait_s > 0:
            eta = max(eta or 0, math.ceil(wait_s / 60.0))
        view["position"] = ahead
        view["eta_minutes"] = max(1, eta) if eta is not None else None
        if (job.last_error_code or "").startswith("quota") and wait_s > 0:
            view["paused_until"] = job.next_attempt_at
    if include_result and job.status == "done" and job.result:
        try:
            view["result"] = json.loads(job.result)
        except ValueError:
            view["result"] = None
    return view


# ------------------------------------------------------------------
# AIMD concurrency (inside the drainer)
# ------------------------------------------------------------------


class ConcurrencyController:
    """Additive increase / multiplicative decrease on provider load.

    Start at AI_CONCURRENCY_START, +1 per 60s without a rate limit up to
    AI_CONCURRENCY_MAX, halve on any 429. Background (match) claims may
    hold at most limit - reserve slots, so an interactive job never waits
    behind a full set of matches; a quota window pauses background claims
    until its reset while interactive rows stay queued and visible.
    """

    INCREASE_EVERY = timedelta(seconds=60)

    def __init__(self, start: int, maximum: int, reserve: int, clock=utc_now):
        self.max = max(1, maximum)
        self.limit = max(1, min(start, self.max))
        self.reserve = max(0, reserve)
        self.clock = clock
        self._last_change = clock()
        self.background_paused_until = None
        self._lock = threading.Lock()

    def on_load(self, err) -> None:
        now = self.clock()
        with self._lock:
            if getattr(err, "quota", False) and err.delay_s:
                until = now + timedelta(seconds=err.delay_s)
                if self.background_paused_until is None or until > self.background_paused_until:
                    self.background_paused_until = until
            if getattr(err, "code", None) not in ("timeout", "connection", "5xx"):
                self.limit = max(1, self.limit // 2)
                self._last_change = now

    def tick(self) -> None:
        now = self.clock()
        with self._lock:
            if self.limit < self.max and now - self._last_change >= self.INCREASE_EVERY:
                self.limit += 1
                self._last_change = now

    def background_allowed(self) -> bool:
        until = self.background_paused_until
        return until is None or self.clock() >= until

    def background_slots(self, *, interactive_waiting: bool) -> int:
        cap = self.limit - self.reserve
        if cap < 1:
            # Tiny limits: background keeps one slot unless a user waits.
            cap = 0 if interactive_waiting else 1
        return cap


def _load_delay_s(err, attempts: int) -> float:
    if getattr(err, "delay_s", None) is not None:
        return float(err.delay_s)
    base = LOAD_BACKOFF_BASE_S * (2 ** max(0, attempts - 1))
    return min(base, LOAD_BACKOFF_MAX_S) * random.uniform(0.75, 1.25)


# ------------------------------------------------------------------
# The drainer
# ------------------------------------------------------------------


def _claimable(now):
    return or_(
        and_(AIJob.status == "queued", AIJob.next_attempt_at <= now),
        # Expired lease: its drainer died mid-call (deploy, crash).
        and_(AIJob.status == "running", AIJob.lease_until < now),
    )


class AIDrainer:
    """Claims due ai_jobs and runs them. One per process (AI_DRAINER).

    Two drainers (Render's zero-downtime deploy overlap) stay correct:
    claims are FOR UPDATE SKIP LOCKED on Postgres plus a compare-and-set
    UPDATE on every backend, and results commit only under the claimer's
    lease_token.
    """

    def __init__(self, session_factory=None, controller: Optional[ConcurrencyController] = None,
                 clock=utc_now):
        self.session_factory = session_factory or _session_factory()
        self.clock = clock
        self.controller = controller or ConcurrencyController(
            settings.AI_CONCURRENCY_START,
            settings.AI_CONCURRENCY_MAX,
            settings.AI_INTERACTIVE_RESERVE,
            clock=clock,
        )
        self._inflight: Dict[int, tuple] = {}  # job id -> (token, background)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._last_heartbeat = None
        self._last_housekeeping = None

    # -- claiming --------------------------------------------------

    def claim(self, *, allow_background: bool = True) -> Optional[tuple]:
        """Claim the next due job: (job_id, token, background) or None.

        Order: priority class, then enqueue time (FIFO within a class).
        """
        now = self.clock()
        lease = timedelta(seconds=settings.AI_JOB_LEASE_SECONDS)
        db = self.session_factory()
        try:
            q = db.query(AIJob.id, AIJob.priority_class).filter(_claimable(now))
            if not allow_background:
                q = q.filter(AIJob.priority_class == PRIORITY_INTERACTIVE)
            candidates = (
                q.order_by(AIJob.priority_class, AIJob.enqueued_at, AIJob.id)
                .limit(8)
                .with_for_update(skip_locked=True)
                .all()
            )
            for job_id, priority in candidates:
                token = str(uuid.uuid4())
                rc = db.execute(
                    update(AIJob)
                    .where(AIJob.id == job_id, _claimable(now))
                    .values(
                        status="running",
                        lease_token=token,
                        lease_until=now + lease,
                        started_at=now,
                        attempts=AIJob.attempts + 1,
                    )
                    .execution_options(synchronize_session=False)
                ).rowcount
                if rc == 1:
                    db.commit()
                    return job_id, token, priority != PRIORITY_INTERACTIVE
            db.rollback()
            return None
        finally:
            db.close()

    def _interactive_waiting(self) -> bool:
        db = self.session_factory()
        try:
            return (
                db.query(AIJob.id)
                .filter(_claimable(self.clock()), AIJob.priority_class == PRIORITY_INTERACTIVE)
                .first()
                is not None
            )
        finally:
            db.close()

    def _background_allowed_now(self, background_running: int) -> bool:
        if not self.controller.background_allowed():
            return False
        slots = self.controller.background_slots(interactive_waiting=self._interactive_waiting())
        return background_running < slots

    # -- running ---------------------------------------------------

    def run_job(self, job_id: int, token: str) -> str:
        """Run one claimed job. Returns done | failed | dropped |
        requeued | lost (lease taken over: result dropped)."""
        from app.services.ai_service import AILoadError, current_user_id, queue_mode

        db = self.session_factory()
        try:
            job = db.get(AIJob, job_id)
            if job is None or job.lease_token != token or job.status != "running":
                return "lost"
            handler = _handler(job.kind)
            attempts = job.attempts
            # ai_usage attribution: every call this job makes is the
            # job's user's, not the drainer's (system) context.
            u_tok = current_user_id.set(job.user_id)
            q_tok = queue_mode.set(True)
            try:
                try:
                    outcome = handler.run(db, job)
                except AILoadError as e:
                    db.rollback()
                    self.controller.on_load(e)
                    code = f"quota:{e.code}" if e.quota else (e.code or "load")
                    if e.quota or e.account:
                        # Page-worthy (WO-31 §4): the account itself is
                        # limited, not one burst. Work stays queued.
                        logger.error(
                            "AI provider %s (%s) — job %s stays queued for %.0fs",
                            "quota window" if e.quota else "account-level limit",
                            e.code, job_id, _load_delay_s(e, attempts),
                        )
                    return self._requeue(db, job_id, token, _load_delay_s(e, attempts), code)
                except Requeue:
                    db.rollback()
                    return self._requeue(db, job_id, token, 0.0, "subject_changed")
                except Exception as e:  # noqa: BLE001 — a non-load failure
                    db.rollback()
                    logger.exception("ai_job %s (%s) failed: %s", job_id, job.kind, e)
                    job = db.get(AIJob, job_id)
                    outcome = handler.fail(db, job, e)
                if not self._finish(db, job_id, token, outcome):
                    db.rollback()
                    logger.warning("ai_job %s lease lost — result dropped", job_id)
                    return "lost"
                self.controller.tick()
                if outcome.after_commit is not None:
                    try:
                        outcome.after_commit()
                    except Exception as e:  # noqa: BLE001 — post-commit is best-effort
                        logger.warning("ai_job %s after-commit hook failed: %s", job_id, e)
                return outcome.status
            finally:
                queue_mode.reset(q_tok)
                current_user_id.reset(u_tok)
        finally:
            db.close()

    def _finish(self, db: Session, job_id: int, token: str, outcome: Outcome) -> bool:
        """The ONLY result writer: CAS on the lease token, committed in
        the same transaction as the handler's staged writes."""
        db.flush()
        values = {
            "status": "failed" if outcome.status == "failed" else "done",
            "finished_at": self.clock(),
            "lease_until": None,
            "error": outcome.error if outcome.status == "failed" else None,
            "result": json.dumps(outcome.result) if outcome.result is not None else None,
        }
        if outcome.status == "dropped":
            values["last_error_code"] = "dropped"
        rc = db.execute(
            update(AIJob)
            .where(AIJob.id == job_id, AIJob.lease_token == token, AIJob.status == "running")
            .values(**values)
            .execution_options(synchronize_session=False)
        ).rowcount
        if rc != 1:
            return False
        db.commit()
        return True

    def _requeue(self, db: Session, job_id: int, token: str, delay_s: float, code: str) -> str:
        rc = db.execute(
            update(AIJob)
            .where(AIJob.id == job_id, AIJob.lease_token == token, AIJob.status == "running")
            .values(
                status="queued",
                next_attempt_at=self.clock() + timedelta(seconds=delay_s),
                lease_until=None,
                lease_token=None,
                last_error_code=code[:40],
            )
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if rc == 1:
            logger.info("ai_job %s re-queued in %.1fs (%s)", job_id, delay_s, code)
            return "requeued"
        return "lost"

    def drain_once(self, *, max_jobs: int = 1000) -> list:
        """Synchronously claim + run every DUE job, one at a time, until
        none is due. Returns each run's status. Tests and inline mode."""
        statuses = []
        for _ in range(max_jobs):
            claimed = self.claim(allow_background=self._background_allowed_now(0))
            if claimed is None:
                break
            job_id, token, _bg = claimed
            statuses.append(self.run_job(job_id, token))
        return statuses

    # -- the loop (AI_DRAINER=api|worker) --------------------------

    def heartbeat(self) -> None:
        """Renew the lease of every in-flight job this drainer owns."""
        with self._lock:
            owned = list(self._inflight.items())
        if not owned:
            return
        until = self.clock() + timedelta(seconds=settings.AI_JOB_LEASE_SECONDS)
        db = self.session_factory()
        try:
            for job_id, (token, _bg) in owned:
                db.execute(
                    update(AIJob)
                    .where(AIJob.id == job_id, AIJob.lease_token == token, AIJob.status == "running")
                    .values(lease_until=until)
                    .execution_options(synchronize_session=False)
                )
            db.commit()
        finally:
            db.close()

    def housekeeping(self) -> int:
        """Purge finished rows past FINISHED_RETENTION."""
        db = self.session_factory()
        try:
            n = (
                db.query(AIJob)
                .filter(
                    AIJob.status.in_(("done", "failed")),
                    AIJob.finished_at < self.clock() - FINISHED_RETENTION,
                )
                .delete(synchronize_session=False)
            )
            db.commit()
            return n
        finally:
            db.close()

    def fill(self) -> int:
        """Claim up to the controller's limit and submit each to the pool."""
        started = 0
        while not self._stop.is_set():
            with self._lock:
                inflight = len(self._inflight)
                background = sum(1 for _t, bg in self._inflight.values() if bg)
            if inflight >= self.controller.limit:
                break
            claimed = self.claim(allow_background=self._background_allowed_now(background))
            if claimed is None:
                break
            job_id, token, bg = claimed
            with self._lock:
                self._inflight[job_id] = (token, bg)
            self._executor.submit(self._run_and_release, job_id, token)
            started += 1
        return started

    def _run_and_release(self, job_id: int, token: str) -> None:
        try:
            self.run_job(job_id, token)
        except Exception:  # noqa: BLE001 — the loop must survive any job
            logger.exception("ai_job %s crashed the runner", job_id)
        finally:
            with self._lock:
                self._inflight.pop(job_id, None)
            _wake.set()  # a slot freed: claim the next one now

    def _loop(self) -> None:
        logger.info("AI drainer started (mode=%s, C=%d)", settings.AI_DRAINER, self.controller.limit)
        while not self._stop.is_set():
            try:
                now = self.clock()
                self.fill()
                if (self._last_heartbeat is None or (now - self._last_heartbeat).total_seconds()
                        >= settings.AI_JOB_HEARTBEAT_SECONDS):
                    self.heartbeat()
                    self._last_heartbeat = now
                if self._last_housekeeping is None or now - self._last_housekeeping >= timedelta(hours=1):
                    self.housekeeping()
                    self._last_housekeeping = now
                self.controller.tick()
            except Exception:  # noqa: BLE001 — never let the drainer die
                logger.exception("AI drainer loop error")
            # Woken by enqueue / a freed slot; otherwise re-check every 5s
            # for Retry-After rows coming due and expired leases.
            _wake.wait(timeout=5.0)
            _wake.clear()
        logger.info("AI drainer stopped")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.AI_CONCURRENCY_MAX), thread_name_prefix="ai-job"
        )
        self._thread = threading.Thread(target=self._loop, name="ai-drainer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        _wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._executor is not None:
            # In-flight jobs keep their leases; they expire and re-queue
            # if the process dies before the calls return.
            self._executor.shutdown(wait=False, cancel_futures=True)


_drainer: Optional[AIDrainer] = None


def start_drainer(mode: str) -> None:
    """Start this process's drainer when AI_DRAINER names this process."""
    global _drainer
    if settings.AI_DRAINER != mode or _drainer is not None:
        return
    _drainer = AIDrainer()
    _drainer.start()


def stop_drainer() -> None:
    global _drainer
    if _drainer is not None:
        _drainer.stop()
        _drainer = None
