"""Unified Job-Market and Outreach Engine — opportunity-centric decision layer."""

from engine.opportunity import (
    Opportunity,
    RecommendedAction,
    ReferralStatus,
    Track,
    job_to_opportunity,
    signal_to_opportunity,
)
from engine.recommend_action import recommend_action, apply_recommendation_to_job

__all__ = [
    "Opportunity",
    "RecommendedAction",
    "ReferralStatus",
    "Track",
    "job_to_opportunity",
    "signal_to_opportunity",
    "recommend_action",
    "apply_recommendation_to_job",
]
