"""Provider scrapers for external job boards."""

from app.services.search.providers.bulldogjob import scrape_bulldogjob
from app.services.search.providers.justjoinit import scrape_justjoinit
from app.services.search.providers.nofluffjobs import scrape_nofluffjobs
from app.services.search.providers.solidjobs import scrape_solidjobs
from app.services.search.providers.theprotocol import scrape_theprotocol

__all__ = [
    "scrape_nofluffjobs",
    "scrape_justjoinit",
    "scrape_bulldogjob",
    "scrape_theprotocol",
    "scrape_solidjobs",
]

