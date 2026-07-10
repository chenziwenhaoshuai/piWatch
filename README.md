# PiWatch

PiWatch is a Raspberry Pi CSI camera Web application with an MJPEG preview, COCO
class filtering, YOLO26n NCNN detection overlays, and device health monitoring.

## Features

- Raspberry Pi CSI camera preview and snapshots
- YOLO26n COCO detection with selectable classes
- Fixed-input NCNN profiles for 256, 320, 416, 512, and 640 pixels
- Configurable detection rate from 1 FPS to any integer value, or unlimited
- Fast detection overlay updates independent from the 3-second system monitor
- CPU temperature, CPU utilization, memory, load average, uptime, and disk usage
- Responsive Web UI served directly by the Python application

## Included models

The repository includes the original `models/yolo26n.pt` COCO weight and five
NCNN exports. NCNN exports use fixed input shapes, so PiWatch selects a separate
model directory when the Web UI inference size changes.

| Web setting | Model directory | Raspberry Pi 4B observed inference |
| --- | --- | --- |
| 256 | `models/yolo26n_256_ncnn_model` | about 67-88 ms |
| 320 | `models/yolo26n_320_ncnn_model` | about 104-140 ms |
| 416 | `models/yolo26n_ncnn_model` | about 170-220 ms |
| 512 | `models/yolo26n_512_ncnn_model` | about 260-280 ms |
| 640 | `models/yolo26n_640_ncnn_model` | about 395-420 ms |

Performance depends on cooling, camera load, selected classes, and other system
activity. With 320 and unlimited detection enabled, the tested Raspberry Pi 4B
processed about 8-9 FPS. The browser overlay observed about 7-8.5 updates per
second over Wi-Fi.

The bundled Ultralytics model and exports are subject to the license recorded in
each model's `metadata.yaml`. Review the Ultralytics licensing terms before
redistributing or using them commercially.

## Raspberry Pi setup

Requirements:

- Raspberry Pi OS with `rpicam-vid` or `libcamera-vid`
- Python 3
- A CSI camera supported by Raspberry Pi OS
- `ultralytics`, OpenCV, NCNN, and their runtime dependencies

Install the application under `/opt/piwatch`:

```bash
sudo git clone https://github.com/chenziwenhaoshuai/piWatch.git /opt/piwatch
sudo chown -R pi:pi /opt/piwatch
python3 -m venv --system-site-packages /opt/piwatch-venv
/opt/piwatch-venv/bin/pip install -r /opt/piwatch/requirements.txt
sudo install -d -o pi -g pi /var/lib/piwatch/data /var/lib/piwatch/recordings
sudo install -m 0644 /opt/piwatch/systemd/piwatch.service /etc/systemd/system/piwatch.service
sudo systemctl daemon-reload
sudo systemctl enable --now piwatch.service
```

The service uses four CPU threads and loads models from `/opt/piwatch/models`.
Open `http://security-camera.local:8080/` or the Raspberry Pi IP address on port
8080.

If Debian/Raspberry Pi OS does not provide compatible Torch packages for the
active Python version, install the platform-supported Torch and torchvision
packages before installing `requirements.txt`.

## Web settings

The settings page controls:

- CSI preview resolution and frame rate
- YOLO inference size
- YOLO target rate, or unlimited processing
- COCO target classes, including a person-and-animal preset

Preview resolution and inference resolution are independent. For example, the
camera can remain at 1280 x 720 while YOLO uses the 320 model.

An unlimited target rate means PiWatch starts the next inference as soon as the
previous one completes. It can keep multiple CPU cores busy and raise the device
temperature. Use an explicit FPS value for lower power consumption.

## Local development

The basic server starts with:

```powershell
python run.py
```

Open `http://127.0.0.1:8080/`. Camera streaming and NCNN inference require the
corresponding Raspberry Pi camera tools and model runtime.

## Verification

Run the project tests with:

```bash
python -m unittest tests.test_core
```

USB preview and microphone capture are not implemented in the current version.
