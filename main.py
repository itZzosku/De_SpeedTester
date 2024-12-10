import subprocess
import json
from influxdb_client import InfluxDBClient, Point, WriteOptions
from datetime import datetime, timezone
import os

# Load configuration from config.json
CONFIG_PATH = "config.json"


def load_config():
    """Load the configuration from a JSON file."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as config_file:
        return json.load(config_file)


# Initialize configuration
config = load_config()
influx_config = config["influxdb"]


def run_speedtest(preferred_server_id=None):
    """
    Run the official Speedtest CLI and return the full JSON result.
    """
    # Construct the Speedtest CLI command
    command = ["speedtest", "--format=json"]

    # Add server ID if preferred server is specified
    if preferred_server_id:
        command.extend(["--server-id", str(preferred_server_id)])

    try:
        # Run the command and capture the output
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        # Decode Unicode escape sequences in server location
        data["server"]["location"] = data["server"]["location"].encode().decode("unicode_escape")
        return data
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Speedtest CLI failed: {e.stderr}")
    except json.JSONDecodeError:
        raise RuntimeError("Failed to parse Speedtest CLI output.")


def write_to_influx(data):
    """Write all Speedtest JSON data to InfluxDB."""
    client = InfluxDBClient(url=influx_config["url"], token=influx_config["token"], org=influx_config["org"])
    write_api = client.write_api(write_options=WriteOptions(batch_size=1))

    try:
        # Extract relevant data from JSON
        ping = data["ping"]
        download = data["download"]
        upload = data["upload"]
        packet_loss = float(data.get("packetLoss", 0))  # Ensure packet_loss is always a float
        isp = data["isp"]
        interface = data["interface"]
        server = data["server"]
        result_id = data["result"]["id"]
        result_url = data["result"]["url"]

        # Create a point with all fields
        point = (
            Point("internet_speed")
            # Ping data
            .field("ping_latency", ping["latency"])
            .field("ping_jitter", ping["jitter"])
            .field("ping_low", ping["low"])
            .field("ping_high", ping["high"])
            # Download data
            .field("download_bandwidth_mbps", download["bandwidth"] * 8 / 1e6)
            .field("download_bytes", download["bytes"])
            .field("download_elapsed", download["elapsed"])
            .field("download_latency_iqm", download["latency"]["iqm"])
            .field("download_latency_low", download["latency"]["low"])
            .field("download_latency_high", download["latency"]["high"])
            .field("download_latency_jitter", download["latency"]["jitter"])
            # Upload data
            .field("upload_bandwidth_mbps", upload["bandwidth"] * 8 / 1e6)
            .field("upload_bytes", upload["bytes"])
            .field("upload_elapsed", upload["elapsed"])
            .field("upload_latency_iqm", upload["latency"]["iqm"])
            .field("upload_latency_low", upload["latency"]["low"])
            .field("upload_latency_high", upload["latency"]["high"])
            .field("upload_latency_jitter", upload["latency"]["jitter"])
            # Packet loss and ISP
            .field("packet_loss", packet_loss)  # Consistently float
            .field("isp", isp)
            # Interface data
            .field("internal_ip", interface["internalIp"])
            .field("external_ip", interface["externalIp"])
            .field("is_vpn", interface["isVpn"])
            # Server details
            .field("server_id", server["id"])
            .field("server_host", server["host"])
            .field("server_name", server["name"])
            .field("server_location", server["location"])
            .field("server_country", server["country"])
            .field("server_ip", server["ip"])
            # Result metadata
            .field("result_id", result_id)
            .field("result_url", result_url)
            .time(datetime.now(timezone.utc))  # Use timezone-aware datetime
        )

        # Write the point to InfluxDB
        write_api.write(bucket=influx_config["bucket"], org=influx_config["org"], record=point)
        print("Data written to InfluxDB:", point)

    except Exception as e:
        print(f"Failed to write to InfluxDB: {e}")
    finally:
        # Ensure the write is flushed and the client is closed
        write_api.__del__()  # Flush the API
        client.__del__()     # Close the client


def main():
    # Check for preferred server ID in the config
    preferred_server_id = influx_config.get("preferred_server_id")

    print("Running speed test...")
    try:
        data = run_speedtest(preferred_server_id)
        print("Speedtest data:", json.dumps(data, indent=2, ensure_ascii=False))  # Print the full result for debugging

        write_to_influx(data)
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
