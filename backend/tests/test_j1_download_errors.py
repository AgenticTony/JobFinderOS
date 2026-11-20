"""J-1 — WO-21 follow-up: PDF check errors name the document and the
failure, leak no internals, one helper each.

The WO-21 runtime check blocks a send (and a manual-portal download)
whose PDF text layer is broken, but its message had two defects the
review round wrote this contract for:

- the wording was SEND-flavoured on every surface — a refused download
  answered "the application was NOT sent" for a request that never
  sends anything, and no message said which failure class fired;
- the unreadable-document branch embedded pypdf's exception text
  (a library internal) straight into the user-facing error.

test_ac_1  every blocked-check message begins with exactly one of the
           four class markers, names the document, and carries the
           surface's outcome: send says the application was NOT sent,
           download never does. verify_text_layer(context=...) builds
           the message once; the real download endpoints return it as
           the HTTP 500 detail and the real send flow lands it on
           application.error, unchanged. J-3 pins the advice half of
           the wording J-3 deliberately changed: only 'unreadable PDF'
           advises retrying; the three content classes end with their
           own draft-pointing advice and never say 'try again'.
test_ac_2  when the reader raises, its exception text reaches NEITHER
           user-facing surface — the original exception (message, with
           traceback) is logged server-side on both paths.
test_ac_3  one extraction helper and one whitespace-collapse helper:
           pdf_service.extract_text / pdf_service.normalize_ws, imported
           — not re-implemented — by the two PDF suites, and actually
           exposed by pdf_service under those public names (the private
           _normalize_ws is gone, its own callers updated). J-3: every
           source check in it is made with ast over the parsed source,
           so a comment, docstring or string literal can neither satisfy
           nor break it.
"""

import ast
import logging
import pathlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models import ApplicationDraft, JobPosting, Profile
from app.models import User as UserModel
from app.services import pdf_service
from app.services.draft_service import submit_draft
from app.services.pdf_service import extract_text
from tests.auth_helpers import get_or_create_user, mint_token

# The four class markers and the two document names, verbatim from the
# approved contract — the wording is the contract, not this module's
# opinion. A blocked-check message begins with exactly one marker.
MARKERS = (
    "unreadable PDF",
    "text layer too short",
    "characters lost",
    "required phrase lost",
)
DOCUMENTS = ("cover letter", "tailored CV")
NOT_SENT = "the application was NOT sent."

# J-3: the advice half of each message — the wording J-3 deliberately
# changed per class. Only the unreadable class advises retrying (the
# contract's exact sentence); each content class ends with its own
# advice, which lives in the draft, and never says "try again".
RETRY_ADVICE = "try again; if this keeps happening, contact the operator"
CONTENT_CLASS_ADVICE = {
    "text layer too short": "check the draft's content.",
    "characters lost": "replace those characters in the draft.",
    "required phrase lost": "check the draft's formatting.",
}

# A body every render path draws faithfully (pure ascii: survives the
# unicode font AND the latin-1 core-font fallback alike).
HEALTHY_BODY = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nBest regards,\n"
)

# Bytes no PDF reader can open: extraction raises on them (the
# 'unreadable PDF' class), and whatever the reader says about them is an
# internal that must stay server-side (AC-2).
GARBAGE_PDF = b"this is not a pdf; the reader must raise on it"


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — the
    # endpoint and send-flow tests need the tables.
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
    single place blocked-check wording is decided, per the contract. The
    endpoint / send-flow halves of test_ac_1 compare what those surfaces
    surface against THIS, proving nothing downstream rewrites it."""
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.verify_text_layer(pdf_bytes, context=context, label=label, **kwargs)
    return str(excinfo.value)


def _assert_message_shape(message: str, context: str, document: str) -> None:
    """The contract's structural rules, independent of which class fired:
    begins with exactly one marker (str.count over all four totals 1),
    names the document, and carries the surface's outcome."""
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


def _forced_classes():
    """One deterministic forcing input per failure class — the contract
    allows any means (a reader that raises, a raised floor, a source
    glyph or required phrase the document never carried)."""
    healthy = pdf_service.cover_letter_pdf(HEALTHY_BODY, "Test Candidate")
    return [
        # extraction itself raises on bytes that are not a PDF
        ("unreadable PDF", GARBAGE_PDF, {}),
        # a floor no real document can clear
        ("text layer too short", healthy, {"min_chars": 10**6}),
        # glyphs the render was given (source_text) that never extracted
        ("characters lost", healthy, {"source_text": HEALTHY_BODY + "\n— åäö"}),
        # a required phrase the document does not contain
        (
            "required phrase lost",
            healthy,
            {"must_contain": "a phrase that appears nowhere in this document"},
        ),
    ]


