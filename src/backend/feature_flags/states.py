from typing import Final, Literal

type FeatureFlagState = Literal["off", "staff", "on"]

FEATURE_FLAG_OFF: Final[FeatureFlagState] = "off"
FEATURE_FLAG_STAFF: Final[FeatureFlagState] = "staff"
FEATURE_FLAG_ON: Final[FeatureFlagState] = "on"
