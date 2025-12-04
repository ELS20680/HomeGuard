import os
import requests
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv
from datetime import datetime, timedelta
from Adafruit_IO import Client, Feed, Data

# ----------------------------------------------------------------------
# 1. CONFIGURATION AND ENVIRONMENT LOADING
# ----------------------------------------------------------------------

# Load .env for local development only. Render ignores this file.
load_dotenv()

app = Flask(__name__)

# --- Essential Configuration Check: Adafruit IO ---
# Load credentials and strictly check for existence.
AIO_USERNAME = os.getenv("ADAFRUIT_IO_USERNAME")
AIO_KEY = os.getenv("ADAFRUIT_IO_KEY")

if not AIO_USERNAME or not AIO_KEY:
    raise ValueError("Missing Adafruit IO credentials in env variables.")

# Initialize Adafruit IO client
aio = Client(AIO_USERNAME, AIO_KEY)

# --- Essential Configuration Check: Database ---
DATABASE_URL = os.getenv("NEON_DATABASE_URL")

# --- Centralized Configuration Validation ---
missing_vars = []
if not AIO_USERNAME:
    missing_vars.append("ADAFRUIT_IO_USERNAME")
if not AIO_KEY:
    missing_vars.append("ADAFRUIT_IO_KEY")

# Check for NEON_DATABASE_URL OR the individual components (if the URL isn't used)
if not DATABASE_URL:
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT", "5432")
    
    # If the URL is missing, we check if the components are present to build it
    if not all([DB_NAME, DB_USER, DB_PASSWORD, DB_HOST]):
        # If components are also missing, report the main URL variable as missing
        missing_vars.append("NEON_DATABASE_URL (or DB_NAME/USER/PASSWORD/HOST)")
    else:
        # If components exist, construct the URL for use later
        DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"

# If any critical variables are missing, the app MUST NOT start.
if missing_vars:
    error_message = (
        f"CRITICAL ERROR: The following required environment variables were not found: "
        f"{', '.join(missing_vars)}. "
        f"Please set these securely in your Render dashboard under Environment Variables."
    )
    # Raising a ValueError during startup will cause the Render build/deploy to fail,
    # and the error message will be visible in the service logs.
    raise ValueError(error_message)


# Required Adafruit IO Feed Keys
FEEDS = {
    # Live readings
    'temp': 'temperature',
    'humid': 'humidity',
    'motion': 'motion',

    # Output controls
    'ctrl_light': 'led-status',      # Room light LED ON/OFF
    'ctrl_buzzer': 'buzzer',         # Buzzer (0/1)
    'ctrl_lcd': 'lcd-message',       # LCD text display
    'ctrl_mode': 'system-mode',      # Security mode ARMED/DISARMED
    'image': 'camera-image',         # Camera capture feed

    # Optional logging feeds if you want to use them later
    'log_image_path': 'log-image-path',
    'log_motion_event': 'log-motion-event'
}

# ----------------------------------------------------------------------
# 2. HELPER FUNCTIONS: ADAFRUIT IO API INTERACTION
# ----------------------------------------------------------------------

def fetch_aio_feed_data(feed_key):
    """
    Fetches the latest value for a specific Adafruit IO feed.
    """
    # Use the now guaranteed-to-exist AIO_USERNAME and AIO_KEY
    url = f"https://io.adafruit.com/api/v2/{AIO_USERNAME}/feeds/{feed_key}/data/last"
    headers = {'X-AIO-Key': AIO_KEY}
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        
        # Return the value and the timestamp (ISO 8601 format)
        return data.get('value', 'N/A'), data.get('created_at', 'N/A')
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching feed {feed_key}: {e}")
        # Return a consistent error value
        return 'ERR', 'N/A'

