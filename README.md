# Pocket Casts Listening Ledger

Pocket Casts only keeps your listening history for a limited time, and old entries eventually drop off. **Listening Ledger** saves a permanent copy on your own computer. It checks your history every hour and adds anything new to a local SQLite database, which you can query with SQL, open in a spreadsheet, or connect to a BI tool.

> **Unofficial.** This project is not affiliated with or endorsed by Pocket Casts or Automattic. It uses the same private API as the Pocket Casts web player, and that API can change at any time. It only *reads* your data and never changes anything in your account.

## Features

- **Permanent history.** Every episode that shows up in your Listening History gets logged, and nothing is ever deleted.
- **Re-listens count.** Replaying an older episode moves it back to the top of your history, and the ledger logs that as another listen.
- **Progress tracking.** It records where you'd gotten to in an episode when the listen was logged, and where you are now, so you can tell finished episodes from ones you sampled.
- **Credentials stay private.** Your password is stored in your operating system's credential store (Windows Credential Manager, macOS Keychain, or Secret Service on Linux), never in a plain file.
- **Failure alerts.** Every failed run is recorded. Optionally, you can get a push notification through [ntfy](https://ntfy.sh) when runs keep failing.
- **Easy to analyze.** The `v_listens` view gives you one row per listen, ready for analysis. CSV export is built in.

## Requirements

- Python 3.10+
- A Pocket Casts account that signs in with **email and password**. "Sign in with Apple" and "Sign in with Google" accounts can't log in from a script.

## Install

```bash
git clone https://github.com/<you>/pocketcasts-listening-ledger.git
cd pocketcasts-listening-ledger
pip install -r requirements.txt
python pc_history.py --setup
```

`--setup` asks for your email and password, checks that they work, saves them to your credential store, and imports your current history.

> **Windows tip:** the password prompt hides what you type. If Ctrl+V doesn't paste, right-click to paste instead. Setup shows how many characters it received, so you can check that against your password manager.

## Schedule it

### Windows (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_task.ps1
# optional failure alerts:
powershell -ExecutionPolicy Bypass -File .\setup_task.ps1 -NtfyTopic "your-secret-topic-name"
```

This registers an hourly task that runs in the background with no console window. It runs while you're signed in, because it needs your login session to read the saved password. If the computer was asleep, it catches up with one run when it wakes.

### macOS / Linux (cron)

```cron
7 * * * * cd /path/to/pocketcasts-listening-ledger && /usr/bin/python3 pc_history.py
```

## Usage

```bash
python pc_history.py              # one polling run (what the scheduler calls)
python pc_history.py --stats      # summary + last 10 listens
python pc_history.py --export-csv listens.csv
```

The database and log file live in your user data folder:

| OS | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\PocketCastsLogger\` |
| macOS / Linux | `~/PocketCastsLogger/` |

To put them somewhere else, set the `PC_LOGGER_DIR` environment variable.

## Data model

| Table / view | Contents |
|---|---|
| `v_listens` | **Start here.** One row per listen, with show, episode, % played, and status at the time it was logged and now. |
| `listen_events` | One row each time an episode reaches the top of your history (a first listen or a re-listen). |
| `episodes` | One row per episode ever seen: metadata, latest progress, and the raw API record (`raw_json`). |
| `runs` | Every polling run: success or failure, counts, and error text. |

### How listen times work

Pocket Casts records the **order** you listened in, not the **time**. Each listen is logged with a time window: it happened between `window_start_utc` (the previous run) and `window_end_utc` (the run that caught it). Hourly runs keep that window about an hour wide.

Listens with `is_initial_import = 1` came from your first import. Their real dates are unknown, and only their order is reliable.

**Known blind spot:** if you replay the episode that's already at the top of your history, nothing moves, so that replay isn't logged as a new listen. Its progress still updates in `episodes`.

## Example queries

```sql
-- Listens per show, most first
SELECT podcast_title, COUNT(*) AS listens
FROM v_listens GROUP BY podcast_title ORDER BY listens DESC;

-- Started but never finished
SELECT podcast_title, episode_title, played_up_to_sec_latest, duration_sec
FROM v_listens WHERE status_latest = 'in progress';
```

## Troubleshooting

- **`Login failed on every endpoint`:** the error lists what Pocket Casts replied to each login attempt. A 401 almost always means the password didn't arrive intact (see the Windows paste tip above). If you've changed your password, run `--setup` again.
- **Runs failing after working fine:** look at the `runs` table or the log file. If Pocket Casts changed its API, please open an issue.

## License

[MIT](LICENSE)
