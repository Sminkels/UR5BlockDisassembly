# Vision: camera + brick detection

Computer vision part of PicoBricks. A Raspberry Pi camera looks down at the table, finds the bricks and reference markers with OpenCV, and works out which brick is highest (the one the UR5 removes first). The result goes to Grasshopper as planes for the Robots plugin.

Everything in this folder is self-contained. It does not touch the Unity project, `robot-bridge/` or any other folder.

## Folder contents

| Path | What it is |
|------|------------|
| `camera_toolkit/camera_toolkit.py` | Core functions: marker detection, table plane fit from the reference tags, block heights |
| `camera_toolkit/live_aruco_bricks.py` | Live view of the stream with detected markers and bricks |
| `camera_toolkit/diagnose.py` | Checks the stream and detection settings when something does not work |
| `camera_toolkit/config.json` | Stream URL, HSV colour ranges, detection limits |
| `grasshopper/` | Grasshopper and Rhino files that turn the detected bricks into pick planes (see below) |
| `pi/` | (optional) rpicam / mediamtx command used on the Raspberry Pi 3 to start the stream |

## Setup

- Raspberry Pi 3 with a **Camera Module 3**, streaming over RTSP with **mediamtx**.
- Camera hangs over the table and views an area of about **400 x 600 mm**. It does not look straight down (tilt about 41.5 degrees against the table), so the table plane is fitted from the reference tags.
- PC: Python (miniconda), OpenCV and NumPy. Use `opencv-contrib-python` if `cv2.aruco` is not found in your OpenCV.

```
pip install opencv-contrib-python numpy
```

## Markers

- Dictionary: **4x4_50**, marker size **25 mm**.
- **Reference tags: IDs 0-3**, flat on the table, **400 mm apart**.
- **ID 0 is the neutral marker**: it is the origin (0, 0, 0). In Grasshopper it sits at world position (200, -200, 0).
- Brick tags use other IDs.
- Brick size in the script: **95 x 75 x 27 mm**.

## How to run

1. Start the stream on the Pi (see `pi/`) and check that the URL in `camera_toolkit/config.json` matches it.
2. Run a first check:

```
python camera_toolkit/diagnose.py
```

3. Live view:

```
python camera_toolkit/live_aruco_bricks.py
```

## How the pick order is decided

- Heights are measured relative to the table plane fitted from the reference tags.
- The highest brick is picked first. If bricks are at the same height, the one closest to the neutral marker goes first.
- Heights are rounded up to levels of 50 mm (below 50 mm becomes 50, above 50 mm becomes 100).
- The pick plane is in the middle of the top of the brick.
- Bricks on the same top level are sent to the robot in one batch, closest to the robot arm first, then the table is scanned again.
- Place (end) positions are added to the list. If a scan finds fewer bricks than expected, the unused end positions are removed.

## Grasshopper

Files are in `grasshopper/`. They are binary files, so Git cannot merge them: only one person should edit them at a time.

Fill in: file names, which Rhino/Grasshopper version, and how to start the script (mock mode with Rhino planes vs camera mode).

## Known issues

Fill in or delete when fixed.

- Pick plane can be flipped the wrong way
- Run command does not always trigger
- Script does not always advance to the next block

## Not in this repo on purpose

Recordings, stopmotion videos, captured images, virtual environments and local settings. See `.gitignore` in this folder.