def send_control_command(feed_key, value):
    """
    Sends a command (new value) to an Adafruit IO control feed.
    """
    url = f"https://io.adafruit.com/api/v2/{AIO_USERNAME}/feeds/{feed_key}/data"
    headers = {'X-AIO-Key': AIO_KEY, 'Content-Type': 'application/json'}
    payload = {'value': value}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        response.raise_for_status()
        return True, f"Command '{value}' successfully sent to feed '{feed_key}'."
    except requests.exceptions.HTTPError as e:
        status_code = response.status_code if response else 'N/A'
        return False, f"Adafruit IO API Error ({status_code}): Failed to send command to '{feed_key}'. Details: {e}"
    except requests.exceptions.RequestException as e:
        return False, f"Network Error: Could not connect to Adafruit IO. Details: {e}"

# ----------------------------------------------------------------------
# 3. HELPER FUNCTIONS: DATABASE INTERACTION (POSTGRESQL)
# ----------------------------------------------------------------------

def get_db_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    try:
        # Use the NEON_DATABASE_URL for connection
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def fetch_sensor_data_by_date(date_str, sensor_type):
    conn = get_db_connection()
    if not conn:
        return [], "Could not connect to the database."

    column = 'temp_c' if sensor_type == 'temperature' else 'humidity_pct'

    query = f"""
        SELECT ts_iso, {column}
        FROM environmental_data
        WHERE ts_iso::date = %s
        ORDER BY ts_iso ASC;
    """

    data = []
    error = None
    try:
        with conn.cursor() as cur:
            cur.execute(query, (date_str,))
            rows = cur.fetchall()

            for ts_iso, value in rows:
                data.append({
                    "time": ts_iso.strftime("%H:%M:%S"),
                    "value": float(value) if value else None
                })

    except Exception as e:
        error = f"Database query failed: {e}"

    finally:
        conn.close()

    return data, error

def fetch_motion_logs_by_date(date_str):
    conn = get_db_connection()
    if not conn:
        return [], "Could not connect to the database."

    query = """
        SELECT ts_iso, system_mode, image_path
        FROM motion_events
        WHERE ts_iso::date = %s
        ORDER BY ts_iso ASC;
    """

    logs = []
    error = None

    try:
        with conn.cursor() as cur:
            cur.execute(query, (date_str,))
            rows = cur.fetchall()

            for ts_iso, mode, image_path in rows:
                logs.append({
                    "timestamp": ts_iso.strftime("%Y-%m-%d %H:%M:%S"),
                    "details": mode,
                    "image_path": image_path or "No image available"
                })

    except Exception as e:
        error = f"Database query failed: {e}"

    finally:
        conn.close()

    return logs, error
    
# ----------------------------------------------------------------------
# 4. FLASK ROUTES
# ----------------------------------------------------------------------

# --- View Routes ---

@app.route('/')
def home():
    """Dashboard view showing live sensor data and system status."""
    
    # 1. Fetch live data from Adafruit IO feeds
    temp_val, _ = fetch_aio_feed_data(FEEDS['temp'])
    humid_val, _ = fetch_aio_feed_data(FEEDS['humid'])
    motion_val, last_motion_time = fetch_aio_feed_data(FEEDS['motion'])
    mode_val, _ = fetch_aio_feed_data(FEEDS['ctrl_mode'])
    
    # 2. Determine last motion timestamp for display
    # Check if last_motion_time is 'N/A' or if motion_val is not '1'
    if motion_val == '1' and last_motion_time != 'N/A':
        # Reformat timestamp for user display (e.g., "HH:MM:SS on MMM DD")
        try:
            # Parse the ISO timestamp string
            dt_object = datetime.fromisoformat(last_motion_time.replace('Z', '+00:00'))
            last_motion_display = dt_object.strftime('%H:%M:%S on %b %d')
        except ValueError:
            last_motion_display = "Timestamp Invalid"
    else:
        last_motion_display = "No recent motion detected"

    # 3. Compile data dictionary for template
    live_data = {
        'temperature': temp_val,
        'humidity': humid_val,
        'motion': motion_val, # '1' for detected, '0' for clear, 'ERR' for failed fetch
    }
    
    # 'ARMED', 'DISARMED', or 'ERR'
    system_mode = mode_val if mode_val in ['ARMED', 'DISARMED'] else 'N/A'


    return render_template('home.html', 
                           live_data=live_data,
                           system_mode=system_mode,
                           last_motion_time=last_motion_display)

