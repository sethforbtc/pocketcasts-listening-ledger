# Phone alerts with ntfy

[ntfy](https://ntfy.sh) is a free, open-source push notification service. The Listening Ledger uses it to tell you when the hourly collector stops working, for example because Pocket Casts changed its API or your password changed.

**What you'll get:**

| When | Notification |
|---|---|
| 3 hourly runs fail in a row | **Pocket Casts Ledger is failing** (high priority), with the error |
| Still failing | The same alert again about once a day (every 24 failed runs) |
| It starts working again | **Pocket Casts Ledger recovered** |
| You press "Send test notification" | **Pocket Casts Ledger test** |

The alerts contain only the failure count and the error message. They never include your listening data or your credentials.

## How ntfy works (and why the topic name matters)

ntfy has no accounts by default. You pick a **topic name**, your phone subscribes to it, and anything posted to that topic shows up on your phone. Anyone who knows the topic name can also read and send messages on it, **so treat the topic name like a password**. Use something random, like `ledger-k3v9q7mxp2`, and not something guessable like `my-podcasts`.

## Setup (about 5 minutes)

### 1. Pick a topic name
Open the dashboard → **Alerts & health** → click **Generate a random topic name**. Copy it somewhere for the next step, but don't save it yet.

### 2. Install the app and subscribe (iPhone)
1. Install **ntfy** from the App Store (publisher: Philipp Heckel).
2. Open it and tap **+** (top right).
3. Enter your topic name exactly. Leave **Use another server** off.
4. Tap **Subscribe**.
5. When iOS asks to allow notifications, tap **Allow**. If you missed the prompt, go to iPhone **Settings → Notifications → ntfy** and turn on **Allow Notifications**.

> Android works the same way: install ntfy from Google Play or F-Droid, tap **+**, and enter the topic.

### 3. Save the topic in the ledger
Back on the dashboard's **Alerts & health** tab, click **Save**, then **Send test notification**. Your phone should buzz within a few seconds.

You can also do this from the command line instead of the dashboard:

```powershell
python pc_history.py --set-ntfy ledger-k3v9q7mxp2 --test-notify
```

or when registering the scheduled tasks:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_task.ps1 -NtfyTopic ledger-k3v9q7mxp2
```

The topic is saved in `config.json` in your data folder (`%LOCALAPPDATA%\PocketCastsLogger\` on Windows), never in the repo.

## Troubleshooting

- **The test says "Sent" but nothing arrives on the phone.** Check that the topic in the app matches exactly (it's case-sensitive) and that ntfy notifications are allowed in iOS Settings. Also check that a Focus mode isn't silencing them.
- **The test says "Failed".** Make sure you clicked **Save** first and that the laptop is online. The detail message shows the HTTP status or network error.
- **I want to turn alerts off.** Clear the topic box and click **Save**, or run `python pc_history.py --set-ntfy ""`.

## Optional: more privacy

- **Self-host ntfy** (it's a single binary or Docker container) and put its address in the **Server** box. On the iPhone, turn on **Use another server** when subscribing.
- **Use a protected topic.** If you create an ntfy.sh account and reserve the topic, or run your own server with access control, set `NTFY_TOKEN` (an access token) as an environment variable, or add `"ntfy_token": "tk_..."` to `config.json`. The ledger sends it as a bearer token.
- Environment variables `NTFY_TOPIC`, `NTFY_SERVER` and `NTFY_TOKEN` take precedence over `config.json`, if you prefer to manage settings that way.
