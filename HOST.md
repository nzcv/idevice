# host

The `host` module is the Python client side of the keeper measurement workflow. It
runs **only on the mac host**; a test script (e.g. `autoscript.py`) uses it to drive
a memory-measurement run on an iOS device. On every other platform the factory
returns a no-op `DummyHost` (health is always `False`, all operations are inert
placeholders) so the controller can drive any platform without special-casing.

## Architecture

```
test script (idevice.host.Host)
   |
   '-- Keeper  --> EndlessKeeper control server (mac host, :18000)
                     POST   /api/runs                 launch an xctest run
                     GET    /api/runs                 list runs
                     GET    /api/runs/{udid}          run status (echoes server_port)
                     GET    /api/runs/{udid}/launch   launch a run + the app (?ip=&bundleId=)
                     DELETE /api/runs/{udid}          kill a run
                     POST   /api/runs/{udid}/export   export memgraphs -> keeper presigns + uploads
                     ANY    /api/runs/{udid}/proxy/{*path}  forward to the on-device runner
                       |
                       '--> RemoteControlTest runner (iOS device, :18100)
                              GET /api/health
                              GET /api/measuring/start?bundleId=...
                              GET /api/measuring/period/{seconds}?bundleId=...
                              GET /api/measuring/stop
                              GET /api/measuring/status
                              /api/launch, /api/activate, /api/terminate,
                              /api/screenshot, /api/screenshot/start, /api/screenshot/stop, /api/exit
```

The host **only** talks to the keeper. Runner calls go through the keeper's
runner proxy (`/api/runs/{udid}/proxy/...`), which forwards them to the
on-device runner at `http://{device_host}:{server_port}`. The host supplies the
device IP as `device_host` at launch, and the keeper owns the on-device runner
port (its `--device-server-port` default, `GAUTO_DEVICE_SERVER_PORT` on the
controller side).

## Module layout

A layered package mirroring `idevice.device`: a thin factory, an abstract base
with shared infrastructure, and one subpackage per platform implementation.

- `host/host.py` — `Host` factory + `Platform` enum + `_HostMeta` singleton
  accessor. `Host.create(...)` / `Host.from_env()` return a `HostBase`
  (`macos` → `MacHost`, every other platform → `DummyHost`).
- `host/config.py` — env-based config accessors.
- `host/base/host.py` — `HostBase` abstract base class (the orchestration API).
- `host/base/keeper.py` — `Keeper` control-server HTTP client.
- `host/base/runner.py` — `Runner` HTTP client that reaches the on-device
  runner through the keeper proxy.
- `host/base/errors.py` — `HostError` hierarchy.
- `host/mac/host.py` — `MacHost(HostBase)`, the real keeper-backed host.
- `host/dummy/host.py` — `DummyHost(HostBase)`, the no-op fallback.

## Environment variables

The controller (`controller/src/worker/engine.rs`) injects these into each subtask:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GAUTO_HOST_TYPE` | – | Host OS (`macos` \| `ios` \| `android` \| `windows`); non-`macos` yields a `DummyHost` |
| `GAUTO_HOST_IP` | – | Keeper control-server IP, e.g. `192.168.1.7` |
| `GAUTO_HOST_ID` | – | Keeper/controller id, e.g. `14` (informational) |
| `GAUTO_HOST_PORT` | `18000` | Keeper control-server port |
| `GAUTO_DEVICE_IP` | – | Target device IP, e.g. `192.168.1.5`; sent to the keeper as `device_host` so it can proxy the runner |
| `GAUTO_DEVICE_UDID` | – | Target device UDID, e.g. `00008120-00123D323` |
| `GAUTO_DEVICE_SERVER_PORT` | `18100` | On-device runner port; owned by the keeper, no longer dialed by the host |
| `IDEVICE_HOST_TIMEOUT` | `15` | Per-request HTTP timeout (seconds) |
| `IDEVICE_HOST_READY_TIMEOUT` | `300` | `wait_until_ready` timeout (seconds) |

## Quick start

Build a host from the environment and run the full measurement workflow:

```python
from idevice.host import Host

host = Host.from_env()  # reads GAUTO_HOST_TYPE / GAUTO_HOST_* / GAUTO_DEVICE_* / GAUTO_BUNDLE_ID
host.launch_app()                   # GET /api/runs/{udid}/launch: run + app in one call
result = host.capture_memgraph(timeout=60)
host.export()                       # POST /api/runs/{udid}/export -> {... "download_url": ...}
host.kill()                         # DELETE /api/runs/{udid}
```

Or construct explicitly:

```python
from idevice.host import Host

host = Host.create(
    platform="macos",
    keeper_ip="192.168.1.7",
    device_udid="00008120-00123D323",
    device_ip="192.168.1.5",
    bundle_id="com.rm42.TrashDash",
)

host.launch_app()                   # GET /api/runs/{udid}/launch: run + app in one call
host.capture_memgraph(timeout=60)   # open a measured window and wait for it
host.export()                       # POST /api/runs/{udid}/export -> {... "download_url": ...}
host.kill()                         # DELETE /api/runs/{udid}
```

The thin clients are also available directly via `host.keeper` and `host.runner()`.
The most recently built host is reachable anywhere via `Host.Instance`.
```