@app.route('/dbtest')
def dbtest():
    try:
        conn = get_db_connection()
        if conn:
            return "Connected successfully!"
        else:
            return "Connection failed."
    except Exception as e:
        return str(e)

@app.route('/environmental', methods=['GET', 'POST'])
def environmental_data():
    selected_date = None
    selected_sensor = None
    chart_data = None
    plot_error = None

    if request.method == 'POST':
        selected_date = request.form.get('date')
        selected_sensor = request.form.get('sensor')

        if selected_sensor == "temperature":
            db_column = "temp_c"
        elif selected_sensor == "humidity":
            db_column = "humidity_pct"
        else:
            plot_error = "Invalid sensor selection"
            db_column = None

        if not selected_date or not selected_sensor:
            plot_error = "Please select a date and sensor."
        else:
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()

                cur.execute(f"""
                    SELECT ts_iso, {db_column}
                    FROM environmental_data
                    WHERE ts_iso::date = %s
                    ORDER BY ts_iso ASC;
                """, (selected_date,))

                rows = cur.fetchall()
                cur.close()
                conn.close()

                if len(rows) == 0:
                    plot_error = f"No data found for {selected_sensor} on {selected_date}."
                else:
                    labels = [row[0].strftime("%H:%M") for row in rows]
                    dataset_values = [float(row[1]) for row in rows]

                    label_name = "Temperature (°C)" if selected_sensor == "temp_c" else "Humidity (%)"
                    color = "rgba(0, 122, 255, 1)" if selected_sensor == "temp_c" else "rgba(52, 199, 89, 1)"

                    chart_data = {
                        "labels": labels,
                        "datasets": [{
                            "label": label_name,
                            "data": dataset_values,
                            "borderColor": color,
                            "backgroundColor": color.replace("1)", "0.2)")
                        }]
                    }

            except Exception as e:
                plot_error = f"Database query failed: {e}"

    return render_template(
        'environmental.html',
        selected_date=selected_date,
        selected_sensor=selected_sensor,
        chart_data=chart_data,
        plot_error=plot_error
    )

@app.route('/manage_security', methods=['GET', 'POST'])
def manage_security():
    """Security management view: control system mode and view intrusion logs."""
    
    status_msg = None
    log_error = None
    intrusion_logs = []
    # Default to today's date for log fetching
    selected_log_date = datetime.now().strftime('%Y-%m-%d')
    
    # Handle POST requests (Arm/Disarm or Log Fetch)
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

@app.route('/control')
def device_control():
    """Device control view: buttons for light, buzzer, LCD."""
    return render_template('device_control.html')

@app.route('/about')
def about():
    """Project information view."""
    return render_template('about.html')


# --- API Endpoint for Device Control ---

@app.route('/api/control/<device>', methods=['POST'])
def api_control(device):
    data = request.get_json()

    if not data or 'value' not in data:
        return jsonify({"message": "Missing 'value' in JSON body"}), 400

    value = data['value']

    # Map frontend names → actual FEED keys
    feed_key_name = {
        'light': 'ctrl_light',
        'buzzer': 'ctrl_buzzer',
        'mode': 'ctrl_mode',
        'lcd_text': 'ctrl_lcd',
        'camera': 'image'
    }.get(device)

    feed_key = FEEDS.get(feed_key_name)

    if not feed_key:
        return jsonify({"message": f"Unknown device: {device}"}), 400
    
    # Now publish to Adafruit
    try:
        aio.send(feed_key, value)
        return jsonify({"message": f"{device} updated to {value}"}), 200
    except Exception as e:
        return jsonify({"message": str(e)}), 500


if __name__ == '__main__':
    # Flask will automatically use the PORT environment variable if deployed
    app.run(debug=True, port=os.getenv("PORT", 5000))
