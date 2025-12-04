import os
import requests
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load environment variables from .env file (Kept for other variables like DB URL)
load_dotenv()

# --- CONFIGURATION ---
app = Flask(__name__)

# Load credentials from .env OR use hardcoded fallback.
# !!! IMPORTANT: REPLACE THE PLACEHOLDER STRINGS BELOW WITH YOUR ACTUAL CREDENTIALS !!!
AIO_USERNAME = os.getenv("ADAFRUIT_IO_USERNAME") or "elias_larhdaf"
AIO_KEY = os.getenv("ADAFRUIT_IO_KEY") or "aio_LYQR355wLPpdFMZNuU2ryVObwnkO"
# !!! END OF HARDCODED SECTION !!!

# Using the standard connection string format for NEON
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL")
# If NEON_DATABASE_URL is not set, try to construct from individual DB settings
if not NEON_DATABASE_URL:
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT", "5432")
    if all([DB_NAME, DB_USER, DB_PASSWORD, DB_HOST]):
        NEON_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Constant for configuration failure
AIO_CONFIG_ERROR_MSG = "CONFIG ERROR"

# Critical check for Adafruit IO configuration
if AIO_USERNAME == "YOUR_AIO_USERNAME" or AIO_KEY == "YOUR_AIO_KEY":
    print("\n!!! CRITICAL WARNING: Hardcoded Adafruit IO placeholders are still present. Dashboard will show 'CONFIG ERROR'. !!!\n")
    AIO_HEADERS = {}
elif not AIO_USERNAME or not AIO_KEY:
    print("\n!!! CRITICAL WARNING: Adafruit IO credentials are not fully configured. Dashboard will show 'CONFIG ERROR'. !!!\n")
    AIO_HEADERS = {}
else:
    AIO_HEADERS = {"X-AIO-Key": AIO_KEY}


# Feeds used by the application
FEEDS = {
    "temperature": "temperature",
    "humidity": "humidity",
    "motion": "motion",
    "ctrl_light": "led-status",
    "ctrl_lcd_text": "lcd-message",
    "ctrl_mode": "system-mode", # This feed holds the ARMED/DISARMED status
    "ctrl_buzzer": "buzzer",
    "ctrl_camera": "camera-image"

}

# Database sensor and column mapping (Used for environmental data plotting)
DB_SENSOR_MAP = {
    'temperature': 'temp_c',
    'humidity': 'humidity_pct',
}

# --- HELPER FUNCTIONS: Adafruit IO Interactions ---

def send_control_command(feed_name, value):
    """Sends a control command to a specified Adafruit IO feed."""
    # Safety check for credentials
    if AIO_USERNAME == "YOUR_AIO_USERNAME" or AIO_KEY == "YOUR_AIO_KEY" or not AIO_USERNAME or not AIO_KEY:
        return False, f"AIO {AIO_CONFIG_ERROR_MSG}: Update credentials in app.py or .env."

    # Note: Using AIO_USERNAME from the .env file
    url = f"https://io.adafruit.com/api/v2/{AIO_USERNAME}/feeds/{feed_name}/data"
    payload = {"value": value}

    try:
        response = requests.post(url, headers=AIO_HEADERS, json=payload, timeout=5)
        response.raise_for_status() # Raises an HTTPError for bad responses (4xx or 5xx)
        return True, f"Command sent successfully to '{feed_name}'."
    except requests.exceptions.HTTPError as e:
        # A 403 or 404 here usually means bad feed name or API key
        return False, f"AIO HTTP Error: Check Feed Name or API Key (Status: {e.response.status_code})"
    except requests.exceptions.RequestException as e:
        # Catch other network/request issues
        return False, f"Network Error: Could not connect to Adafruit IO."

