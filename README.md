# Pocket Casts Listening Ledger

Pocket Casts only keeps your listening history for a limited time, and old entries eventually drop off. **Listening Ledger** saves a permanent copy on your own computer. It checks your history every hour and adds anything new to a local SQLite database. A local web dashboard shows your listening stats and builds OPML, Excel and CSV exports.

![Dashboard overview (demo data)](docs/dashboard.png)

> **Unofficial.** This project is not affiliated with or endorsed by Pocket Casts or Automattic. It uses the same private API as the Pocket Casts web player, and that API can change at any time. It only *reads* your data and never changes anything in your account.

## Features

- **Permanent history.** Every episode that shows up in your Listening History gets logged, and nothing is ever deleted.
- **Re-listens count.** Replaying an older episode moves it back to the top of your history, and the ledger logs that as another listen.
- **Progress tracking.** It records where you'd gotten to in an episode when the listen was logged, and where you are now, so you can tell finished episodes from ones you sampled.
- **Credentials stay private.** Your password is stored in your operating system's credential store (Windows Credential Manager, macOS Keychain, or Secret Service on Linux), never in a plain file.
- **Dashboard.** Hours per week, top shows, how often you finish episodes, when you listen (a day-by-hour heatmap), new shows per month, unfinished episodes, and recent listens.
- **OPML export.** Build a podcast subscription list from your actual listening: top shows by completed episodes, top shows by hours, or every show in your most recent episodes, over any number of weeks, months or years. You can import the file into any podcast app.
- **Excel and CSV export.** Any report, several reports at once (one sheet each), or the full raw log.
- **Failure alerts.** Get a push notification through [ntfy](https://ntfy.sh) when runs keep failing, and another when they recover. See [docs/ntfy.md](docs/ntfy.md).

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

This registers two tasks:

- **Pocket Casts History Logger** runs the collector every hour in the background.
- **Pocket Casts Ledger Dashboard** starts the dashboard when you sign in and keeps it running at **http://127.0.0.1:8770**. Add `-NoDashboard` to skip it.

Both tasks run only while you're signed in, because they need your login session to read the saved password. If the computer was asleep, the collector catches up with one run when it wakes.

### macOS / Linux (cron)

```cron
7 * * * * cd /path/to/pocketcasts-listening-ledger && /usr/bin/python3 pc_history.py
```

## Dashboard

```bash
python dashboard.py               # opens http://127.0.0.1:8770
```

It only listens on `127.0.0.1`, so it's reachable from this computer and nothing else on your network.

| Tab | What's there |
|---|---|
| **Overview** | Summary numbers, hours per week, top shows by hours, how episodes ended, when you listen, new shows per month, unfinished episodes, recent listens. Filter by period and set the completion threshold. |
| **Reports & export** | The three show reports below, with a preview. Download as **OPML**, **CSV** or **Excel**, or several reports at once as one workbook or a zip of CSVs. |
| **Feeds** | The RSS feed URL on file for each show. Look up missing ones or paste your own. |
| **Alerts & health** | Collector status, the last error, and ntfy setup with a test button. |

### Reports

| Report | Definition |
|---|---|
| **Top shows by completed episodes** | The *N* shows with the most episodes played to at least the completion threshold (default **75%**) in the period. Episodes Pocket Casts marks as played also count. |
| **Top shows by hours listened** | The *N* shows with the most time played in the period (each episode counts once, up to its full length). |
| **Shows in my most recent episodes** | Every show among your *N* most recently played episodes in the period. |

The period is the last *N* weeks, months or years, or all time. Re-listening to an episode doesn't count it twice.

### Why OPML needs the Feeds tab

OPML files list each show's **RSS feed URL**, and Pocket Casts history doesn't include it. The collector looks feeds up in Apple's free podcast directory, up to 10 new shows per hourly run, and accepts only confident matches (the title *and* the author have to match). For shows it can't match, paste the feed URL on the **Feeds** tab. Shows without a feed are left out of the OPML file and listed in a comment at the top of it. To look up everything at once, run `python pc_history.py --resolve-feeds`.

## Command line

```bash
python pc_history.py                    # one polling run (what the scheduler calls)
python pc_history.py --stats            # summary + last 10 listens
python pc_history.py --export-csv listens.csv
python pc_history.py --resolve-feeds    # look up all missing RSS feed URLs
python pc_history.py --set-ntfy TOPIC   # save the ntfy topic for alerts ("" to clear)
python pc_history.py --test-notify      # send a test alert
```

The database and log file live in your user data folder:

| OS | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\PocketCastsLogger\` |
| macOS / Linux | `~/PocketCastsLogger/` |

The data folder also holds `config.json`, which stores your ntfy settings. To put everything somewhere else, set the `PC_LOGGER_DIR` environment variable.

## Data model

| Table / view | Contents |
|---|---|
| `v_listens` | **Start here.** One row per listen, with show, episode, % played, and status at the time it was logged and now. |
| `listen_events` | One row each time an episode reaches the top of your history (a first listen or a re-listen). |
| `episodes` | One row per episode ever seen: metadata, latest progress, and the raw API record (`raw_json`). |
| `runs` | Every polling run: success or failure, counts, and error text. |
| `podcasts` | One row per show: the RSS feed URL used for OPML, and where it came from (`itunes`, `manual`, or `notfound`). |

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
- **Runs failing after working fine:** check the **Alerts & health** tab, the `runs` table or the log file. If Pocket Casts changed its API, please open an issue.
- **Dashboard won't open:** run `python dashboard.py` in a terminal to see the error. If something else is using port 8770, start it with `--port 8771`.
- **No phone alerts:** see the troubleshooting section in [docs/ntfy.md](docs/ntfy.md).

## License

[MIT](LICENSE)
