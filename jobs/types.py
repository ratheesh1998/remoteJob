from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class JobData:
    """A normalized job as produced by a source adapter (JSON-serializable for Celery/DB)."""
    title: str
    company: str
    url: str
    source: str
    region: str = ""
    description: str = ""
    salary: str = ""
    posted_at: datetime | None = None
    application_email: str | None = None
    tags: list[str] = field(default_factory=list)
    match_score: int = 0
    dedupe_key: str | None = None   # set for LinkedIn posts: email + post URL instead of company + title

    def to_dict(self) -> dict:
        d = asdict(self)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JobData":
        d = dict(d)
        if d.get("posted_at"):
            d["posted_at"] = datetime.fromisoformat(d["posted_at"])
        return cls(**d)
