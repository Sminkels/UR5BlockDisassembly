# Vision: camera + brick detection

Computer vision part of PicoBricks. A Raspberry Pi camera looks down at the table, finds the bricks and reference markers with OpenCV, and works out which brick is highest (the one the UR5 removes first). The result goes to Grasshopper as planes for the Robots plugin.

Everything in this folder is self-contained. It does not touch the Unity project, `robot-bridge/` or any other folder.

## Folder contents

| Path | What it is |
|------|------------|
| `camera_toolkit/camera_toolkit.py` | Core functions: marker detection, table plane fit from the reference tags, block heights, custom stopmotion generator |
| `robot_toolkit/` | Grasshopper and Rhino files that turn the detected bricks into pick planes and can send signals to the UR5 |

## Setup
To set up the computer vision (CV), first you need a camera which can actively stream an output to your PC. Therefore, it is advisory to use a Raspberry Pi with a Camera Module. The total list of components necessary for CV:

- A Raspberry Pi (3) with a **Camera Module (3)**, streaming over RTSP with the software **mediamtx**.
- A tripod with an attachement for the RpiCam
- AruCo tags (25 mm) utilising the 4x4_50 library (generate via https://chev.me/arucogen/)
- OpenCV checkerboard (https://github.com/opencv/opencv/blob/4.x/doc/pattern.png)
- Python
- Visual Studio Code (VSCode) or another coding engine
- OpenCV and Numpy. If not installed, first make sure to do ```pip install opencv-contrib-python numpy``` within VSCode
- Rhino and Grasshopper with the Robots Plugin installed (https://www.food4rhino.com/en/app/robots)

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