def fetch_live_data():
    """Fetches the latest value for Temperature, Humidity, and Motion from Adafruit IO via HTTP."""
    data = {}

    # Safety check for credentials
    if AIO_USERNAME == "YOUR_AIO_USERNAME" or AIO_KEY == "YOUR_AIO_KEY" or not AIO_USERNAME or not AIO_KEY:
        for feed_key in ["temperature", "humidity", "motion"]:
             data[feed_key] = AIO_CONFIG_ERROR_MSG # Indicate configuration failure
        return data

    for feed_key in ["temperature", "humidity", "motion"]:
        feed_name = FEEDS.get(feed_key)
        if not feed_name:
            data[feed_key] = "FEED NAME MISSING"
            continue

        # Note: Using AIO_USERNAME from the .env file
        url = f"https://io.adafruit.com/api/v2/{AIO_USERNAME}/feeds/{feed_name}/data/last"

        try:
            response = requests.get(url, headers=AIO_HEADERS, timeout=5)
            response.raise_for_status()

            # Adafruit IO returns a JSON object with a 'value' key for the last data point
            result = response.json()
            value = result.get('value', 'N/A')

            data[feed_key] = value

        except requests.exceptions.RequestException:
            # On any failure (network or API), return 'NETWORK ERROR'
            data[feed_key] = "NETWORK ERROR"

    return data

def fetch_system_mode():
    """Fetches the current system mode (ARMED/DISARMED) from the control feed."""
    # Safety check for credentials
    if AIO_USERNAME == "YOUR_AIO_USERNAME" or AIO_KEY == "YOUR_AIO_KEY" or not AIO_USERNAME or not AIO_KEY:
        return AIO_CONFIG_ERROR_MSG # Indicate configuration failure

    feed_name = FEEDS.get("ctrl_mode")
    if not feed_name:
        return "UNKNOWN"

    url = f"https://io.adafruit.com/api/v2/{AIO_USERNAME}/feeds/{feed_name}/data/last"

    try:
        response = requests.get(url, headers=AIO_HEADERS, timeout=5)
        response.raise_for_status()
        result = response.json()
        # The value should be 'ARMED' or 'DISARMED'
        return result.get('value', 'UNKNOWN').upper()
    except requests.exceptions.RequestException:
        # Return a safe, known error state if fetch fails
        return "NETWORK ERROR"

# --- NEW HELPER FUNCTION: Fetching Last Motion Time ---

def fetch_most_recent_motion_log():
    """Fetches the timestamp of the single, most recent motion=TRUE log entry."""
    if not NEON_DATABASE_URL:
        return "DB Error: Not Configured"

    try:
        conn = psycopg2.connect(NEON_DATABASE_URL)
        cursor = conn.cursor()

        # Query only motion = TRUE logs, ordered by timestamp descending, limit to 1
        cursor.execute("""
            SELECT ts_iso
            FROM sensor_data
            WHERE motion = TRUE
            ORDER BY ts_iso DESC
            LIMIT 1;
        """)

        result = cursor.fetchone()

        cursor.close()
        conn.close()

        if result:
            ts = result[0]
            # Convert timestamp (can be string or datetime object) to formatted string
            if isinstance(ts, str):
                # Handle ISO format conversion
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except ValueError:
                    # Handle other potential formats if ISO conversion fails
                    return "Date Format Error"

            # Return a friendly, relative-time-style format (e.g., 'Dec 3, 2025 at 10:30 AM')
            return ts.strftime('%b %d, %Y at %I:%M %p')
        else:
            return "Never Detected" # No motion logs found

    except psycopg2.Error as e:
        print(f"[DB ERROR] Failed to fetch most recent motion log: {e}")
        return "DB Error: Could Not Fetch"

# --- HELPER FUNCTIONS: Database Interactions (Existing) ---

