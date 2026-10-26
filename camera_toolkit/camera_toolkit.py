"""
Table Camera Toolkit
====================
Laptop app for an RTSP camera (e.g. Raspberry Pi + mediamtx):
  1. Live view      - just watch the stream
  2. Marker check   - detect ArUco markers (ArUco, default 4X4_50) and verify they are in view
  3. Height test    - marker heights above a table plane fitted from flat reference markers
                      (no camera calibration: a simple pinhole model from the lens field of view)
  4. Stop-motion    - capture frames from the stream and export an MP4
  + Manual          - built-in help window

Install:   pip install opencv-contrib-python pillow numpy
Run:       python camera_toolkit.py
"""
import os

# Must be set before cv2 is imported
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import json
import math
import statistics
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk

# ----------------------------------------------------------------------------
# Paths, config
# ----------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
SNAP_DIR = APP_DIR / "snapshots"
PROJECT_DIR = APP_DIR / "stopmotion_projects"
POSES_PATH = APP_DIR / "poses.json"      # read by the Grasshopper script

DEFAULTS = {
    "stream_url": "rtsp://192.168.0.50:8554/cam",
    "expected_marker_ids": [0, 1],
    "marker_distance_mm": None,
    "marker_dictionary": "4X4_50",
    "stopmotion_fps": 8,
    "camera_hfov_deg": 66.0,
    "height_marker_mm": 50.0,
    "height_ref_id": 1,
    "height_target_id": 3,
    "height_mode": "pair",
    "height_candidates": "",
    "height_ref_ids": "0, 1, 2, 3",
    "height_ref_size_mm": "",
}
_config_readable = True


def load_config():
    global _config_readable
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            _config_readable = False  # don't overwrite a file we couldn't parse
    return cfg


def save_config(cfg):
    if not _config_readable:
        return
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Palette (earth tones)
# ----------------------------------------------------------------------------
C = {
    "linen": "#F2EAE3", "sandstone": "#EBDED1", "beige": "#D8C3A6", "oat": "#C4B195",
    "clay": "#C07D58", "terracotta": "#B56B46", "sienna": "#994D2C", "rust": "#8A4E35",
    "sage": "#A8AB8A", "olive": "#8D8961", "moss": "#5D573B", "forest": "#54523A",
    "cocoa": "#6A513F", "chestnut": "#734F3A", "walnut": "#563F32", "espresso": "#574134",
}
HEAD = "Georgia"
BODY = "Segoe UI"


