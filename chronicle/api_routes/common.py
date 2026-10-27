"""What the API routers share: the translation of the repository's errors into HTTP answers."""

from __future__ import annotations

from fastapi import HTTPException

from .. import repository
from ..evidence import EvidenceRejected


def write(action):
    """Run a change and turn the refusals of the repository into 404, 409 and 422."""
    try:
        return action()
    except repository.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (repository.Invalid, EvidenceRejected) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
