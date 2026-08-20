# SEMC Flask App for Google Health Data Extraction

This Flask app is a local OAuth and data-extraction server for the SEMC wearable study. It authorizes multiple Google/Fitbit-linked user accounts, stores Google OAuth tokens locally, and exposes Google Health API data through local CSV export routes.

This app was adapted from the original Fitbit OAuth server used for the GIMWearables project. The original app used the Fitbit Web API and `python-fitbit`; this version uses the Google Health API.

---

## What this app does

The app allows you to:

1. Authorize multiple Google accounts connected to Fitbit/Google Health data.
2. Store each user's OAuth access and refresh tokens in a local SQLite database.
3. Export wearable metrics as CSV files through local browser links.
4. Pull heart rate, resting heart rate, steps, sleep episodes, sleep stages, and sleep summaries.
5. Run a separate analysis script to summarize data across SEF participants.

This app is intended to run locally only.

**Do not deploy this app publicly.**

---

## Important security note

This app stores OAuth tokens in a local SQLite database. This setup is not intended to be secure for web deployment.

Do not commit any of the following to GitHub:

```text
Google client secrets
.env files
SQLite databases
CSV exports
analysis outputs
```

If a Google client secret has ever been pasted into chat, email, GitHub, or shared documentation, regenerate the secret in Google Cloud before continuing.

---

## Recommended Python version

This app has been tested with:

```text
Python 3.8.10
```

Python 3.8 or 3.9 is recommended for this older Flask codebase.

---

## Installation

Clone the repo:

```bash
git clone <repo-url>
cd SEMC-Flask-App-for-Google-Health
```

Create and activate a virtual environment - Windows powershell or Conda

### Windows PowerShell

```powershell
python -m venv venv1
.\venv1\Scripts\Activate.ps1
```

### Conda option

```bash
conda create --name semc_google_health python=3.8 pip
conda activate semc_google_health
```

Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Do not manually upgrade Flask, Werkzeug, or Flask-WTF unless the code has been tested with those versions. This app depends on an older Flask stack, and unpinned upgrades can break the app.

---


## Google Cloud setup

Create or use a Google Cloud project with the Google Health API enabled.

In Google Cloud Console:

```text
APIs & Services
→ Library
→ Google Health API
→ Enable
```

Then configure OAuth.

Go to:

```text
APIs & Services
→ OAuth consent screen / Google Auth Platform
```

Set the app to testing mode and add the study Google accounts as test users.

Example test users:

```text
user1@gmail.com
user2@gmail.com
user3@gmail.com
...
user12@gmail.com
```

Add the following OAuth scopes under Data Access

```text
https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
https://www.googleapis.com/auth/googlehealth.sleep.readonly
```

Create an OAuth 2.0 Web Application client.

Use these Authorized JavaScript origins:

```text
http://localhost:5000
http://127.0.0.1:5000
```

Use these Authorized redirect URIs:

```text
http://localhost:5000/oauth-redirect
http://127.0.0.1:5000/oauth-redirect
```

---

## Environment variables

Before running the app, set these environment variables.

### Windows PowerShell

```powershell
$env:GOOGLE_CLIENT_ID="PASTE_CLIENT_ID_HERE"
$env:GOOGLE_CLIENT_SECRET="PASTE_CLIENT_SECRET_HERE"
$env:GOOGLE_REDIRECT_URI="http://localhost:5000/oauth-redirect"
$env:SECRET_KEY="testsecret123"
$env:FLASK_CONFIG="development"
```

Do not commit real client secrets to GitHub.

---

### Windows Conda
No quotes when setting!

```conda
:: %CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat
set GOOGLE_CLIENT_ID=PASTE_CLIENT_ID_HERE
set GOOGLE_CLIENT_SECRET=PASTE_CLIENT_SECRET_HERE
set GOOGLE_REDIRECT_URI=http://localhost:5000/oauth-redirect
set SECRET_KEY=testsecret123
set FLASK_CONFIG=development
```


---


## Create the local database

The app stores user tokens in SQLite.

The table is still called:

```text
fitbit_tokens
```

This name is inherited from the original Fitbit app, but the table now stores Google Health OAuth tokens.

The first time you run the app on a new device, create the database tables:

```powershell
python manage.py createdb
```

You should see:

```text
database created
```

---

## Run the Flask app

With your virtual environment activated and environment variables set, run:

```powershell
python manage.py
```

You should see something like:

```text
Running on http://127.0.0.1:5000/
```

Open:

```text
http://127.0.0.1:5000/
```

or:

```text
http://localhost:5000/
```

---

## Authorize users

To authorize a user, open the app with a `state` query parameter equal to the user's email.

Example:

```text
http://localhost:5000/?state=user1%40gmail.com
```

Then sign into the matching Google account:

```text
user1@gmail.com
```

After authorization, check the list of stored users:

```text
http://localhost:5000/users
```

A new local database starts empty, so `/users` may initially show:

```json
[]
```

That is normal until accounts are authorized.

---


## Delete users


To remove a user:

```text
http://localhost:5000/users/<user_id>/delete
```


## Available raw data route

To inspect raw Google Health API data for one user:

