"""J-3 — J-1's blocked-PDF messages say what is wrong and what to do.

J-1 gave every blocked PDF check one shape (class marker first, the
document name, the surface's outcome) but left the "what now" identical
for all four classes: every message ended in the same retry sentence.
That advice is wrong for three of them — a lost character or a lost
phrase is in the DRAFT, and no retry can fix it; only a file the reader
cannot even open is worth another attempt. This contract pins, per
class, what the message says is wrong and what the user should do, on
both outbound surfaces.

test_ac_1  the four classes' wording at the seam (both contexts, both
           document names): the characters lost are named literally with
           their code points, up to six then 'and N more'; the too-short
           class keeps the extraction count; the phrase-lost class keeps
           'text layer'; only 'unreadable PDF' advises retrying; the
           three content classes end with their own advice. Then the two
           real surfaces pass a content class's new wording through
           unchanged, and the seam plus the verified builders refuse any
           context other than 'send'/'download' with ValueError.
test_ac_2  every J-1 and WO-21 test is run for real, against the live
           service — AC-2's 'all J-1 and WO-21 tests still pass' clause,
           observed rather than assumed. Then J-1's structural checks
           are made with ast over the parsed source, so a comment,
           docstring or string literal can neither satisfy nor break
           them — and each check still bites on a real structural
           violation.
"""

import ast
import inspect
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models import ApplicationDraft, JobPosting, Profile
from app.models import User as UserModel
from app.services import pdf_service
from app.services.draft_service import submit_draft
from tests.auth_helpers import get_or_create_user, mint_token

# The four class markers and the two document names, verbatim from the
# approved contracts (J-1 named them; J-3 keeps the shape they define).
MARKERS = (
    "unreadable PDF",
    "text layer too short",
    "characters lost",
    "required phrase lost",
)
DOCUMENTS = ("cover letter", "tailored CV")
NOT_SENT = "the application was NOT sent."
# The only class that may advise retrying must carry this exact advice.
RETRY_ADVICE = "try again; if this keeps happening, contact the operator"

# A body every render path draws faithfully (pure ascii: survives the
# unicode font AND the latin-1 core-font fallback alike).
HEALTHY_BODY = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nBest regards,\n"
)

# The letter the surface tests seed: HEALTHY_BODY plus exactly one
# character outside latin-1 (the em-dash), so a render that dropped it
# names exactly one lost character.
EM_DASH_LETTER = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nI am available to start "
    "immediately — next month at the latest.\n\nBest regards,\n"
)

# Bytes no PDF reader can open: extraction raises on them (the
# 'unreadable PDF' class).
GARBAGE_PDF = b"this is not a pdf; the reader must raise on it"

# The 'characters lost' listing, spelled the contract's way — each lost
# character literally, then its code point in parentheses. The listings
# below are hand-worked expectations, not the implementation's output:
# _required_glyphs sorts by code point, so SIX_LOST (all above U+2000,
# already ascending) lists in the order written, and prepending ± (U+00B1)
# and × (U+00D7) pushes them to the FRONT of the sorted listing — the
# first six shown are then ± × and the first four of SIX_LOST, and the
# last two of SIX_LOST are the "2 more" the message must only count.
SIX_LOST = "— ’ “ ” … €"
SIX_LISTING = (
    "— (U+2014), ’ (U+2019), “ (U+201C), ” (U+201D), … (U+2026), € (U+20AC)"
)
EIGHT_LOST = "± × " + SIX_LOST
EIGHT_LISTING = (
    "± (U+00B1), × (U+00D7), — (U+2014), ’ (U+2019), “ (U+201C), ” "
    "(U+201D) and 2 more"
)
# The two characters EIGHT_LISTING must NOT name (they are the count).
COUNTED_WHEN_EIGHT = ("…", "€")

