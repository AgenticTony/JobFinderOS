"""J-5 — J-3 follow-up: the 'characters lost' message tells the user
WHERE to fix each lost character.

J-3's message named every lost character and said "Please replace those
characters in the draft." — but the render draws the applicant's PROFILE
NAME as the document subtitle, so a character the fontless fallback
destroys in the name is not in the draft at all: the user was sent
hunting through a document that does not contain the character they were
told to replace.

test_ac_1 holds the message verify_text_layer builds (the one home of
blocked-check wording, per J-1) to the J-5 rules, on BOTH surfaces:

- a lost character that comes only from the applicant's name and is not
  present in the draft text belongs to the NAME group; every other lost
  character belongs to the DRAFT group;
- when only the draft group is non-empty the message is EXACTLY J-3's
  (asserted as whole-string literals, not by rebuilding it);
- when the name group is non-empty the characters are listed by group,
  name group first — 'from your profile name: ' then its characters,
  '; from the draft: ' then the draft's — each group capped at six
  characters with its own ', and N more' overflow, and the message ends
  with the advice for each group present, name group first;
- J-3's shape survives: one class marker first, the document name, 'the
  application was NOT sent.' on send and never 'NOT sent' on download,
  and never 'try again'.

The wiring is proved too: the verified builders (the entry points the
send path and both download endpoints call) pass the applicant name
through, so a genuinely fontless render of a name outside latin-1 is
reported against the profile name — observed on a REAL degraded render
(the font resolver forced to None, the same fixture test_pdf_fonts and
test_wo21 use), not on a hand-built message.

Every expected string below is a literal from the approved contract (or
an assembled J-1/J-3 literal), never a recomputation of the service's
wording.
"""

import pytest

from app.services import pdf_service

# J-1's markers/shape and J-3's literals this contract builds on.
MARKERS = (
    "unreadable PDF",
    "text layer too short",
    "characters lost",
    "required phrase lost",
)
NOT_SENT = "the application was NOT sent."
DOCUMENT = "cover letter"

# A body every render path draws faithfully (pure ascii) — the healthy
# document every fixture below starts from. Its characters never appear
# in a lost-character list.
HEALTHY_BODY = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nBest regards,\n"
)

# A profile name whose first character sits OUTSIDE latin-1 (U+0141) —
# exactly the character the fontless core-font fallback substitutes with
# '?'. The draft body above does not contain it, so a render that loses
# it lost it from the NAME.
NAME_LOST = "Łukasz Nowak"

# Two visible non-ascii characters (em-dash, a-ring) that only appear in
# the draft text — the DRAFT group.
DRAFT_LOST = HEALTHY_BODY + "\n— å"

# A profile name carrying EXACTLY eight distinct characters outside
# latin-1 (Č U+010C, Ď U+010E, Ľ U+013D, Ň U+0147, Ő U+0150, š U+0161,
# Ť U+0164, Ž U+017D) — two more than the six a group may name, so the
# name group alone must finish with its own overflow count.
NAME_EIGHT = "Ľuboš Čekor Ňora Ďula Ťava Őrs Žeder"

# Eight distinct visible non-ascii characters in the draft text — the
# draft group's own overflow.
DRAFT_EIGHT = HEALTHY_BODY + "\n— å ä ö ç ñ ü ï"

# The grouped fragments, verbatim from the approved contract.
FROM_NAME = "from your profile name: "
FROM_DRAFT = "; from the draft: "
# J-7 replaced J-5's name advice ("Please check your name in your
# profile.") — with fallback fonts installed, a character lost from the
# NAME is a gap in the operator's fonts, not a name the user can fix.
NAME_ADVICE = (
    "Your name contains characters this PDF font cannot show. "
    "Please contact the operator."
)
DRAFT_ADVICE = "Please replace those characters in the draft."

