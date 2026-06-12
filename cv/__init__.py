"""Computer vision investigation — ViCoS dataset, pose features, baseline classifiers."""

from .baseline_classifier import evaluate_baseline, train_baseline
from .pose_features import PoseFeatures, extract_pose_features
from .vicos_download import VicosSample, download_vicos
from .vicos_explore import explore_vicos, plot_class_distribution, plot_pose_skeleton

__all__ = [
    "download_vicos", "VicosSample",
    "explore_vicos", "plot_class_distribution", "plot_pose_skeleton",
    "extract_pose_features", "PoseFeatures",
    "train_baseline", "evaluate_baseline",
]
