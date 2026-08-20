""" July 30-31 - adding new data points

"""

import csv
import io
import json
from datetime import datetime

from flask import Response
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for

from app.main import main
from app import db
from app.main.forms import DeviceUserForm
from app.models import get_user_fitbit_credentials, get_all_fitbit_credentials
from app.google_health_client import get_permission_screen_url, do_google_auth, list_data_points



@main.route("/", methods=["GET", "POST"])
def index():
    form = DeviceUserForm(request.form)
    creds = get_all_fitbit_credentials()

    user_profiles = []
    for cred in creds:
        user_profiles.append({
            "username": cred.user_id,
            "fullName": "Google Health user"
        })

    user_state = request.args.get("state")

    if user_state:
        perm_url = get_permission_screen_url(user_state)
    else:
        perm_url = None

    return render_template(
        "index.html",
        user_state=user_state,
        form=form,
        user_profiles=user_profiles,
        permission_url=perm_url
    )


@main.route("/oauth-redirect", methods=["GET"])
def handle_redirect():
    code = request.args.get("code")
    user_id = request.args.get("state")

    if not code:
        return jsonify({
            "error": "No authorization code returned.",
            "details": dict(request.args)
        }), 400

    if not user_id:
        return jsonify({
            "error": "No user/state returned.",
            "details": dict(request.args)
        }), 400

    do_google_auth(code, user_id)

    return redirect(url_for("main.index"))


@main.route("/users", methods=["GET"])
def get_users():
    return jsonify([cred.user_id for cred in get_all_fitbit_credentials()])


@main.route('/users/<user_id>/delete', methods=['POST', 'DELETE','GET'])
def delete_user(user_id):
    """
    Endpoint to remove a user's Fitbit credentials and delete them from the system.
    """

    cred = get_user_fitbit_credentials(user_id)
    
    if not cred:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.method == 'DELETE':
            return jsonify({"error": "User not found"}), 404
        return redirect(url_for('main.index'))

    try:
        # 2. Delete the record from the database session
        db.session.delete(cred)
        db.session.commit()
        
        # 3. Handle responses based on request type (AJAX vs Standard Form)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.method == 'DELETE':
            return jsonify({"status": "success", "message": "User successfully removed."}), 200
            
        
    except ValueError:
        db.session.rollback()
        return jsonify({"error": "Database error occurred while deleting user."}), 500        

        
    return redirect(url_for('main.index'))




@main.route("/google-data/<username>/<data_type>", methods=["GET"])
def google_data(username, data_type):
    if username == "all":
        response = {}

        for cred in get_all_fitbit_credentials():
            response[cred.user_id] = list_data_points(cred, data_type)

        return jsonify(response)

    cred = get_user_fitbit_credentials(username)

    if not cred:
        return jsonify({
            "error": "No saved credentials found for {}".format(username)
        }), 404

    return jsonify(list_data_points(cred, data_type))


def get_credentials_for_export(username):
    if username == "all":
        return get_all_fitbit_credentials(), None

    cred = get_user_fitbit_credentials(username)

    if not cred:
        return None, jsonify({
            "error": "No saved credentials found for {}".format(username)
        })

    return [cred], None


def make_csv_response(output, filename):
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename={}".format(filename)
    return response


def format_google_date(date_obj):
    if not date_obj:
        return ""

    return "{:04d}-{:02d}-{:02d}".format(
        date_obj.get("year", 0),
        date_obj.get("month", 0),
        date_obj.get("day", 0)
    )


def format_google_time(time_obj):
    if not time_obj:
        return ""

    return "{:02d}:{:02d}:{:02d}".format(
        time_obj.get("hours", 0),
        time_obj.get("minutes", 0),
        time_obj.get("seconds", 0)
    )


def parse_utc_datetime(timestamp):
    """
    Converts strings like:
    2026-05-26T20:00:00Z
    2026-05-26T20:00:00.123456Z

    into Python datetime objects.
    """
    if not timestamp:
        return None

    cleaned = timestamp.replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def get_export_window():
    """
    Reads optional start/end query parameters.

    Example:
    ?start=2026-05-26T20:00:00Z&end=2026-05-27T21:00:00Z
    """
    start = request.args.get("start")
    end = request.args.get("end")

    if not start or not end:
        return None, None

    return parse_utc_datetime(start), parse_utc_datetime(end)


