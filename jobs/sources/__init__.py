from .adzuna import AdzunaSource
from .base import BaseSource, SearchQuery
from .himalayas import HimalayasSource
from .jsearch import JSearchSource
from .linkedin import LinkedInSource
from .linkedin_posts import LinkedInPostsViaSearch
from .remoteok import RemoteOKSource
from .remotive import RemotiveSource
from .weworkremotely import WeWorkRemotelySource

ALL_SOURCES: dict[str, BaseSource] = {
    s.name: s
    for s in (RemotiveSource(), RemoteOKSource(), HimalayasSource(), WeWorkRemotelySource(),
              LinkedInSource(), LinkedInPostsViaSearch(), AdzunaSource(), JSearchSource())
}


def enabled_sources() -> list[BaseSource]:
    return [s for s in ALL_SOURCES.values() if s.is_enabled()]


def get_source(name: str) -> BaseSource:
    return ALL_SOURCES[name]


__all__ = ["ALL_SOURCES", "BaseSource", "SearchQuery", "enabled_sources", "get_source"]