# Each content class ends with its own advice — these phrases, from the
# contract text, are what "what to do" means per class.
ADVICE_REPLACE = "replace those characters in the draft."
ADVICE_CONTENT = "check the draft's content."
ADVICE_FORMATTING = "check the draft's formatting."


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — the
    # endpoint and send-flow halves need the tables.
    with TestClient(app) as c:  # lifespan runs init_db -> alembic head
        yield c
    engine.dispose()


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _message_for(pdf_bytes: bytes, *, context: str, label: str, **kwargs) -> str:
    """The message verify_text_layer itself builds for this input — the
    single place blocked-check wording is decided. The surface halves of
    test_ac_1 compare what those surfaces surface against THIS, proving
    nothing downstream rewrites it."""
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.verify_text_layer(pdf_bytes, context=context, label=label, **kwargs)
    return str(excinfo.value)


def _graphics_only_pdf() -> bytes:
    """A visually-valid PDF with no text layer at all: extraction returns
    nothing, so the message reports 'text extraction returned 0 characters'
    — the count the too-short class must keep naming."""
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_draw_color(0, 0, 0)
    pdf.rect(20.0, 20.0, 150.0, 80.0)
    return bytes(pdf.output())


def _assert_shape(message: str, context: str, document: str) -> None:
    """J-1's structural rules, kept by every J-3 message: begins with
    exactly one class marker, names the document, and carries the
    surface's outcome."""
    begins = [m for m in MARKERS if message.startswith(m)]
    assert begins, f"no class marker at the start of {message!r}"
    assert sum(message.count(m) for m in MARKERS) == 1, (
        f"marker literals must total exactly 1: {message!r}"
    )
    assert document in message, message
    if context == "send":
        assert NOT_SENT in message, message
    else:
        assert "NOT sent" not in message, message


def _assert_ends_with_advice(message: str, advice: str) -> None:
    """A content class ends with its own advice — the last thing the user
    reads is what to do, not the failure."""
    assert message.rstrip().lower().endswith(advice), (message, advice)


def _assert_no_retry(message: str) -> None:
    """A content failure lives in the draft: retrying cannot fix it, so
    the message must not offer it."""
    assert "try again" not in message.lower(), message


def _seed_download_draft(db):
    """A ready draft whose cover letter carries the em-dash, owned by a
    user whose profile names them — so the verification source the
    download surface feeds the seam is known to this test."""
    uid = get_or_create_user(f"j3-dl-{uuid.uuid4().hex[:6]}@test.example")
    profile = db.query(Profile).filter(Profile.user_id == uid).one()
    profile.full_name = "J3 Downloader"
    job = JobPosting(
        source="manual",
        source_id=uuid.uuid4().hex[:8],
        title="Dev",
        company="Acme",
        url=f"https://x/{uuid.uuid4().hex[:6]}",
        status="matched",
        description="A Python role.",
    )
    draft = ApplicationDraft(
        user_id=uid,
        job=job,
        status="ready",
        cover_letter=EM_DASH_LETTER,
        tailored_cv=HEALTHY_BODY,
        changes_summary="[]",
    )
    db.add_all([job, draft])
    db.commit()
    return uid, draft.id


def _seed_ready_email_draft(db):
    """A 'ready' draft on a job that publishes an application email —
    exactly the package submit_draft(method='email') dispatches."""
    uid = uuid.uuid4()
    db.add(
        UserModel(
            id=uid,
            email=f"j3-{uid.hex[:6]}@test.example",
            hashed_password="test-only",
        )
    )
    db.flush()
    profile = Profile(
        user_id=uid, is_active=1, full_name="J3 Sender", cv_text="J3 CV", cv_file_name="cv.pdf"
    )
    job = JobPosting(
        source="manual",
        source_id=uuid.uuid4().hex[:8],
        title="Dev",
        company="Acme",
        url=f"https://x/{uuid.uuid4().hex[:6]}",
        status="matched",
        description="A Python role.",
        application_email="jobs@acme.example",
    )
    db.add_all([profile, job])
    db.flush()
    draft = ApplicationDraft(
        user_id=uid,
        job_id=job.id,
        status="ready",
        cover_letter=EM_DASH_LETTER,
        tailored_cv=HEALTHY_BODY,
        changes_summary="[]",
    )
    db.add(draft)
    db.commit()
    return uid, profile, draft