def bgr(hex_color):
    h = hex_color.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def read_img(path):
    """Unicode-safe image read (cv2.imread fails on some Windows paths)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def write_img(path, img, quality=95):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        Path(path).write_bytes(buf.tobytes())
    return ok


BUTTON_STYLES = {
    "primary": (C["sienna"], C["linen"], C["terracotta"]),
    "secondary": (C["oat"], C["espresso"], C["beige"]),
    "dark": (C["moss"], C["linen"], C["forest"]),
}


def make_button(parent, text, command, kind="primary", **kw):
    bg, fg, hover = BUTTON_STYLES[kind]
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                  activebackground=hover, activeforeground=fg, relief="flat", bd=0,
                  font=(BODY, 11, "bold"), padx=14, pady=8, cursor="hand2",
                  takefocus=0, **kw)
    b.bind("<Enter>", lambda e: b.config(bg=hover))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


def make_label(parent, text="", size=10, bold=False, bg=None, fg=None, **kw):
    return tk.Label(parent, text=text, bg=bg or C["sandstone"], fg=fg or C["espresso"],
                    font=(BODY, size, "bold" if bold else "normal"), **kw)


def make_entry(parent, var, width=None):
    kw = {"width": width} if width else {}
    return tk.Entry(parent, textvariable=var, font=(BODY, 11), bg=C["linen"], fg=C["espresso"],
                    relief="flat", highlightthickness=1, highlightbackground=C["oat"],
                    highlightcolor=C["terracotta"], insertbackground=C["espresso"], **kw)


CHECK = dict(bg=C["sandstone"], fg=C["espresso"], activebackground=C["sandstone"],
             activeforeground=C["espresso"], selectcolor=C["linen"], font=(BODY, 10),
             anchor="w", takefocus=0)


# ----------------------------------------------------------------------------
# Threaded video reader (always keeps only the newest frame -> no lag build-up)
# ----------------------------------------------------------------------------
class VideoStream:
    def __init__(self, url):
        self.url = str(url).strip()
        self.cap = None
        self.frame = None
        self.count = 0
        self.connected = False
        self.running = False
        self.lock = threading.Lock()

    @property
    def status(self):
        return "Connected" if self.connected else "Connecting…"

    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.running = False

    def get_frame(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def _open(self):
        if self.url.isdigit():  # "0" = laptop webcam, handy for testing
            return cv2.VideoCapture(int(self.url))
        return cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

    def _run(self):
        while self.running:
            if self.cap is None:
                self.cap = self._open()
                if not self.cap.isOpened():
                    self.cap.release()
                    self.cap = None
                    self.connected = False
                    time.sleep(2)
                    continue
            ok, frame = self.cap.read()
            if not ok:
                self.cap.release()
                self.cap = None
                self.connected = False
                time.sleep(1)
                continue
            self.connected = True
            with self.lock:
                self.frame = frame
                self.count += 1
        if self.cap is not None:
            self.cap.release()
            self.cap = None


# ----------------------------------------------------------------------------
# ArUco compatibility (OpenCV 4.6 and 4.7+)
# ----------------------------------------------------------------------------
DICT_NAMES = ["4X4_50", "4X4_100", "4X4_250", "4X4_1000", "5X5_1000", "6X6_1000",
              "7X7_1000", "ARUCO_ORIGINAL", "APRILTAG_36h11"]


class MarkerDetector:
    def __init__(self):
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("cv2.aruco missing. Run: pip install opencv-contrib-python")
        self.name = None
        self.sensitive = None
        self.configure("4X4_50", True)

    def configure(self, name, sensitive):
        if (name, sensitive) == (self.name, self.sensitive):
            return
        self.name, self.sensitive = name, sensitive
        self.dict = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, "DICT_" + name))
        new_api = hasattr(cv2.aruco, "ArucoDetector")
        p = cv2.aruco.DetectorParameters() if new_api else cv2.aruco.DetectorParameters_create()
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX  # precise corners (needed for heights)
        if sensitive:
            p.adaptiveThreshWinSizeMin = 3
            p.adaptiveThreshWinSizeMax = 53
            p.adaptiveThreshWinSizeStep = 4
            p.minMarkerPerimeterRate = 0.01
            p.polygonalApproxAccuracyRate = 0.06
            p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        if new_api:
            self.detector = cv2.aruco.ArucoDetector(self.dict, p)
            self.params = None
        else:
            self.detector = None
            self.params = p

    def detect(self, gray):
        if self.detector is not None:
            return self.detector.detectMarkers(gray)
        return cv2.aruco.detectMarkers(gray, self.dict, parameters=self.params)


# ----------------------------------------------------------------------------
# Manual
# ----------------------------------------------------------------------------
MANUAL = [
    ("h1", "Table Camera Toolkit – Manual"),
    ("p", "This app shows the camera stream of your table, checks the ArUco markers and "
          "lets you build stop-motion videos from the same stream."),

    ("h2", "1. Getting started"),
    ("b", "Install once:  pip install opencv-contrib-python pillow numpy"),
    ("b", "Start the app:  python camera_toolkit.py"),
    ("b", "Type your stream address in the box on the menu, e.g.  rtsp://<pi-ip>:8554/<path>  "
          "(the path is the one set in your mediamtx config)."),
    ("b", "To test without the Pi, enter 0 to use your laptop's webcam."),
    ("b", "The address is saved in config.json next to the app and remembered next time."),
    ("b", "Press Esc in any mode to return to the menu."),

    ("h2", "2. Live view"),
    ("p", "Just the stream, nothing else. Useful to check focus, lighting and camera position."),
    ("b", "Alignment grid: thirds plus a centre cross, handy when you hang the camera over the table."),
    ("b", "Save snapshot: stores the current frame as a JPG in the 'snapshots' folder."),
    ("b", "The bar at the bottom shows connection state, resolution and frames per second."),

    ("h2", "3. Marker check"),
    ("p", "Detects ArUco markers (dictionary selectable, default 4X4_50) and tells you whether the markers "
          "you expect are visible."),
    ("b", "Expected marker IDs: comma-separated list, e.g.  0, 1.  Green outline = expected marker, "
          "orange outline = a marker you did not list."),
    ("b", "The status line turns to 'All expected markers found' when nothing is missing, "
          "otherwise it lists the missing IDs."),
    ("b", "Optional: enter the real distance between the two markers in mm. The app then shows the "
          "scale (mm per pixel) and the approximate area the camera sees, so you can check "
          "your 400 x 600 mm target."),
    ("b", "The scale is only accurate when the camera looks straight down and the markers lie "
          "flat on the table surface."),

    ("h2", "4. Height test"),
    ("p", "Estimates the height difference between two ArUco markers with a single camera. "
          "The app calculates the 3D position of each marker from its known printed size."),
    ("b", "Two modes: 'Compare two markers' (height of B above A) and 'Find the highest marker' "
          "(ranks all visible markers and highlights the highest one)."),
    ("b", "Marker size: measure the outer side of the BLACK square of your printed markers in mm. "
          "A wrong size gives wrong heights, so measure carefully."),
    ("b", "Reference ID (A): a marker lying flat at the base level, e.g. on the table. "
          "Target ID (B): the marker placed on the object you want to measure."),
    ("b", "Table reference IDs: lay 3 or 4 markers FLAT on the table, spread out (for example one near "
          "each corner of the work area). The app fits a plane through them and measures every other "
          "marker's height above that plane. This works for a tilted camera too. Use IDs that are not "
          "used on bricks. Leave the field empty to use the old method (distance along the camera axis, "
          "only valid for a camera looking straight down)."),
    ("b", "Ref. size (mm): only needed when the table reference markers are printed at a different size "
          "than the other markers. Leave it empty to use the normal marker size."),
    ("b", "Reference markers stay in the calculation for 3 seconds after they leave the view, so a "
          "hand or the robot arm covering one for a moment does not break the measurement. The status "
          "text shows the camera tilt relative to the table and, with 4 or more references, how well "
          "the plane fits (a large value means one reference marker does not lie flat)."),
    ("b", "Grasshopper link: while this screen is open the app writes the pose of every detected marker "
          "to poses.json next to the program (10 times per second). Use 'Find the highest marker' with "
          "the candidate field empty so that all markers are included."),
    ("b", "The same ID may be printed on the top and on a side face of a brick. The app tells them apart "
          "by the direction the tag faces (more than 60 degrees away from the table normal = side tag). "
          "Only top tags are measured; side tags are drawn in clay colour and labelled 'side'."),
    ("b", "The big line shows how far B is above A. A positive value means B is higher."),
    ("b", "Measure (average 30 frames) gives a mean value with its spread. A small spread means a "
          "stable measurement."),
    ("b", "Find the highest marker: enter the candidate IDs (or leave empty to use every visible marker). "
          "The list shows each marker's height above the lowest one, plus the margin between the "
          "highest and second highest. If the margin is under about 3 mm the result is flagged as "
          "too close to call, because it is within the measurement noise."),
    ("b", "In this mode, 'Measure' also counts in how many of the 30 frames each marker was the highest, "
          "which shows how reliable the answer is."),
    ("b", "Test the accuracy: put B on something with a known height, for example a stack of a few "
          "sheets of known thickness or a brick, and compare the result with a ruler."),
    ("b", "There is no camera calibration. The app uses a simple pinhole camera model built from the "
          "'Lens horizontal FOV' field (66 degrees for a standard Camera Module 3, about 102 for the Wide "
          "version). A wrong FOV distorts heights and distances: the markers at different heights are "
          "affected differently, so the differences between levels shrink or grow and the margin to the "
          "second highest marker changes. To tune it, look at the 'Spacing from ref' line and change the "
          "FOV until it shows the real distance between your reference markers (for example 400 mm). "
          "A higher FOV gives larger spacing numbers, a lower FOV gives smaller ones."),
    ("b", "Accuracy depends on marker size, distance and sharpness. Larger markers, a sharp locked focus "
          "and a high stream resolution give better results."),
    ("b", "Every marker must lie flat on its surface. Wrinkled paper under a reference marker tilts the "
          "plane and shifts all heights, so glue the references to stiff card or tape them to the table."),

    ("h2", "5. Stop-motion"),
    ("p", "Build a stop-motion film frame by frame from the live stream."),
    ("b", "Capture frame (or press Space): saves the current picture as the next frame."),
    ("b", "Onion skin: shows the last captured frame semi-transparently over the live image, "
          "so you can see how far to move your objects. It is not stored in the frames."),
    ("b", "Delete last frame: removes the newest frame if you made a mistake."),
    ("b", "Play preview: plays all captured frames at the chosen FPS."),
    ("b", "Export video: writes an MP4 with the chosen FPS (8–12 FPS is a good start)."),
    ("b", "Frames are kept in 'stopmotion_projects/<date_time>/' so nothing is lost if the app "
          "closes. 'Load project…' continues an earlier project folder."),
    ("b", "Tip: keep the camera, table and lighting fixed between frames. Switch off auto-exposure "
          "flicker by avoiding daylight changes."),

    ("h2", "6. Troubleshooting"),
    ("b", "Stuck on 'Connecting…': check the address, that the Pi is on, and that mediamtx is running. "
          "Open the same URL in VLC to test."),
    ("b", "Markers not detected: they need a white border around them, must be flat, sharp and not "
          "glossy/reflecting, and large enough in the image. Pick the matching dictionary in the dropdown (your printed IDs 0-3 are 4X4_50)."),
    ("b", "Blurry image: Camera Module 3 has autofocus. For a fixed mount you can set a fixed "
          "lens position in the rpicam command (--autofocus-mode manual --lens-position <1/distance in m>)."),
    ("b", "Laggy video: reduce resolution or bitrate on the Pi side; the app always shows the newest frame."),
    ("b", "Export fails: choose a save location without special characters and make sure at "
          "least one frame exists."),
]


class ManualWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Manual")
        self.geometry("760x680")
        self.configure(bg=C["linen"])
        frame = tk.Frame(self, bg=C["linen"])
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        sb = tk.Scrollbar(frame, troughcolor=C["sandstone"], bg=C["oat"], relief="flat")
        sb.pack(side="right", fill="y")
        t = tk.Text(frame, wrap="word", bg=C["linen"], fg=C["espresso"], relief="flat",
                    padx=18, pady=12, yscrollcommand=sb.set, cursor="arrow",
                    font=(BODY, 11), spacing3=6, highlightthickness=0)
        t.pack(side="left", fill="both", expand=True)
        sb.config(command=t.yview)
        t.tag_configure("h1", font=(HEAD, 22, "bold"), foreground=C["sienna"], spacing3=12)
        t.tag_configure("h2", font=(HEAD, 15, "bold"), foreground=C["moss"], spacing1=14, spacing3=6)
        t.tag_configure("p", font=(BODY, 11), foreground=C["espresso"])
        t.tag_configure("b", font=(BODY, 11), foreground=C["espresso"], lmargin1=22, lmargin2=38)
        for kind, text in MANUAL:
            t.insert("end", ("•  " + text if kind == "b" else text) + "\n", kind)
        t.config(state="disabled")


# ----------------------------------------------------------------------------
# Base class for the three stream modes
# ----------------------------------------------------------------------------
class ModeFrame(tk.Frame):
    title = ""

    def __init__(self, app):
        super().__init__(app.container, bg=C["linen"])
        self.app = app
        self.stream = None
        self._job = None
        self._running = False
        self._photo = None
        self.fps = 0.0
        self._fps_t = 0.0
        self._fps_n = 0
        self.frame_size = None

        header = tk.Frame(self, bg=C["espresso"])
        header.pack(fill="x")
        back = tk.Button(header, text="←  Menu", command=app.show_menu, bg=C["espresso"],
                         fg=C["linen"], activebackground=C["walnut"], activeforeground=C["linen"],
                         relief="flat", bd=0, font=(BODY, 11, "bold"), padx=16, pady=10,
                         cursor="hand2", takefocus=0)
        back.pack(side="left")
        tk.Label(header, text=self.title, bg=C["espresso"], fg=C["linen"],
                 font=(HEAD, 16, "bold")).pack(side="left", padx=10)

        self.status_var = tk.StringVar()
        tk.Label(self, textvariable=self.status_var, bg=C["oat"], fg=C["espresso"],
                 font=(BODY, 9), anchor="w", padx=12, pady=4).pack(side="bottom", fill="x")

        body = tk.Frame(self, bg=C["linen"])
        body.pack(fill="both", expand=True)
        self.side = tk.Frame(body, bg=C["sandstone"], width=310)
        self.side.pack(side="right", fill="y", padx=(0, 16), pady=16)
        self.side.pack_propagate(False)
        self.canvas = tk.Canvas(body, bg=C["walnut"], highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True, padx=16, pady=16)

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        self.stream = VideoStream(self.app.cfg["stream_url"])
        self.stream.start()
        self._running = True
        self._fps_t = time.time()
        self._fps_n = 0
        self.on_start()
        self._loop()

    def stop(self):
        self._running = False
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        if self.stream:
            self.stream.stop()
            self.stream = None
        self.on_stop()

    def on_start(self):
        pass

    def on_stop(self):
        pass

    def process(self, frame):
        return frame

    # -- display loop --------------------------------------------------------
    def _loop(self):
        if not self._running:
            return
        frame = self.stream.get_frame()
        now = time.time()
        if now - self._fps_t >= 1:
            self.fps = (self.stream.count - self._fps_n) / (now - self._fps_t)
            self._fps_t = now
            self._fps_n = self.stream.count
        if frame is not None:
            self.frame_size = (frame.shape[1], frame.shape[0])
        try:
            out = self.process(frame)
        except Exception as exc:  # keep the UI alive on processing errors
            out = frame
            self.status_var.set(f"Processing error: {exc}")
        self._show(out)
        if frame is not None:
            size = f"{self.frame_size[0]}×{self.frame_size[1]}"
            self.status_var.set(f"{self.stream.status}  ·  {size}  ·  {self.fps:.1f} fps")
        else:
            self.status_var.set(f"{self.stream.status}  ·  {self.stream.url}")
        self._job = self.after(33, self._loop)

    def _show(self, img):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        self.canvas.delete("all")
        if img is None:
            self.canvas.create_text(cw // 2, ch // 2, fill=C["beige"], font=(HEAD, 18),
                                    text=f"{self.stream.status}\n\n{self.stream.url}", justify="center")
            return
        h, w = img.shape[:2]
        s = min(cw / w, ch / h)
        small = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)


# ----------------------------------------------------------------------------
# Mode 1: live view
# ----------------------------------------------------------------------------
class LiveView(ModeFrame):
    title = "Live view"

    def __init__(self, app):
        super().__init__(app)
        make_label(self.side, "Options", 13, True).pack(anchor="w", padx=16, pady=(16, 8))
        self.grid_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self.side, text="Show alignment grid", variable=self.grid_var,
                       **CHECK).pack(fill="x", padx=16)
        make_button(self.side, "Save snapshot", self.snapshot).pack(fill="x", padx=16, pady=16)
        self.msg = make_label(self.side, "", 9, wraplength=270, justify="left")
        self.msg.pack(anchor="w", padx=16)

    def process(self, frame):
        if frame is None or not self.grid_var.get():
            return frame
        h, w = frame.shape[:2]
        col = bgr(C["sage"])
        t = max(1, w // 700)
        for i in (1, 2):
            cv2.line(frame, (w * i // 3, 0), (w * i // 3, h), col, t)
            cv2.line(frame, (0, h * i // 3), (w, h * i // 3), col, t)
        cv2.drawMarker(frame, (w // 2, h // 2), bgr(C["clay"]), cv2.MARKER_CROSS, w // 25, t * 2)
        return frame

    def snapshot(self):
        frame = self.stream.get_frame() if self.stream else None
        if frame is None:
            self.msg.config(text="No frame available yet.")
            return
        SNAP_DIR.mkdir(exist_ok=True)
        path = SNAP_DIR / f"snapshot_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        write_img(path, frame)
        self.msg.config(text=f"Saved: {path.name}")


# ----------------------------------------------------------------------------
# Mode 2: marker check
# ----------------------------------------------------------------------------
class MarkerCheck(ModeFrame):
    title = "Marker check"

    def __init__(self, app):
        super().__init__(app)
        self.detector = None
        self.detector_error = None
        try:
            self.detector = MarkerDetector()
        except Exception as exc:
            self.detector_error = str(exc)

        cfg = app.cfg
        if cfg.get("marker_dictionary") not in DICT_NAMES:
            cfg["marker_dictionary"] = "4X4_50"
        make_label(self.side, "Expected marker IDs", 11, True).pack(anchor="w", padx=16, pady=(16, 4))
        self.ids_var = tk.StringVar(value=", ".join(str(i) for i in cfg.get("expected_marker_ids", [])))
        make_entry(self.side, self.ids_var).pack(fill="x", padx=16)
        make_label(self.side, "Distance between the two markers (mm, optional)", 9,
                   wraplength=270, justify="left").pack(anchor="w", padx=16, pady=(14, 4))
        d = cfg.get("marker_distance_mm")
        self.dist_var = tk.StringVar(value="" if not d else str(d))
        make_entry(self.side, self.dist_var).pack(fill="x", padx=16)

        make_label(self.side, "Marker dictionary", 9).pack(anchor="w", padx=16, pady=(14, 4))
        self.dict_var = tk.StringVar(value=cfg.get("marker_dictionary", "4X4_50"))
        om = tk.OptionMenu(self.side, self.dict_var, *DICT_NAMES)
        om.config(bg=C["linen"], fg=C["espresso"], activebackground=C["beige"], relief="flat",
                  highlightthickness=0, font=(BODY, 10), takefocus=0, anchor="w")
        om["menu"].config(bg=C["linen"], fg=C["espresso"], font=(BODY, 10))
        om.pack(fill="x", padx=16)
        self.sens_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.side, text="Sensitive detection", variable=self.sens_var,
                       **CHECK).pack(fill="x", padx=16, pady=(8, 0))
        self.rej_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self.side, text="Show rejected candidates", variable=self.rej_var,
                       **CHECK).pack(fill="x", padx=16)


        tk.Frame(self.side, bg=C["oat"], height=1).pack(fill="x", padx=16, pady=16)
        self.status_lbl = make_label(self.side, "", 13, True, wraplength=270, justify="left")
        self.status_lbl.pack(anchor="w", padx=16)
        self.detail_lbl = make_label(self.side, "", 10, wraplength=270, justify="left")
        self.detail_lbl.pack(anchor="w", padx=16, pady=(8, 0))
        self._last = None

    def expected(self):
        ids = set()
        for part in self.ids_var.get().replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids

    def mm(self):
        try:
            v = float(self.dist_var.get().replace(",", "."))
            return v if v > 0 else None
        except ValueError:
            return None

    def on_stop(self):
        self.app.cfg["expected_marker_ids"] = sorted(self.expected())
        self.app.cfg["marker_distance_mm"] = self.mm()
        self.app.cfg["marker_dictionary"] = self.dict_var.get()
        save_config(self.app.cfg)

    def process(self, frame):
        if frame is None:
            return None
        if self.detector is None:
            self._set(("✖ " + self.detector_error, ""), bad=True)
            return frame
        out = frame.copy()
        h, w = frame.shape[:2]
        self.detector.configure(self.dict_var.get(), self.sens_var.get())
        corners, ids, rejected = self.detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        exp = self.expected()
        found = {}
        sc = max(w / 1000, 0.7)
        if self.rej_var.get():
            for r in rejected:
                cv2.polylines(out, [r.reshape(4, 2).astype(np.int32)], True, bgr(C["clay"]), 1)
        if ids is not None:
            for quad, mid in zip(corners, ids.flatten()):
                pts = quad.reshape(4, 2)
                cen = pts.mean(axis=0)
                mid = int(mid)
                found[mid] = (float(cen[0]), float(cen[1]))
                col = bgr(C["sage"]) if (mid in exp or not exp) else bgr(C["terracotta"])
                cv2.polylines(out, [pts.astype(np.int32)], True, col, max(2, int(3 * sc)))
                pos = (int(cen[0]) - int(12 * sc), int(cen[1]) + int(8 * sc))
                cv2.putText(out, str(mid), pos, cv2.FONT_HERSHEY_SIMPLEX, sc, bgr(C["walnut"]),
                            max(3, int(5 * sc)))
                cv2.putText(out, str(mid), pos, cv2.FONT_HERSHEY_SIMPLEX, sc, bgr(C["linen"]),
                            max(1, int(2 * sc)))

        # status
        missing = sorted(exp - found.keys())
        if exp:
            ok = not missing
            status = "✔ All expected markers found" if ok else "✖ Missing: " + ", ".join(map(str, missing))
        else:
            ok = bool(found)
            status = f"Detected {len(found)} marker(s)" if found else "No markers detected"
        lines = ["Detected IDs: " + (", ".join(map(str, sorted(found))) or "none")]
        extra = sorted(found.keys() - exp) if exp else []
        if extra:
            lines.append("Not in expected list: " + ", ".join(map(str, extra)))

        # distance / scale
        pair = sorted(i for i in found if i in exp)[:2] if exp else []
        if len(pair) < 2:
            pair = sorted(found)[:2]
        if len(pair) == 2:
            (x1, y1), (x2, y2) = found[pair[0]], found[pair[1]]
            px = math.hypot(x2 - x1, y2 - y1)
            cv2.line(out, (int(x1), int(y1)), (int(x2), int(y2)), bgr(C["linen"]), max(1, int(2 * sc)))
            lines.append(f"Markers {pair[0]}–{pair[1]}: {px:.0f} px apart")
            mm = self.mm()
            if mm and px > 0:
                s = mm / px
                lines.append(f"Scale: {s:.3f} mm/px")
                lines.append(f"Visible area ≈ {w * s:.0f} × {h * s:.0f} mm")
        self._set((status, "\n".join(lines)), bad=not ok)
        return out

    def _set(self, texts, bad):
        if texts == self._last:
            return
        self._last = texts
        self.status_lbl.config(text=texts[0], fg=C["sienna"] if bad else C["moss"])
        self.detail_lbl.config(text=texts[1])


# ----------------------------------------------------------------------------
# Mode 3: stop-motion
# ----------------------------------------------------------------------------
class StopMotion(ModeFrame):
    title = "Stop-motion"

    def __init__(self, app):
        
        super().__init__(app)
        self.project = None
        self.frames = []
        self.last_img = None
        self.playing = False
        self.play_i = -1
        self.play_t = 0.0
        self.play_img = None

        s = self.side
        make_label(s, "Frames", 11, True).pack(anchor="w", padx=16, pady=(16, 0))
        self.count_lbl = make_label(s, "0", 28, True, fg=C["sienna"])
        self.count_lbl.pack(anchor="w", padx=16)
        make_button(s, "Capture frame  (Space)", self.capture).pack(fill="x", padx=16, pady=(8, 6))
        make_button(s, "Delete last frame", self.delete_last, "secondary").pack(fill="x", padx=16, pady=3)

        make_label(s, "Onion skin (previous frame overlay)", 9).pack(anchor="w", padx=16, pady=(14, 0))
        self.onion = tk.Scale(s, from_=0, to=80, orient="horizontal", bg=C["sandstone"], fg=C["espresso"],
                              troughcolor=C["oat"], highlightthickness=0, activebackground=C["terracotta"],
                              bd=0, font=(BODY, 9), takefocus=0)
        self.onion.set(40)
        self.onion.pack(fill="x", padx=16)

        row = tk.Frame(s, bg=C["sandstone"])
        row.pack(fill="x", padx=16, pady=(12, 0))
        make_label(row, "FPS", 10, True).pack(side="left")
        self.fps_var = tk.StringVar(value=str(app.cfg.get("stopmotion_fps", 8)))
        tk.Spinbox(row, from_=1, to=30, width=5, textvariable=self.fps_var, font=(BODY, 11),
                   bg=C["linen"], fg=C["espresso"], relief="flat", buttonbackground=C["oat"],
                   highlightthickness=1, highlightbackground=C["oat"]).pack(side="left", padx=10)

        self.play_btn = make_button(s, "Play preview", self.toggle_play, "dark")
        self.play_btn.pack(fill="x", padx=16, pady=(12, 3))
        make_button(s, "Export video…", self.export).pack(fill="x", padx=16, pady=3)
        make_button(s, "Load project…", self.load_project, "secondary").pack(fill="x", padx=16, pady=3)
        make_button(s, "New project", self.new_project, "secondary").pack(fill="x", padx=16, pady=3)

    # -- lifecycle -----------------------------------------------------------
    def on_start(self):
        self.new_project(confirm=False)
        self.app.bind("<space>", self._space)

    def on_stop(self):
        self.app.unbind("<space>")
        self.playing = False
        try:
            self.app.cfg["stopmotion_fps"] = self.get_fps()
            save_config(self.app.cfg)
        except Exception:
            pass

    def _space(self, _event):
        if isinstance(self.app.focus_get(), (tk.Entry, tk.Spinbox)):
            return
        self.capture()

    def get_fps(self):
        try:
            return max(1, min(60, int(self.fps_var.get())))
        except ValueError:
            return 8

    # -- project handling ----------------------------------------------------
    def _refresh(self):
        self.count_lbl.config(text=str(len(self.frames)))
        self.last_img = read_img(self.frames[-1]) if self.frames else None

    def new_project(self, confirm=True):
        if confirm and self.frames and not messagebox.askyesno(
                "New project", "Start a new project? The current frames stay saved on disk."):
            return
        self.project = None
        self.frames = []
        self.playing = False
        self.play_btn.config(text="Play preview")
        self._refresh()

    def load_project(self):
        PROJECT_DIR.mkdir(exist_ok=True)
        folder = filedialog.askdirectory(initialdir=PROJECT_DIR, title="Choose a project folder")
        if not folder:
            return
        files = sorted(Path(folder).glob("frame_*.jpg"))
        if not files:
            messagebox.showinfo("Load project", "No frame_*.jpg files found in that folder.")
            return
        self.project = Path(folder)
        self.frames = files
        self.playing = False
        self.play_btn.config(text="Play preview")
        self._refresh()

    # -- actions -------------------------------------------------------------
    def capture(self):
        if self.playing:
            self.toggle_play()
        frame = self.stream.get_frame() if self.stream else None
        if frame is None:
            self.status_var.set("No frame available yet – wait for the stream to connect.")
            return
        if self.project is None:
            self.project = PROJECT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
            self.project.mkdir(parents=True, exist_ok=True)
        n = len(self.frames) + 1
        while (self.project / f"frame_{n:04d}.jpg").exists():
            n += 1
        path = self.project / f"frame_{n:04d}.jpg"
        write_img(path, frame)
        self.frames.append(path)
        self.last_img = frame
        self.count_lbl.config(text=str(len(self.frames)))

    def delete_last(self):
        if not self.frames:
            return
        path = self.frames.pop()
        try:
            path.unlink()
        except OSError:
            pass
        self._refresh()

    def toggle_play(self):
        if not self.frames:
            return
        self.playing = not self.playing
        self.play_btn.config(text="Stop preview" if self.playing else "Play preview")
        self.play_i = -1
        self.play_t = 0.0
        self.play_img = None

    def export(self):
        if not self.frames:
            messagebox.showinfo("Export", "Capture at least one frame first.")
            return
        if self.playing:
            self.toggle_play()
        PROJECT_DIR.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(
            defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")],
            initialdir=self.project or PROJECT_DIR,
            initialfile=f"stopmotion_{datetime.now():%Y%m%d_%H%M}.mp4")
        if not path:
            return
        first = read_img(self.frames[0])
        h, w = first.shape[:2]
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), self.get_fps(), (w, h))
        if not writer.isOpened():
            messagebox.showerror("Export", "Could not create the video file. Try another location.")
            return
        for i, f in enumerate(self.frames, 1):
            img = read_img(f)
            if img is None:
                continue
            if img.shape[:2] != (h, w):
                img = cv2.resize(img, (w, h))
            writer.write(img)
            self.status_var.set(f"Exporting… {i}/{len(self.frames)}")
            self.update_idletasks()
        writer.release()
        messagebox.showinfo("Export", f"Video saved:\n{path}")

    # -- display -------------------------------------------------------------
    def process(self, frame):
        if self.playing and self.frames:
            now = time.time()
            if self.play_img is None or now - self.play_t >= 1 / self.get_fps():
                self.play_i = (self.play_i + 1) % len(self.frames)
                self.play_img = read_img(self.frames[self.play_i])
                self.play_t = now
            img = self.play_img.copy()
            cv2.putText(img, f"Preview {self.play_i + 1}/{len(self.frames)}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, max(0.8, img.shape[1] / 1200), bgr(C["linen"]), 2)
            return img
        if frame is None:
            return None
        a = self.onion.get() / 100
        if a > 0 and self.last_img is not None:
            prev = self.last_img
            if prev.shape != frame.shape:
                prev = cv2.resize(prev, (frame.shape[1], frame.shape[0]))
            return cv2.addWeighted(frame, 1 - a, prev, a, 0)
        return frame


# ----------------------------------------------------------------------------
# Mode: height test (pose of two ArUco markers with solvePnP)
# ----------------------------------------------------------------------------
def marker_pose(quad, size_mm, K, dist):
    """Pose (rvec, tvec in mm) of one square marker from its 4 detected corners."""
    h = size_mm / 2.0
    obj = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float32)
    img = np.asarray(quad, np.float32).reshape(4, 2)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    if not ok:
        return None
    return rvec, tvec.reshape(3)


def height_between(pose_a, pose_b):
    """Returns (dh, dz, tilt).
    dz = depth difference along the camera axis (mm): the stable height measure for a camera that
         looks straight down. dh = height along marker A's normal (noisy, kept for reference).
    tilt = tilt of marker A relative to the camera axis (degrees, rough)."""
    R = cv2.Rodrigues(pose_a[0])[0]
    n = R[:, 2].copy()          # normal of marker A in camera coordinates
    if n[2] > 0:                # make it point towards the camera ("up")
        n = -n
    dh = float(np.dot(n, pose_b[1] - pose_a[1]))
    dz = float(pose_a[1][2] - pose_b[1][2])
    tilt = math.degrees(math.acos(min(1.0, abs(R[2, 2]))))
    return dh, dz, tilt


SIDE_ANGLE_DEG = 60.0     # a tag whose normal is further than this from the table normal is a side tag


def tag_normal(rvec):
    """Normal of a marker in camera coordinates, pointing towards the camera."""
    n = cv2.Rodrigues(rvec)[0][:, 2].copy()
    return -n if n[2] > 0 else n


def split_top_side(dets, ref_ids):
    """dets: [(id, pose, corner points, is_ref)]. The same ID may be seen twice (top face and side face of
    a brick). A tag is a TOP tag when its normal is within SIDE_ANGLE_DEG of the table normal (average
    normal of the visible reference markers; the camera axis if none is visible), otherwise a SIDE tag.
    Returns poses, centers, quads (top tags only) and side = [(id, pose, points)]."""
    refn = [tag_normal(pose[0]) for _m, pose, _p, isr in dets if isr]
    up = np.mean(refn, axis=0) if refn else np.array([0.0, 0.0, -1.0])
    up = up / (np.linalg.norm(up) + 1e-9)
    cos_lim = math.cos(math.radians(SIDE_ANGLE_DEG))
    best, side = {}, []
    for mid, pose, pts, isr in dets:
        al = float(np.dot(tag_normal(pose[0]), up))
        if not isr and al < cos_lim:
            side.append((mid, pose, pts))
        elif mid not in best or al > best[mid][0]:
            best[mid] = (al, pose, pts)
    poses = {m: v[1] for m, v in best.items()}
    centers = {m: v[2].mean(axis=0) for m, v in best.items()}
    quads = {m: v[2] for m, v in best.items()}
    return poses, centers, quads, side


_last_export = [0.0]


def export_poses(poses, w, h, calibrated, side_ids=()):
    """Write all marker poses of the current frame to poses.json (camera frame, mm) for Grasshopper.
    Written atomically, at most 10x per second. Failures are ignored on purpose (file locked by GH)."""
    now = time.time()
    if now - _last_export[0] < 0.1:
        return
    _last_export[0] = now
    try:
        data = {"t": now, "image": [int(w), int(h)], "calibrated": bool(calibrated), "markers": {},
                "side_ids": sorted(set(int(i) for i in side_ids))}
        for mid, (rv, tv) in poses.items():
            R = cv2.Rodrigues(rv)[0]
            data["markers"][str(mid)] = {
                "t": [round(float(v), 2) for v in tv],
                "R": [[round(float(v), 5) for v in row] for row in R]}
        tmp = POSES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(str(tmp), str(POSES_PATH))
    except Exception:
        pass


def draw_label(img, text, pos, scale, color):
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, bgr(C["walnut"]), max(3, int(5 * scale)))
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, max(1, int(2 * scale)))


def parse_ids(text):
    """'0, 1; 2 3' -> {0, 1, 2, 3}"""
    return {int(t) for t in text.replace(",", " ").replace(";", " ").split() if t.isdigit()}


def fit_table_plane(points):
    """Least-squares plane through >= 3 marker positions (mm, camera coordinates).
    Returns (centroid, unit normal pointing towards the camera, rms residual in mm),
    or None when the points lie (almost) on one line."""
    P = np.asarray(points, np.float64).reshape(-1, 3)
    c = P.mean(axis=0)
    _u, sv, vt = np.linalg.svd(P - c)
    if len(sv) < 2 or sv[1] < 50.0:
        return None
    n = vt[2]
    if n[2] > 0:                # camera looks along +Z, so "towards the camera" is -Z
        n = -n
    rms = float(np.sqrt(np.mean(((P - c) @ n) ** 2)))
    return c, n, rms


def height_above_plane(tvec, plane):
    """Signed distance (mm) of a point from the table plane; positive = above the table."""
    c, n, _rms = plane
    return float(np.dot(np.asarray(tvec, np.float64).reshape(3) - c, n))


def rank_heights(hmap):
    """hmap: {marker_id: height in mm, larger = higher}.
    Returns ([(id, height), ...] highest first, margin between #1 and #2 in mm)."""
    ranked = sorted(hmap.items(), key=lambda x: -x[1])
    margin = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else 0.0
    return ranked, margin


class HeightTest(ModeFrame):
    title = "Height test"
    N_AVG = 30
    TIE_MM = 3.0      # below this margin the "highest" marker is flagged as uncertain

    def __init__(self, app):
        super().__init__(app)
        cfg = app.cfg
        s = self.side
        self.detector, self.detector_error = None, None
        try:
            self.detector = MarkerDetector()
        except Exception as exc:
            self.detector_error = str(exc)

        make_label(s, "Marker size (mm)", 10, True).pack(anchor="w", padx=16, pady=(10, 0))
        make_label(s, "Outer side of the black square", 8).pack(anchor="w", padx=16)
        self.size_var = tk.StringVar(value=str(cfg.get("height_marker_mm", 50.0)))
        make_entry(s, self.size_var).pack(fill="x", padx=16, pady=(2, 6))

        self.mode_var = tk.StringVar(value=cfg.get("height_mode", "pair"))
        for text, val in (("Compare two markers", "pair"), ("Find the highest marker", "highest")):
            tk.Radiobutton(s, text=text, variable=self.mode_var, value=val,
                           command=self._mode_changed, **CHECK).pack(fill="x", padx=16)

        self.mode_box = tk.Frame(s, bg=C["sandstone"])
        self.mode_box.pack(fill="x", padx=8, pady=(6, 0))
        # pair mode inputs
        self.pair_box = tk.Frame(self.mode_box, bg=C["sandstone"])
        left = tk.Frame(self.pair_box, bg=C["sandstone"])
        left.pack(side="left", expand=False, fill="x", padx=(0, 4))
        right = tk.Frame(self.pair_box, bg=C["sandstone"])
        right.pack(side="left", expand=False, fill="x", padx=(4, 0))
        make_label(left, "Reference ID A", 9, True).pack(anchor="w")
        self.ref_var = tk.StringVar(value=str(cfg.get("height_ref_id", 1)))
        make_entry(left, self.ref_var).pack(fill="x")
        make_label(right, "ID B", 9, True).pack(anchor="w")
        self.tgt_var = tk.StringVar(value=str(cfg.get("height_target_id", 3)))
        make_entry(right, self.tgt_var).pack(fill="x")
        # highest mode inputs
        self.high_box = tk.Frame(self.mode_box, bg=C["sandstone"])
        make_label(self.high_box, "Candidate IDs (empty = all)", 9, True).pack(anchor="w")
        self.cand_var = tk.StringVar(value=str(cfg.get("height_candidates", "")))
        make_entry(self.high_box, self.cand_var).pack(fill="x")

        # table reference markers (flat on the table) -> plane fit
        refrow = tk.Frame(s, bg=C["sandstone"])
        refrow.pack(fill="x", padx=8, pady=(8, 0))
        rl = tk.Frame(refrow, bg=C["sandstone"])
        rl.pack(side="left", expand=True, fill="x", padx=(8, 4))
        rr = tk.Frame(refrow, bg=C["sandstone"])
        rr.pack(side="left", padx=(4, 8))
        make_label(rr, "Ref. size (mm)", 9, True).pack(anchor="w")
        self.ref_size_var = tk.StringVar(value=str(cfg.get("height_ref_size_mm", "")))
        make_entry(rr, self.ref_size_var, width=8).pack()
        make_label(rl, "Table reference IDs", 9, True).pack(anchor="w")
        self.refs_var = tk.StringVar(value=str(cfg.get("height_ref_ids", "")))
        make_entry(rl, self.refs_var).pack(fill="x")
        make_label(s, "Markers lying flat on the table (3 or more, spread out).",
                   8, wraplength=270, justify="left").pack(anchor="w", padx=16, pady=(2, 0))
        
        make_label(s, "Lens horizontal FOV (degrees)", 9, True).pack(anchor="w", padx=16, pady=(8, 0))
        make_label(s, "Standard Camera Module 3: 66, Wide: 102", 8).pack(anchor="w", padx=16)
        self.fov_var = tk.StringVar(value=str(cfg.get("camera_hfov_deg", 66.0)))
        make_entry(s, self.fov_var).pack(fill="x", padx=16, pady=(2, 0))

        make_label(s, "Marker dictionary", 9).pack(anchor="w", padx=16, pady=(8, 2))
        d = cfg.get("marker_dictionary", "4X4_50")
        self.dict_var = tk.StringVar(value=d if d in DICT_NAMES else "4X4_50")
        om = tk.OptionMenu(s, self.dict_var, *DICT_NAMES)
        om.config(bg=C["linen"], fg=C["espresso"], activebackground=C["beige"], relief="flat",
                  highlightthickness=0, font=(BODY, 10), takefocus=0, anchor="w")
        om["menu"].config(bg=C["linen"], fg=C["espresso"], font=(BODY, 10))
        om.pack(fill="x", padx=16)

        tk.Frame(s, bg=C["oat"], height=1).pack(fill="x", padx=16, pady=8)
        self.status_lbl = make_label(s, "", 12, True, wraplength=270, justify="left")
        self.status_lbl.pack(anchor="w", padx=16)
        self.detail_lbl = make_label(s, "", 10, wraplength=270, justify="left")
        self.detail_lbl.pack(anchor="w", padx=16, pady=(4, 0))

        b = make_button(s, f"Measure", self.start_measure, "dark")
        b.config(pady=6)
        b.pack(fill="x", padx=16, pady=(8, 4))
        self.meas_lbl = make_label(s, "", 10, True, fg=C["sienna"], wraplength=270, justify="left")
        self.meas_lbl.pack(anchor="w", padx=16)

        self.ref_dist_txt = ""
        self.hist = []
        self.zh = {}
        self.ref_hist = {}
        self.plane_mode = None
        self.meas_plane = False
        self.samples = None
        self._ui_t = 0.0
        self._mode_changed()

    # -- lifecycle -----------------------------------------------------------
    def on_start(self):
        self.hist, self.zh, self.ref_hist, self.samples = [], {}, {}, None
        self.plane_mode = None
        self.meas_lbl.config(text="")

    def on_stop(self):
        cfg = self.app.cfg
        sz, a, b = self.get_size(), self.get_id(self.ref_var), self.get_id(self.tgt_var)
        if sz:
            cfg["height_marker_mm"] = sz
        if a is not None:
            cfg["height_ref_id"] = a
        if b is not None:
            cfg["height_target_id"] = b
        cfg["height_mode"] = self.mode_var.get()
        cfg["height_candidates"] = self.cand_var.get().strip()
        cfg["height_ref_ids"] = self.refs_var.get().strip()
        cfg["marker_dictionary"] = self.dict_var.get()
        cfg["camera_hfov_deg"] = self.get_fov()
        save_config(cfg)

    def _mode_changed(self):
        self.pair_box.pack_forget()
        self.high_box.pack_forget()
        (self.pair_box if self.mode_var.get() == "pair" else self.high_box).pack(fill="x")
        self.hist, self.zh, self.samples = [], {}, None
        self.plane_mode = None
        self.meas_lbl.config(text="")
        self.status_lbl.config(text="")
        self.detail_lbl.config(text="")

    # -- helpers -------------------------------------------------------------
    def get_size(self):
        try:
            v = float(self.size_var.get().replace(",", "."))
        except ValueError:
            return None
        return v if v > 0 else None

    @staticmethod
    def get_id(var):
        t = var.get().strip()
        return int(t) if t.isdigit() else None

    def get_ref_size(self, size):
        """Reference marker size: empty field = same as the normal marker size, None = invalid."""
        t = self.ref_size_var.get().strip().replace(",", ".")
        if not t:
            return size
        try:
            v = float(t)
        except ValueError:
            return None
        return v if v > 0 else None

    def get_candidates(self):
        tokens = self.cand_var.get().replace(",", " ").replace(";", " ").split()
        return {int(t) for t in tokens if t.isdigit()}

    def get_fov(self):
        """Horizontal field of view in degrees (falls back to 66 when the field is not a sensible number)."""
        try:
            v = float(self.fov_var.get().replace(",", "."))
        except ValueError:
            return 66.0
        return v if 20.0 <= v <= 170.0 else 66.0

    def get_cam(self, w, h):
        """Simple pinhole camera: no distortion, principal point in the image centre,
        focal length from the horizontal field of view."""
        f = w / (2 * math.tan(math.radians(self.get_fov() / 2)))
        return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], np.float64), np.zeros(5)

    def start_measure(self):
        self.meas_plane = bool(parse_ids(self.refs_var.get()))
        self.samples = []
        self.meas_lbl.config(text="Measuring… keep the markers in view", fg=C["moss"])

    def _ui(self, status, detail, bad):
        now = time.time()
        if now - self._ui_t < 0.2:
            return
        self._ui_t = now
        self.status_lbl.config(text=status, fg=C["sienna"] if bad else C["moss"])
        self.detail_lbl.config(text=detail)

    # -- display -------------------------------------------------------------
    def process(self, frame):
        if frame is None:
            return None
        if self.detector is None:
            self._ui("✖ " + self.detector_error, "", True)
            return frame
        h, w = frame.shape[:2]
        mode = self.mode_var.get()
        size = self.get_size()
        a, b = self.get_id(self.ref_var), self.get_id(self.tgt_var)
        if size is None:
            self._ui("Enter the marker size", "", True)
            return frame
        if mode == "pair" and (a is None or b is None or a == b):
            self._ui("Enter two different marker IDs", "", True)
            return frame
        ref_ids = parse_ids(self.refs_var.get())
        ref_size = self.get_ref_size(size)
        if ref_ids and ref_size is None:
            self._ui("Reference marker size is not a number", "Fix 'Ref. size (mm)' or leave it empty.", True)
            return frame
        use_plane = bool(ref_ids)
        if use_plane != self.plane_mode:      # method changed: old values are not comparable
            self.plane_mode = use_plane
            self.hist, self.zh, self.ref_hist, self.samples = [], {}, {}, None
            self.meas_lbl.config(text="")
        K, dist = self.get_cam(w, h)

        self.detector.configure(self.dict_var.get(), True)
        corners, ids, _ = self.detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        out = frame.copy()
        sc = max(w / 1000, 0.7)
        if mode == "pair":
            wanted = {a, b}
        else:
            wanted = self.get_candidates() or None   # None = every detected marker
        dets = []
        if ids is not None:
            for quad, mid in zip(corners, ids.flatten()):
                mid = int(mid)
                pts = quad.reshape(4, 2)
                is_ref = mid in ref_ids
                use = is_ref or wanted is None or mid in wanted
                cv2.polylines(out, [pts.astype(np.int32)], True,
                              bgr(C["olive"]) if is_ref else (bgr(C["sage"]) if use else bgr(C["oat"])),
                              max(1, int(2 * sc)))
                if use:
                    pose = marker_pose(quad, ref_size if is_ref else size, K, dist)
                    if pose is not None:
                        dets.append((mid, pose, pts, is_ref))
        # the same ID can be on the top and on a side face of a brick: only top tags are measured
        poses, centers, quads, side = split_top_side(dets, ref_ids)
        for mid, pose in poses.items():
            if mid in ref_ids:
                draw_label(out, f"ref {mid}", (int(centers[mid][0]) - 25,
                           int(centers[mid][1]) - int(30 * sc)), 0.5 * sc, bgr(C["linen"]))
            elif mode == "pair":
                cv2.drawFrameAxes(out, K, dist, pose[0], pose[1].reshape(3, 1),
                                  size * 0.6, max(2, int(2 * sc)))
        for mid, _pose, pts in side:       # side tags: shown in a different colour, not measured
            cv2.polylines(out, [pts.astype(np.int32)], True, bgr(C["clay"]), max(1, int(2 * sc)))
            draw_label(out, f"side {mid}", (int(pts[:, 0].mean()) - 25, int(pts[:, 1].mean()) - int(25 * sc)),
                       0.45 * sc, bgr(C["linen"]))
        export_poses(poses, w, h, False, [m for m, _p, _q in side])   # 'calibrated' stays False: no calibration
        if mode == "pair":
            return self._pair(out, poses, centers, sc, a, b, ref_ids)
        return self._highest(out, poses, centers, quads, sc, wanted, ref_ids)

    # table plane from the flat reference markers ------------------------------
    def _table_plane(self, poses, ref_ids):
        """Fit the table plane from the reference markers. Returns (plane, ids) or None
        (after showing the reason in the status area)."""
        now = time.time()
        for mid in ref_ids & poses.keys():
            lst = self.ref_hist.get(mid, ([], 0.0))[0]
            self.ref_hist[mid] = ((lst + [poses[mid][1].copy()])[-15:], now)
        for mid in [m for m, (_l, t) in self.ref_hist.items() if now - t > 3.0 or m not in ref_ids]:
            del self.ref_hist[mid]
        pts = {m: np.median(np.array(l), axis=0) for m, (l, _t) in self.ref_hist.items()}
        if len(pts) >= 2:
            first = min(pts)
            self.ref_dist_txt = "Spacing from ref %d: " % first + ", ".join(
                "%d: %.0f mm" % (m, float(np.linalg.norm(pts[m] - pts[first]))) for m in sorted(pts) if m != first)
        else:
            self.ref_dist_txt = ""
        if len(pts) < 3:
            self._ui("Need 3 or more table reference markers",
                     f"Seen: {', '.join(map(str, sorted(pts))) or 'none'}\n"
                     f"Expected: {', '.join(map(str, sorted(ref_ids)))}\n"
                     "They must lie flat on the table and be visible.", True)
            return None
        plane = fit_table_plane([pts[m] for m in sorted(pts)])
        if plane is None:
            self._ui("Reference markers lie on one line",
                     "Spread them over the table, for example one near each corner.", True)
            return None
        return plane, sorted(pts)

    def _plane_text(self, plane, ids):
        _c, n, rms = plane
        tilt = math.degrees(math.acos(min(1.0, abs(float(n[2])))))
        txt = f"Table plane from markers {', '.join(map(str, ids))}\nCamera tilt vs. table: {tilt:.1f}°"
        if len(ids) >= 4:
            txt += f"\nPlane fit residual: {rms:.1f} mm" + ("  (a marker is not flat?)" if rms > 3.0 else "")
        if self.ref_dist_txt:
            txt += "\n" + self.ref_dist_txt
        return txt

    # pair mode ---------------------------------------------------------------
    def _pair(self, out, poses, centers, sc, a, b, ref_ids):
        missing = [str(i) for i in (a, b) if i not in poses]
        if missing:
            self._ui("Missing marker: " + ", ".join(missing),
                     "Both markers must be visible and sharp.", True)
            return out
        if ref_ids:
            res = self._table_plane(poses, ref_ids)
            if res is None:
                return out
            plane, ids = res
            ha = height_above_plane(poses[a][1], plane)
            hb = height_above_plane(poses[b][1], plane)
            dz = hb - ha
            extra = (f"Height above table: A {ha:+.1f} mm, B {hb:+.1f} mm\n"
                     + self._plane_text(plane, ids))
        else:
            dh, dz, tilt = height_between(poses[a], poses[b])
            dist_a, dist_b = float(np.linalg.norm(poses[a][1])), float(np.linalg.norm(poses[b][1]))
            extra = (f"Distance to camera: A {dist_a:.0f} mm, B {dist_b:.0f} mm\n"
                     f"Tilt of marker A (rough): {tilt:.1f}°")
        self.hist = (self.hist + [dz])[-15:]
        smooth = statistics.median(self.hist)
        self._ui(f"B is {smooth:+.1f} mm above A",
                 f"Current frame: {dz:+.1f} mm (median of last 15 shown above)\n" + extra, False)
        draw_label(out, f"A ({a}) ref", (int(centers[a][0]) - 30, int(centers[a][1]) - int(40 * sc)),
                   0.7 * sc, bgr(C["linen"]))
        draw_label(out, f"B ({b}) {smooth:+.1f} mm", (int(centers[b][0]) - 30, int(centers[b][1]) - int(40 * sc)),
                   0.7 * sc, bgr(C["linen"]))
        cv2.line(out, tuple(int(v) for v in centers[a]), tuple(int(v) for v in centers[b]),
                 bgr(C["linen"]), max(1, int(2 * sc)))

        if self.samples is not None:
            self.samples.append(dz)
            self.meas_lbl.config(text=f"Measuring… {len(self.samples)}/{self.N_AVG}", fg=C["moss"])
            if len(self.samples) >= self.N_AVG:
                hs = self.samples
                self.meas_lbl.config(
                    text=(f"Result: B is {statistics.mean(hs):.1f} mm above A\n"
                          f"± {statistics.pstdev(hs):.1f} mm (std, n={len(hs)})\n"
                          f"Range {min(hs):.1f} to {max(hs):.1f} mm"),
                    fg=C["sienna"])
                self.samples = None
        return out

    # highest mode ------------------------------------------------------------
    def _highest(self, out, poses, centers, quads, sc, wanted, ref_ids):
        now = time.time()
        plane_txt = ""
        plane = None
        if ref_ids:
            res = self._table_plane(poses, ref_ids)
            if res is None:
                return out
            plane, ids = res
            plane_txt = "\n" + self._plane_text(plane, ids)
        # "up" value per candidate marker: height above the table plane (plane method) or the
        # negative camera distance (old method, only valid for a camera looking straight down)
        frame_up = {}
        for mid, (_rv, tv) in poses.items():
            if mid in ref_ids:
                continue
            frame_up[mid] = height_above_plane(tv, plane) if plane else -float(tv[2])
            lst = self.zh.get(mid, ([], 0.0))[0]
            self.zh[mid] = ((lst + [frame_up[mid]])[-15:], now)
        for mid in [m for m, (_l, t) in self.zh.items() if now - t > 0.5]:
            del self.zh[mid]
        cur = {m: statistics.median(l) for m, (l, _t) in self.zh.items()}
        if wanted:
            cur = {m: z for m, z in cur.items() if m in wanted}
        missing = sorted(wanted - ref_ids - cur.keys()) if wanted else []
        miss_txt = ("\nNot visible: " + ", ".join(map(str, missing))) if missing else ""
        if len(cur) < (1 if plane else 2):
            need = "at least one marker" if plane else "at least two markers"
            self._ui(f"Need {need} in view",
                     "Visible: " + (", ".join(map(str, sorted(cur))) or "none") + miss_txt + plane_txt, True)
            return out

        base = 0.0 if plane else min(cur.values())      # plane: absolute height above the table
        ranked, margin = rank_heights({m: v - base for m, v in cur.items()})
        top = ranked[0][0]
        unsure = len(ranked) > 1 and margin < self.TIE_MM
        for mid, hgt in ranked:
            if mid not in quads:
                continue
            pts = quads[mid]
            cx, cy = centers[mid]
            if mid == top:
                cv2.polylines(out, [pts.astype(np.int32)], True, bgr(C["terracotta"]), max(4, int(6 * sc)))
                draw_label(out, f"HIGHEST  ID {mid}", (int(cx) - 70, int(cy) - int(50 * sc)),
                           0.8 * sc, bgr(C["clay"]))
            draw_label(out, f"{mid}: {hgt:+.1f} mm", (int(cx) - 40, int(cy) + int(55 * sc)),
                       0.6 * sc, bgr(C["linen"]))

        lines = [f"{'★' if i == 0 else '   '} ID {m}:  {hgt:+.1f} mm" for i, (m, hgt) in enumerate(ranked[:8])]
        if len(ranked) > 1:
            lines.append(f"Margin to #2: {margin:.1f} mm")
        status = f"Highest: marker {top}" + ("  (too close to call)" if unsure else "")
        head = "Height above the table" if plane else "Height above the lowest marker"
        self._ui(status, head + "\n" + "\n".join(lines) + miss_txt + plane_txt, unsure)

        if self.samples is not None and len(frame_up) >= (1 if plane else 2):
            self.samples.append({m: v for m, v in frame_up.items() if not wanted or m in wanted})
            self.meas_lbl.config(text=f"Measuring… {len(self.samples)}/{self.N_AVG}", fg=C["moss"])
            if len(self.samples) >= self.N_AVG:
                self._finish_highest_measure()
        return out

    def _finish_highest_measure(self):
        per = {}
        votes = {}
        samples = [fr for fr in self.samples if fr]
        for fr in samples:
            win = max(fr, key=fr.get)
            votes[win] = votes.get(win, 0) + 1
            for m, v in fr.items():
                per.setdefault(m, []).append(v)
        n = len(samples)
        plane = self.meas_plane
        means = {m: statistics.mean(v) for m, v in per.items() if len(v) >= 3}
        self.samples = None
        if len(means) < (1 if plane else 2):
            self.meas_lbl.config(text="Not enough data – keep the markers visible.", fg=C["sienna"])
            return
        base = 0.0 if plane else min(means.values())
        ranked, margin = rank_heights({m: v - base for m, v in means.items()})
        unsure = len(ranked) > 1 and margin < self.TIE_MM
        lines = [f"Highest: marker {ranked[0][0]}" + (" (too close to call)" if unsure else "")]
        for m, hgt in ranked[:6]:
            sd = statistics.pstdev(per[m])
            lines.append(f"ID {m}: {hgt:+.1f} mm (±{sd:.1f}), top in {votes.get(m, 0)}/{n}")
        if len(ranked) > 1:
            lines.append(f"Margin to #2: {margin:.1f} mm")
        self.meas_lbl.config(text="\n".join(lines), fg=C["sienna"])