# The whole messages, verbatim: J-3's wording where only the draft group
# is non-empty, and the grouped form the contract spells out clause by
# clause where the name group is not.
# (code-point order, as J-3's single list has always been sorted: å
# U+00E5 before — U+2014.)
DRAFT_ONLY = {
    "send": (
        "characters lost — cover letter: its text layer lost these "
        "characters during rendering: å (U+00E5), — (U+2014). "
        "the application was NOT sent. "
        "Please replace those characters in the draft."
    ),
    "download": (
        "characters lost — cover letter: its text layer lost these "
        "characters during rendering: å (U+00E5), — (U+2014). "
        "the download was stopped. "
        "Please replace those characters in the draft."
    ),
}
NAME_ONLY = {
    "send": (
        "characters lost — cover letter: its text layer lost these "
        "characters during rendering: from your profile name: Ł (U+0141). "
        "the application was NOT sent. "
        "Your name contains characters this PDF font cannot show. "
        "Please contact the operator."
    ),
    "download": (
        "characters lost — cover letter: its text layer lost these "
        "characters during rendering: from your profile name: Ł (U+0141). "
        "the download was stopped. "
        "Your name contains characters this PDF font cannot show. "
        "Please contact the operator."
    ),
}
BOTH_GROUPS = {
    "send": (
        "characters lost — cover letter: its text layer lost these "
        "characters during rendering: from your profile name: Ł (U+0141); "
        "from the draft: å (U+00E5), — (U+2014). "
        "the application was NOT sent. "
        "Your name contains characters this PDF font cannot show. "
        "Please contact the operator. "
        "Please replace those characters in the draft."
    ),
    "download": (
        "characters lost — cover letter: its text layer lost these "
        "characters during rendering: from your profile name: Ł (U+0141); "
        "from the draft: å (U+00E5), — (U+2014). "
        "the download was stopped. "
        "Your name contains characters this PDF font cannot show. "
        "Please contact the operator. "
        "Please replace those characters in the draft."
    ),
}


def _blocked(pdf_bytes, context, **kwargs) -> str:
    """The message verify_text_layer itself builds for this input — the
    single place blocked-check wording is decided (J-1), observed here on
    one surface at a time."""
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.verify_text_layer(
            pdf_bytes, context=context, label=DOCUMENT, **kwargs
        )
    return str(excinfo.value)


def _assert_shape(message, context) -> None:
    """J-3's shape, which J-5 keeps: one class marker first (and exactly
    one anywhere), the document named, the surface's outcome, and never
    'try again' — a retry cannot restore what the render lost."""
    assert message.startswith("characters lost"), (context, message)
    assert sum(message.count(m) for m in MARKERS) == 1, message
    assert DOCUMENT in message, message
    if context == "send":
        assert NOT_SENT in message, message
    else:
        assert "NOT sent" not in message, message
    assert "try again" not in message, (context, message)