def _live_email_settings(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "apply@jobfinderos.test")
    monkeypatch.setattr(settings, "EMAIL_APPLY_ENABLED", True)


def _fake_resend(monkeypatch, sink):
    import sys as _sys

    class _Emails:
        @staticmethod
        def send(params):
            sink.append(params)
            return {"id": f"msg_{len(sink)}"}

    monkeypatch.setitem(
        _sys.modules, "resend", type("R", (), {"Emails": _Emails, "api_key": None})
    )


def test_ac_1(client, db, monkeypatch):
    """AC-1: per class, the message says what is wrong and what to do.

    Part 1 drives all four failure classes through verify_text_layer in
    both contexts and for both document names. Parts 2 and 3 land one
    content class on each real surface — the manual-portal download and
    the email send — and compare what the surface says against what the
    seam built. Part 4 pins the context contract: only 'send' and
    'download' exist.
    """
    healthy = pdf_service.cover_letter_pdf(HEALTHY_BODY, "Test Candidate")

    # --- Part 1: four classes x two contexts x two documents, at the seam ---
    for context in ("send", "download"):
        for document in DOCUMENTS:
            # unreadable PDF: the ONE class that advises retrying, and it
            # must carry the contract's exact retry advice.
            unreadable = _message_for(GARBAGE_PDF, context=context, label=document)
            assert unreadable.startswith("unreadable PDF"), unreadable
            assert RETRY_ADVICE in unreadable, unreadable
            _assert_shape(unreadable, context, document)

            # text layer too short: keeps the extraction count, says the
            # document has too little readable text, points at the content.
            empty = _message_for(_graphics_only_pdf(), context=context, label=document)
            assert empty.startswith("text layer too short"), empty
            assert "text extraction returned 0 characters" in empty, empty
            assert "too little readable text" in empty, empty
            _assert_ends_with_advice(empty, ADVICE_CONTENT)
            _assert_no_retry(empty)
            _assert_shape(empty, context, document)

            # required phrase lost: part of the text did not survive, the
            # fix is in the draft's formatting.
            phrase = _message_for(
                healthy,
                context=context,
                label=document,
                must_contain="a phrase that appears nowhere in this document",
            )
            assert phrase.startswith("required phrase lost"), phrase
            assert "text layer" in phrase, phrase
            assert "part of the text" in phrase, phrase
            assert "did not survive" in phrase, phrase
            _assert_ends_with_advice(phrase, ADVICE_FORMATTING)
            _assert_no_retry(phrase)
            _assert_shape(phrase, context, document)

            # characters lost: every lost character named literally with
            # its code point — one, six, then six-of-eight plus a count.
            for lost, listing, counted in (
                ("—", "— (U+2014)", ()),
                (SIX_LOST, SIX_LISTING, ()),
                (EIGHT_LOST, EIGHT_LISTING, COUNTED_WHEN_EIGHT),
            ):
                lost_message = _message_for(
                    healthy,
                    context=context,
                    label=document,
                    source_text=HEALTHY_BODY + "\n" + lost,
                )
                assert lost_message.startswith("characters lost"), lost_message
                assert listing in lost_message, (listing, lost_message)
                for ch in counted:
                    assert ch not in lost_message, (ch, lost_message)
                if counted:
                    assert "and 2 more" in lost_message, lost_message
                else:
                    assert "more" not in lost_message, lost_message
                assert "text layer" in lost_message, lost_message
                _assert_ends_with_advice(lost_message, ADVICE_REPLACE)
                _assert_no_retry(lost_message)
                _assert_shape(lost_message, context, document)

    # A non-zero count is named too: the floor is raised past what the
    # document carries, and the message reports the count the reader saw
    # (measured here with the same read an ATS does, not by re-reading
    # the message).
    extracted = len(pdf_service.extract_text(healthy).strip())
    raised = _message_for(
        healthy, context="send", label="cover letter", min_chars=extracted + 1
    )
    assert f"text extraction returned {extracted} characters" in raised, raised
    _assert_ends_with_advice(raised, ADVICE_CONTENT)
    _assert_no_retry(raised)

    # --- Part 2: a content class reaches the download surface unchanged ---
    uid, draft_id = _seed_download_draft(db)
    headers = {"Authorization": f"Bearer {mint_token(uid, 'j3-dl@test.example')}"}
    # The builder hands the endpoint a faithful ascii render while the
    # draft carries the em-dash: the glyph never extracted, exactly the
    # 'characters lost' class, forced the same way the seam forced it.
    expected = _message_for(
        healthy,
        context="download",
        label="cover letter",
        source_text="J3 Downloader\n" + EM_DASH_LETTER,
    )
    with monkeypatch.context() as mp:
        mp.setattr(pdf_service, "cover_letter_pdf", lambda text, name: healthy)
        resp = client.get(
            f"/api/v1/applications/draft/{draft_id}/download/cover-letter", headers=headers
        )
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == expected, (resp.text, expected)
    detail = resp.json()["detail"]
    assert "— (U+2014)" in detail, detail
    _assert_ends_with_advice(detail, ADVICE_REPLACE)
    _assert_no_retry(detail)
    _assert_shape(detail, "download", "cover letter")

    # --- Part 3: the same class lands on application.error, unchanged ---
    send_uid, profile, draft = _seed_ready_email_draft(db)
    _live_email_settings(monkeypatch)
    sent: list = []
    _fake_resend(monkeypatch, sent)
    expected_send = _message_for(
        healthy,
        context="send",
        label="cover letter",
        source_text=f"{profile.full_name}\n{EM_DASH_LETTER}",
    )
    with monkeypatch.context() as mp:
        mp.setattr(pdf_service, "cover_letter_pdf", lambda text, name: healthy)
        application = submit_draft(db, draft, "email", profile, user_id=send_uid)
    assert application.status == "failed", application.error
    assert application.error == expected_send, (application.error, expected_send)
    _assert_ends_with_advice(application.error, ADVICE_REPLACE)
    _assert_no_retry(application.error)
    _assert_shape(application.error, "send", "cover letter")
    assert not sent, f"the email was dispatched anyway: {sent}"

    # --- Part 4: only 'send' and 'download' are contexts ---
    for bad in ("email", "preview", "", "SEND", None):
        with pytest.raises(ValueError):
            pdf_service.verify_text_layer(healthy, context=bad)
        for builder in (
            pdf_service.verified_cover_letter_pdf,
            pdf_service.verified_tailored_cv_pdf,
        ):
            with pytest.raises(ValueError):
                builder(HEALTHY_BODY, "Test Candidate", context=bad)
    # The two real contexts are accepted, end to end, by all three.
    for context in ("send", "download"):
        assert pdf_service.verify_text_layer(healthy, context=context)
        for builder in (
            pdf_service.verified_cover_letter_pdf,
            pdf_service.verified_tailored_cv_pdf,
        ):
            assert builder(HEALTHY_BODY, "Test Candidate", context=context)


