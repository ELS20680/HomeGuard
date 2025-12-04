import time
import board
import adafruit_dht
import RPi.GPIO as GPIO
from RPLCD.i2c import CharLCD
from datetime import datetime
from paho.mqtt import client as mqtt
from picamera2 import Picamera2
from pathlib import Path
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Load the config file
with open("config.json", "r") as f:
    config = json.load(f)

# Import custom modules
try:
    from src.db_logger import DatabaseLogger
    print("[IMPORT] Database logger module loaded")
except ImportError as e:
    print(f"[ERROR] Could not import DatabaseLogger: {e}")
    DatabaseLogger = None

try:
    from src.gdrive_uploader import GoogleDriveUploader
    print("[IMPORT] Google Drive uploader module loaded")
except ImportError as e:
    print(f"[ERROR] Could not import GoogleDriveUploader: {e}")
    GoogleDriveUploader = None

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# Pin setup
DHT_PIN = 4
MOTION_PIN = 19
BUZZER = 24
RED = 16      # Control LED (Red)
YELLOW = 20   # Control LED (Yellow)
GREEN = 21    # Motion indicator LED (Green)

GPIO.setup(MOTION_PIN, GPIO.IN)
GPIO.setup(BUZZER, GPIO.OUT)
GPIO.setup(RED, GPIO.OUT)
GPIO.setup(YELLOW, GPIO.OUT)
GPIO.setup(GREEN, GPIO.OUT)

GPIO.output(RED, False)
GPIO.output(YELLOW, False)
GPIO.output(GREEN, False)
GPIO.output(BUZZER, False)

# Initialize sensors
dht_sensor = adafruit_dht.DHT11(board.D4)
lcd = CharLCD('PCF8574', 0x27)
camera = Picamera2()
camera.configure(camera.create_still_configuration())
camera.start()

# Initialize Database Logger
NEON_DATABASE_URL = os.getenv('NEON_DATABASE_URL')
if DatabaseLogger and NEON_DATABASE_URL:
    db_logger = DatabaseLogger(
        local_db_path='data/local_sensors.db',
        neon_connection_string=NEON_DATABASE_URL
    )
    print("[DB] Database logger initialized with NEON connection")
else:
    print("[WARNING] Database logging disabled - check NEON_DATABASE_URL in .env")
    db_logger = None

# Initialize Google Drive uploader
if GoogleDriveUploader:
    try:
        gdrive = GoogleDriveUploader()
        print("[GDRIVE] Google Drive uploader ready")
    except Exception as e:
        print(f"[WARNING] Google Drive init failed: {e}")
        gdrive = None
else:
    gdrive = None

# MQTT setup
BROKER = mqtt_config["broker"]
PORT = mqtt_config["port"]
USERNAME = mqtt_config["username"]
KEY = mqtt_config["key"]

# Feed paths - ALL FEEDS
FEED_TEMP = f"{USERNAME}/feeds/temperature"
FEED_HUMIDITY = f"{USERNAME}/feeds/humidity"
FEED_MOTION = f"{USERNAME}/feeds/motion"
FEED_LED = f"{USERNAME}/feeds/led-status"
FEED_BUZZER = f"{USERNAME}/feeds/buzzer"
FEED_CAMERA = f"{USERNAME}/feeds/camera-image"
FEED_LCD_MESSAGE = f"{USERNAME}/feeds/lcd-message"
FEED_SYSTEM_MODE = f"{USERNAME}/feeds/system-mode"

# State variables
light_on = False
system_mode = "DISARMED"
lcd_message = ""
last_motion_time = 0
motion_debounce = 2
last_temp_read = 0
last_db_log = 0
last_sync_check = 0
mqtt_connected = False

def publish(feed, value):
    global mqtt_connected
    if not mqtt_connected:
        return
    try:
        result = client.publish(feed, str(value))
        if result.rc == 0:
            print(f"[MQTT PUB] {feed.split('/')[-1]}: {value}")
    except Exception as e:
        print(f"[MQTT ERROR] Publish failed: {e}")

def take_photo():
    try:
        Path("captured_images").mkdir(exist_ok=True)
        filename = f"captured_images/image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        camera.capture_file(filename)
        print(f"[CAMERA] Photo saved: {filename}")
        return filename
    except Exception as e:
        print(f"[CAMERA ERROR]: {e}")
        return None

def upload_to_gdrive(filename, photo_type):
    if not gdrive or not filename:
        return False
    try:
        print(f"[GDRIVE] Uploading {photo_type} photo...")
        file_id = gdrive.upload_photo(filename, photo_type)
        if file_id:
            print(f"[GDRIVE] Upload complete! File ID: {file_id}")
            return True
        return False
    except Exception as e:
        print(f"[GDRIVE ERROR]: {e}")
        return False

