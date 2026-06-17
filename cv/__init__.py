"""Computer vision investigation — ViCoS dataset, pose features, baseline classifiers."""

from .baseline_classifier import evaluate_baseline, train_baseline, write_classifier_meta
from .inference import (
    ClassifierBundle,
    classify_frame,
    classify_pose_pair,
    classify_pose_pair_probs,
    load_classifier,
)
from .pose_estimate import PoseEstimator
from .pose_features import (
    FEATURE_NAMES,
    build_feature_matrix,
    normalize_pose,
    pair_features,
    pair_to_features,
    single_pose_features,
)
from .roboflow_classifier import RoboflowClassifier
from .roboflow_labels import roboflow_to_vicos
from .segmenter import PositionEvent, segment, smooth_labels
from .vicos_download import VicosSample, download_annotations, download_images, verify
from .vicos_explore import explore_vicos, plot_class_distribution, plot_pose_skeleton
from .vocab_map import (
    VICOS_POSITION_ALIASES,
    NodeRef,
    VocabMatch,
    build_vocab_index,
    load_app_nodes,
    map_all,
    map_vicos_class,
)

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
    "pair_to_features",
    "FEATURE_NAMES",
    "build_feature_matrix",
    "train_baseline",
    "evaluate_baseline",
    "write_classifier_meta",
    "load_classifier",
    "classify_pose_pair",
    "classify_pose_pair_probs",
    "classify_frame",
    "ClassifierBundle",
    "PoseEstimator",
    "load_app_nodes",
    "build_vocab_index",
    "map_vicos_class",
    "map_all",
    "VocabMatch",
    "NodeRef",
    "VICOS_POSITION_ALIASES",
    "segment",
    "smooth_labels",
    "PositionEvent",
    "RoboflowClassifier",
    "roboflow_to_vicos",
]