# --- AC-2, first clause: 'All J-1 and WO-21 tests still pass' ---

def _run_suite(request, module) -> None:
    """Run every test in `module`, for real — the real functions with
    the real fixtures, exactly as pytest would run them.

    AC-2's closing clause is 'All J-1 and WO-21 tests still pass': this
    observes it against the live service instead of re-stating what
    those tests check. Each invoked test gets a FRESH database session
    and a FRESH monkeypatch (WO-21's download test calls
    monkeypatch.undo(), so a shared patcher would clobber the caller),
    and every fixture but those two comes from this session's own
    fixture machinery."""
    names = sorted(n for n in dir(module) if n.startswith("test_"))
    assert names, f"{module.__name__} defines no tests"
    for name in names:
        fn = getattr(module, name)
        params = list(inspect.signature(fn).parameters)
        kwargs = {
            p: request.getfixturevalue(p)
            for p in params
            if p not in ("db", "monkeypatch")
        }
        if "db" in params:
            kwargs["db"] = session = SessionLocal()
        try:
            if "monkeypatch" in params:
                with pytest.MonkeyPatch.context() as mp:
                    fn(**kwargs, monkeypatch=mp)
            else:
                fn(**kwargs)
        finally:
            if "db" in params:
                session.rollback()
                session.close()


