"""Feedback model — beta testers' one-box feedback.

Owner decision 2026-09-01: the console gets a "Beta feedback" page.
No fields to fill — one box plus category chips — but each row IS
linked to the submitting account (disclosed on the page) so the owner
can follow up: "scores are wrong" is only actionable when you can look
at that user's matches.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Uuid

from app.core.orm import Base
from app.core.timeutil import utc_now


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    # Disclosed account link — see module docstring. Nullable never in
    # practice (the endpoint requires auth), kept strict (non-null FK).
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    # One of the page's category chips: bug / confusing / missing /
    # idea / love_it — free text rejected by the schema Literal.
    category = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)  # 1..1000 chars, schema-enforced
    created_at = Column(DateTime, default=utc_now, nullable=False)

    def __repr__(self) -> str:
        return f"<Feedback {self.category} user={self.user_id}>"