def buzz_alert():
    GPIO.output(BUZZER, True)
    time.sleep(0.15)
    GPIO.output(BUZZER, False)

def check_motion():
    global last_motion_time
    current_time = time.time()
    if GPIO.input(MOTION_PIN) == GPIO.HIGH:
        if current_time - last_motion_time > motion_debounce:
            last_motion_time = current_time
            return True
    return False

def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        print(f"\n[MQTT] Connected to Adafruit IO!")
        
        # Subscribe to dashboard control feeds
        client.subscribe(FEED_LED)
        print(f"[SUBSCRIBE] {FEED_LED}")
        
        client.subscribe(FEED_BUZZER)
        print(f"[SUBSCRIBE] {FEED_BUZZER}")
        
        client.subscribe(FEED_CAMERA)
        print(f"[SUBSCRIBE] {FEED_CAMERA}")
        
        # Subscribe to Flask app control feeds
        client.subscribe(FEED_LCD_MESSAGE)
        print(f"[SUBSCRIBE] {FEED_LCD_MESSAGE}")
        
        client.subscribe(FEED_SYSTEM_MODE)
        print(f"[SUBSCRIBE] {FEED_SYSTEM_MODE}")
        
        time.sleep(0.5)
        publish(FEED_LED, "OFF")
        publish(FEED_MOTION, "0")
        publish(FEED_SYSTEM_MODE, system_mode)
        print("[MQTT] Initial states published\n")
    else:
        mqtt_connected = False
        print(f"\n[MQTT ERROR] Connection failed (code {rc})")

def on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    print(f"[MQTT] Disconnected (code: {rc})")

def on_message(client, userdata, msg):
    global light_on, system_mode, lcd_message, last_temp_read
    
    topic = msg.topic
    payload = msg.payload.decode().strip()
    
    print(f"\n[MQTT RCV] {topic}: '{payload}'")

    try:
        # Dashboard LED control
        if topic == FEED_LED:
            lcd.clear()
            if payload.upper() == "ON" or payload == "1":
                light_on = True
                GPIO.output(RED, True)
                GPIO.output(YELLOW, True)
                lcd.write_string("LEDs: ON")
                print("[ACTION] RED + YELLOW LEDs turned ON")
            elif payload.upper() == "OFF" or payload == "0":
                light_on = False
                GPIO.output(RED, False)
                GPIO.output(YELLOW, False)
                lcd.write_string("LEDs: OFF")
                print("[ACTION] RED + YELLOW LEDs turned OFF")
            time.sleep(1)
            last_temp_read = 0

        # Dashboard buzzer control
        elif topic == FEED_BUZZER:
            lcd.clear()
            lcd.write_string("Buzzer Active!")
            print("[ACTION] Buzzer activated from dashboard")
            buzz_alert()
            time.sleep(0.8)
            last_temp_read = 0

        # Dashboard camera control
        elif topic == FEED_CAMERA:
            if payload == "1" or payload.upper() == "ON":
                lcd.clear()
                lcd.write_string("Taking Photo...")
                print("[ACTION] Camera button pressed")
                filename = take_photo()
                if filename:
                    lcd.clear()
                    lcd.write_string("Uploading...")
                    upload_to_gdrive(filename, 'manual')
                    lcd.clear()
                    lcd.write_string("Photo Saved!")
                    time.sleep(1.5)
                    print(f"[SUCCESS] Manual photo: {filename}")
                last_temp_read = 0

        # Flask LCD message control
        elif topic == FEED_LCD_MESSAGE:
            lcd_message = payload[:32]
            lcd.clear()
            if len(lcd_message) <= 16:
                lcd.write_string(lcd_message)
            else:
                lcd.write_string(lcd_message[:16] + "\n" + lcd_message[16:32])
            print(f"[ACTION] LCD message from Flask: '{lcd_message}'")
            time.sleep(2)
            last_temp_read = 0

        # Flask system mode control
        elif topic == FEED_SYSTEM_MODE:
            lcd.clear()
            if payload.upper() == "ARMED":
                system_mode = "ARMED"
                lcd.write_string("System: ARMED")
                print("[ACTION] System ARMED from Flask")
            elif payload.upper() == "DISARMED":
                system_mode = "DISARMED"
                lcd.write_string("System:DISARMED")
                print("[ACTION] System DISARMED from Flask")
            time.sleep(1.5)
            last_temp_read = 0

    except Exception as e:
        print(f"[ERROR] on_message: {e}")

# Create MQTT client
client = mqtt.Client(client_id=f"{USERNAME}", protocol=mqtt.MQTTv311)
client.username_pw_set(USERNAME, KEY)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