# --- AC-2: J-1's source checks are structural (ast), not textual ---
#
# The proof runs J-1's OWN test_ac_3 — the real function, not a copy of
# its checks — against doctored sources, through the one seam it reads
# sources with, tests.test_j1_download_errors._read_source.

def _with_text_traps(source: str) -> str:
    """Text that lies: comments, a docstring and a bare string literal,
    each naming everything a substring check would look for. ast.parse
    cannot see a word of it — a text-based check would break on it."""
    return (
        "# pypdf PdfReader( pdfplumber._extract — the reader is "
        "from app.services.pdf_service import extract_text, normalize_ws\n"
        "# the floor is >= 100 and the phrase is truly about the role, "
        "with SWEDISH åäö ÅÄÖ characters\n"
        '"""pypdf PdfReader pdfplumber — from app.services.pdf_service '
        "import extract_text, normalize_ws — >= 100 truly about the role "
        'SWEDISH åäö ÅÄÖ"""\n'
        '"pypdf PdfReader extract_text( normalize_ws >= 100 truly SWEDISH"\n'
        + source
    )


def _rewritten(source: str, surgery) -> str:
    """AST surgery: parse, mutate, unparse. The violation is structural
    and no text is touched, so traps added on top can only lie about it."""
    tree = ast.parse(source)
    changed = surgery(tree)
    assert changed, "the surgery matched nothing — no violation was induced"
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _function(tree, name):
    homes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert homes, f"no function named {name} to operate on"
    return homes[0]


def _m_drop_helper_import(tree):
    """The suites' shared-helper import statement, gone."""
    dropped = 0
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if not isinstance(body, list):
            continue
        kept = [
            s
            for s in body
            if not (
                isinstance(s, ast.ImportFrom)
                and s.module == "app.services.pdf_service"
            )
        ]
        dropped += len(body) - len(kept)
        parent.body = kept
    return dropped


def _m_import_pypdf(statement):
    def surgery(tree):
        tree.body.append(ast.parse(statement).body[0])
        return 1

    return surgery


def _m_construct_reader_inside(function_name):
    def surgery(tree):
        # A PdfReader construction somewhere it must never appear.
        home = _function(tree, function_name)
        home.body.append(ast.parse("PdfReader(None)").body[0])
        return 1

    return surgery


def _m_define_function(source_text):
    def surgery(tree):
        tree.body.append(ast.parse(source_text).body[0])
        return 1

    return surgery


def _m_rename_function(old, new):
    def surgery(tree):
        renamed = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == old:
                node.name = new
                renamed += 1
        return renamed

    return surgery


def _m_rename_name_inside(function_name, old, new):
    def surgery(tree):
        renamed = 0
        for node in ast.walk(_function(tree, function_name)):
            if isinstance(node, ast.Name) and node.id == old:
                node.id = new
                renamed += 1
        return renamed

    return surgery


def _m_rename_name(old, new):
    def surgery(tree):
        renamed = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == old:
                node.id = new
                renamed += 1
        return renamed

    return surgery


def _m_retarget_int_constant(old, new):
    def surgery(tree):
        retargeted = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == old:
                node.value = new
                retargeted += 1
        return retargeted

    return surgery


def _m_replace_string_constant(*substrings):
    def surgery(tree):
        replaced = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and all(
                s in node.value for s in substrings
            ):
                node.value = "nothing a check could match"
                replaced += 1
        return replaced

    return surgery


