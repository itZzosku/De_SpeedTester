import subprocess
import time
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, Point, WriteOptions, WritePrecision
import os
import json
import platform

# Configure target, interval, and logging
TARGET = "google.com"  # Target to ping
INTERVAL = 1           # Interval between pings in seconds
DURATION = 24 * 60 * 60  # Run for 1 day (in seconds)

# Load InfluxDB configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "pinger_config.json")


def load_config():
    """Load the configuration from a JSON file."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as config_file:
        return json.load(config_file)


# Initialize configuration
config = load_config()
influx_config = config["influxdb"]


def ping_target():
    """Ping the target and return success and response time."""
    try:
        # Determine the correct ping command based on the OS
        if platform.system() == "Windows":
            # Windows: Use "-n" for count and "-w" for timeout (milliseconds)
            command = ["ping", "-n", "1", "-w", "1000", TARGET]
        else:
            # Linux/macOS: Use "-c" for count and "-W" for timeout (seconds)
            command = ["ping", "-c", "1", "-W", "1", TARGET]

        # Execute ping command
        result = subprocess.run(command, capture_output=True, text=True)
        print(f"Ping stdout: {result.stdout}")  # Debugging
        print(f"Ping stderr: {result.stderr}")  # Debugging

        if result.returncode == 0:
            # Parse response time from ping output
            for line in result.stdout.split("\n"):
                if "time=" in line:
                    # Handle both Windows and Linux formats
                    time_part = line.split("time=")[-1].split(" ")[0]
                    response_time = float(time_part.replace("ms", ""))  # Remove "ms" if present
                    return True, response_time
        return False, None
    except Exception as e:
        print(f"Error while pinging: {e}")
        return False, None


def write_to_influx(timestamp, success, response_time):
    """Write ping results to InfluxDB."""
    client = InfluxDBClient(url=influx_config["url"], token=influx_config["token"], org=influx_config["org"])
    write_api = client.write_api(write_options=WriteOptions(batch_size=1))

    try:
        # Create a point with the ping data
        point = (
            Point("ping_results")
            .tag("target", TARGET)
            .field("success", int(success))  # 1 for success, 0 for failure
            .field("response_time", response_time if success else None)  # Response time only if success
            .time(timestamp, WritePrecision.S)  # Specify precision explicitly
        )

        # Write the point to InfluxDB
        write_api.write(bucket=influx_config["bucket"], org=influx_config["org"], record=point)
        print(f"Data written to InfluxDB: {point}")

    except Exception as e:
        print(f"Failed to write to InfluxDB: {e}")
    finally:
        write_api.__del__()  # Flush the API
        client.__del__()     # Close the client


def main():
    print(f"Starting ping to {TARGET}. Logging results to InfluxDB.")
    start_time = time.time()

    try:
        while (time.time() - start_time) < DURATION:
            # Get current timestamp
            timestamp = datetime.now(timezone.utc)

            # Ping the target
            success, response_time = ping_target()

            # Log the result to InfluxDB
            write_to_influx(timestamp, success, response_time)

            # Wait for the next interval
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\nPing logging stopped manually.")
    except Exception as e:
        print(f"Error occurred: {e}")
    finally:
        print("Logging completed.")


if __name__ == "__main__":
    main()
