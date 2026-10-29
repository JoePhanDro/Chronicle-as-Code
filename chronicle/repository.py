"""The one place the rest of the application imports its data operations from (issue #124).

The work is done in smaller modules, each with one subject:

- `queries`: reads on the index
- `index`: building the index from the topic files (import, reindex)
- `folder_writes`: what every change of the data folder shares (lock, rollback, link lines)
- `events`, `topics`, `evidence_store`: the changes themselves

Importing the names from here keeps callers short and lets the modules be rearranged. Nothing is defined in this file.
"""

from __future__ import annotations

# ruff: noqa: F401
from .errors import Conflict, Invalid, NotFound, RepositoryError
from .evidence_store import add_evidence, delete_evidence
from .events import add_comment, create_event, delete_event, update_event
from .index import ImportReport, build_index, import_timeline, import_timelines, reindex
from .queries import (
    EventFilter,
    count_events,
    find_events,
    get_event,
    get_event_links,
    get_evidence_content,
    get_topic,
    list_all_topic_keys,
    list_events,
    list_topic_events,
    list_topics,
    most_pressing_status,
    search_actors,
    search_systems,
)
from .topics import create_topic, delete_topic, link_event, topic_delete_plan, unlink_event, update_topic
