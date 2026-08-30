"""Scrape watermark — when a (source, query, scope) was last fetched.

Delta scrapes key off this: fetch only ads PUBLISHED after the
watermark (minus an overlap), so each hunt pulls exactly the new
arrivals instead of re-reading the same result window. The pair key
means a brand-new query or a brand-new municipality set has no
watermark and automatically gets a deep backfill.
"""

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.core.orm import Base
from app.core.timeutil import utc_now


class ScrapeWatermark(Base):
    __tablename__ = "scrape_watermarks"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=False, index=True)
    query = Column(String(500), nullable=False, default="")  # "" = query-less source
    scope = Column(String(500), nullable=False, default="")  # sorted municipalities, "" = unscoped
    watermark_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "query", "scope", name="uq_scrape_watermark"),
    )
