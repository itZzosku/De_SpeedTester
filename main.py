import subprocess
import json
import requests
from influxdb_client import InfluxDBClient, Point, WriteOptions
from datetime import datetime, timezone
import os

# Dynamically determine the directory of the script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "main_config.json")


def load_config():
    """Load the configuration from a JSON file."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as config_file:
        return json.load(config_file)


REGIONAL_ROUTING = {
    "na1": "americas",
    "br1": "americas",
    "lan1": "americas",
    "las1": "americas",
    "oce1": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "jp1": "asia",
    "kr": "asia"
}


def get_regional_domain(platform_region):
    if platform_region not in REGIONAL_ROUTING:
        raise ValueError(f"No known regional mapping for platform '{platform_region}'")
    regional_route = REGIONAL_ROUTING[platform_region]
    return f"{regional_route}.api.riotgames.com"


def get_puuid_from_riot_id(game_name, tag_line, region, riot_api_key):
    # Convert platform region (e.g., 'euw1') to something like 'europe.api.riotgames.com'
    regional_domain = get_regional_domain(region)

    # Example: https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/itZzosku/EUW
    url = f"https://{regional_domain}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"

    headers = {"X-Riot-Token": riot_api_key}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("puuid")
        else:
            print(f"[LoL] Failed to retrieve PUUID from gameName/tagLine (status {response.status_code}): {response.text}")
            return None
    except requests.RequestException as e:
        print(f"[LoL] Error calling Account-V1: {e}")
        return None


def is_in_league_game_v5(puuid, region, riot_api_key):
    url = f"https://{region}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}"
    headers = {"X-Riot-Token": riot_api_key}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return True
        elif response.status_code == 404:
            return False
        else:
            print(f"[LoL] Unexpected Spectator-V5 status {response.status_code}: {response.text}")
            return False
    except requests.RequestException as e:
        print(f"[LoL] Error calling Spectator-V5: {e}")
        # Default to False so speedtest proceeds if there's an error
        return False


def run_speedtest(preferred_server_id=None):
    """
    Run the official Speedtest CLI and return the full JSON result.
    """
    command = ["speedtest", "--format=json"]

    if preferred_server_id:
        command.extend(["--server-id", str(preferred_server_id)])

    try:
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
    config = load_config()
    influx_config = config["influxdb"]
    client = InfluxDBClient(url=influx_config["url"], token=influx_config["token"], org=influx_config["org"])
    write_api = client.write_api(write_options=WriteOptions(batch_size=1))

    try:
        ping = data["ping"]
        download = data["download"]
        upload = data["upload"]
        packet_loss = float(data.get("packetLoss", 0))
        isp = data["isp"]
        interface = data["interface"]
        server = data["server"]
        result_id = data["result"]["id"]
        result_url = data["result"]["url"]

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
            .field("packet_loss", packet_loss)
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
            .time(datetime.now(timezone.utc))
        )

        write_api.write(bucket=influx_config["bucket"], org=influx_config["org"], record=point)
        print("Data written to InfluxDB:", point)

    except Exception as e:
        print(f"Failed to write to InfluxDB: {e}")
    finally:
        write_api.__del__()
        client.__del__()


def main():
    # Load config once at the start
    config = load_config()
    influx_config = config["influxdb"]

    lol_config = config.get("league_of_legends", {})
    lol_api_key = lol_config.get("api_key", "")
    lol_puuid = lol_config.get("puuid")
    lol_region = lol_config.get("region", "euw1")
    game_name = lol_config.get("game_name")   # e.g. "itZzosku"
    tag_line = lol_config.get("tag_line")     # e.g. "EUW"

    preferred_server_id = influx_config.get("preferred_server_id")

    # 1) If PUUID is missing, try retrieving it via /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}
    if lol_api_key and not lol_puuid and game_name and tag_line:
        print("PUUID not found in config. Attempting to retrieve via gameName/tagLine...")
        fetched_puuid = get_puuid_from_riot_id(game_name, tag_line, lol_region, lol_api_key)
        if fetched_puuid:
            print(f"Successfully retrieved PUUID: {fetched_puuid}")
            # Store it in the local variable. (Optional: You could also write it back to main_config.json.)
            lol_puuid = fetched_puuid
        else:
            print("Failed to get PUUID from account-v1. Continuing without LoL check...")

    # 2) If we have a PUUID + API key, check if user is in-game
    if lol_api_key and lol_puuid:
        print(f"Checking if summoner (PUUID: {lol_puuid}) is currently in a game...")
        if is_in_league_game_v5(lol_puuid, lol_region, lol_api_key):
            print("Summoner is currently in a League of Legends game. Skipping speedtest.")
            return
        else:
            print("Summoner is not in a game. Proceeding with speedtest.")
    else:
        print("No valid PUUID/API key found in config, or account lookup failed. Skipping LoL in-game check.")

    # 3) Run Speedtest
    print("Running speed test...")
    try:
        data = run_speedtest(preferred_server_id)
        print("Speedtest data:", json.dumps(data, indent=2, ensure_ascii=False))
        write_to_influx(data)
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
