from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = REPO_ROOT / "systemd" / "user"
REPO_PATH = "/home/vetor/GrapplingArc/GrapplingArcAnalytics"
ENV_FILE = "%h/GrapplingArc/GrapplingArcAnalytics/.env"
UV = "/home/vetor/.local/bin/uv"
RUNBOOK = REPO_ROOT / "docs" / "PRO_ANALYTICS_LOCAL_PUBLISHER.md"


def _unit(name: str) -> ConfigParser:
    parser = ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read(SYSTEMD_DIR / name)
    return parser


def test_pro_analytics_systemd_units_use_the_local_repository_environment_and_schedule() -> None:
    for cadence, calendar in (
        ("daily", "*-*-* 03:15:00 UTC"),
        ("weekly", "Sun *-*-* 04:15:00 UTC"),
    ):
        service = _unit(f"grapplingarc-pro-analytics-{cadence}.service")
        timer = _unit(f"grapplingarc-pro-analytics-{cadence}.timer")

        assert service["Service"]["WorkingDirectory"] == REPO_PATH
        assert service["Service"]["EnvironmentFile"] == ENV_FILE
        assert service["Service"]["ExecStart"] == (
            f"{UV} run --extra postgres python -m jobs.publish_pro_analytics --cadence {cadence}"
        )
        assert timer["Timer"]["OnCalendar"] == calendar
        assert timer["Timer"]["Persistent"] == "true"
        assert timer["Timer"]["Unit"] == f"grapplingarc-pro-analytics-{cadence}.service"


def test_local_publisher_runbook_loads_the_env_and_documents_a_real_weekly_backfill() -> None:
    text = RUNBOOK.read_text()

    assert "set -a\n. ./.env\nset +a" in text
    assert 'USER_AUTH_UUID="<exact auth.users.id UUID>"' in text
    assert '--cadence weekly --user-id "$USER_AUTH_UUID"' in text
