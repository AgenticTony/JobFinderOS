"""Scraper registry — add new sources here."""

from typing import Dict, Type

from app.services.scrapers.adzuna import AdzunaScraper
from app.services.scrapers.arbeitnow import ArbeitnowScraper
from app.services.scrapers.base import BaseScraper, NormalizedJob
from app.services.scrapers.careerjet import CareerjetScraper
from app.services.scrapers.jobicy import JobicyScraper
from app.services.scrapers.jobtech import JobtechScraper
from app.services.scrapers.reed import ReedScraper
from app.services.scrapers.remotive import RemotiveScraper
from app.services.scrapers.workingnomads import WorkingNomadsScraper

SCRAPER_REGISTRY: Dict[str, Type[BaseScraper]] = {
    "arbeitnow": ArbeitnowScraper,
    "remotive": RemotiveScraper,
    "jobicy": JobicyScraper,
    "workingnomads": WorkingNomadsScraper,
    "jobtech": JobtechScraper,
    "reed": ReedScraper,
    "adzuna": AdzunaScraper,
    "careerjet": CareerjetScraper,
}

__all__ = [
    "BaseScraper",
    "NormalizedJob",
    "SCRAPER_REGISTRY",
    "ArbeitnowScraper",
    "RemotiveScraper",
    "JobicyScraper",
    "WorkingNomadsScraper",
    "JobtechScraper",
    "TeamtailorScraper",
    "ReedScraper",
    "AdzunaScraper",
    "CareerjetScraper",
]