def test_ac_1():
    """AC-1: the 'characters lost' message points each lost character at
    the place the user can fix it — the profile name or the draft — per
    group, on both surfaces, keeping J-3's shape."""
    healthy = pdf_service.cover_letter_pdf(HEALTHY_BODY, "Test Candidate")

    # --- only the draft group is non-empty: the message is EXACTLY
    # J-3's (no name given, and no name character lost) ---
    for context in ("send", "download"):
        message = _blocked(healthy, context, source_text=DRAFT_LOST)
        assert message == DRAFT_ONLY[context], (context, message)
        _assert_shape(message, context)
        assert "profile name" not in message, (context, message)

    # --- only the name group is non-empty: the characters are listed
    # under 'from your profile name: ' and the advice is the name's own
    # (the draft is clean — nothing to replace there) ---
    for context in ("send", "download"):
        message = _blocked(
            healthy, context, source_text=HEALTHY_BODY, applicant_name=NAME_LOST
        )
        assert message == NAME_ONLY[context], (context, message)
        _assert_shape(message, context)
        assert "from the draft:" not in message, (context, message)
        assert "replace those characters" not in message, (context, message)

    # The name alone is a source the render was given: with no draft text
    # supplied, its lost characters still report against the profile.
    message = _blocked(healthy, "send", applicant_name=NAME_LOST)
    assert message == NAME_ONLY["send"], message

    # --- both groups non-empty: name group first, then the draft group,
    # and the advice for each group present, name group first ---
    for context in ("send", "download"):
        message = _blocked(
            healthy, context, source_text=DRAFT_LOST, applicant_name=NAME_LOST
        )
        assert message == BOTH_GROUPS[context], (context, message)
        _assert_shape(message, context)
        assert message.index(FROM_NAME) < message.index("from the draft:"), (
            context,
            message,
        )

    # --- a lost character the draft ALSO contains belongs to the draft
    # group even though the name carries it: the contract's name group is
    # 'lost only from the applicant's name and not present in the draft
    # text'. Only the draft group is non-empty, so the message is J-3's
    # single-list form with the draft advice ---
    message = _blocked(
        healthy,
        "send",
        source_text=HEALTHY_BODY + "\nŁ",
        applicant_name=NAME_LOST,
    )
    assert "from your profile name:" not in message, message
    assert "rendering: Ł (U+0141)." in message, message
    assert NAME_ADVICE not in message, message
    assert DRAFT_ADVICE in message, message

    # --- each group caps at six characters and counts its own overflow ---
    for context in ("send", "download"):
        # eight lost from the name alone
        message = _blocked(
            healthy, context, source_text=HEALTHY_BODY, applicant_name=NAME_EIGHT
        )
        _assert_shape(message, context)
        assert message.count(" (U+") == 6, (context, message)
        assert ", and 2 more" in message, (context, message)
        assert "from the draft:" not in message, (context, message)
        assert NAME_ADVICE in message, (context, message)

        # eight from the draft alone keeps J-3's single-list form
        message = _blocked(healthy, context, source_text=DRAFT_EIGHT)
        _assert_shape(message, context)
        assert message.count(" (U+") == 6, (context, message)
        assert ", and 2 more" in message, (context, message)
        assert "profile name" not in message, (context, message)

        # and both groups overflowing: twelve named, one overflow count
        # per group
        message = _blocked(
            healthy, context, source_text=DRAFT_EIGHT, applicant_name=NAME_EIGHT
        )
        _assert_shape(message, context)
        assert message.count(" (U+") == 12, (context, message)
        assert message.count(", and 2 more") == 2, (context, message)


def test_ac_1_wiring(monkeypatch):
    """The production entry points group by name on a REAL degraded
    render: with no unicode font (the fontless slim image's state — the
    same fixture test_pdf_fonts and test_wo21 use), the fallback
    substitutes a name character outside latin-1, and the verified
    builder's message points at the profile name instead of sending the
    user into a draft that does not contain the character."""
    monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: None)
    assert pdf_service._UnicodePDF().unicode_ok is False

    with pytest.raises(pdf_service.PdfTextLayerError) as name_only:
        pdf_service.verified_cover_letter_pdf(
            HEALTHY_BODY, NAME_LOST, context="download"
        )
    message = str(name_only.value)
    assert FROM_NAME + "Ł (U+0141)" in message, message
    assert "from the draft:" not in message, message
    assert NAME_ADVICE in message, message
    assert "NOT sent" not in message, message  # the download surface

    # The same render with typography the draft itself carries: both
    # groups reported, name group first, on the send surface.
    with pytest.raises(pdf_service.PdfTextLayerError) as both:
        pdf_service.verified_tailored_cv_pdf(
            HEALTHY_BODY + "\nI am — truly — excited about this role.",
            NAME_LOST,
            context="send",
        )
    message = str(both.value)
    assert FROM_NAME + "Ł (U+0141); from the draft: — (U+2014)." in message, message
    assert NOT_SENT in message, message
    assert NAME_ADVICE + " " + DRAFT_ADVICE in message, message