def timestamp_in_window(timestamp, start_window, end_window):
    """
    For single timestamp data, such as heart-rate samples.
    If no start/end is provided, exports everything.
    """
    if not start_window or not end_window:
        return True

    parsed_time = parse_utc_datetime(timestamp)

    if not parsed_time:
        return False

    return start_window <= parsed_time <= end_window


def interval_overlaps_window(start_time, end_time, start_window, end_window):
    """
    For interval-based data, such as sleep, steps, active zone minutes.
    Keeps the row if any part of the interval overlaps the requested window.
    """
    if not start_window or not end_window:
        return True

    parsed_start = parse_utc_datetime(start_time)
    parsed_end = parse_utc_datetime(end_time)

    if not parsed_start and not parsed_end:
        return False

    if parsed_start and not parsed_end:
        return start_window <= parsed_start <= end_window

    if parsed_end and not parsed_start:
        return start_window <= parsed_end <= end_window

    return parsed_start <= end_window and parsed_end >= start_window


def google_date_in_window(date_obj, start_window, end_window):
    """
    For daily summary data like daily resting heart rate.
    If a time window is provided, keeps dates between the start and end dates.
    """
    if not start_window or not end_window:
        return True

    if not date_obj:
        return False

    try:
        row_date = datetime(
            date_obj.get("year", 0),
            date_obj.get("month", 0),
            date_obj.get("day", 0),
            tzinfo=start_window.tzinfo
        ).date()
    except ValueError:
        return False

    return start_window.date() <= row_date <= end_window.date()


