import time
import board
import adafruit_dht
import RPi.GPIO as GPIO
from RPLCD.i2c import CharLCD
from datetime import datetime
from paho.mqtt import client as mqtt
from picamera2 import Picamera2
from pathlib import Path

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# Pin setup
DHT_PIN = 4
MOTION_PIN = 19
BUZZER = 24
RED = 16
YELLOW = 20
GREEN = 21

GPIO.setup(MOTION_PIN, GPIO.IN)
GPIO.setup(BUZZER, GPIO.OUT)
GPIO.setup(RED, GPIO.OUT)
GPIO.setup(YELLOW, GPIO.OUT)
GPIO.setup(GREEN, GPIO.OUT)

GPIO.output(RED, False)
GPIO.output(YELLOW, False)
GPIO.output(GREEN, False)
GPIO.output(BUZZER, False)

# Initialize DHT sensor
dht_sensor = adafruit_dht.DHT11(board.D4)

lcd = CharLCD('PCF8574', 0x27)
camera = Picamera2()
camera.configure(camera.create_still_configuration())
camera.start()

# MQTT setup
BROKER = "io.adafruit.com"
PORT = 1883
USERNAME = "elias_larhdaf"
KEY = "aio_FLRl655fA7y4YquOmv64pWiVIQhH"

# Feed paths
FEED_TEMP = f"{USERNAME}/feeds/temperature"
FEED_MOTION = f"{USERNAME}/feeds/motion"
FEED_CAMERA_TRIGGER = f"{USERNAME}/feeds/camera-image"
FEED_LED = f"{USERNAME}/feeds/led-status"
FEED_BUZZER = f"{USERNAME}/feeds/buzzer"

# State variables
led_control_state = False
last_motion_time = 0
motion_debounce = 2
last_temp_read = 0
mqtt_connected = False


def publish(feed, value):
    global mqtt_connected
    if not mqtt_connected:
        print(f"[WARNING] Not connected, can't publish to {feed}")
        return
    try:
        result = client.publish(feed, str(value))
        if result.rc == 0:
            print(f"[PUBLISH OK] {feed.split('/')[-1]}: {value}")
        else:
            print(f"[PUBLISH FAIL] {feed.split('/')[-1]}: {value} (error code: {result.rc})")
    except Exception as e:
        print(f"[ERROR] Publish failed: {e}")


def take_photo():
    try:
        Path("captured_images").mkdir(exist_ok=True)
        filename = f"captured_images/image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        camera.capture_file(filename)
        print(f"[CAMERA] Photo saved: {filename}")
        return filename
    except Exception as e:
        print(f"[ERROR] Camera: {e}")
        return None


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
        print(f"\n[SUCCESS] MQTT Connected to Adafruit IO!")

        # Subscribe to control feeds ONLY
        client.subscribe(FEED_LED)
        print(f"[SUBSCRIBE] {FEED_LED}")

        client.subscribe(FEED_BUZZER)
        print(f"[SUBSCRIBE] {FEED_BUZZER}")

        client.subscribe(FEED_CAMERA_TRIGGER)
        print(f"[SUBSCRIBE] {FEED_CAMERA_TRIGGER}")

        # Publish initial states
        time.sleep(0.5)
        publish(FEED_LED, "OFF")
        publish(FEED_MOTION, "0")
        print("[SUCCESS] Initial states published\n")

    elif rc == 5:
        mqtt_connected = False
        print(f"\n[ERROR] Authentication failed (code 5)")
        print(f"[ERROR] Check your USERNAME and KEY!")
        print(f"  Username: {USERNAME}")
        print(f"  Key starts with: {KEY[:15]}...")
        print(f"  Go to: https://io.adafruit.com/{USERNAME}/settings")
    else:
        mqtt_connected = False
        print(f"\n[ERROR] Connection failed with code: {rc}")


def on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    print(f"[MQTT] Disconnected (code: {rc})")