# One structural violation per check AC-2 names, with the finding it must
# still produce while satisfying text sits right next to it.
_VIOLATIONS = (
    # the helper imports (both suites)
    ("test_pdf_fonts.py", _m_drop_helper_import, "import extract_text"),
    ("test_wo21_pdf_text_layer.py", _m_drop_helper_import, "import extract_text"),
    # no pypdf import
    ("test_pdf_fonts.py", _m_import_pypdf("import pypdf"), "imports pypdf"),
    (
        "test_wo21_pdf_text_layer.py",
        _m_import_pypdf("from pypdf import PdfReader"),
        "imports pypdf",
    ),
    # no PdfReader use in a suite
    (
        "test_pdf_fonts.py",
        _m_construct_reader_inside("_extract"),
        "uses PdfReader",
    ),
    (
        "test_wo21_pdf_text_layer.py",
        _m_construct_reader_inside("_graphics_only_pdf"),
        "uses PdfReader",
    ),
    # no local whitespace-collapsing function — either re-implementation
    (
        "test_pdf_fonts.py",
        _m_define_function(
            "def _collapse(text):\n    return re.sub(r'\\s+', ' ', text)"
        ),
        "whitespace-collapse",
    ),
    (
        "test_wo21_pdf_text_layer.py",
        _m_define_function("def _collapse(text):\n    return ' '.join(text.split())"),
        "whitespace-collapse",
    ),
    # the pdfplumber _extract present — and still the pdfplumber reader
    (
        "test_pdf_fonts.py",
        _m_rename_function("_extract", "_extract_texts"),
        "_extract",
    ),
    (
        "test_pdf_fonts.py",
        _m_rename_name_inside("_extract", "pdfplumber", "another_reader"),
        "pdfplumber",
    ),
    # the ATS-side survival checks over test_pdf_fonts.py
    (
        "test_pdf_fonts.py",
        _m_rename_name("extract_text", "the_service_reader"),
        "calls extract_text",
    ),
    ("test_pdf_fonts.py", _m_retarget_int_constant(100, 99), "floor"),
    (
        "test_pdf_fonts.py",
        _m_replace_string_constant("truly", "about the role"),
        "known-phrase",
    ),
    ("test_pdf_fonts.py", _m_replace_string_constant("åäö"), "Swedish"),
    # no PdfReader use or construction outside pdf_service.extract_text
    (
        "pdf_service",
        _m_construct_reader_inside("cover_letter_pdf"),
        "outside extract_text",
    ),
    (
        "pdf_service",
        _m_rename_name_inside("extract_text", "PdfReader", "AnotherReader"),
        "no longer uses PdfReader",
    ),
)


def test_ac_2(request, monkeypatch):
    """AC-2: J-1's tests check structure, not text.

    Four steps — the first against the live service, the rest against
    the real test_ac_3 through the one source seam it reads:

    1. every J-1 and WO-21 test still passes — run for real, against
       the service J-3 reshaped (on the pre-J-3 service this fails:
       J-1's message test pins the advice wording J-3 introduced);
    2. green on the real sources — every structural check still holds;
    3. still green when comments, a docstring and string literals name
       everything a substring check looks for (text cannot BREAK one);
    4. red on every structural violation AC-2 names, with that same
       satisfying text sitting next to it (text cannot SATISFY one).
    """
    from tests import test_j1_download_errors as j1
    from tests import test_wo21_pdf_text_layer as wo21

    # 1. all J-1 and WO-21 tests still pass, observed on the live service.
    for module in (j1, wo21):
        _run_suite(request, module)

    names = (*j1.PDF_SUITES, "pdf_service")
    sources = {name: j1._read_source(name) for name in names}

    # The stale comment and the unused scratch-database handling are gone
    # from both J-1's and WO-21's files (AC-2's last paragraph).
    for name in j1.PDF_SUITES:
        assert "Built by concatenation" not in sources[name], name
        assert "_SCRATCH_DB" not in sources[name], name

    # 2. the real sources pass the real test.
    j1.test_ac_3()

    # 3. ...and so do sources whose text lies about all of it.
    monkeypatch.setattr(j1, "_read_source", lambda name: _with_text_traps(sources[name]))
    j1.test_ac_3()

    # 4. every structural violation is still caught, satisfying text or not.
    for name, surgery, expected in _VIOLATIONS:
        doctored = dict(sources)
        doctored[name] = _with_text_traps(_rewritten(sources[name], surgery))
        monkeypatch.setattr(
            j1, "_read_source", lambda key, s=doctored: _with_text_traps(s[key])
        )
        with pytest.raises(AssertionError, match=re.escape(expected)):
            j1.test_ac_3()
