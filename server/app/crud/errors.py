"""
Typed domain errors raised by the data-access layer.

The API layer maps these to HTTP responses (e.g. GameFullError -> 409,
NotPermittedError -> 403, NotFoundError -> 404).
"""

from __future__ import annotations


class PickupDBError(Exception):
    """Base class for all data-access domain errors."""


class NotFoundError(PickupDBError):
    pass


class NotPermittedError(PickupDBError):
    pass


class NotPurdueEmailError(NotPermittedError):
    """FR-01: only verified @purdue.edu accounts may sign in."""


class AccountConflictError(PickupDBError):
    """The email already belongs to a different Firebase account."""


class GameFullError(PickupDBError):
    """FR-05: the game's roster has reached its player cap."""


class GameNotJoinableError(PickupDBError):
    """The game is cancelled, finished, or otherwise closed to joins."""


class BlockedError(NotPermittedError):
    """FR-15: the organizer has blocked this player."""


class SkillOutOfRangeError(NotPermittedError):
    """SYS-04: the player's rating is outside the game's skill range."""


class ReliabilityTooLowError(NotPermittedError):
    """SYS-06: organizer's reliability score is below the public-game threshold."""


class CheckInRejectedError(PickupDBError):
    """FR-06: outside the geofence, outside the time window, or not a participant."""


class InvalidTagError(PickupDBError):
    """FR-10: the self-tag is not allowed (not a participant, track claimed, ...)."""


class InvalidReviewError(PickupDBError):
    """FR-10: the shot event cannot be confirmed/rejected (not flagged, not a participant)."""


class TagsLockedError(PickupDBError):
    """Tags and event reviews are locked 14 days after stats were finalized."""


class JobStateError(PickupDBError):
    """A processing job or recording is not in a state that allows this action."""
