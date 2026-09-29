"""
runner.py -- Step 1: basic connectivity check (phone camera -> PC).

No model inference yet. This just proves a phone can stream JPEG camera
frames over the local network (Wi-Fi or phone hotspot) to this PC process,
and that the PC can decode and display them live. See
LIVE_FEED_RUNNER_PLAN.md at the repo root for the full staged plan --
detector/segmenter/checklist logic get wired in at later steps, not here.

Run:
    pip install -r requirements.txt
    python runner.py [--port 8090]

Then point the phone's WebSocket client at:
    ws://<this-pc-ip>:<port>/stream

Each WebSocket message must be the raw bytes of one JPEG-encoded frame.
"""

import argparse
import queue
import threading
import time

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

# cv2.imshow()/waitKey() pump a native GUI event loop and can take tens of
# milliseconds per call. Doing that inline in the async websocket handler
# stalls the asyncio loop, which stops draining the phone's TCP socket --
# on Windows that shows up as "WinError 121: semaphore timeout period has
# expired" a few frames in, and the phone gets disconnected.
#
# The fix is NOT to move cv2.imshow onto a worker thread -- OpenCV's
# HighGUI window on Windows is unreliable (often silently never paints)
# when driven from a thread other than the process's main thread. Instead
# the server itself runs on a background thread, and the main thread runs
# the cv2 display loop, pulling decoded frames through a small queue. That
# keeps imshow/waitKey on the main thread (where Windows wants it) while
# still keeping the websocket receive loop from ever blocking on the
# window.
_display_queue: "queue.Queue[np.ndarray | None]" = queue.Queue(maxsize=2)


@app.websocket("/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    print("[runner] phone connected")

    frame_count = 0
    last_report = time.monotonic()

    try:
        while True:
            frame_bytes = await websocket.receive_bytes()

            frame = cv2.imdecode(
                np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR
            )
            if frame is None:
                print("[runner] received bytes that did not decode as an image, skipping")
                continue

            frame_count += 1
            await websocket.send_text(f"ack:{frame_count}")

            now = time.monotonic()
            if now - last_report >= 1.0:
                print(f"[runner] {frame_count} frames received so far")
                last_report = now

            cv2.putText(
                frame,
                f"frames: {frame_count}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2,
            )
            # Drop the frame if the display loop is still behind rather
            # than blocking the receive loop on a full queue.
            try:
                _display_queue.put_nowait(frame)
            except queue.Full:
                pass

    except WebSocketDisconnect:
        print(f"[runner] phone disconnected after {frame_count} frames")


class _ServerNoSignalHandlers(uvicorn.Server):
    # uvicorn installs OS signal handlers (Ctrl+C, etc.) on startup, which
    # Python only allows from the main thread -- on Windows that raises
    # ValueError: signal only works in main thread of the main interpreter.
    # That exception killed this thread before the server ever bound its
    # socket, so it printed nothing and accepted no connections, old APK
    # or new. The main thread's cv2 loop below already handles Ctrl+C, so
    # this thread doesn't need its own signal handlers.
    def install_signal_handlers(self) -> None:
        pass


def _run_server(port: int):
    config = uvicorn.Config(app, host="0.0.0.0", port=port)
    _ServerNoSignalHandlers(config).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    print(f"[runner] listening on ws://0.0.0.0:{args.port}/stream")
    threading.Thread(target=_run_server, args=(args.port,), daemon=True).start()

    # Main thread: own the cv2 window. Windows' HighGUI backend wants
    # imshow/waitKey called consistently from one thread, and reliably
    # that means the main thread.
    window = "runner.py -- connectivity check (no model)"
    try:
        while True:
            frame = _display_queue.get()
            cv2.imshow(window, frame)
            cv2.waitKey(1)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