```text
http://localhost:5000/google-data/<username>/<data_type>
```

Example:

```text
http://localhost:5000/google-data/user1%40gmail.com/heart-rate
```

Supported data types include:

```text
heart-rate
daily-resting-heart-rate
steps
sleep
active-zone-minutes
```

---

## CSV export routes

CSV exports use this format:

```text
http://localhost:5000/export/<username>/<metric>?start=<UTC_START>&end=<UTC_END>
```

The username email must be URL-encoded.

Example:

```text
user1@gmail.com
```

becomes:

```text
user1%40gmail.com
```

Supported export metrics:

```text
heart-rate
daily-resting-heart-rate
steps
sleep
sleep-stages
sleep-summary
active-zone-minutes
```

Example heart-rate export:

```text
http://localhost:5000/export/user1%40gmail.com/heart-rate?start=2026-06-29T20:00:00Z&end=2026-06-30T22:00:00Z
```

Example steps export:

```text
http://localhost:5000/export/user1%40gmail.com/steps?start=2026-06-29T20:00:00Z&end=2026-06-30T22:00:00Z
```

Example sleep summary export:

```text
http://localhost:5000/export/user1%40gmail.com/sleep-summary?start=2026-06-29T20:00:00Z&end=2026-06-30T22:00:00Z
```

---

## Time zones

The app expects `start` and `end` times in UTC.

For Toronto during June/July, local time is usually EDT, which is UTC-4.

Example:

```text
June 29, 2026 4:00 PM Toronto = 2026-06-29T20:00:00Z
June 30, 2026 6:00 PM Toronto = 2026-06-30T22:00:00Z
```

---

## Analysis script

The repo includes an analysis script for summarizing data across SEF participants.

Expected structure:

```text
analysis/
├── analyze_sef_data.py
├── participant_windows.csv
└── output/
```

The participant window file should look like:

```csv
sef_id,email,start_utc,end_utc
SEF-01,user1@gmail.com,2026-05-26T20:00:00Z,2026-05-27T21:00:00Z
SEF-02,user2@gmail.com,2026-06-02T21:00:00Z,2026-06-03T21:00:00Z
SEF-03,user3@gmail.com,2026-06-10T21:00:00Z,2026-06-11T21:00:00Z
SEF-10,user10@gmail.com,2026-07-02T19:00:00Z,2026-07-03T22:00:00Z
SEF-11,user11@gmail.com,2026-07-08T20:00:00Z,2026-07-09T22:00:00Z
SEF-12,user12@gmail.com,2026-07-09T20:00:00Z,2026-07-10T21:00:00Z
```

To run the analysis, keep Flask running in one terminal:

```powershell
python manage.py
```

Then open a second terminal and run:

```powershell
python analysis\analyze_sef_data.py
```

The analysis creates:

```text
analysis/output/master_summary.csv
analysis/output/raw_exports/
analysis/output/plots/
```

The master summary includes metrics such as:

```text
HR sample count
mean HR
median HR
HR standard deviation
total steps
sleep episode count
minutes asleep
minutes awake
sleep efficiency
missing data notes
download errors
```

The plots folder includes participant-level figures such as:

```text
SEF-10_heart_rate_timeseries.png
SEF-10_steps_per_hour.png
SEF-10_sleep_stage_duration.png
```

---

## Common errors and fixes


### `sqlite3.OperationalError: no such table: fitbit_tokens`

The database tables have not been created yet.

Run:

```powershell
python -c "from app import create_app, db; from config import get_current_config; app = create_app(get_current_config()); app.app_context().push(); db.create_all(); print('database created')"
```

---

### Google access blocked / app has not completed verification

The app is in testing mode and the account has not been added as a test user.

Fix:

```text
Google Cloud Console
→ OAuth consent screen / Google Auth Platform
→ Audience
→ Test users
→ Add the account email
```

---

### `invalid_grant`, `Token has been expired or revoked`, or `unauthorized_client`

The stored token is no longer valid or was created under a different Google OAuth client.

Fix:

1. Confirm the correct `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are being used.
2. Add the user as a test user in that Google Cloud project.
3. Reauthorize the user through the app.
4. If needed, delete the local SQLite database and create a fresh one.

---

### Empty CSV with only headers

The export route worked, but no matching data was found.

Check raw data first:

```text
http://localhost:5000/google-data/<username>/sleep
```

Then try the export without a date filter:

```text
http://localhost:5000/export/<username>/sleep
```

If raw data is empty, the issue is likely Google Health/Fitbit sync, account authorization, or unavailable data for that metric.

---

## Git ignore recommendations

Add this to `.gitignore`:

```gitignore
.env
*.sqlite
data-dev.sqlite
data-test.sqlite
__pycache__/
*.pyc
analysis/output/
*.csv
.idea/
.vscode/
```

---

## Legacy naming notes

This app still contains some old Fitbit naming from the original project, including:

```text
fitbit_tokens
save_fitbit_token
get_user_fitbit_credentials
get_all_fitbit_credentials
```

These names are legacy names. In the current app, they are used to store and retrieve Google Health OAuth tokens.

Renaming them would make the code cleaner, but it is not required for the current local workflow.
