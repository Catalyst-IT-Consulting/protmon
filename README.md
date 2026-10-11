# Protmon

[![checks](https://github.com/Catalyst-IT-Consulting/protmon/actions/workflows/checks.yml/badge.svg)](https://github.com/Catalyst-IT-Consulting/protmon/actions/workflows/checks.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)
![Platform: Raspberry Pi](https://img.shields.io/badge/platform-Raspberry%20Pi-c51a4a.svg)

Wall-mounted security alert display for UniFi Protect. A Raspberry Pi drives a
monitor or TV that shows large flashing alert text the moment a Protect sensor
fires, with live video from the mapped camera.

## How it works

```
UniFi Protect  --HTTP webhook-->  alert server  --WebSocket-->  kiosk page
                                                                    ^
cameras  --RTSP-->  go2rtc  ---------------WebRTC-------------------+
```

- Each Alarm Manager rule calls `/alert/<key>`, and `<key>` selects an entry in
  the config. The payload is never parsed.
- Alert text renders immediately. Video is attached afterward and falls back to
  text-only if the camera is unavailable.
- Idle shows a clock and status, never a blank screen. A lost server connection
  is flagged on screen.
- Active alerts persist across restarts. Unmapped keys show an "Unmapped alert"
  banner.
- The Pi pulls and applies changes from the `release` branch, which CI
  fast-forwards from `main` when checks pass.

Stack: Python (FastAPI, uvicorn), vanilla JS with no build step, go2rtc,
Chromium kiosk, systemd.

## Hardware

- Raspberry Pi 4B running Raspberry Pi OS Lite 64-bit
- HDMI monitor or TV (two displays supported)
- A static IP or DHCP reservation for the Pi

## Install

Create a read-only deploy key and add its public half under the repository's
Deploy keys. Then, on the Pi:

```bash
GIT_SSH_COMMAND='ssh -i /path/to/deploy-key -o IdentitiesOnly=yes' \
  git clone git@github.com:Catalyst-IT-Consulting/protmon.git /tmp/bootstrap
sudo /tmp/bootstrap/deploy/bootstrap.sh \
  --repo git@github.com:Catalyst-IT-Consulting/protmon.git \
  --key  /path/to/deploy-key
```

`bootstrap.sh` is idempotent. It installs packages, clones to `/opt/protmon`,
installs the files under `deploy/system/` to `/`, and enables the deploy
timer. Then create `/etc/protmon/config.json` and `/etc/protmon/secrets.env`
as described below.

## Configuration

`/etc/protmon/config.json`, validated at startup. Start from
[`config.example.json`](config.example.json).

```json
{
  "cameras": { "garage": "rtsp://user:pass@192.0.2.21/stream2" },
  "alerts": {
    "garage-motion": { "text": "Garage Motion", "priority": 3, "camera": "garage", "duration": 30 }
  }
}
```

| Alert field | Meaning |
| --- | --- |
| `text` | Banner text |
| `priority` | `1` (most urgent), `2` or `3` |
| `camera` | A key in `cameras`, or `null` for text-only |
| `duration` | Seconds until the alert clears. A repeat trigger restarts it |
| `style` | Optional `fg`, `bg`, `alt` colors and `animation` (`none`, `flash`, `flash-alt`, `invert`) |
| `sound` | Optional `{"tone": "<name>", "plays": 1-20}`. Tones: `chime`, `ding-dong`, `bell`, `beep`, `double-beep`, `triple-beep`, `pulse`, `alarm`, `klaxon`, `siren`, `warble`, `rising`, `falling`, `chirp` |

| Optional section | Default | Meaning |
| --- | --- | --- |
| `video.max_streams` | `2` | Most simultaneous video tiles |
| `display.show_ip` | `true` | Show the device IP on the idle screen |
| `protect.address` | | Console address. Raises a priority 1 alert when unreachable |
| `protect.interval` | `30` | Seconds between reachability checks |
| `protect.failures` | `3` | Failed checks before alerting |

Use each camera's H.264 substream (720p or lower, 10 to 15 fps). Invalid config
refuses to start. `systemctl reload protmon-server` applies changes; a reload
that fails validation keeps the previous config.

### Secrets

`/etc/protmon/secrets.env`:

```
PROTMON_TOKEN=a-long-random-string
PROTMON_ALLOWED_IPS=192.0.2.10
PROTMON_ADMIN_PASSWORD=another-long-random-string
PROTMON_ADMIN_NETWORKS=192.0.2.0/24
```

| Variable | Meaning |
| --- | --- |
| `PROTMON_TOKEN` | Required. Webhook token, sent in the `X-Protmon-Token` header |
| `PROTMON_ALLOWED_IPS` | Required. Comma-separated Protect console addresses |
| `PROTMON_ADMIN_PASSWORD` | Admin password (HTTP Basic, any username). Unset disables admin |
| `PROTMON_ADMIN_NETWORKS` | Networks allowed to reach `/admin` and SSH. Required by the firewall |

## Protect setup

In Alarm Manager, add a rule with an HTTP request action:

- Method: `POST`
- URL: `http://<pi>:8080/alert/<key>`
- Header: `X-Protmon-Token: <token>`

Test it with:

```bash
curl -X POST -H "X-Protmon-Token: $TOKEN" http://<pi>:8080/alert/<key>
```

A valid request returns `204`. A bad token or source address returns `403`.

## Admin

`http://<pi>:8080/admin` edits the full config, shows each alert's webhook URL,
previews the wall with live video, and browses the event log. Changes apply
without a restart.

## Operations

```bash
sudo /usr/local/bin/protmon-update --force   # apply the latest release now
sudo touch /etc/protmon/HOLD                 # pause deploys
cat /var/lib/protmon/deployed.json           # deployed commit
journalctl -u protmon-server -f              # server log
```

A deploy never touches `/etc/protmon`, waits while an alert is on screen, and
rolls back if the health check fails. Back up `/etc/protmon` off the Pi; it is
the only state `bootstrap.sh` cannot rebuild.

## Security

- LAN only. Never expose port 8080 to the internet.
- Webhooks require both the token and an allowlisted source IP.
- `/admin` requires an allowlisted network and the admin password. It is plain
  HTTP, so use it only on trusted networks.
- Camera credentials stay on the Pi. The browser only receives stream names.
- go2rtc and the state API listen on localhost only.
- An nftables firewall allows only the webhook, admin and SSH traffic above.

## Development

```bash
pip install -r requirements-dev.txt
pytest
./deploy/selftest.sh
```

## License

[MIT](LICENSE)
