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
import time

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()


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
            cv2.imshow("runner.py -- connectivity check (no model)", frame)
            cv2.waitKey(1)

    except WebSocketDisconnect:
        print(f"[runner] phone disconnected after {frame_count} frames")
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    print(f"[runner] listening on ws://0.0.0.0:{args.port}/stream")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
