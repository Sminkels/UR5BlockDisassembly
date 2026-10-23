# Computer Vision: camera + brick detection

This github is a part of PicoBricks, an AR assembly tool for the UR5. In order to properly disassemble the strucutre made by through AR, or by hand, Computer Vision (CV) can step in. This github therefore contains files to set up CV for disassembly.

Be aware that this CV approach utilises RTSP streams. The code is purely based on retrieving RTSP streams, and is not suitable for other streams or regular cameras, unless the code is changed. 

## Folder contents

| Path | What it is |
|------|------------|
| `camera_toolkit/camera_toolkit.py` | Core functions: marker detection, table plane fit from the reference tags, block heights, custom stopmotion generator |
| `robot_toolkit/` | Grasshopper and Rhino files that turn the detected bricks into pick planes and can send signals to the UR5 |

## Setup
To set up the computer vision (CV), first you need a camera which can actively stream an RTSP stream to your PC. Therefore, it is advisory to use a Raspberry Pi with a Camera Module. 

To get started, first you need the following materials:

- A Raspberry Pi (3) with a **Camera Module (3)**, streaming over RTSP with the software **mediamtx**.
- A tripod with an attachement for the RpiCam
- AruCo tags (25 mm) utilising the 4x4_50 library (generated via https://chev.me/arucogen/)
- Visual Studio Code (VSCode) with Python
- OpenCV and Numpy. If not installed, first make sure to do ```pip install opencv-contrib-python numpy``` within VSCode
- Rhino and Grasshopper with the Robots Plugin installed (https://www.food4rhino.com/en/app/robots)

## How to install the RaspberryPi Camera (RPi Cam)

1. Download and install the RaspberryPi Imager software to your PC (https://www.raspberrypi.com/software/)
2. Plug a micro SD card into your laptop and click on "Install Raspberry Pi OS (64 bit)"
3. Insert the micro SD into the RaspberryPi and boot up the system
4. Connect the Pi to a screen, keyboard and mouse
5. Connect the Pi to wifi
6. Set up RaspberryPi Connect (https://www.raspberrypi.com/documentation/services/connect.html#content) so you can connect from your own PC
7. Install Mediamtx via Chromium. Make sure to download the arm64 version (https://github.com/bluenviron/mediamtx/releases)
8. Extract the file, and make sure you have "mediamtx.exe" and "mediamtx.yaml" in the folder "mediamtx"
9. In the .yaml file, add the following things under "Path":
   paths:
     cam:
       source: rpiCamera
       rpiCameraHFlip: True (depends on orientation)
       rpiCameraVFlip: True
       rpiCameraEV: -1 (depends on light, change accordingly
10. Execute "mediamtx.exe"
11. Open the RPi terminal and look for "hostname -I". This will be the IP address of the RTSP stream
12. To test the stream, go to http://<IP-address>:8889/cam
13. Now place your camera on the tripod, and you are all set

## How to setup the Camera for the UR5

1. Open `camera_toolkit.py` via VSCode
2. Typ the stream address in the "RTSP Stream address" box
3. Check if the camera toolkit is able to receive the stream by clicking "Live view"
4. Print out 10 different AruCo tags (ideally 25 mm) with unique ID's
5. Using the layout `UR5TableLayout.png` stick AruCo's 0-3 on the table and stick the remaining AruCo's on top of the blocks
6. Check if the markers are visible by opening "Marker check". If markers are not showing up, try chaning the EV of the camera in the .yaml file
7. Open "Height check", select "Find the heighest marker" and fill in the table plane AruCo tags (0,1,2,3) and the AruCo size (25 mm)
8. The distance between ArUco's 0-1 and 0-3 should roughly be 400 mm. If not, change the FOV until it is more precise. The FOV accounts for the angle of the camera
9. By leaving the "Height check" tab open, a .json file will automatically be exported (10x per second). You need this file for Grasshopper

Be aware that the heights will not be entirely accurate. This is because the RPi Camera is not a depth camera. The heights will be fixed in Grasshopper.

## How to setup the Grasshopper

1. Open `DisassemblyModel.gh` and `DisassemblyModel.3dm`
2. Check in the .3dm file if the end positions of the blocks are corresponding with real life
3. Set the end positions of the blocks in the GH file under "End positions - blocks"
4. Choose the path to the .json file (from Camera Toolkit) by right clicking on "Path"
5. Select the correct mode. If a wall has already been built with no prior recorded history, toggle "layer_mode". This will allow you to scan a layer, disassemble it, and rescan the next layer until the full wall has been removed. If a wall is yet to be constructed, leave "layer_mode" off
6. Set the maximum amount of blocks the wall or a layer contains under "max_blocks"
7. Set "run" to true
8. Preview the scanned blocks by going to "Robot Settings" and clicking on "Playback". If all goes well, you are ready to run the robot
9. Press "Upload" once and let the robot run
10. To reset the cycle, first turn off "run" and click on "reset"

## FAQ

_What if the position of the blocks is slightly off?_
In that case, there are a couple of quick fixes:
1. Change the FOV in `Camera_Toolkit.py`. Test to see if this has made a difference
2. Measure the true distance between the ArUco tags. You should measure this from centre to centre. This distance should be 400 mm. If not, change the ArUco positions in the GH file under the "Do NOT change, unless necessary". If for example ArUco's 0 and 3 are more than 400 mm apart, change the X position of ArUco 3 (fourth X coordinate) until it corresponds. 

_Is it possible to use a phone?_
Whilst not advisory, it is possible to use a phone. If you download an RTSP stream app, you should be able to use a phone for an RTSP stream.

## Future improvements
Whilst the program works well for small walls with identical blocks, it would be even better if further improvements were to be made. In the future, this CV tool could be extended to:
1. Include a closed loop system. Right now, the system is only able to record a wall and fix its positions, which means that if the user were to make small changes after the recording, that it cannot process these updates.
2. Recognising differently shaped objects. In the current workflow, only identical bricks can be detected. Detecting different objects and disassembling them would require a different gripper system, as well as machine learning. 
