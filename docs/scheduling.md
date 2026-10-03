# amlcheck — Scheduling

> Run the daily jobs without remembering to: macOS (launchd), Linux (cron or a systemd timer).
> Part of P8 (T-8.07); `monitor run` joins in P10 the same way.

## What to schedule

| Job | How often | Why | Exit codes |
|---|---|---|---|
| `amlcheck sync` | Daily (more often is fine) | The OFAC list must be younger than 48 h and the TRON freeze index current, or every check is INCOMPLETE | 0 ok, 1 failed |
| `amlcheck watch run` | Daily, after `sync` | Re-screens every watched address; a changed verdict is listed, alerted and recorded | 0 no change, **6 a verdict changed**, 1 could not run |

- `batch`, `watch run` (and `monitor run`) share one lock file, `runs.lock` in the data folder: a run
  that finds another one going stops at once with exit 1 instead of competing for the provider limits.
- A changed verdict raises a macOS notification. Set `[monitor] webhook_url` in `config.toml` to also
  receive the changes as a JSON POST (for example a local Telegram bot); see D-054.
- Every check made by a scheduled run is in the audit log (`amlcheck audit list`), marked with the note
  `watch run`.

## Before you schedule

1. **Use the full path of the `amlcheck` you want.** Schedulers don't load your shell profile. In this
   repository it is `<repo>/.venv/bin/amlcheck` after `uv sync`. Check with
   `<repo>/.venv/bin/amlcheck --version`.
2. **Pick the data folder** and set `AMLCHECK_HOME` to it in the job. amlcheck refuses a database it did
   not create (D-035), so an older amlcheck's folder can't be mixed up with this one.
3. **Keys go in `<AMLCHECK_HOME>/.env`** (`AMLCHECK_TRONGRID_API_KEY=…`, `AMLCHECK_HYPERSYNC_TOKEN=…`).
   A scheduled job doesn't run in the repository, so it won't see the repository's `.env`. Make the file
   readable only by you: `chmod 600 <AMLCHECK_HOME>/.env`.
4. **Try the jobs once by hand** with the same `AMLCHECK_HOME`: `amlcheck sync`, then
   `amlcheck watch run`.

In the examples, replace `/Users/you/aml-checker-v1` with your repository and `/Users/you/.amlcheck-v1`
with your data folder.

## macOS: launchd

One agent per job, in `~/Library/LaunchAgents/`. This one runs `sync` and then `watch run` every day at
07:30; the notification shows because a LaunchAgent runs in your login session.

`~/Library/LaunchAgents/com.amlcheck.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.amlcheck.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>A=/Users/you/aml-checker-v1/.venv/bin/amlcheck; "$A" sync; "$A" watch run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AMLCHECK_HOME</key>
    <string>/Users/you/.amlcheck-v1</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/you/.amlcheck-v1/logs/daily.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/you/.amlcheck-v1/logs/daily.log</string>
</dict>
</plist>
```

```bash
mkdir -p ~/.amlcheck-v1/logs
launchctl load ~/Library/LaunchAgents/com.amlcheck.daily.plist
launchctl start com.amlcheck.daily        # run it once now
tail -n 30 ~/.amlcheck-v1/logs/daily.log
```

To stop: `launchctl unload ~/Library/LaunchAgents/com.amlcheck.daily.plist`. A Mac that is asleep at
07:30 runs the job when it wakes.

## Linux: cron

`crontab -e`, then one line (07:30 every day):

```cron
30 7 * * * AMLCHECK_HOME=/home/you/.amlcheck-v1 sh -c 'A=/home/you/aml-checker-v1/.venv/bin/amlcheck; "$A" sync; "$A" watch run' >> /home/you/.amlcheck-v1/logs/daily.log 2>&1
```

cron does not show notifications; use `[monitor] webhook_url` to be told about changes, or let cron mail
you the output.

## Linux: systemd timer (user units)

`~/.config/systemd/user/amlcheck-daily.service`:

```ini
[Unit]
Description=amlcheck daily sync and watch run

[Service]
Type=oneshot
Environment=AMLCHECK_HOME=%h/.amlcheck-v1
ExecStart=%h/aml-checker-v1/.venv/bin/amlcheck sync
ExecStart=%h/aml-checker-v1/.venv/bin/amlcheck watch run
# exit 6 means "a verdict changed": report it, but it is not a failure of the unit
SuccessExitStatus=6
```

`~/.config/systemd/user/amlcheck-daily.timer`:

```ini
[Unit]
Description=Run amlcheck daily

[Timer]
OnCalendar=*-*-* 07:30
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now amlcheck-daily.timer
systemctl --user start amlcheck-daily.service   # run it once now
journalctl --user -u amlcheck-daily.service -n 50
```

`Persistent=true` runs a missed job after the machine was off. For runs while you are logged out:
`loginctl enable-linger $USER`.

## Checking that it works

- `amlcheck status`: the OFAC list's age and the freeze index lag should stay well inside their limits.
- `amlcheck watch list`: every watched address shows the time of its last run.
- `amlcheck audit list --since 1`: the scheduled checks of the last day.
