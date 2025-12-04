import os
import requests
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv
from datetime import datetime, timedelta

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

# --- Essential Configuration Check: Database ---
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL")

# --- Centralized Configuration Validation ---
missing_vars = []
if not AIO_USERNAME:
    missing_vars.append("ADAFRUIT_IO_USERNAME")
if not AIO_KEY:
    missing_vars.append("ADAFRUIT_IO_KEY")

aio = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)

# Check for NEON_DATABASE_URL OR the individual components (if the URL isn't used)
if not NEON_DATABASE_URL:
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
        NEON_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"

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
    'temp': 'temp',
    'humid': 'humid',
    'motion': 'motion',
    'ctrl_light': 'ctrl.light',
    'ctrl_buzzer': 'ctrl.buzzer',
    'ctrl_lcd': 'ctrl.lcd-text',
    'ctrl_mode': 'ctrl.mode',
    'image': 'image-url' # The feed used to store the URL of the last captured image
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
        conn = psycopg2.connect(NEON_DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def fetch_sensor_data_by_date(date_str, sensor_type):
    """
    Fetches time series data (timestamp and value) for a specific sensor on a given date.
    """
    conn = get_db_connection()
    if not conn:
        return [], "Could not connect to the database."

    # Determine the column name based on sensor_type
    if sensor_type == 'temperature':
        column = 'temperature'
    elif sensor_type == 'humidity':
        column = 'humidity'
    else:
        return [], "Invalid sensor type specified."

    # Define the start and end of the selected day
    start_dt = f"{date_str} 00:00:00"
    end_dt = f"{date_str} 23:59:59"

    query = f"""
    SELECT timestamp, {column}
    FROM sensor_logs
    WHERE timestamp BETWEEN %s AND %s
    ORDER BY timestamp ASC;
    """
    
    data = []
    error = None
    try:
        with conn.cursor() as cur:
            cur.execute(query, (start_dt, end_dt))
            results = cur.fetchall()
            
            for timestamp, value in results:
                # Format timestamp for display (e.g., 'HH:MM:SS')
                time_label = timestamp.strftime('%H:%M:%S')
                data.append({
                    'time': time_label,
                    'value': float(value) # Ensure it's a float for Chart.js
                })
    except psycopg2.Error as e:
        error = f"Database query failed: {e}"
        print(error)
    finally:
        if conn:
            conn.close()
        
    return data, error

def fetch_motion_logs_by_date(date_str):
    """
    Fetches all intrusion logs for a specific date.
    """
    conn = get_db_connection()
    if not conn:
        return [], "Could not connect to the database."
    
    # Define the start and end of the selected day
    start_dt = f"{date_str} 00:00:00"
    end_dt = f"{date_str} 23:59:59"

    # Fetch logs where event_type is 'MOTION_DETECTED'
    query = """
    SELECT log_timestamp, event_details, image_url
    FROM intrusion_logs
    WHERE log_timestamp BETWEEN %s AND %s
    ORDER BY log_timestamp DESC;
    """

    logs = []
    error = None
    try:
        with conn.cursor() as cur:
            cur.execute(query, (start_dt, end_dt))
            results = cur.fetchall()
            
            for timestamp, details, image_url in results:
                logs.append({
                    'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    'details': details,
                    # Fallback for old records without image_url
                    'image_path': image_url or "No image link saved"
                })
    except psycopg2.Error as e:
        error = f"Database query failed: {e}"
        print(error)
    finally:
        if conn:
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

@app.route('/environmental', methods=['GET', 'POST'])
def environmental_data():
    """Historical data visualization route."""
    
    sensor_data = []
    error_msg = None
    selected_date = datetime.now().strftime('%Y-%m-%d')
    selected_sensor = 'temperature' # Default sensor

    if request.method == 'POST':
        selected_date = request.form.get('date')
        selected_sensor = request.form.get('sensor')
        
        if selected_date and selected_sensor:
            sensor_data, error_msg = fetch_sensor_data_by_date(selected_date, selected_sensor)

    # If GET or POST failed, still fetch for today's default view
    if not sensor_data and not error_msg:
        sensor_data, error_msg = fetch_sensor_data_by_date(selected_date, selected_sensor)

    return render_template('environmental.html', 
                           selected_date=selected_date, 
                           selected_sensor=selected_sensor,
                           sensor_data=sensor_data, 
                           error_msg=error_msg)


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