def on_message(client, userdata, msg):
    global led_control_state, last_temp_read

    topic = msg.topic
    payload = msg.payload.decode().strip()

    print(f"\n====================================")
    print(f"MESSAGE RECEIVED!")
    print(f"Topic: {topic}")
    print(f"Payload: '{payload}'")
    print(f"====================================")

    try:
        if msg.topic == FEED_LED:
            lcd.clear()
            # Handle both "ON"/"OFF" and "1"/"0"
            if payload.upper() == "ON" or payload == "1":
                led_control_state = True
                GPIO.output(RED, True)
                GPIO.output(YELLOW, True)
                # Don't control green - it's for motion only
                lcd.write_string("LEDs: ON")
                print("[ACTION] RED + YELLOW LEDs turned ON")
            elif payload.upper() == "OFF" or payload == "0":
                led_control_state = False
                GPIO.output(RED, False)
                GPIO.output(YELLOW, False)
                # Don't control green - it's for motion only
                lcd.write_string("LEDs: OFF")
                print("[ACTION] RED + YELLOW LEDs turned OFF")
            else:
                print(f"[WARNING] Unknown LED payload: '{payload}'")
            time.sleep(1)
            # Force LCD to update temperature after
            last_temp_read = 0

        elif msg.topic == FEED_BUZZER:
            lcd.clear()
            lcd.write_string("Buzzer Active!")
            print("[ACTION] Buzzer activated from dashboard")
            buzz_alert()
            time.sleep(0.8)
            # Force LCD to update temperature after
            last_temp_read = 0

        elif msg.topic == FEED_CAMERA_TRIGGER:
            # Only respond to "1" or "ON" from button press
            if payload == "1" or payload.upper() == "ON":
                lcd.clear()
                lcd.write_string("Taking Photo...")
                print("[ACTION] Camera button pressed - taking photo")
                filename = take_photo()
                if filename:
                    lcd.clear()
                    lcd.write_string("Photo Saved!")
                    time.sleep(1.5)
                    print(f"[SUCCESS] Manual photo: {filename}")
                # Force LCD to update temperature after
                last_temp_read = 0
        else:
            print(f"[WARNING] Message from unknown topic: {topic}")

    except Exception as e:
        print(f"[ERROR] on_message: {e}")


# Create MQTT client
client = mqtt.Client(client_id=f"homeguardian_{int(time.time())}", protocol=mqtt.MQTTv311)
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

    # Wait for connection
    for i in range(10):
        if mqtt_connected:
            break
        time.sleep(1)
        print(f"Waiting for connection... {i + 1}/10")

    if not mqtt_connected:
        print("\n[ERROR] Failed to connect after 10 seconds")
        print("[ERROR] Please check your Adafruit IO credentials!")
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

        # Read temperature every 3 seconds
        if current_time - last_temp_read > 3:
            try:
                temperature = dht_sensor.temperature
                humidity = dht_sensor.humidity

                if temperature is not None and humidity is not None:
                    publish(FEED_TEMP, temperature)
                    lcd.clear()
                    lcd.write_string(f"T:{temperature}C H:{humidity}%")
                    print(f"[SENSOR] Temp: {temperature}C, Humidity: {humidity}%")
                    last_temp_read = current_time

            except RuntimeError as e:
                print(f"[SENSOR] DHT read error (will retry)")
            except Exception as e:
                print(f"[SENSOR] Error: {e}")

        time.sleep(1)

        # Check motion
        if check_motion():
            print("\n" + "!" * 50)
            print("!!! MOTION DETECTED !!!")
            print("!" * 50)

            # Turn on green LED for motion (independent of LED control)
            GPIO.output(GREEN, True)

            # Publish to motion feed
            publish(FEED_MOTION, "1")

            # Show on LCD
            lcd.clear()
            lcd.write_string("MOTION DETECTED!")

            # Buzzer alert
            buzz_alert()
            time.sleep(1)

            # Take photo
            lcd.clear()
            lcd.write_string("Taking Photo...")
            filename = take_photo()
            if filename:
                lcd.clear()
                lcd.write_string("Photo Saved!")
                time.sleep(1.5)
                print(f"[SUCCESS] Motion photo: {filename}")

            # Turn off green LED after motion
            GPIO.output(GREEN, False)
            publish(FEED_MOTION, "0")

            # Force temperature display update
            last_temp_read = 0

            print("Motion reset\n")
        else:
            # Make sure green LED is off when no motion
            if cycle_count % 10 == 0:
                publish(FEED_MOTION, "0")
                GPIO.output(GREEN, False)

        time.sleep(1)

except KeyboardInterrupt:
    print("\n\nShutting down...")

finally:
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
