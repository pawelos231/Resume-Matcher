"""Provider scrapers for external job boards."""

from app.services.search.providers.bulldogjob import scrape_bulldogjob
from app.services.search.providers.careerbuilder import scrape_careerbuilder
from app.services.search.providers.glassdoor import scrape_glassdoor
from app.services.search.providers.indeed import scrape_indeed
from app.services.search.providers.justjoinit import scrape_justjoinit
from app.services.search.providers.nofluffjobs import scrape_nofluffjobs
from app.services.search.providers.olxpraca import scrape_olxpraca
from app.services.search.providers.pracujpl import scrape_pracujpl
from app.services.search.providers.rocketjobs import scrape_rocketjobs
from app.services.search.providers.solidjobs import scrape_solidjobs
from app.services.search.providers.theprotocol import scrape_theprotocol
from app.services.search.providers.ziprecruiter import scrape_ziprecruiter

__all__ = [
    "scrape_nofluffjobs",
    "scrape_justjoinit",
    "scrape_bulldogjob",
    "scrape_theprotocol",
    "scrape_solidjobs",
    "scrape_pracujpl",
    "scrape_rocketjobs",
    "scrape_olxpraca",
    "scrape_indeed",
    "scrape_glassdoor",
    "scrape_ziprecruiter",
    "scrape_careerbuilder",
]