# Connect to broker
try:
    print("=" * 50)
    print("Connecting to Adafruit IO...")
    print(f"Broker: {BROKER}:{PORT}")
    print(f"Username: {USERNAME}")
    print("=" * 50)
    
    client.connect(BROKER, PORT, 60)
    client.loop_start()
    
    for i in range(10):
        if mqtt_connected:
            break
        time.sleep(1)
        print(f"Waiting for connection... {i + 1}/10")
    
    if not mqtt_connected:
        print("\n[ERROR] Failed to connect after 10 seconds")
        exit(1)

except Exception as e:
    print(f"[ERROR] Connection error: {e}")
    exit(1)

# Startup
lcd.clear()
lcd.write_string("HomeGuardian\nStarting...")
time.sleep(2)
lcd.clear()
lcd.write_string("System Ready!")
time.sleep(1)

print("\n" + "=" * 50)
print("SYSTEM RUNNING")
print("=" * 50 + "\n")

cycle_count = 0

try:
    while True:
        if not mqtt_connected:
            print("[WARNING] MQTT disconnected, attempting reconnect...")
            time.sleep(5)
            continue

        cycle_count += 1
        current_time = time.time()

        # Read temperature every 5 seconds
        if current_time - last_temp_read > 5:
            try:
                temperature = dht_sensor.temperature
                humidity = dht_sensor.humidity

                if temperature is not None and humidity is not None:
                    # Publish to Adafruit IO
                    publish(FEED_TEMP, temperature)
                    publish(FEED_HUMIDITY, humidity)
                    
                    # Log to database every 30 seconds
                    if db_logger and (current_time - last_db_log > 30):
                        db_logger.log_environmental(
                            temp_c=temperature,
                            humidity_pct=humidity,
                            motion=False
                        )
                        last_db_log = current_time
                    
                    # Display on LCD
                    lcd.clear()
                    lcd.write_string(f"T:{temperature}C H:{humidity}%")
                    last_temp_read = current_time

            except RuntimeError:
                pass  # DHT sensor read error, will retry
            except Exception as e:
                print(f"[SENSOR ERROR]: {e}")

        # Check for motion (only if system is ARMED)
        if check_motion() and system_mode == "ARMED":
            print("\n" + "!" * 50)
            print("!!! MOTION DETECTED - SYSTEM ARMED !!!")
            print("!" * 50)

            image_path = None

            # Turn on green LED for motion
            GPIO.output(GREEN, True)
            
            # Publish to Adafruit IO
            publish(FEED_MOTION, "1")
            
            # Show on LCD
            lcd.clear()
            lcd.write_string("MOTION DETECTED!")
            
            # Buzzer alert
            buzz_alert()
            time.sleep(0.5)
            
            # Take photo
            lcd.clear()
            lcd.write_string("Taking Photo...")
            filename = take_photo()
            
            if filename:
                # Upload to Google Drive
                lcd.clear()
                lcd.write_string("Uploading...")
                result = upload_to_gdrive(filename, 'motion')
                if isinstance(result, tuple):
                    file_id, file_link = result
                    image_path = file_link
                else:
                    file_id = None
                    file_link = None
                    image_path = None

                # Log to database with image path
                if db_logger:
                     try:
                         temp_now = dht_sensor.temperature
                         hum_now = dht_sensor.humidity
                     except:
                         temp_now = None
                         hum_now = None
                    
                     db_logger.log_motion_event(
                         temp_c=temperature,
                         humidity_pct=humidity,
                         system_mode=system_mode,
                         image_path=image_path
                     )
                
                lcd.clear()
                lcd.write_string("Photo Saved!")
                time.sleep(1.5)
            
            # Turn off green LED
            GPIO.output(GREEN, False)
            publish(FEED_MOTION, "0")
            
            last_temp_read = 0
            print("Motion event complete\n")
        
        # Periodic sync check (every 120 seconds)
        if db_logger and (current_time - last_sync_check > 120):
            unsynced = db_logger.get_unsynced_count()
            if unsynced > 0:
                print(f"[DB] {unsynced} records waiting to sync...")
                db_logger.sync_to_cloud()
            last_sync_check = current_time
        
        # Periodic motion publish when no motion
        if cycle_count % 20 == 0:
            publish(FEED_MOTION, "0")
            GPIO.output(GREEN, False)

        time.sleep(1)

except KeyboardInterrupt:
    print("\n\nShutting down...")

finally:
    # Final sync attempt
    if db_logger:
        print("[DB] Final sync attempt...")
        db_logger.sync_to_cloud()
        unsynced = db_logger.get_unsynced_count()
        if unsynced > 0:
            print(f"[DB] WARNING: {unsynced} records not synced")
    
    lcd.clear()
    lcd.write_string("Goodbye!")
    time.sleep(1)
    
    GPIO.output(BUZZER, False)
    GPIO.output(RED, False)
    GPIO.output(YELLOW, False)
    GPIO.output(GREEN, False)
    GPIO.cleanup()
    
    camera.stop()
    client.loop_stop()
    client.disconnect()
    lcd.clear()
    print("Shutdown complete")
