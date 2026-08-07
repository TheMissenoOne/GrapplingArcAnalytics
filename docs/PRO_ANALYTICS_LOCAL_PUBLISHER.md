# Local Pro Analytics Publisher

Pro analytics publishing runs on this PC, not on GitHub Actions or a server. The tracked GitHub
workflow remains manual-only. These systemd user-unit templates make the local machine publish:

- daily snapshots at 03:15 UTC;
- weekly snapshots and athlete dossiers at 04:15 UTC on Sunday.

The templates are intentionally specific to this workstation: they run from
`/home/vetor/GrapplingArc/GrapplingArcAnalytics`, use `/home/vetor/.local/bin/uv`, and read
`%h/GrapplingArc/GrapplingArcAnalytics/.env`. Install them only for the `vetor` user.

## Prerequisites

Create the local `.env` file from `.env.example` if it does not already exist. It must contain a
valid `DATABASE_URL` with the service/admin database credentials needed to write Pro snapshot rows.
Do not commit this file or place the URL in a unit file. Use one `KEY=VALUE` assignment per line;
the systemd `EnvironmentFile` parser does not execute shell commands.

Confirm the publisher can load its dependencies before enabling a timer:

```bash
cd /home/vetor/GrapplingArc/GrapplingArcAnalytics
/home/vetor/.local/bin/uv run --extra postgres python -m jobs.publish_pro_analytics --help
```

## Install and enable

These commands intentionally write only to the local user's systemd configuration. They are not
run by repository tooling.

```bash
cd /home/vetor/GrapplingArc/GrapplingArcAnalytics
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 systemd/user/grapplingarc-pro-analytics-*.service "$HOME/.config/systemd/user/"
install -m 0644 systemd/user/grapplingarc-pro-analytics-*.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now grapplingarc-pro-analytics-daily.timer
systemctl --user enable --now grapplingarc-pro-analytics-weekly.timer
systemctl --user list-timers 'grapplingarc-pro-analytics-*'
```

To keep timers running while this user is logged out, an administrator must enable lingering once:

```bash
loginctl enable-linger vetor
```

## Operate and troubleshoot

Run a due job immediately without changing the schedule:

```bash
systemctl --user start grapplingarc-pro-analytics-daily.service
systemctl --user start grapplingarc-pro-analytics-weekly.service
```

Inspect the most recent runs:

```bash
systemctl --user status grapplingarc-pro-analytics-daily.service
systemctl --user status grapplingarc-pro-analytics-weekly.service
journalctl --user -u grapplingarc-pro-analytics-daily.service -n 100 --no-pager
journalctl --user -u grapplingarc-pro-analytics-weekly.service -n 100 --no-pager
```

After changing a tracked template, repeat the install and `systemctl --user daemon-reload` steps.

## Manual weekly user backfill

Use the exact Supabase authentication UUID (`profiles.id` / `auth.users.id`) for the affected user;
do not use an email, athlete UUID, or display name. This is a real write because it intentionally
omits `--dry-run`.

```bash
cd /home/vetor/GrapplingArc/GrapplingArcAnalytics
USER_AUTH_UUID="<exact auth.users.id UUID>"
set -a
. ./.env
set +a
/home/vetor/.local/bin/uv run --extra postgres python -m jobs.publish_pro_analytics --cadence weekly --user-id "$USER_AUTH_UUID"
```

The user must already have `profiles.is_pro = true`. Relaunch the app after the command succeeds so
it fetches the new weekly snapshot.
