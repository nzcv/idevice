# record

The `record` module is the Python side of the screen-recording workflow. On the
**mac host** it is the iRecord client: a test script uses it to start/stop a
video-only H.264 recording of a USB-connected iOS device. On an **Android host**
it shells out to the local `scrcpy` CLI, and on a **Windows host** it shells out
to the local `ffmpeg` CLI (`gdigrab` desktop capture). On every other platform
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
  `RecordBase` (`macos` → `MacRecord`, `android` → `AndroidRecord`, `windows` →
  `WindowsRecord`, every other host type → `DummyRecord`).
- `record/config.py` — env-based config accessors.
- `record/base/record.py` — `RecordBase` abstract base class (the record API).
- `record/base/client.py` — `IRecordClient` control-server HTTP client.
- `record/base/errors.py` — `RecordError` hierarchy.
- `record/mac/record.py` — `MacRecord(RecordBase)`, the real iRecord-backed recorder.
- `record/android/record.py` — `AndroidRecord(RecordBase)`, the local scrcpy recorder.
- `record/windows/record.py` — `WindowsRecord(RecordBase)`, the local ffmpeg
  desktop recorder.
- `record/dummy/record.py` — `DummyRecord(RecordBase)`, the no-op fallback.

## Local recorders (Android & Windows)

Unlike the iRecord-backed macOS recorder, the `android` and `windows` recorders
run **on the host itself** and have no control server — they supervise a local
CLI subprocess and write an MP4 to a local directory
(`IDEVICE_RECORD_OUTPUT_DIR`, defaults to `~/.idevice/records`):

- **Android** shells out to `scrcpy` to record a USB/TCP-connected Android
  device (`GAUTO_DEVICE_UDID` is the adb serial). Records at **720p** by default
  (`--max-size 1280`) with a **4M** bit rate (`--video-bit-rate 4M`; scrcpy's own
  default is 8M). Configure via `IDEVICE_SCRCPY_BINARY` / `IDEVICE_SCRCPY_MAX_SIZE`
  / `IDEVICE_SCRCPY_VIDEO_BIT_RATE` / `IDEVICE_SCRCPY_EXTRA_ARGS`.
- **Windows** shells out to `ffmpeg` with the `gdigrab` input to record the
  local desktop (`GAUTO_DEVICE_UDID` labels the output file — typically the host
  name). Configure via `IDEVICE_FFMPEG_BINARY` / `IDEVICE_FFMPEG_FRAMERATE` /
  `IDEVICE_FFMPEG_EXTRA_ARGS`. Requires `ffmpeg` on `PATH`
  (https://ffmpeg.org/download.html).

Both stop the recording cleanly so the MP4 footer (`moov` atom) is written, and
support an in-process auto-stop `timeout` on `start(...)`.

## Environment variables

The record client reuses the mac-host coordinates injected by the controller
(`controller/src/worker/engine.rs`), since the iRecord server runs on the mac host:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GAUTO_HOST_TYPE` | – | Host OS (`macos` \| `ios` \| `android` \| `windows`); `macos`/`android`/`windows` bind a real recorder, others yield a `DummyRecord` |
| `GAUTO_HOST_IP` | – | iRecord server IP (the mac host), e.g. `127.0.0.1`; unused for `android`/`windows` |
| `IRECORD_PORT` | `8080` | iRecord control-server port; unused for `android`/`windows` |
| `GAUTO_DEVICE_UDID` | – | Target device UDID (adb serial for `android`, host name for `windows`) |
| `IDEVICE_RECORD_TIMEOUT` | `60` | Per-request HTTP timeout (seconds, macOS only) |
| `IDEVICE_RECORD_OUTPUT_DIR` | `~/.idevice/records` | Local output dir for `android`/`windows` recordings |
| `IDEVICE_SCRCPY_BINARY` | `scrcpy.exe` | scrcpy CLI path (`android`) |
| `IDEVICE_SCRCPY_MAX_SIZE` | `1280` | scrcpy `--max-size` cap; defaults to 720p (`android`). Set `0` for native resolution |
| `IDEVICE_SCRCPY_VIDEO_BIT_RATE` | `4M` | scrcpy `--video-bit-rate` (`android`). Set `0`/empty to use scrcpy's default (8M) |
| `IDEVICE_SCRCPY_EXTRA_ARGS` | – | Extra scrcpy args, e.g. `--video-bit-rate 8M` (`android`) |
| `IDEVICE_FFMPEG_BINARY` | `ffmpeg.exe` | ffmpeg CLI path (`windows`) |
| `IDEVICE_FFMPEG_FRAMERATE` | `30` | Desktop capture frame rate (`windows`) |
| `IDEVICE_FFMPEG_EXTRA_ARGS` | – | Extra ffmpeg args, e.g. `-vf scale=1280:-1` (`windows`) |

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