@main.route("/export/<username>/heart-rate", methods=["GET"])
def export_heart_rate(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "sample_time_utc",
        "utc_offset",
        "local_date",
        "local_time",
        "beats_per_minute",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "heart-rate")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            heart_rate = point.get("heartRate", {})
            sample_time = heart_rate.get("sampleTime", {})
            civil_time = sample_time.get("civilTime", {})

            sample_time_utc = sample_time.get("physicalTime")

            if not timestamp_in_window(sample_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                sample_time_utc,
                sample_time.get("utcOffset"),
                format_google_date(civil_time.get("date", {})),
                format_google_time(civil_time.get("time", {})),
                heart_rate.get("beatsPerMinute"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "heart_rate.csv")

@main.route("/export/<username>/oxygen-saturation", methods=["GET"])
def export_oxygen_saturation(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "sample_time_utc",
        "utc_offset",
        "local_date",
        "local_time",
        "oxygenSaturation",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "oxygen-saturation")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            temp_data = point.get("oxygenSaturation", {})
            sample_time =  temp_data.get("sampleTime", {})

            sample_time_utc = sample_time.get("physicalTime")

            if not timestamp_in_window(sample_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                sample_time_utc,
                sample_time.get("utcOffset"),
                temp_data.get("percentage"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "oxygen-saturation.csv")

@main.route("/export/<username>/daily-resting-heart-rate", methods=["GET"])
def export_daily_resting_heart_rate(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "date",
        "beats_per_minute",
        "calculation_method",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "daily-resting-heart-rate")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            rhr = point.get("dailyRestingHeartRate", {})
            metadata = rhr.get("dailyRestingHeartRateMetadata", {})
            rhr_date = rhr.get("date", {})

            if not google_date_in_window(rhr_date, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                format_google_date(rhr_date),
                rhr.get("beatsPerMinute"),
                metadata.get("calculationMethod"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "daily_resting_heart_rate.csv")


@main.route("/export/<username>/steps", methods=["GET"])
def export_steps(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "steps",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "steps")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            steps = point.get("steps", {})
            interval = steps.get("interval", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                steps.get("count"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "steps.csv")


@main.route("/export/<username>/sleep", methods=["GET"])
def export_sleep_episodes(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "episode_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "type",
        "minutes_in_sleep_period",
        "minutes_asleep",
        "minutes_awake",
        "minutes_to_fall_asleep",
        "minutes_after_wakeup",
        "stages_status",
        "processed",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "sleep")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            sleep = point.get("sleep", {})
            interval = sleep.get("interval", {})
            summary = sleep.get("summary", {})
            metadata = sleep.get("metadata", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                point.get("name"),
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                sleep.get("type"),
                summary.get("minutesInSleepPeriod"),
                summary.get("minutesAsleep"),
                summary.get("minutesAwake"),
                summary.get("minutesToFallAsleep"),
                summary.get("minutesAfterWakeUp"),
                metadata.get("stagesStatus"),
                metadata.get("processed"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "sleep_episodes.csv")


@main.route("/export/<username>/sleep-stages", methods=["GET"])
def export_sleep_stages(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "episode_id",
        "stage_start_time_utc",
        "stage_end_time_utc",
        "stage_start_utc_offset",
        "stage_end_utc_offset",
        "stage_type"
    ])

    for cred in creds:
        data = list_data_points(cred, "sleep")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            episode_id = point.get("name")
            sleep = point.get("sleep", {})

            for stage in sleep.get("stages", []):
                stage_start_time_utc = stage.get("startTime")
                stage_end_time_utc = stage.get("endTime")

                if not interval_overlaps_window(stage_start_time_utc, stage_end_time_utc, start_window, end_window):
                    continue

                writer.writerow([
                    cred.user_id,
                    episode_id,
                    stage_start_time_utc,
                    stage_end_time_utc,
                    stage.get("startUtcOffset"),
                    stage.get("endUtcOffset"),
                    stage.get("type")
                ])

    return make_csv_response(output, "sleep_stages.csv")


@main.route("/export/<username>/active-zone-minutes", methods=["GET"])
def export_active_zone_minutes(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "local_date",
        "local_time",
        "heart_rate_zone",
        "active_zone_minutes",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "active-zone-minutes")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            azm = point.get("activeZoneMinutes", {})
            interval = azm.get("interval", {})
            civil_start = interval.get("civilStartTime", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                format_google_date(civil_start.get("date", {})),
                format_google_time(civil_start.get("time", {})),
                azm.get("heartRateZone"),
                azm.get("activeZoneMinutes"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "active_zone_minutes.csv")

@main.route("/export/<username>/sleep-summary", methods=["GET"])
def export_sleep_summary(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "sleep_date",
        "total_sleep_records",
        "total_minutes_in_sleep_period",
        "total_minutes_asleep",
        "total_minutes_awake",
        "total_minutes_to_fall_asleep",
        "total_minutes_after_wakeup",
        "stages_summary",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "sleep")
        data_points = data.get("dataPoints", [])

        daily_summary = {}

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            sleep = point.get("sleep", {})
            interval = sleep.get("interval", {})
            summary = sleep.get("summary", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            # Prefer the civil/local date from the start of the sleep interval if available
            civil_start = interval.get("civilStartTime", {})
            sleep_date = format_google_date(civil_start.get("date", {}))

            # Fallback: use UTC date if civil date is missing
            if not sleep_date and start_time_utc:
                parsed_start = parse_utc_datetime(start_time_utc)
                if parsed_start:
                    sleep_date = parsed_start.date().isoformat()

            if not sleep_date:
                sleep_date = "unknown"

            if sleep_date not in daily_summary:
                daily_summary[sleep_date] = {
                    "total_sleep_records": 0,
                    "total_minutes_in_sleep_period": 0,
                    "total_minutes_asleep": 0,
                    "total_minutes_awake": 0,
                    "total_minutes_to_fall_asleep": 0,
                    "total_minutes_after_wakeup": 0,
                    "stages_summary": [],
                    "device": device.get("displayName"),
                    "platform": data_source.get("platform"),
                    "recording_method": data_source.get("recordingMethod")
                }

            daily_summary[sleep_date]["total_sleep_records"] += 1
            daily_summary[sleep_date]["total_minutes_in_sleep_period"] += int(summary.get("minutesInSleepPeriod") or 0)
            daily_summary[sleep_date]["total_minutes_asleep"] += int(summary.get("minutesAsleep") or 0)
            daily_summary[sleep_date]["total_minutes_awake"] += int(summary.get("minutesAwake") or 0)
            daily_summary[sleep_date]["total_minutes_to_fall_asleep"] += int(summary.get("minutesToFallAsleep") or 0)
            daily_summary[sleep_date]["total_minutes_after_wakeup"] += int(summary.get("minutesAfterWakeUp") or 0)

            if summary.get("stagesSummary"):
                daily_summary[sleep_date]["stages_summary"].append(summary.get("stagesSummary"))

        for sleep_date, summary_row in sorted(daily_summary.items()):
            writer.writerow([
                cred.user_id,
                sleep_date,
                summary_row["total_sleep_records"],
                summary_row["total_minutes_in_sleep_period"],
                summary_row["total_minutes_asleep"],
                summary_row["total_minutes_awake"],
                summary_row["total_minutes_to_fall_asleep"],
                summary_row["total_minutes_after_wakeup"],
                json.dumps(summary_row["stages_summary"]),
                summary_row["device"],
                summary_row["platform"],
                summary_row["recording_method"]
            ])

    return make_csv_response(output, "sleep_summary.csv")

@main.route("/export/<username>/daily-heart-rate-variability", methods=["GET"])
def export_hrv(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "date",
        "averageHeartRateVariabilityMilliseconds",
        "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds",
        "entropy",
        "nonRemHeartRateBeatsPerMinute",        
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "daily-heart-rate-variability")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            hrv_data = point.get("dailyHeartRateVariability", {})
            data_date = hrv_data.get("date", {})

            if not google_date_in_window(data_date, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                format_google_date(data_date),
                hrv_data.get("averageHeartRateVariabilityMilliseconds"),
                hrv_data.get("deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds"),
                hrv_data.get("entropy"),
                hrv_data.get("nonRemHeartRateBeatsPerMinute"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "daily-heart-rate-variability.csv")


@main.route("/export/<username>/daily-respiratory-rate", methods=["GET"])
def export_respiratory_rate(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "date",
        "breathsPerMinute",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "daily-respiratory-rate")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            # Try common keys for breathing rate
            resp_data = point.get("dailyRespiratoryRate", {})
            data_date = resp_data.get("date", {})

            if not google_date_in_window(data_date, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                format_google_date(data_date),
                resp_data.get("breathsPerMinute"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "daily-respiratory-rate.csv")

#does not work - but no core temp with Sense2 so not working on it
@main.route("/export/<username>/core-body-temperature", methods=["GET"])
def export_temperature(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "date",
        "temperature_value",
        "temperature_type",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        # Often registered as body-temperature or skin-temperature
        data = list_data_points(cred, "core-body-temperature") 
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            temp_data = point.get("core-body-temperature", {})
            data_date = temp_data.get("date", {})

            if not google_date_in_window(data_date, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                format_google_date(data_date),
                temp_data.get("value"),
                temp_data.get("type", "unknown"), # e.g., 'core' vs 'skin'
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "core-body-temperature.csv")


@main.route("/export/<username>/daily-sleep-temperature-derivations", methods=["GET"])
def export_sleep_temperature(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "date",
        "nightlyTemperatureCelsius",
        "baselineTemperatureCelsius",
        "relativeNightlyStddev30dCelsius",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        # Often registered as body-temperature or skin-temperature
        data = list_data_points(cred, "daily-sleep-temperature-derivations") 
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            temp_data = point.get("dailySleepTemperatureDerivations", {})
            data_date = temp_data.get("date", {})

            if not google_date_in_window(data_date, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                format_google_date(data_date),
                temp_data.get("nightlyTemperatureCelsius"),
                temp_data.get("baselineTemperatureCelsius"),
                temp_data.get("relativeNightlyStddev30dCelsius"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "daily-sleep-temperature-derivations.csv")


@main.route("/export/<username>/altitude", methods=["GET"])
def export_altitude(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "gainMillimeters",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "altitude")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            altitude = point.get("altitude", {})
            interval = altitude.get("interval", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                altitude.get("gainMillimeters"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "altitude.csv")


@main.route("/export/<username>/activity-level", methods=["GET"])
def export_activity_level(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "activityLevelType",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "activity-level")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            exercise = point.get("activityLevel", {})
            interval = exercise.get("interval", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                exercise.get("activityLevelType"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "activity-level.csv")

@main.route("/export/<username>/distance", methods=["GET"])
def export_distance(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "millimeters",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "distance")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            distance = point.get("distance", {})
            interval = distance.get("interval", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                distance.get("millimeters"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "distance.csv")


@main.route("/export/<username>/exercise", methods=["GET"])
def export_exercise(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "activeDuration",
        "createTime",
        "exerciseType",
        "activeZoneMinutes",        
        "averageHeartRateBeatsPerMinute",  
        "caloriesKcal",
        "lightTime",
        "moderateTime",
        "peakTime",
        "vigorousTime",       
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "exercise")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            exercise = point.get("exercise", {})
            interval = exercise.get("interval", {})
            metricsSummary = exercise.get("metricsSummary", {})
            heartRateZoneDurations = metricsSummary.get("heartRateZoneDurations", {})
            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                exercise.get("activeDuration"),
                exercise.get("createTime"),
                exercise.get("exerciseType"),
                metricsSummary.get("activeZoneMinutes"),                
                metricsSummary.get("averageHeartRateBeatsPerMinute"),                     
                metricsSummary.get("caloriesKcal"),
                heartRateZoneDurations.get("lightTime"),
                heartRateZoneDurations.get("moderateTime"),
                heartRateZoneDurations.get("peakTime"),
                heartRateZoneDurations.get("vigorousTime"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "exercise.csv")




@main.route("/export/<username>/daily-oxygen-saturation", methods=["GET"])
def export_daily_oxygen_saturation(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "date",
        "averagePercentage",
        "lowerBoundPercentage",
        "standardDeviationPercentage",
        "upperBoundPercentage",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        # Often registered as body-temperature or skin-temperature
        data = list_data_points(cred, "daily-oxygen-saturation") 
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            temp_data = point.get("dailyOxygenSaturation", {})
            data_date = temp_data.get("date", {})

            if not google_date_in_window(data_date, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                format_google_date(data_date),
                temp_data.get("averagePercentage"),
                temp_data.get("lowerBoundPercentage"),
                temp_data.get("standardDeviationPercentage"),
                temp_data.get("upperBoundPercentage"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "daily-oxygen-saturation.csv")

@main.route("/export/<username>/respiratory-rate-sleep-summary", methods=["GET"])
def export_respiratory_rate_sleep_summary(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "date",
        "date_offset",
        "deepSleepStats_breathsPerMinute",
        "deepSleepStats_signalToNoise",
        "deepSleepStats_standardDeviation",
        "fullSleepStats_breathsPerMinute",
        "fullSleepStats_signalToNoise",
        "fullSleepStats_standardDeviation",
        "lightSleepStats_breathsPerMinute",
        "lightSleepStats_signalToNoise",
        "lightSleepStats_standardDeviation",
        "remSleepStats_breathsPerMinute",
        "remSleepStats_signalToNoise",
        "remSleepStats_standardDeviation",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        # Often registered as body-temperature or skin-temperature
        data = list_data_points(cred, "respiratory-rate-sleep-summary") 
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            temp_data = point.get("respiratoryRateSleepSummary", {})
            sample_time = temp_data.get("sampleTime", {})         
            sample_time_utc = sample_time.get("physicalTime")
            if not timestamp_in_window(sample_time_utc, start_window, end_window):
                continue         
            
            deepSleepStats = temp_data.get("deepSleepStats", {}) 
            fullSleepStats = temp_data.get("fullSleepStats", {}) 
            lightSleepStats = temp_data.get("lightSleepStats", {}) 
            remSleepStats = temp_data.get("remSleepStats", {}) 
            
            writer.writerow([
                cred.user_id,
                sample_time_utc,
                sample_time.get("utcOffset"),
                deepSleepStats.get("breathsPerMinute"),
                deepSleepStats.get("signalToNoise"),
                deepSleepStats.get("standardDeviation"),
                fullSleepStats.get("breathsPerMinute"),
                fullSleepStats.get("signalToNoise"),
                fullSleepStats.get("standardDeviation"),
                lightSleepStats.get("breathsPerMinute"),
                lightSleepStats.get("signalToNoise"),
                lightSleepStats.get("standardDeviation"),
                remSleepStats.get("breathsPerMinute"),
                remSleepStats.get("signalToNoise"),
                remSleepStats.get("standardDeviation"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "respiratory-rate-sleep-summary.csv")


@main.route("/export/<username>/sedentary-period", methods=["GET"])
def export_sedentar_period(username):
    creds, error = get_credentials_for_export(username)

    if error:
        return error, 404

    start_window, end_window = get_export_window()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "user_id",
        "start_time_utc",
        "end_time_utc",
        "start_utc_offset",
        "end_utc_offset",
        "device",
        "platform",
        "recording_method"
    ])

    for cred in creds:
        data = list_data_points(cred, "sedentary-period")
        data_points = data.get("dataPoints", [])

        for point in data_points:
            data_source = point.get("dataSource", {})
            device = data_source.get("device", {})

            steps = point.get("sedentaryPeriod", {})
            interval = steps.get("interval", {})

            start_time_utc = interval.get("startTime")
            end_time_utc = interval.get("endTime")

            if not interval_overlaps_window(start_time_utc, end_time_utc, start_window, end_window):
                continue

            writer.writerow([
                cred.user_id,
                start_time_utc,
                end_time_utc,
                interval.get("startUtcOffset"),
                interval.get("endUtcOffset"),
                device.get("displayName"),
                data_source.get("platform"),
                data_source.get("recordingMethod")
            ])

    return make_csv_response(output, "sedentary-period.csv")




