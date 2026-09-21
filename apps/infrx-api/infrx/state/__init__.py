"""Durable state: the PostgreSQL adapters of the JobStore, StreamStore,
FeedbackService and JudgeCoordinator ports.

D1 ships only what the schema needs: `migrations` (where the SQL lives and how to
apply it to a task-local database) plus the two test fixtures beside it. The
adapters themselves are D2-D6.
"""