def fetch_motion_logs_by_date(target_date):
    """
    Fetches motion logs for a specific date and filters them to display events
    separated by a minimum interval (2 minutes) to debounce rapid events.
    """
    if not NEON_DATABASE_URL:
        return None, "Database connection not configured (NEON_DATABASE_URL is missing)."

    raw_logs = []

    # Define start and end timestamps for the target date
    start_ts = f"{target_date} 00:00:00"
    end_ts = f"{target_date} 23:59:59"

    try:
        conn = psycopg2.connect(NEON_DATABASE_URL)
        cursor = conn.cursor()

        # Query only motion = TRUE logs, ordered by timestamp ascending
        cursor.execute("""
            SELECT ts_iso, image_path
            FROM sensor_data
            WHERE motion = TRUE AND ts_iso BETWEEN %s AND %s
            ORDER BY ts_iso ASC;
        """, (start_ts, end_ts))

        results = cursor.fetchall()

        for row in results:
            ts, image_path = row
            # If ts is a string, convert it to datetime
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))

            raw_logs.append({
                'datetime_obj': ts, # Keep datetime object for comparison
                'timestamp': ts.strftime('%Y-%m-%d %H:%M:%S'),
                'image_path': image_path if image_path else "No image recorded"
            })

        cursor.close()
        conn.close()

        # --- Post-processing: Filter Logs by Time Interval (2 minutes) ---

        # Define the minimum interval between reported events (2 minutes)
        MIN_INTERVAL = timedelta(minutes=2)

        # We process the logs in ascending order (as fetched)
        filtered_logs = []
        last_reported_time = None

        for log in raw_logs:
            current_time = log['datetime_obj']

            # If this is the first log, or if the current log is past the minimum interval
            # since the last reported log, then report it.
            if last_reported_time is None or (current_time - last_reported_time) >= MIN_INTERVAL:
                # Remove the temporary 'datetime_obj' before adding to the final list
                log.pop('datetime_obj')
                filtered_logs.append(log)
                last_reported_time = current_time

        # The frontend expects logs in descending order (most recent first) for display
        return filtered_logs[::-1], None

    except psycopg2.Error as e:
        print(f"[DB ERROR] Failed to fetch motion logs: {e}")
        return None, f"Database query failed: {e}"

def fetch_historical_data(sensor, target_date):
    """Fetches historical data for a given sensor and date from the cloud database."""
    if not NEON_DATABASE_URL:
        return None, "Database connection not configured."

    column = DB_SENSOR_MAP.get(sensor)
    if not column:
        return None, "Invalid sensor selected."

    # Define start and end timestamps for the target date
    start_ts = f"{target_date} 00:00:00"
    end_ts = f"{target_date} 23:59:59"

    try:
        conn = psycopg2.connect(NEON_DATABASE_URL)
        cursor = conn.cursor()

        # Select timestamp and sensor value
        cursor.execute(f"""
            SELECT ts_iso, {column}
            FROM sensor_data
            WHERE {column} IS NOT NULL AND ts_iso BETWEEN %s AND %s
            ORDER BY ts_iso ASC;
        """, (start_ts, end_ts))

        results = cursor.fetchall()

        cursor.close()
        conn.close()

        # Prepare data for Chart.js
        labels = []
        data_points = []

        for row in results:
            ts_iso, value = row
            # Convert string timestamp to datetime object for formatting
            if isinstance(ts_iso, str):
                try:
                    ts = datetime.fromisoformat(ts_iso.replace('Z', '+00:00'))
                except ValueError:
                    ts = datetime.strptime(ts_iso, '%Y-%m-%d %H:%M:%S.%f')
            else:
                ts = ts_iso # It's already a datetime object

            labels.append(ts.strftime('%H:%M'))
            data_points.append(value)


        # Determine the unit/label for the chart
        if sensor == 'temperature':
            label = 'Temperature (°C)'
            color = 'rgba(255, 99, 132, 1)'
        else:
            label = 'Humidity (%)'
            color = 'rgba(54, 162, 235, 1)'

        chart_data = {
            'labels': labels,
            'datasets': [{
                'label': label,
                'data': data_points,
                'borderColor': color,
                'backgroundColor': f'{color}0.5', # Light background fill
                'fill': False,
                'tension': 0.1
            }]
        }

        return chart_data, None

    except psycopg2.Error as e:
        print(f"[DB ERROR] Failed to fetch historical data: {e}")
        return None, f"Database query failed: {e}"

# --- FLASK ROUTES ---

@app.route('/')
def home():
    live_data = fetch_live_data()
    system_mode = fetch_system_mode() # Fetch the ARMED/DISARMED status
    last_motion_time = fetch_most_recent_motion_log() # NEW: Fetch the last motion event time

    # Pass live sensor data, system mode, and last motion time to the template
    return render_template('home.html',
                           live_data=live_data,
                           system_mode=system_mode,
                           last_motion_time=last_motion_time)

