"""
SQLAlchemy ORM models. Importing this package registers every table on
`server.app.db.base.Base.metadata`.
"""

from server.app.models.court import Court, CourtCalibration, CourtStatus  # noqa: F401
from server.app.models.game import (  # noqa: F401
    Attendance,
    Game,
    GameBlock,
    GameParticipant,
    GameStatus,
    ParticipantStatus,
    ReliabilityEvent,
    ReliabilityKind,
    ScoringRule,
    Visibility,
)
from server.app.models.player import DeviceToken, PlayerProfile, PlayerRating, RatingHistory, User  # noqa: F401
from server.app.models.recording import JobStatus, ProcessingJob, Recording, UploadStatus  # noqa: F401
from server.app.models.stat import DetectedTrack, HighlightClip, ReviewStatus, ShotEvent  # noqa: F401
