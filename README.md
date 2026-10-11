# Protmon

Wall-mounted security alert display for UniFi Protect. A Raspberry Pi drives a
standard monitor or TV that lights up with large flashing text the moment a
Protect sensor fires (silent-alarm fob, motion, door, and so on), plus a live
camera feed for the locations that have one mapped.

Adding a sensor is a config entry and a Protect rule, with no new hardware.

It also deploys itself. Push to `main`, and the Pi applies the change within a
minute, config files and all, with no keyboard on the wall. See
[Deployment](#deployment).

## Status

**Design stage for the application. The deploy pipeline is built and tested.**

`deploy/` works today and is covered by `deploy/selftest.sh` (39 assertions,
green). Nothing of the alert server, the kiosk page, or the go2rtc integration
exists yet. Two things gate the first line of application code:

1. The open issues labeled `discovery`, mainly whether Protect's Alarm Manager
   posts its webhook from the Cloud Key on the LAN or routes it through
   Ubiquiti's cloud. The whole design assumes LAN only.
2. The open issues labeled `decision`.

Start with the discovery listener (step 1 of the build order below). It is
throwaway code that answers questions the rest of the system rests on.

## How it works

```
UniFi Protect (Cloud Key)
        |  HTTP webhook, LAN only
        v
 Pi: alert server (webhook listener + config + state + event log)
        |  WebSocket (or SSE)
        v
 Pi: Chromium kiosk page  <---- WebRTC ----  go2rtc  <---- RTSP ---- cameras
```

Everything runs on the one Pi. Five pieces:

| Piece | Job |
| --- | --- |
| Alert server | Takes webhooks, resolves them against config, owns active-alert state and expiry, writes the event log, pushes state to the page |
| Kiosk page | Full-screen view of server state: alert banners, video tiles, idle clock, health indicator. Holds no logic of its own |
| go2rtc | Pulls RTSP from the cameras and hands it to the browser over WebRTC. Browsers cannot play RTSP, so this is required |
| systemd units | Start and restart all three unattended, on boot and on failure |
| Deployer | A timer that pulls this repo and applies it, so a push is the only deploy action |

Each Alarm Manager rule gets its own webhook URL, and **the URL path is the
lookup key**: `/alert/garage-motion` resolves to the `garage-motion` entry in
config. Nothing depends on parsing the webhook payload, which is what keeps the
system working regardless of what Protect decides to send.

## Design rules that are not negotiable

This is safety-adjacent. An alert that never appears, or that vanishes quietly,
is the worst thing the system can do. Three rules follow from that:

- **Text never waits on video.** The banner renders on receipt. The stream is
  attached afterward, and a camera that is offline or a relay that fails
  degrades to text-only with a "video unavailable" note. It never blocks or
  blanks the alert.
- **A dead sign must not look like a quiet one.** Idle shows a dim clock and an
  "All clear / system OK" status, never a blank screen. The page flags a lost
  server connection prominently, and an external heartbeat catches a Pi that has
  stopped checking in entirely.
- **Nothing is dropped silently.** Unmapped paths show an "Unmapped alert"
  banner and get logged. Rejected auth gets logged. Active alerts survive a
  reboot via persisted state. A priority-1 alert stays up for its full
  configured duration, and a repeat trigger restarts it.

## Security posture

- **LAN only, permanently.** The webhook listener must be reachable by the Cloud
  Key and nothing else. Never port-forward it, never expose it through a tunnel.
  A spoofed webhook can raise a false alarm or bury a real one.
- **Two checks on every webhook:** a shared secret token in the
  `X-Protmon-Token` header (never the URL, which Protect logs on a failed
  delivery) and an allowlist of the Cloud Key's source IP. Rejections are
  logged, not just dropped.
- **Only the webhook and the admin API are on the LAN.** `/healthz`,
  `/api/state` and `/ws` answer localhost only. `/admin` answers localhost and
  the networks listed in `PROTMON_ADMIN_NETWORKS`; anyone else gets `403`. There
  is no admin login, so that allowlist is the only gate: list only networks you
  trust, and keep the port off the internet. The admin connection is plain HTTP.
  Every state-changing admin request must carry the header `X-Protmon-Admin: 1`
  and a JSON content type, which a page on another site cannot send. Camera
  credentials and the webhook token are never returned by the admin API.
- **RTSP credentials stay on the Pi.** The browser only ever receives a go2rtc
  stream name. The server generates go2rtc's stream config from the `cameras`
  section of the config file, so credentials never reach the page.
- **go2rtc binds to localhost.** Its API and web UI are not on the LAN. The
  kiosk browser is on the same box.
- **Firewall:** inbound allows the webhook port from the Cloud Key and from the
  admin network (for `/admin`), plus SSH from the admin network. Nothing else.

## Non-goals

- Internet exposure or remote access.
- Replacing Protect's own notifications and recording. This is an additional,
  in-room display.
- Admin login. The admin network allowlist is the only gate.
- Camera recording or playback history.

## Stack

Python with FastAPI and a WebSocket endpoint, and a vanilla JS kiosk page with
no build step. Boring and dependency-light on purpose, since the box runs
unattended.

## Deployment

The Pi is wall-mounted and unattended, so a change that needs a keyboard is a
change that does not happen. Push to `main` and the Pi picks it up:

```
git push main  ->  CI checks  ->  'release' branch  ->  Pi polls every 60s
                                                          preflight
                                                          defer if an alert is live
                                                          apply + restart
                                                          health check, rollback if sick
```

Pull-based, not push-based: the design forbids inbound exposure, and a
GitHub-hosted runner cannot reach the LAN. The Pi makes one outbound
`git ls-remote` per minute over SSH with a read-only deploy key.

Pi-specific config lives in the repo under `deploy/system/`, which is installed
to the matching path under `/`. Adding a systemd unit, a logrotate rule, or a
sysctl drop-in is a file and a push. Files that leave the
overlay are removed from the Pi, so a deleted unit does not linger.

What a deploy will not do: touch `/etc/protmon/config.json`, restart
anything while an alert is on screen, leave a broken commit running, or retry a
known-bad commit on a loop.

```bash
sudo ./deploy/bootstrap.sh --repo <ssh-url> --key <deploy-key>  # once, on the Pi
sudo /usr/local/bin/protmon-update --force                     # apply a push now
sudo touch /etc/protmon/HOLD                           # pause deploys
cat /var/lib/protmon/deployed.json                     # what is live
./deploy/selftest.sh                                            # test the deployer
```

Full detail, including the contract the alert server has to honor for the
rollback and defer logic to work: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Configuration

A hand-edited JSON file, validated at startup. Cameras and alerts are separate
sections so several alerts can share a camera and RTSP credentials live in one
place. Copy [`config.example.json`](config.example.json) to `config.json` and
edit; `config.json` is gitignored because it holds camera credentials.

```json
{
  "cameras": {
    "garage": "rtsp://user:pass@192.0.2.21/stream2"
  },
  "alerts": {
    "garage-motion": {
      "text": "Garage Motion",
      "priority": 3,
      "camera": "garage",
      "duration": 30
    }
  }
}
```

| Field | Meaning |
| --- | --- |
| `text` | What the banner says. Written for someone reading it across the room |
| `priority` | `1`, `2` or `3`; lower is more urgent. See the tiers below |
| `camera` | A key into `cameras`, or `null` for text-only (a room with no camera) |
| `duration` | Seconds until the alert expires. Required; a repeat trigger restarts it |
| `style` | Optional object that overrides the tier look; see below |

`style` takes any of `fg` (text color), `bg` (background color), `alt` (second
background color for `flash-alt`, default `#fff`) and `animation`. Colors are hex,
`#rgb` or `#rrggbb`. Anything omitted falls back to the tier, so an alert with no
`style` looks like its tier. Styled animations flash once per second at most:

| `animation` | Effect |
| --- | --- |
| `none` | Static colors |
| `flash` | Text switches on and off |
| `flash-alt` | Background alternates between `bg` and `alt` |
| `invert` | `fg` and `bg` swap |

When `animation` is omitted the tier's own flash is used, with the custom
colors. Unmapped alerts ignore styling and keep their fixed look.

| Tier | Class | Default lifetime | Flash | Sound |
| --- | --- | --- | --- | --- |
| 1 | Silent alarm, life-safety | Long (e.g. 30 minutes) | Hardest | Yes, if audio is adopted |
| 2 | Doors, perimeter | Medium | Moderate | No |
| 3 | Motion, informational | Short | Gentle | No |

Every alert needs a `duration`. There is no acknowledge step, so nothing stays up
indefinitely. Unmapped alerts show for 30 minutes.

### Video tiles

An alert with a `camera` shows that camera's live video above the alert text.
An optional `video` section caps how many streams play at once:

```json
"video": { "max_streams": 2 }
```

| Field | Meaning |
| --- | --- |
| `max_streams` | Most simultaneous video streams, a positive integer. Default `2` |

Alerts are taken in display order (priority, then first seen), one tile per
camera. The first `max_streams` cameras get tiles and the rest stay text-only,
so the lowest priority alerts lose their video first. The video area takes the
top 80% of the screen with the highest priority tile largest; the text panel
below lists every active alert and never overlaps the video. Without a camera
alert the text fills the screen. A stream closes when its alert clears.

The page plays each stream from go2rtc (`127.0.0.1:1984`, stream name = camera
key) over a WebSocket using go2rtc's own player. If no frames arrive within 5
seconds, or the stream drops or the camera is offline, the tile shows "video
unavailable" and the page retries every 15 seconds. The alert text is never
affected.

### Protect reachability

An optional `protect` section makes the server check that the Protect console is
up:

```json
"protect": { "address": "192.0.2.10", "interval": 30, "failures": 3 }
```

| Field | Meaning |
| --- | --- |
| `address` | Host name or IPv4 address of the console, no scheme or path. Required if `protect` is present |
| `interval` | Seconds between checks. Default `30` |
| `failures` | Consecutive failed checks before alerting. Default `3` |

Each check is an HTTPS request to `https://<address>/` (certificate not verified,
since the console uses a self-signed one). Any HTTP response counts as up; a
refused connection, TLS error or timeout counts as a failure. After `failures` in
a row the server raises a priority 1 system alert, "Protect unreachable", and
keeps it up for as long as the checks fail. It clears on the first successful
check. The events `protect-unreachable` and `protect-restored` are logged. With
no `protect` section the check is off.

This proves the console answers HTTPS. It does not prove that Alarm Manager rules
still fire, so a quiet screen is not a guarantee that Protect is alerting.

Alerts reference cameras **by name**, which is what makes the eventual move to
UniFi cameras a URL change under `cameras` and nothing more. Use each camera's
low-resolution H.264 substream: WebRTC support for H.265 is unreliable in
Chromium (it would need a transcode), and a wall display does not need 4K.
Set the substream to 640x360 or 720p at 10 to 15 fps. The `cameras` keys are
the go2rtc stream names, so rename a camera by changing its key and every
alert that references it.

Config validation fails loudly and refuses to start on an unknown camera
reference, a duplicate key, or a bad type. It never silently skips an entry,
because a skipped entry is a sensor that looks configured and is not.

`systemctl reload protmon-server` re-reads the file without a restart. A reload
that fails validation is logged and the previous config stays in effect.

## Repository layout

```
docs/
  DEPLOYMENT.md       How a push reaches the wall, and the deploy contract
deploy/
  bootstrap.sh        One-time provisioning of a fresh Pi
  update.sh           The deployer: pull, preflight, apply, health check, roll back
  install-system.sh   Installs the deploy/system overlay to /
  preflight.sh        Validates a commit before it reaches the live tree
  selftest.sh         Drives update.sh against a throwaway remote
  deploy.env.example  Per-host deploy settings, copied to /etc
  system/             Filesystem overlay: everything here lands under / on the Pi
.github/workflows/
  checks.yml          Preflight, shellcheck, selftest, then promote main to release
config.example.json
```

Application code, the app's systemd units, and the kiosk setup land here as
the build order below proceeds. The build order, tracked as milestones, is:

1. Discovery listener that logs full webhook requests
2. Core server: config, webhook auth, active-alert state, event log, WebSocket
3. Text-only page, tested fully before any video is added
4. Persistence across restart
5. go2rtc integration, tile layout, stream cap, text-only fallback
6. Hardening: systemd, kiosk, heartbeat, firewall, rebuild docs

Step 0, the deploy pipeline, is done, which means steps 1 through 6 ship by
push rather than by SSH.

Steps 3 and 5 are deliberately apart. A working text-only sign is already
useful on its own, and video is the part most likely to misbehave.

## Testing

Every acceptance test (see the go-live milestone) runs with `curl` against the
webhook endpoint. No real sensors are needed to exercise the full lifecycle:
mapped and unmapped paths, bad tokens, duplicate triggers, expiry, active alerts
across a reboot, multiple simultaneous alerts, and a killed server.

## Hardware

Raspberry Pi 4B, HDMI to a 32-43" wall-mounted TV or monitor, Chromium
in kiosk mode. The Pi needs a static IP or DHCP reservation so the Alarm Manager
rules have a stable target. The display is still open. Together with the Pi 4B, it will
set the ceiling on simultaneous video tiles.

Keep SD card writes modest. Log rotation is already in place via the overlay.
NTP must be working, because the event log is the record you will go back to.

## Webhook

`POST` (or `GET`) `http://<pi>:8080/alert/<key>` with the header
`X-Protmon-Token: <token>`. `<key>` is a key under `alerts` in the config
(letters, digits, `_` and `-`). A valid request gets `204` immediately and the
alert is raised after the response. A wrong or missing token or a source IP
outside the allowlist gets an empty `403` and a `rejected-auth` log line. A
valid request for a key that is not in the config raises an "Unmapped alert"
banner and logs `unmapped`. The request body is logged (first 2 KB) and never
parsed.

The secrets live in `/etc/protmon/secrets.env`, not in the repo, and the server
refuses to start without both:

```
PROTMON_TOKEN=a-long-random-string
PROTMON_ALLOWED_IPS=192.168.1.10
```

`PROTMON_ALLOWED_IPS` is a comma-separated list of the Protect console's
addresses.

`PROTMON_ADMIN_NETWORKS` is optional: a comma-separated list of CIDR networks or
single IPs (for example `192.168.1.0/24`) allowed to use `/admin` in addition to
localhost. Unset or empty means localhost only. An invalid value stops startup.
Changing it needs a service restart.

### Protect rule setup

In Alarm Manager, create a rule for the sensor, choose the HTTP request action,
and set:

- Method: `POST`
- URL: `http://<pi>:8080/alert/<key>`
- Header: `X-Protmon-Token` with the token from `secrets.env`

Check it from the LAN with `curl -X POST -H "X-Protmon-Token: $TOKEN"
http://<pi>:8080/alert/<key>` (expect `204`); a wrong token or an address outside
the allowlist gives `403`, and an unknown `<key>` shows an "Unmapped alert".

### Admin page

Open `http://<pi>:8080/admin` from localhost or a network listed in
`PROTMON_ADMIN_NETWORKS`. It edits the whole config in one form: alerts (key,
text, priority, duration, camera, text style), cameras, the Protect reachability
check and the video stream cap. Alerts are listed in wall order, and each shows
its webhook URL and header with copy buttons. Renaming a key changes its URL, so
update the matching Alarm Manager rule. Camera RTSP URLs are write-only: they are
never shown, and an empty field keeps the stored URL. A camera that an alert
uses cannot be deleted. The webhook token is never shown; the page displays a
placeholder for it. Save validates and applies the config without a restart.

The Preview section embeds the real wall page and shows the alerts ticked
"Preview", including unsaved edits, with live camera video proxied through the
admin page. It keeps the wall's aspect ratio, offers a two-screen layout and a
fullscreen button, and never raises an alert or writes an event.

The Event log section reads the JSON-lines event log, including rotated files,
newest first. Filter by alert key, event type and date range; webhook events show
the source IP and request body. It is read-only: retention stays with logrotate.

## State feed

Localhost only. `GET /api/state` returns
`{"alerts": [...], "commit": "<sha>", "server_time": "<ISO-8601>", "max_streams": 2}`, alerts
sorted by priority then first seen. Each alert carries `key`, `text`,
`priority`, `camera` (the stream name, never a URL), `style` (or `null`), `first_seen`, `last_seen`
and `expires_at`.

`/ws` is a WebSocket that sends `{"type": "state", "alerts": [...], "commit":
..., "server_time": ...}` on connect and after every change (always the full
state, so a reconnect is a resync), plus `{"type": "ping"}` every 15 seconds so
the page can tell a dead socket from a quiet one.

`GET /healthz` returns `{"status": "ok", "commit": "<sha>"}` once the config
and secrets are loaded and the server is accepting requests.
