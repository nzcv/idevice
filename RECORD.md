# record

The `record` module is the Python client side of the iRecord screen-recording
workflow. It runs **only on the mac host**; a test script uses it to start/stop a
video-only H.264 recording of a USB-connected iOS device. On every other platform
the factory returns a no-op `DummyRecord` (health is always `False`, all
operations are inert placeholders) so the controller can drive any platform
without special-casing.

## Architecture

```
test script (idevice.record.Record)
   |
   '-- IRecordClient --> iRecord control server (mac host, :8080)
                           GET /api/{udid}/start?timeout=2h            start a recording
                           GET /api/{udid}/stop?upload=true&preset=720p stop (+ optional upload/downscale)
                           GET /api/{udid}/status                       recording status
                             |
                             '--> USB / CoreMediaIO --> iOS device
```

Unlike `host` (keeper + on-device runner proxy), iRecord records USB-connected
devices directly via Apple's native capture stack (CoreMediaIO + AVFoundation),
so the client needs only **one** thin HTTP client and no on-device runner.

## Module layout

A layered package mirroring `idevice.host`: a thin factory, an abstract base with
shared infrastructure, and one subpackage per host-type implementation.

- `record/record.py` — `Record` factory + `RecordType` enum + `_RecordMeta`
  singleton accessor. `Record.create(...)` / `Record.from_env()` return a
  `RecordBase` (`macos` → `MacRecord`, every other host type → `DummyRecord`).
- `record/config.py` — env-based config accessors.
- `record/base/record.py` — `RecordBase` abstract base class (the record API).
- `record/base/client.py` — `IRecordClient` control-server HTTP client.
- `record/base/errors.py` — `RecordError` hierarchy.
- `record/mac/record.py` — `MacRecord(RecordBase)`, the real iRecord-backed recorder.
- `record/dummy/record.py` — `DummyRecord(RecordBase)`, the no-op fallback.

## Environment variables

The record client reuses the mac-host coordinates injected by the controller
(`controller/src/worker/engine.rs`), since the iRecord server runs on the mac host:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GAUTO_HOST_TYPE` | – | Host OS (`macos` \| `ios` \| `android` \| `windows`); non-`macos` yields a `DummyRecord` |
| `GAUTO_HOST_IP` | – | iRecord server IP (the mac host), e.g. `127.0.0.1` |
| `IRECORD_PORT` | `8080` | iRecord control-server port |
| `GAUTO_DEVICE_UDID` | – | Target device UDID, e.g. `00000000-0000000000000000` |
| `IDEVICE_RECORD_TIMEOUT` | `60` | Per-request HTTP timeout (seconds) |

## Quick start

Build a recorder from the environment and drive a recording:

```python
from idevice.record import Record

recorder = Record.from_env()  # reads GAUTO_HOST_TYPE / GAUTO_HOST_IP / IRECORD_PORT / GAUTO_DEVICE_UDID
recorder.start()                          # GET /api/{udid}/start
recorder.status()                         # GET /api/{udid}/status
recorder.stop(upload=True, preset="720p") # GET /api/{udid}/stop?upload=true&preset=720p
```

Or construct explicitly:

```python
from idevice.record import Record

recorder = Record.create(
    record_type="macos",
    server_ip="127.0.0.1",
    device_udid="00000000-0000000000000000",
)

recorder.start(timeout="2h")   # auto-stop after 2h (or pass a number of seconds)
recorder.status()
recorder.stop()
```

The thin client is also available directly via `recorder.client`. The most
recently built recorder is reachable anywhere via `Record.Instance`.
