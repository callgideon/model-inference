"""T2F: the feedback projection from D6F's outbox (`projector`), and its PostgreSQL relay
(`pg`, imported on use: it needs psycopg)."""
from .projector import (TABLE, ClickHouseFeedbackProjection, Event, FeedbackProjector)

__all__ = ["TABLE", "ClickHouseFeedbackProjection", "Event", "FeedbackProjector"]
