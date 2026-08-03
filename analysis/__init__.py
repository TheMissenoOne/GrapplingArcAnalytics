"""
Analysis modules — ELO calibration, technique frequency, benchmarking, similarity,
graph embedding, GNN predictor, and archetype validation.
"""

from .benchmark import compare, pro_baseline, user_submission_profile
from .decision_flow import (
    DecisionPattern,
    PatternEvidence,
    aggregate_patterns,
    build_athlete_decision_patterns,
    extract_patterns,
)
from .dossier_decision_flow import (
    DecisionFlowEligibility,
    DecisionFlowQuality,
    build_decision_flow_from_patterns,
    check_eligibility,
    decision_flow_cache_key,
)
from .elo_calibration import (
    calibrate_k_factor,
    compute_adcc_elo,
    compute_elo_with_draws,
    draw_probability,
    expected_with_draw,
)
from .pro_analytics import build_athlete_dossier_v1, build_performance_snapshot_v1
from .similarity import (
    fighter_similarity,
    fighter_vectors,
    find_similar_fighters,
    top_similar,
    user_vector,
)
from .technique_freq import position_distribution, submission_frequency, submission_trend
from .user_insights import export_insights, generate_insights, load_competition_data

__all__ = [
    "DecisionPattern",
    "PatternEvidence",
    "extract_patterns",
    "aggregate_patterns",
    "build_athlete_decision_patterns",
    "DecisionFlowEligibility",
    "DecisionFlowQuality",
    "check_eligibility",
    "build_decision_flow_from_patterns",
    "decision_flow_cache_key",
    "compute_adcc_elo",
    "calibrate_k_factor",
    "compute_elo_with_draws",
    "draw_probability",
    "expected_with_draw",
    "position_distribution",
    "submission_frequency",
    "submission_trend",
    "user_submission_profile",
    "pro_baseline",
    "compare",
    "fighter_similarity",
    "find_similar_fighters",
    "fighter_vectors",
    "top_similar",
    "user_vector",
    "export_insights",
    "generate_insights",
    "load_competition_data",
    "build_performance_snapshot_v1",
    "build_athlete_dossier_v1",
]