# ----------------------------------------------------------------------------
# Main menu
# ----------------------------------------------------------------------------
class Menu(tk.Frame):
    def __init__(self, app):
        super().__init__(app.container, bg=C["linen"])
        self.app = app
        inner = tk.Frame(self, bg=C["linen"])
        inner.place(relx=0.5, rely=0.5, anchor="center")

        strip = tk.Frame(inner, bg=C["linen"])
        strip.pack(pady=(0, 18))
        for key in ("linen", "sandstone", "beige", "oat", "clay", "terracotta", "sienna", "rust",
                    "sage", "olive", "moss", "forest", "cocoa", "chestnut", "walnut", "espresso"):
            tk.Frame(strip, bg=C[key], width=34, height=8,
                     highlightthickness=1 if key in ("linen", "sandstone") else 0,
                     highlightbackground=C["oat"]).pack(side="left")

        tk.Label(inner, text="Table Camera Toolkit", bg=C["linen"], fg=C["espresso"],
                 font=(HEAD, 34, "bold")).pack()
        tk.Label(inner, text="Watch, check and capture your workspace", bg=C["linen"],
                 fg=C["cocoa"], font=(HEAD, 14, "italic")).pack(pady=(2, 20))

        card = tk.Frame(inner, bg=C["sandstone"], padx=28, pady=24)
        card.pack()

        tk.Label(card, text="RTSP stream address", bg=C["sandstone"], fg=C["espresso"],
                 font=(BODY, 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        self.url_var = tk.StringVar(value=app.cfg["stream_url"])
        make_entry(card, self.url_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 20), ipady=4)

        items = [
            ("Live view", "live", "primary", "Just watch the stream"),
            ("Marker check", "markers", "primary", "Detect ArUco markers and verify they are visible"),
            ("Height test", "height", "primary", "Height difference between markers, or find the highest"),
            ("Stop-motion", "stopmotion", "primary", "Capture frames and export a video"),
            ("Manual", "manual", "secondary", "How everything works"),
        ]
        for i, (text, key, kind, desc) in enumerate(items, start=2):
            make_button(card, text, lambda k=key: app.open_mode(k), kind, width=18).grid(
                row=i, column=0, sticky="ew", pady=5)
            tk.Label(card, text=desc, bg=C["sandstone"], fg=C["cocoa"], font=(BODY, 10),
                     anchor="w").grid(row=i, column=1, sticky="w", padx=(18, 0))

    def start(self):
        pass

    def stop(self):
        pass


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Table Camera Toolkit")
        self.geometry("1200x780")
        self.minsize(960, 640)
        self.configure(bg=C["linen"])
        self.cfg = load_config()
        self.container = tk.Frame(self, bg=C["linen"])
        self.container.pack(fill="both", expand=True)
        self.menu = Menu(self)
        self.modes = {"live": LiveView(self), "markers": MarkerCheck(self),
                      "height": HeightTest(self),
                      "stopmotion": StopMotion(self)}
        self.current = None
        self.manual = None
        self.show_menu()
        self.bind("<Escape>", lambda e: self.show_menu())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _switch(self, frame):
        if self.current is not None:
            self.current.stop()
            self.current.pack_forget()
        self.current = frame
        frame.pack(fill="both", expand=True)

    def show_menu(self):
        if self.current is self.menu:
            return
        self._switch(self.menu)

    def open_mode(self, key):
        if key == "manual":
            if self.manual is None or not self.manual.winfo_exists():
                self.manual = ManualWindow(self)
            else:
                self.manual.lift()
            return
        url = self.menu.url_var.get().strip()
        if not url:
            messagebox.showwarning("Stream address", "Please enter the stream address first.")
            return
        self.cfg["stream_url"] = url
        save_config(self.cfg)
        mode = self.modes[key]
        self._switch(mode)
        mode.start()

    def on_close(self):
        if self.current is not None:
            self.current.stop()
        self.destroy()


def main():
    try:  # sharp text on high-DPI Windows screens
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
