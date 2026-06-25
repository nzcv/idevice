# host

The `host` module is the Python client side of the keeper measurement workflow. It
runs **only on the mac host** (other platforms are unsupported); a test script
(e.g. `autoscript.py`) uses it to drive a memory-measurement run on an iOS device.

## Architecture

```
test script (idevice.host.Host)
   |
   |-- Keeper  --> EndlessKeeper control server (mac host, :18000)
   |                 POST   /api/runs                 launch an xctest run
   |                 GET    /api/runs                 list runs
   |                 GET    /api/runs/{udid}          run status (echoes server_port)
   |                 DELETE /api/runs/{udid}          kill a run
   |                 POST   /api/runs/{udid}/export   export memgraphs -> presigned PUT
   |
   '-- Runner  --> RemoteControlTest runner (iOS device, :18100)
                     GET /api/health
                     GET /api/startMeasuring?bundleId=...
                     GET /api/stopMeasuring
                     /api/launch, /api/activate, /api/terminate, /api/screenshot, /api/exit
```

start/stop measuring is **not** proxied by the keeper: the runner embeds its own
HTTP server on the device, so the host talks to `http://{device-ip}:{port}`
directly. The runner port is taken from the keeper launch/status `server_port`,
falling back to `GAUTO_DEVICE_SERVER_PORT` (default `18100`).

## Module layout

Mirrors the `device/` module:

- `host/host.py` — `Platform` enum + `Host.create(...)` / `Host.from_env()` factory.
- `host/config.py` — env-based config accessors.
- `host/base/host.py` — `HostBase` (ABC) orchestrator interface.
- `host/base/keeper.py` — `Keeper` control-server HTTP client.
- `host/base/runner.py` — `Runner` on-device HTTP client.
- `host/base/errors.py` — `HostError` hierarchy.
- `host/mac/host.py` — `MacHost(HostBase)` concrete orchestrator.

## Environment variables

The controller (`controller/src/worker/engine.rs`) injects these into each subtask:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GAUTO_HOST_IP` | – | Keeper control-server IP, e.g. `192.168.1.7` |
| `GAUTO_HOST_ID` | – | Keeper/controller id, e.g. `14` (informational) |
| `GAUTO_HOST_PORT` | `18000` | Keeper control-server port |
| `GAUTO_DEVICE_IP` | – | Target device IP, e.g. `192.168.1.5` |
| `GAUTO_DEVICE_UDID` | – | Target device UDID, e.g. `00008120-00123D323` |
| `GAUTO_DEVICE_SERVER_PORT` | `18100` | On-device runner port (fallback) |
| `IDEVICE_HOST_TIMEOUT` | `15` | Per-request HTTP timeout (seconds) |
| `IDEVICE_HOST_READY_TIMEOUT` | `300` | `wait_until_ready` timeout (seconds) |

## Quick start

Build a host from the environment and run the full measurement workflow:

```python
from idevice.host import Host

host = Host.from_env()  # reads GAUTO_HOST_* / GAUTO_DEVICE_*
summary = host.measure("com.rm42.TrashDash", duration_s=60, export_url=presigned_url)
```

Or construct explicitly and drive each step:

```python
from idevice.host import Host

host = Host.create(
    "macos",
    keeper_ip="192.168.1.7",
    device_udid="00008120-00123D323",
    device_ip="192.168.1.5",
)

host.launch()                       # POST /api/runs
host.wait_until_ready()             # poll the on-device runner
host.start_measuring("com.rm42.TrashDash")
# ... exercise the app ...
host.stop_measuring()
host.export(presigned_url)          # POST /api/runs/{udid}/export
host.kill()                         # DELETE /api/runs/{udid}
```

The thin clients are also available directly via `host.keeper` and `host.runner()`.
```