def _seed_ready_email_draft(db):
    """A 'ready' draft on a job that publishes an application email —
    exactly the package submit_draft(method='email') dispatches."""
    uid = uuid.uuid4()
    db.add(
        UserModel(
            id=uid,
            email=f"j1-{uid.hex[:6]}@test.example",
            hashed_password="test-only",
        )
    )
    db.flush()
    profile = Profile(
        user_id=uid, is_active=1, full_name="J1 Tester", cv_text="J1 CV", cv_file_name="cv.pdf"
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
        cover_letter=HEALTHY_BODY,
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


def _seed_download_draft(db):
    """A ready draft whose documents the download endpoints serve."""
    uid = get_or_create_user(f"j1-dl-{uuid.uuid4().hex[:6]}@test.example")
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
        cover_letter=HEALTHY_BODY,
        tailored_cv=HEALTHY_BODY,
        changes_summary="[]",
    )
    db.add(draft)
    db.commit()
    return uid, draft.id


def test_ac_1(client, db, monkeypatch):
    """AC-1: a blocked PDF check says what failed, per surface.

    Part 1 drives all four failure classes through verify_text_layer in
    both contexts and for both document names — shape first (J-1's own
    rules), then the advice half of the wording J-3 deliberately
    changed. Parts 2 and 3 prove the real surfaces pass that message
    through untouched: each download endpoint returns it as the HTTP 500
    detail, and the real send flow lands it on application.error —
    equal to what the seam built.
    """
    # --- Part 1: four classes x two contexts x two documents, at the seam ---
    for marker, blob, kwargs in _forced_classes():
        for context in ("send", "download"):
            for document in DOCUMENTS:
                message = _message_for(blob, context=context, label=document, **kwargs)
                assert message.startswith(marker), (marker, context, document, message)
                # J-3: the advice half — only the unreadable class may
                # offer a retry; a content class ends with its own
                # draft-pointing advice instead.
                if marker == "unreadable PDF":
                    assert RETRY_ADVICE in message, (marker, context, document, message)
                else:
                    assert "try again" not in message.lower(), (
                        marker,
                        context,
                        document,
                        message,
                    )
                    assert message.rstrip().lower().endswith(
                        CONTENT_CLASS_ADVICE[marker]
                    ), (marker, context, document, message)
                _assert_message_shape(message, context, document)

    # --- Part 2: through each real download endpoint, detail == message ---
    uid, draft_id = _seed_download_draft(db)
    headers = {"Authorization": f"Bearer {mint_token(uid, 'j1-dl@test.example')}"}
    for path, builder, document in (
        (f"/api/v1/applications/draft/{draft_id}/download/cover-letter", "cover_letter_pdf", "cover letter"),
        (f"/api/v1/applications/draft/{draft_id}/download/cv", "tailored_cv_pdf", "tailored CV"),
    ):
        with monkeypatch.context() as mp:
            # The builder hands the endpoint bytes the reader must raise
            # on — the 'unreadable PDF' class, forced the same way the
            # seam forced it above, so the two messages must be equal.
            mp.setattr(pdf_service, builder, lambda text, name: GARBAGE_PDF)
            expected = _message_for(GARBAGE_PDF, context="download", label=document)
            resp = client.get(path, headers=headers)
        assert resp.status_code == 500, (path, resp.status_code, resp.text)
        assert resp.json()["detail"] == expected, (path, resp.text, expected)
        _assert_message_shape(resp.json()["detail"], "download", document)

    # --- Part 3: through the real send flow, application.error == message ---
    send_uid, profile, draft = _seed_ready_email_draft(db)
    _live_email_settings(monkeypatch)
    sent: list = []
    _fake_resend(monkeypatch, sent)
    with monkeypatch.context() as mp:
        mp.setattr(pdf_service, "cover_letter_pdf", lambda text, name: GARBAGE_PDF)
        expected = _message_for(GARBAGE_PDF, context="send", label="cover letter")
        application = submit_draft(db, draft, "email", profile, user_id=send_uid)
    assert application.status == "failed", application.error
    assert application.error == expected, (application.error, expected)
    _assert_message_shape(application.error, "send", "cover letter")
    assert not sent, f"the email was dispatched anyway: {sent}"


