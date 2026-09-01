"""Feedback schemas — the one-box beta feedback form."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# The page's chips. Keep in sync with the frontend FeedbackView list.
FeedbackCategory = Literal["bug", "confusing", "missing", "idea", "love_it"]


class FeedbackCreate(BaseModel):
    category: FeedbackCategory
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        # min_length counts whitespace; a spaces-only box is no feedback
        if not v.strip():
            raise ValueError("message must not be blank")
        return v


class FeedbackAck(BaseModel):
    ok: bool = True
    stored: bool = True
    # Whether the owner email notification went out — the row is the
    # source of truth either way; False just means Resend wasn't wired.
    notified: bool = False
