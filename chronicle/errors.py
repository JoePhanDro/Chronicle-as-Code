"""Errors the repository raises. The API maps them to HTTP status codes."""


class RepositoryError(Exception):
    """Base class for errors the API maps to HTTP status codes."""


class NotFound(RepositoryError):
    pass


class Conflict(RepositoryError):
    pass


class Invalid(RepositoryError, ValueError):
    pass


APPLY_HINT = "Bitte chronicle apply ausführen."


def changed_outside(path: str, outcome: str, cause: object = None) -> Conflict:
    """The conflict for a topic file that someone changed outside Chronicle since the index was built.

    `outcome` says what did not happen ("Nichts wurde gelöscht"), `cause` is the error of the store, if there is one."""
    detail = f" ({cause})" if cause is not None else ""
    return Conflict(f"{path} wurde außerhalb von Chronicle geändert. {APPLY_HINT} {outcome}{detail}.")


def not_in_a_file(what: str) -> str:
    """The text for an event or topic the index knows, but no file of the data folder holds. `what`: "Event 'x'"."""
    return f"{what} steht in keiner Datei des Datenordners. {APPLY_HINT}"