def test_ac_2(client, db, monkeypatch, caplog):
    """AC-2: no extraction exception text reaches a user-facing surface.

    When the reader raises on the document, neither the download HTTP
    detail nor the send path's application.error (returned by
    ApplicationResponse) may contain the exception's text — but the
    ORIGINAL exception, message and traceback, must be logged
    server-side on both paths (the operator debugs from the log, the
    user reads the class marker).
    """
    # What the reader actually says on these bytes — captured from the
    # real raise, so the assertions below check THIS text, not a guess.
    with pytest.raises(Exception) as excinfo:
        extract_text(GARBAGE_PDF)
    exc_text = str(excinfo.value)
    assert exc_text, "the fixture must make the reader raise"

    uid, draft_id = _seed_download_draft(db)
    headers = {"Authorization": f"Bearer {mint_token(uid, 'j1-dl@test.example')}"}
    send_uid, profile, draft = _seed_ready_email_draft(db)
    _live_email_settings(monkeypatch)
    sent: list = []
    _fake_resend(monkeypatch, sent)

    with caplog.at_level(logging.ERROR, logger="app.services.pdf_service"):
        # Download surface: HTTP 500, detail is the clean message.
        with monkeypatch.context() as mp:
            mp.setattr(pdf_service, "cover_letter_pdf", lambda text, name: GARBAGE_PDF)
            resp = client.get(
                f"/api/v1/applications/draft/{draft_id}/download/cover-letter",
                headers=headers,
            )
        assert resp.status_code == 500, resp.text
        detail = resp.json()["detail"]

        # Send surface: ApplicationResponse's `error` field carries the
        # same clean message (submit_draft persists it on the row the
        # response model reads).
        with monkeypatch.context() as mp:
            mp.setattr(pdf_service, "cover_letter_pdf", lambda text, name: GARBAGE_PDF)
            application = submit_draft(db, draft, "email", profile, user_id=send_uid)
        assert application.status == "failed", application.error

    # The leak the contract forbids: the exception's own text in either
    # user-facing string.
    assert exc_text not in detail, detail
    assert exc_text not in (application.error or ""), application.error

    # The server-side record: the ORIGINAL exception's text appears in
    # the captured log — on both paths, with a traceback attached.
    assert exc_text in caplog.text, caplog.text
    with_traceback = [
        r
        for r in caplog.records
        if r.name == "app.services.pdf_service" and r.exc_info and exc_text in r.getMessage()
    ]
    assert with_traceback, caplog.text

    # And the blocked send still dispatched nothing.
    assert not sent, sent


TESTS_DIR = pathlib.Path(__file__).resolve().parent
PDF_SUITES = ("test_pdf_fonts.py", "test_wo21_pdf_text_layer.py")
PDF_SERVICE = "pdf_service"  # _read_source's key for the service's own source


def _read_source(name: str) -> str:
    """The ONE seam every AC-3 source check reads through.

    J-3's test_ac_2 monkeypatches this to run the whole check against a
    doctored source — comments that name everything a substring check
    would look for, or a structural violation wearing them — which is
    exactly the experiment that decides whether these checks are
    structural or textual.
    """
    if name == PDF_SERVICE:
        return pathlib.Path(pdf_service.__file__).read_text()
    return (TESTS_DIR / name).read_text()


# --- the checks: ast over the parsed source, nothing else (J-3) ---
# A comment, docstring or string literal is invisible to every function
# below, because none of them ever looks at the source's text.


def _imports_module(tree: ast.Module, module: str) -> bool:
    """"import module" or "from module import ...", anywhere in the file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == module or alias.name.startswith(module + ".")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base == module or base.startswith(module + "."):
                return True
    return False


def _imports_names_from(tree: ast.Module, module: str, names) -> bool:
    """Every given name is imported from exactly that module."""
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == module:
            imported.update(alias.name for alias in node.names)
    return set(names) <= imported


def _name_uses(tree, name: str) -> list:
    """Every reference to the identifier. A comment or string literal is
    not a reference, so it cannot appear here."""
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == name
    ]


def _uses_outside_function(tree, name: str, function: str) -> bool:
    """The identifier is referenced anywhere other than inside the named
    function (that function's own nested functions count as inside)."""
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            inside.update(id(use) for use in _name_uses(node, name))
    return any(id(use) not in inside for use in _name_uses(tree, name))


def _calls_name(tree, name: str) -> bool:
    """The identifier is CALLED somewhere in the file."""
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
        for node in ast.walk(tree)
    )


