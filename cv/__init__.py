"""Computer vision investigation — ViCoS dataset, pose features, baseline classifiers."""

from .baseline_classifier import evaluate_baseline, train_baseline
from .pose_features import build_feature_matrix, normalize_pose, pair_features, single_pose_features
from .vicos_download import VicosSample, download_annotations, download_images, verify
from .vicos_explore import explore_vicos, plot_class_distribution, plot_pose_skeleton

__all__ = [
    "download_annotations",
    "download_images",
    "verify",
    "VicosSample",
    "explore_vicos",
    "plot_class_distribution",
    "plot_pose_skeleton",
    "normalize_pose",
    "single_pose_features",
    "pair_features",
    "build_feature_matrix",
    "train_baseline",
    "evaluate_baseline",
]