@app.route('/about')
def about():
    """Renders the About page template, fixing the BuildError from base.html."""
    return render_template('about.html')

@app.route('/device_control')
def device_control():
    return render_template('device_control.html')

@app.route('/api/control/<device>', methods=['POST'])
def device_control_api(device):
    data = request.json
    value = data.get('value', '').strip()
    
    feed_name = FEEDS.get(f'ctrl_{device}')
    
    if not feed_name:
        return jsonify({"success": False, "message": "Invalid device"}), 400

    # Handle LCD Text input validation
    if device == 'lcd_text':
        MAX_LENGTH = 32
        if len(value) > MAX_LENGTH:
             return jsonify({"success": False, "message": f"Text must be {MAX_LENGTH} characters or less."}), 400
        # For LCD, the value is the text itself
        control_value = value
        
    elif device == 'mode':
        # Expects 'ON' (ARMED) or 'OFF' (DISARMED) from button state in device_control.html
        control_value = 'ARMED' if value.lower() == 'on' else 'DISARMED'

    elif device == 'buzzer':
        control_value = '1'  # Just trigger the buzzer
        
    elif device == 'camera':
        control_value = '1'  # Just trigger the camera

    else: # Light (ON/OFF)
        control_value = value.upper()

    success, msg = send_control_command(feed_name, control_value)
    
    if success:
        message = f"{device.replace('_', ' ').capitalize()} set to '{control_value}'" if device == 'lcd_text' else f"{device.capitalize()} set to {control_value}"
        # Special case messages for momentary controls
        if device == 'buzzer':
            return jsonify({"success": True, "message": "Buzzer triggered!"})
        if device == 'camera':
            return jsonify({"success": True, "message": "Photo capture initiated!"})
            
        return jsonify({"success": True, "message": message})
    else:
        # If there's an error message from send_control_command, return it
        return jsonify({"success": False, "message": msg}), 500

@app.route('/environmental_data', methods=['GET', 'POST'])
def environmental_data():
    chart_data = None
    selected_date = datetime.now().strftime('%Y-%m-%d')
    selected_sensor = 'temperature' # Default to temperature for initial view
    plot_error = None
    
    if request.method == 'POST':
        selected_date = request.form.get('date')
        selected_sensor = request.form.get('sensor')
        
        if selected_date and selected_sensor:
            chart_data, plot_error = fetch_historical_data(selected_sensor, selected_date)
        else:
            plot_error = "Please select both a date and a sensor."
            
    return render_template('environmental.html', 
                           chart_data=chart_data, 
                           selected_date=selected_date, 
                           selected_sensor=selected_sensor,
                           plot_error=plot_error)

@app.route('/manage_security', methods=['GET', 'POST'])
def manage_security():
    status_msg = None
    intrusion_logs = []
    log_error = None
    # Default to today's date for log viewing
    selected_log_date = datetime.now().strftime('%Y-%m-%d')
    
    if request.method == 'POST':
        action = request.form.get('action')
        log_date = request.form.get('date')

        if action in ['arm', 'disarm']:
            # Handle security mode change
            mode = 'ARMED' if action == 'arm' else 'DISARMED'
            feed_key = FEEDS.get('ctrl_mode')
            if feed_key:
                success, msg = send_control_command(feed_key, mode)
                status_msg = msg
        
        # If a date was submitted (for log retrieval)
        if log_date:
            selected_log_date = log_date
            intrusion_logs, log_error = fetch_motion_logs_by_date(selected_log_date)
        else:
            # If no date was explicitly submitted, try to fetch logs for the default/current date
            intrusion_logs, log_error = fetch_motion_logs_by_date(selected_log_date)


    # Initial GET request handling or if only mode was changed: 
    # Try to fetch today's logs automatically for initial load/view
    if not request.method == 'POST' or (request.method == 'POST' and not request.form.get('date')):
        intrusion_logs, log_error = fetch_motion_logs_by_date(selected_log_date)

    return render_template('manage_security.html', 
                           status_msg=status_msg, 
                           intrusion_logs=intrusion_logs,
                           log_error=log_error,
                           selected_log_date=selected_log_date)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