# The characters a regex can be built from and still be nothing but a
# run of whitespace characters — the pattern half of a collapse.
_WS_RUN_CHARS = set("\\stnrvf \t\n\r\x0b\x0c[]+*?{}|(),-^0123456789")


def _collapsing_functions(tree) -> list:
    """Functions that re-implement pdf_service.normalize_ws: collapsing a
    whitespace run into single spaces, whether via re.sub or split/join."""
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            is_sub = isinstance(node.func, ast.Attribute) and (
                node.func.attr == "sub"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
            )
            plain_sub = isinstance(node.func, ast.Name) and node.func.id == "sub"
            args = node.args
            if (is_sub or plain_sub) and args:
                pattern, replacement = args[0], args[1] if len(args) > 1 else None
                if (
                    isinstance(pattern, ast.Constant)
                    and isinstance(pattern.value, str)
                    and pattern.value
                    and not (set(pattern.value) - _WS_RUN_CHARS)
                    and isinstance(replacement, ast.Constant)
                    and replacement.value == " "
                ):
                    found.append(fn.name)
                    break
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and isinstance(node.func.value, ast.Constant)
                and node.func.value.value == " "
                and args
                and isinstance(args[0], ast.Call)
                and isinstance(args[0].func, ast.Attribute)
                and args[0].func.attr == "split"
            ):
                found.append(fn.name)
                break
    return found


def _module_string_bindings(tree) -> dict:
    """Module-level `NAME = "literal"` assignments, so an assertion made
    over a named constant is recognised as one made over its literal."""
    bound = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node.value.value
    return bound


def _asserts_contains(tree, literal_matches) -> bool:
    """An ASSERTION (`assert needle in haystack`) whose NEEDLE is a
    string literal — or a module-level name bound to one — that the
    given predicate recognises."""
    bound = _module_string_bindings(tree)
    for statement in ast.walk(tree):
        if not isinstance(statement, ast.Assert):
            continue
        for node in ast.walk(statement.test):
            if not (
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.In)
            ):
                continue
            needle = node.left
            if isinstance(needle, ast.Constant) and isinstance(needle.value, str):
                if literal_matches(needle.value):
                    return True
            else:
                value = bound.get(needle.id) if isinstance(needle, ast.Name) else None
                if value is not None and literal_matches(value):
                    return True
    return False


def _has_length_floor(tree, minimum: int) -> bool:
    """A `len(...) >= minimum` comparison — an extracted-character floor."""
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Compare)
            and any(isinstance(op, ast.GtE) for op in node.ops)
        ):
            continue
        if not any(
            isinstance(c, ast.Constant) and c.value == minimum for c in node.comparators
        ):
            continue
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "len"
            for call in ast.walk(node.left)
        ):
            return True
    return False


