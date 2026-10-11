# Deployment

The display Pi is on a wall, in a room, behind a TV. Nobody should need a
keyboard to change a sensor label. So the Pi pulls from this repo
and applies what it finds, and a push is the only deploy action.

```
you: git push main
        |
        v
  GitHub Actions: preflight, shellcheck, deployer self-test
        |  green only
        v
  'release' branch fast-forwarded
        |
        v  every 60s: one git ls-remote
  Pi: protmon-deploy.timer -> update.sh
        |
        +-- preflight on a scratch extract      (bad commit stops here)
        +-- defer while an alert is on screen
        +-- git reset --hard, deps hook
        +-- install deploy/system overlay to /
        +-- restart what changed
        +-- health check, rollback if unhealthy
```

## Why pull and not push

A push-based deploy needs something on the internet to reach the Pi, and the
one hard rule of this project is that this box is never reachable from the
internet. A GitHub-hosted runner cannot see the LAN, and a webhook receiver on
the Pi would mean exactly the inbound exposure the design forbids.

So the Pi polls. One `git ls-remote` per minute, outbound only, over SSH with a
read-only deploy key. Worst-case latency from push to screen is the poll
interval plus the deploy itself.

The tradeoff is that the Pi now makes an outbound connection to github.com. If
that is unacceptable for your site, see
[Zero-egress variant](#zero-egress-variant) below.

## What lives where

Nothing operational is mixed in with the code the deployer overwrites.

| Path | Owner | Contents |
| --- | --- | --- |
| `/opt/protmon` | git | the repo, reset hard on every deploy |
| `/etc/protmon/config.json` | you, by hand | cameras and alerts, holds RTSP credentials |
| `/etc/protmon/secrets.env` | you, by hand | the webhook token, allowed source IPs, the admin password and optional admin networks |
| `/etc/protmon/deploy.env` | you, by hand | this host's deploy settings |
| `/etc/protmon/HOLD` | you, by hand | exists means deploys are paused |
| `/var/lib/protmon` | the app and deployer | active alerts, deployed commit, manifests |
| `/usr/local/bin/go2rtc` | the deployer | pinned go2rtc binary, version and checksum in `deploy/go2rtc/pin.env` |
| `/run/protmon-go2rtc/go2rtc.yaml` | go2rtc unit | generated stream config, mode 0600, rebuilt on every start |
| `/var/log/protmon` | the app and deployer | event log and deploy log, rotated |

A deploy never touches the first group. Config and credentials cannot be
clobbered by a push, and the repo stays safe to make public or share.

## Pi-specific config in the repo

Everything under `deploy/system/` is installed to the matching path under `/`.
`deploy/system/etc/systemd/system/protmon-deploy.timer` lands at
`/etc/systemd/system/protmon-deploy.timer`. Adding a logrotate rule, a udev
rule, a sysctl drop-in, or a new unit means adding
the file here and pushing.

Two details worth knowing:

- A file that **leaves** the overlay is removed from the Pi on the next deploy.
  `install-system.sh` keeps a manifest of what it put there, so a deleted unit
  does not linger as a ghost that still starts on boot.
- Anything under `etc/systemd/system/` triggers a `daemon-reload`, so a changed
  unit takes effect in the same deploy.

Secrets must never go in the overlay. CI fails the build if anything that looks
like an RTSP URL with a password appears outside `config.example.json` and the
docs.

## Host configuration

A freshly flashed card plus `bootstrap.sh` reaches the same state as a running
Pi. Everything below is applied by the deployer, so a change is a push.

| What | Tracked in | Applied by |
| --- | --- | --- |
| apt packages | `deploy/packages.txt` | `host-setup.sh`, from `postfetch.sh`. Installs only what is missing, and only when the file changed |
| the `protmon-kiosk` user and its groups | `deploy/host-setup.sh` | same, every deploy |
| systemd units, logrotate, udev rules, journald limits, firewall, ALSA default output (HDMI 0) | `deploy/system/` | `install-system.sh` |
| `video=` mode per HDMI port in `cmdline.txt` | `deploy/boot/cmdline.args` | `boot-config.sh`, from `install-system.sh` |
| `config.txt` settings | `deploy/boot/config.txt`, kept between `# BEGIN protmon` and `# END protmon` | `boot-config.sh` |
| go2rtc binary | `deploy/go2rtc/pin.env` | `postfetch.sh` |

Boot config changes take effect on the next reboot. The deployer writes a
`reboot pending` line to the journal and never reboots by itself. `video=`
tokens already in `cmdline.txt` are replaced and all other tokens are kept.
The video modes carry no force flag, so hotplug detection keeps working.

Deliberately not tracked:

- `/etc/protmon/config.json`: camera addresses and RTSP credentials.
- `/etc/protmon/secrets.env`: the webhook token, allowed IPs and admin password.
- `/etc/protmon/deploy.env`: per-host deploy settings, written once by bootstrap.
- The deploy key under `/root/.ssh`: a credential.
- Network addressing: a DHCP reservation on the router, not a Pi setting.
- Debug tools installed by hand (`gdb`, `htop`, `strace` and the like): not
  needed to run the display. The `grim` screenshot tool is tracked.
- Stock Raspberry Pi OS settings such as time sync.

## Firewall

`protmon-firewall` renders an nftables ruleset from `/etc/protmon/secrets.env`
and applies it at boot (`protmon-firewall.service`) and on every deploy. It owns
one table, `inet protmon`, and replaces it in a single atomic transaction, so
other tables are untouched and an open SSH session is never dropped. Inbound
policy is drop; outbound is unrestricted.

| Allowed inbound | From |
| --- | --- |
| tcp 8080 (webhook and `/admin`) | `PROTMON_ALLOWED_IPS` and `PROTMON_ADMIN_NETWORKS` |
| tcp 22 (SSH) | `PROTMON_ADMIN_NETWORKS` |
| ICMP echo (ping) | `PROTMON_ADMIN_NETWORKS` |
| established and related traffic, loopback, DHCP replies, IPv6 neighbor discovery and packet-too-big | anywhere |

Only those two variables are read, as plain `KEY=value` lines, and IPv4 and IPv6
entries are split into separate rules. After editing `secrets.env`:

```bash
sudo systemctl restart protmon-firewall
sudo nft list table inet protmon        # what is active
journalctl -u protmon-firewall -n 20
```

The script validates with `nft -c` before applying. If `PROTMON_ADMIN_NETWORKS`
is empty or invalid, or nft rejects the ruleset, it exits non-zero and leaves
the current rules in place, so a bad edit cannot lock out SSH. `preflight.sh`
renders and checks the ruleset from sample addresses, so a broken script never
deploys.

Keep the stock `nftables.service` disabled. Its default `/etc/nftables.conf`
flushes the whole ruleset when it starts.

Emergency recovery, from the console or an already open session:

```bash
sudo nft delete table inet protmon      # open the box until the next restart of the unit
```

## Safety interlocks

This deploys to a safety display unattended, which means the interesting cases
are the ones nobody is watching.

**A bad commit never reaches the live tree.** The incoming commit is extracted
to a scratch directory and `deploy/preflight.sh` runs there. If it fails, the
running tree has not moved and the display never noticed.

**Deploys wait for a quiet screen.** Before applying, the deployer asks the
server how many alerts are active. If any are, it defers and tries again next
cycle. Nothing restarts under an operator who is mid-response. A deploy that has
been waiting past `DEFER_WARN_AFTER` (default 30 minutes, about the longest an alert
normally stays up) logs a louder event. If the server is unreachable the
count is zero, because then there is nothing to interrupt and the update may be
the fix.

**An unhealthy deploy rolls itself back.** After restarting, the deployer polls
the health endpoint. No healthy response inside `HEALTH_TIMEOUT` means reset to
the previous commit, restart, and log `rolled-back`. If the previous commit is
not healthy either, the health check itself is suspect, so the deployer stops
flip-flopping and logs `rollback-unhealthy` at critical.

**A failed commit is not retried on a loop.** The failing SHA is remembered. The
deployer skips it until a newer commit appears, because a bad commit retried
every 60 seconds would restart the display every 60 seconds, forever. Push a fix
and it clears itself.

**The deployer survives deploying itself.** `update.sh` replaces the tree it
lives in, and a shell reads a script incrementally. `/usr/local/bin/protmon-update`
copies the script to a temp file and runs the copy.

**The screen is left alone when it can be.** The server restarts on every
deploy. go2rtc and the browser only restart when something they own changed, so
a docs commit does not blank the wall.

## The deploy contract

The deployer is stack-agnostic, but it expects these from the app. Honor them
when the server gets built, or the safety interlocks degrade to nothing.

| What | Why |
| --- | --- |
| `GET /healthz` on localhost, 200 once config is loaded and the webhook endpoint is live | the rollback trigger. Should also report the running commit |
| `GET /api/state` returning `{"alerts": [...], "commit": ..., "server_time": ...}` | the live-alert gate. The page consumes this too |
| config read from `/etc/protmon/config.json` | keeps credentials out of the deployed tree |
| state in `/var/lib/protmon`, logs in `/var/log/protmon/*.jsonl` | survives `git reset --hard`, and logrotate already covers that glob |
| `deploy/postfetch.sh`, if dependencies need installing | run after fetch, before restart. Non-zero fails the deploy and triggers rollback. It installs missing packages, ensures the kiosk user, and maintains the virtualenv at `/var/lib/protmon/venv`, reinstalling only when `requirements.txt` changes |
| `deploy/preflight-app.sh`, for stack-specific validation | runs in the scratch extract. The place to validate the live config against incoming code |
| the running commit shown on the idle screen | lets you read the deployed version off the wall, or off a photo of it |

Units the deployer restarts are `protmon-server.service`, `protmon-go2rtc.service`
and `protmon-kiosk.service`, and they belong in the overlay as each one is
built. Units that do not exist yet are skipped with a log line, which is why
the deployer is already usable before the app exists.

## Kiosk

`protmon-kiosk.service` runs `cage` (a single-window Wayland compositor) with
Chromium in kiosk mode on tty1, as the unprivileged `protmon-kiosk` user, and
loads `http://127.0.0.1:8080/`. It starts on boot with no login, restarts on a
crash, and is restarted by the deployer when `web/` changes. The Chromium
profile lives in tmpfs and is incognito, so there are never restore, first-run,
translate or update prompts. The compositor has no idle timeout, so the screen
never blanks or dims, and the page hides the cursor. A udev rule hides the HDMI
CEC remotes from the compositor, since they report pointer axes and would
otherwise put a cursor on screen. Chromium runs with
`--autoplay-policy=no-user-gesture-required` so alert sounds play without a
click, and `/etc/asound.conf` sends audio to HDMI 0. Raspberry Pi OS Lite has
no desktop, so this unit replaces the desktop auto-login.

```bash
sudo systemctl restart protmon-kiosk
journalctl -u protmon-kiosk -f
```

Output is 1080p when the TV advertises it as its preferred mode. To force it,
append `video=HDMI-A-1:1920x1080@60` to `/boot/firmware/cmdline.txt` and reboot.

If the TV goes to standby on its own, check what it receives over CEC with
`cec-ctl -d /dev/cec0 --show-topology`. Most sets honor a `Standby` only when
something sends one, and nothing here does. Turn off the set's own "auto power
off" and "CEC" energy options if it still sleeps.

On-site checks: power cycle the Pi and confirm it reaches the idle screen
unattended, then leave it 24 hours and confirm nothing dims or blanks.

### Two displays

Cage runs in `-m extend` mode, so Chromium's one window spans every connected
output. When the window is about two screens wide (aspect ratio 3:1 or wider)
the page splits its roles: the left screen shows the video tiles at full
height, the right screen is a complete text sign (alert banners, idle clock
and date, connection-lost screen). The video screen is black
when no camera is attached to an alert. To put the video on the right instead,
point the kiosk at `http://127.0.0.1:8080/?video=right`.

With one display, or when either display is unplugged, the layout shrinks to
one screen and the page falls back to the single-screen layout (video above the
text panel) without a reload. The stream limit (`video.max_streams`) counts
streams across both screens.

The server checks `/sys/class/drm/*-HDMI-*/status` every 5 seconds and writes
these events to the event log: `displays` once at startup (connected and known
connectors), then `display-connected` or `display-disconnected` with the
`connector` name whenever one changes.

## go2rtc

`protmon-go2rtc.service` runs go2rtc as a transient unprivileged user and turns
the RTSP cameras into WebRTC for the kiosk browser. Everything listens on
`127.0.0.1` only: the API and web UI on 1984, WebRTC media on 8555 (TCP and
UDP). RTSP and SRTP listeners are off and no STUN server is configured. Streams
are named after the `cameras` keys and open only while a client is watching.

Before each start the unit generates its config from `/etc/protmon/config.json`
(the same validator the server uses) into `/run/protmon-go2rtc/go2rtc.yaml`,
mode 0600. The server restarts the unit on a config reload only when the
`cameras` section changed. The deployer installs the binary named in
`deploy/go2rtc/pin.env` and fails the deploy if its SHA-256 does not match.

```bash
sudo ss -ltnup | grep go2rtc        # every line must show 127.0.0.1
curl -s http://127.0.0.1:1984/api/streams
journalctl -u protmon-go2rtc -f
```

## First-time setup

Once, with a keyboard or an SSH session on the Pi:

```bash
# On the admin machine: a read-only deploy key for this repo
ssh-keygen -t ed25519 -f protmon-deploy -C protmon -N ''
# Add protmon-deploy.pub under the repo's Settings > Deploy keys, write access OFF

# On the Pi
GIT_SSH_COMMAND='ssh -i /path/to/protmon-deploy -o IdentitiesOnly=yes' \
  git clone git@github.com:Catalyst-IT-Consulting/protmon.git /tmp/bootstrap
sudo /tmp/bootstrap/deploy/bootstrap.sh \
  --repo git@github.com:Catalyst-IT-Consulting/protmon.git \
  --key  /path/to/protmon-deploy \
  --ref  release
```

`bootstrap.sh` installs packages, places the deploy key, clones to
`/opt/protmon`, writes `deploy.env`, installs the overlay, and enables
the timer. It is idempotent, so re-running it is safe. It then prints what is
left to do by hand: `config.json`, `secrets.env`, the static IP or DHCP
reservation, and the firewall rules.

## Replacing the SD card

`bootstrap.sh` rebuilds everything except `/etc/protmon`, which holds the
camera credentials and secrets. Keep a copy of it off the Pi:

```bash
ssh <user>@<pi> 'sudo tar -C /etc -cz protmon' > protmon-etc.tgz
```

To move to a spare card, flash Raspberry Pi OS Lite 64-bit, boot it with the
same network reservation, and restore and bootstrap:

```bash
scp protmon-etc.tgz <user>@<pi>:/tmp/
ssh <user>@<pi> 'sudo tar -C /etc -xzf /tmp/protmon-etc.tgz'
# then run bootstrap.sh as in First-time setup, and reboot
```

A high-endurance card is worth buying for a box that runs unattended for years.

## Operating it

```bash
# Apply a push now instead of waiting for the timer
sudo /usr/local/bin/protmon-update --force

# Pause deploys while troubleshooting. The display keeps running.
sudo touch /etc/protmon/HOLD
sudo rm /etc/protmon/HOLD

# What is deployed right now
cat /var/lib/protmon/deployed.json

# Deploy history, newest last
tail -20 /var/log/protmon/deploy.jsonl
journalctl -u protmon-deploy.service -n 50

# Pin the Pi to a known commit while you work on main
sudo git -C /opt/protmon reset --hard <sha>
sudo touch /etc/protmon/HOLD
```

Deploy log events, all written to `deploy.jsonl` and the journal:

`start`, `deployed`, `held`, `deferred`, `defer-overdue`, `skipped`,
`preflight-failed`, `postfetch-start`, `postfetch-failed`, `restarted`,
`restart-failed`, `unit-absent`, `health-skipped`, `unhealthy`, `rolled-back`,
`rollback-unhealthy`, `remote-unreachable`, `fetch-failed`, `fatal`.

The events worth alerting on are `rollback-unhealthy`, `defer-overdue`, and
repeated `preflight-failed`. Feed them to the site's monitoring, so a Pi stuck
on an old commit is visible without logging into it.

## Testing the deployer

```bash
./deploy/selftest.sh
```

Builds a throwaway git remote, a stub systemctl and a relocated filesystem root,
then drives `update.sh` through the cases that matter: a no-op check, HOLD, a
normal deploy, a docs-only deploy that must not restart the browser, overlay
install and removal, a commit rejected by preflight, deferral on a live alert,
`--force`, rollback of an unhealthy deploy, no retry loop on a known-bad commit,
and recovery when a fix lands. It also checks the boot config merge and the package and user logic against stubs. It touches nothing real and runs in CI.

## Zero-egress variant

If the Pi should not talk to the internet at all, put the git remote on the LAN.
Any internal host that can mirror this repo (for example `git fetch --mirror`
on a schedule or from a GitHub webhook) will do, and the Pi pulls from it:

```
GitHub --> LAN mirror: git fetch --mirror  <--pull-- Pi (LAN only)
```

Only `DEPLOY_REMOTE` on the Pi changes. The rest of the mechanism is identical.
The cost is a second moving part in the path, and a mirror that can silently
fall behind, so monitoring should check the deployed commit either way.
