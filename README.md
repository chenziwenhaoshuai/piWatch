# PiWatch MVP

## Run locally

```powershell
python run.py
```

Open http://127.0.0.1:8080.

## Raspberry Pi 4B

Install Raspberry Pi OS camera tools and FFmpeg, then run `python3 run.py`.
The Web UI supports CSI/USB selection, video width/height/FPS, retention days, SMTP settings, configurable abnormal event types, YOLO model selection, target classes, normalized ROI, confidence threshold, and alert cooldown.

USB microphone support is intentionally not implemented yet, per the current product decision.

## YOLO detection

Install the optional packages on the Raspberry Pi:

```bash
python3 -m pip install -r requirements.txt
```

Enable YOLO in the Web UI, set a model such as `yolo11n.pt`, choose target classes such as `person,car`, and configure ROI values from `0` to `1`. A matching detection creates a `yolo_target_detected` event and sends an SMTP alert when that event type is enabled and email is configured.

The first model load may download weights. On Raspberry Pi 4B, use a nano model and a low sampling FPS.