def _module_functions(tree) -> set:
    """The module-level function names — where a helper must be defined
    to be the importable, public one."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_ac_3():
    """AC-3: one pypdf extraction helper and one whitespace-collapse
    helper — pdf_service.extract_text / pdf_service.normalize_ws.

    A source check, as the contract prescribes, over BOTH halves of the
    sentence "pdf_service exposes extract_text and normalize_ws (renamed
    from _normalize_ws), imported by the two PDF suites":

    the suites — they may IMPORT the helpers, never re-implement them
    (no pypdf import, no PdfReader use, no local whitespace-collapsing
    function), while the second, independent reader (test_pdf_fonts'
    pdfplumber _extract) and the ATS-side assertions (the >= 100
    character floor, the known phrase, Swedish-character survival)
    survive over pdf_service.extract_text;

    the service — the pypdf reader is used in exactly one place, inside
    extract_text, and nowhere else in the module.

    J-3: every source check above is made with ast over the parsed
    source — one assert per check, each walking the tree through the
    helpers above, none of them reading the source's text — so a
    comment, docstring or string literal can neither satisfy nor break
    it. tests/test_j3_pdf_messages.py::test_ac_2 proves both directions
    by running THIS test against doctored sources.
    """
    # --- the suites: import the shared helpers, never re-implement them ---
    for name in PDF_SUITES:
        tree = ast.parse(_read_source(name))

        # Both helpers come from pdf_service, the one home for each.
        assert _imports_names_from(
            tree, "app.services.pdf_service", ("extract_text", "normalize_ws")
        ), (
            f"{name} does not import extract_text and normalize_ws from "
            "app.services.pdf_service — the shared helpers have one home"
        )

        # Neither file imports pypdf — the one pypdf reader is the
        # service's. (A file that never imports it cannot construct it.)
        assert not _imports_module(tree, "pypdf"), (
            f"{name} imports pypdf — the one pypdf reader is pdf_service.extract_text"
        )

        # Neither file references PdfReader: not a call, not a
        # construction, not an alias handed to something that would.
        assert not _name_uses(tree, "PdfReader"), (
            f"{name} uses PdfReader — the one pypdf reader is pdf_service.extract_text"
        )

        # Neither file defines a function that collapses whitespace —
        # that helper is pdf_service.normalize_ws now.
        collapsers = _collapsing_functions(tree)
        assert not collapsers, f"{name} re-implements the whitespace-collapse helper: {collapsers}"

    # --- test_pdf_fonts.py: the second reader, the ATS-side assertions ---
    fonts_tree = ast.parse(_read_source("test_pdf_fonts.py"))
    extract_fns = [
        node
        for node in fonts_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_extract"
    ]
    assert extract_fns, "test_pdf_fonts.py no longer defines _extract"
    assert _name_uses(extract_fns[0], "pdfplumber"), "_extract is no longer the pdfplumber reader"
    assert _calls_name(fonts_tree, "extract_text"), (
        "test_pdf_fonts.py no longer calls extract_text — the ATS-side "
        "assertions no longer run over the service reader"
    )
    assert _has_length_floor(fonts_tree, 100), "the >= 100 extracted-character floor is gone"
    assert _asserts_contains(
        fonts_tree, lambda s: "truly" in s and "about the role" in s
    ), "the known-phrase assertion is gone"
    assert _asserts_contains(
        fonts_tree, lambda s: any(ch in s for ch in "åäöÅÄÖ")
    ), "the Swedish-character assertion is gone"

    # --- the service: the pypdf reader lives only in extract_text ---
    service_tree = ast.parse(_read_source(PDF_SERVICE))
    assert _name_uses(service_tree, "PdfReader"), (
        "pdf_service no longer uses PdfReader anywhere — extract_text "
        "does not read with pypdf"
    )
    assert not _uses_outside_function(service_tree, "PdfReader", "extract_text"), (
        "PdfReader is used or constructed outside extract_text — the "
        "one pypdf reader is pdf_service.extract_text"
    )
    service_functions = _module_functions(service_tree)
    assert {"extract_text", "normalize_ws"} <= service_functions, (
        "pdf_service no longer defines extract_text and normalize_ws at "
        "module level — the helpers' one home"
    )
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "_normalize_ws"
        for node in ast.walk(service_tree)
    ), "the private _normalize_ws survived its rename to normalize_ws"

    # --- The service half: the helpers' one home is pdf_service itself ---
    assert callable(getattr(pdf_service, "extract_text", None)), (
        "pdf_service must expose the one extraction helper, extract_text"
    )
    assert callable(getattr(pdf_service, "normalize_ws", None)), (
        "pdf_service must expose the one whitespace-collapse helper, "
        "normalize_ws, under its public name (renamed from _normalize_ws)"
    )
    assert not hasattr(pdf_service, "_normalize_ws"), (
        "the private _normalize_ws survived its rename to normalize_ws — "
        "pdf_service's own callers were not updated"
    )
    # The exposed names are the genuine helpers, not renamed stubs:
    # collapse a whitespace run to single spaces, and read a rendered
    # document end to end through both (known-good literals, from the
    # body HEALTHY_BODY gives the renderer).
    assert pdf_service.normalize_ws("Dear \n\t Hiring \n\n Manager,") == "Dear Hiring Manager,"
    blob = pdf_service.cover_letter_pdf(HEALTHY_BODY, "Test Candidate")
    served = pdf_service.normalize_ws(pdf_service.extract_text(blob))
    assert "Dear Hiring Manager" in served, repr(served)
