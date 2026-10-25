"""Reading YAML that does not come from the app itself.

Files from the data folder, from Git and from a remote are foreign content. Before PyYAML builds objects from them,
the event stream is scanned and anything the schema never uses is refused: deep nesting (the C loader crashes with
SIGSEGV on about 120 KB of `[`), anchors and aliases (an alias bomb expands to millions of nodes), tags, the merge key,
duplicate keys (PyYAML silently keeps the last one), keys that are not text, huge files and many documents.

The scan and the load use the same loader, so both see the same events. Messages name the place and the rule, never
the content of the file.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

import yaml
from pydantic import ValidationError
from yaml.resolver import Resolver

# The C loader (libyaml) reads the same safe subset about eight times faster than the pure-Python one (issue #96).
# It only ever sees input the scan has passed: its crash is in the recursion of the composer, which depth 12 rules out.
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# One topic is one file (ADR 0005), and a long incident has thousands of events: 1,200 events of 15 to 17 nodes and
# 250 bytes are 300 KB and 18,000 to 20,000 nodes. The limits bound the memory and the scan (about 40 ms per 20,000
# nodes), not the time of a change: writers other than update_event_in_file load the whole file with ruamel, which
# takes seconds near the limits.
MAX_YAML_BYTES = 4 * 1024 * 1024
MAX_NODES = 100_000  # scalars, mappings and sequences together
MAX_DEPTH = 12  # a topic file needs 6 levels
MAX_CACHED_KEY = 64  # longer keys are resolved again, so that a file of long keys cannot fill the cache
MAX_MESSAGE_CHARS = 200
MAX_LISTED_ERRORS = 3
TOO_LARGE = f"Datei größer als {MAX_YAML_BYTES // 2**20} MiB (Grenze für eine Topic-Datei)"

_STR_TAG = "tag:yaml.org,2002:str"
_RESOLVER = Resolver()


class YamlRejected(Exception):
    """The text is no YAML this app reads. The message names the rule and the place, not the content."""


def load_yaml(text: str):
    """The data of a foreign YAML text. Raises YamlRejected if the scan refuses it or it cannot be read."""
    try:
        scan(text)
        return yaml.load(text, Loader=YAML_LOADER)
    except yaml.YAMLError as exc:
        raise YamlRejected(describe_yaml_error(exc)) from exc
    except ValueError as exc:  # e.g. a date that does not exist, 2026-02-31
        raise YamlRejected("ungültiger Wert (zum Beispiel ein Datum, das es nicht gibt)") from exc


def scan(text: str) -> None:
    """Raises YamlRejected if the text uses anything the schema does not need. Builds no objects."""
    if len(text.encode("utf-8", "replace")) > MAX_YAML_BYTES:
        raise YamlRejected(TOO_LARGE)
    stack: list[list] = []  # one entry per open collection: [keys seen (None for a sequence), nodes seen]
    nodes = documents = 0
    for event in yaml.parse(text, Loader=YAML_LOADER):  # lazy: stops at the first refusal, deep input is never read
        kind = type(event)
        if kind is yaml.ScalarEvent or kind is yaml.MappingStartEvent or kind is yaml.SequenceStartEvent:
            if event.anchor:
                raise _rejected("Anker (&) ist nicht erlaubt", event)
            if event.tag:
                raise _rejected("Tags (!) sind nicht erlaubt", event)
            nodes += 1
            if nodes > MAX_NODES:
                raise _rejected(f"mehr als {MAX_NODES} Knoten", event)
            if stack and stack[-1][0] is not None:  # in a mapping: every second node is a key
                stack[-1][1] += 1
                if stack[-1][1] % 2 == 1:
                    _check_key(event, stack[-1][0])
            if kind is not yaml.ScalarEvent:
                if len(stack) >= MAX_DEPTH:
                    raise _rejected(f"Verschachtelung tiefer als {MAX_DEPTH} Ebenen", event)
                stack.append([set() if kind is yaml.MappingStartEvent else None, 0])
        elif kind is yaml.MappingEndEvent or kind is yaml.SequenceEndEvent:
            stack.pop()
        elif kind is yaml.AliasEvent:
            raise _rejected("Alias (*) ist nicht erlaubt", event)
        elif kind is yaml.DocumentStartEvent:
            documents += 1
            if documents > 1:
                raise _rejected("mehrere Dokumente in einer Datei", event)


@lru_cache(maxsize=1024)  # the keys of a folder are the same few field names
def _is_text(value: str, implicit: tuple[bool, bool]) -> bool:
    return _resolves_to_text(value, implicit)


def _resolves_to_text(value: str, implicit: tuple[bool, bool]) -> bool:
    return _RESOLVER.resolve(yaml.ScalarNode, value, implicit) == _STR_TAG


def _check_key(event: yaml.NodeEvent, seen: set[str]) -> None:
    """A key must be a text (the resolved value, so `a` and "a" are the same key and `1` or `no` are no text)."""
    if type(event) is yaml.ScalarEvent and event.value == "<<":
        raise _rejected("Merge-Schlüssel (<<) ist nicht erlaubt", event)
    resolves = _is_text if type(event) is yaml.ScalarEvent and len(event.value) <= MAX_CACHED_KEY else _resolves_to_text
    if type(event) is not yaml.ScalarEvent or not resolves(event.value, tuple(event.implicit)):
        raise _rejected("Schlüssel müssen Text sein", event)
    if event.value in seen:
        raise _rejected("doppelter Schlüssel", event)
    seen.add(event.value)


def _rejected(rule: str, event: yaml.Event) -> YamlRejected:
    return YamlRejected(f"{rule} (Zeile {event.start_mark.line + 1})")


def describe_yaml_error(exc: Exception) -> str:
    """Where and what, from a syntax error of a parser (PyYAML or ruamel). Their own text quotes the lines of the file."""
    mark = getattr(exc, "problem_mark", None)
    where = f" (Zeile {mark.line + 1}, Spalte {mark.column + 1})" if mark is not None else ""
    if isinstance(exc, yaml.reader.ReaderError):
        return "YAML-Syntaxfehler: unzulässiges Zeichen"
    return one_line(f"YAML-Syntaxfehler{where}: {getattr(exc, 'problem', None) or 'nicht lesbar'}")


def describe_validation_error(exc: ValidationError) -> str:
    """Field and rule of the first errors on one line, without the value that failed (it is file content)."""
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    parts = [f"{'.'.join(str(part) for part in error['loc']) or 'Datei'}: {error['msg']}" for error in errors[:MAX_LISTED_ERRORS]]
    more = f" (und {len(errors) - MAX_LISTED_ERRORS} weitere)" if len(errors) > MAX_LISTED_ERRORS else ""
    return one_line("; ".join(parts)) + more


def one_line(text: str) -> str:
    """The text without control and format characters (line breaks, escape, bidi), cut to a length for a message."""
    clean = "".join(" " if char.isspace() else char for char in text if unicodedata.category(char) not in ("Cc", "Cf", "Cs") or char.isspace())
    return clean if len(clean) <= MAX_MESSAGE_CHARS else clean[:MAX_MESSAGE_CHARS - 1] + "…"
