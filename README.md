# PiWatch

[中文](#中文说明) | [English](#english)

## 中文说明

PiWatch 是运行在树莓派上的 CSI 摄像头 Web 应用，提供实时预览、YOLO26n
目标检测、移动检测、分段录像、重点录像管理、设备状态监测和 SMTP 邮件报警。

### 功能

- CSI 摄像头 MJPEG 实时预览和截图
- YOLO26n NCNN 推理，可筛选 80 个 COCO 类别
- 256、320、416、512、640 五种固定输入尺寸
- 推理帧率可设置为 1 FPS 以上的任意整数或不限制
- 在实时画面中快速更新目标框
- H.264 MP4 持续录像，默认每 60 秒生成一个切片
- 默认最大录像容量 64 GB，超限后优先删除最老的非重点录像
- 移动检测和 YOLO 目标自动标记重点录像
- 可选择只保留重点录像
- 支持跨午夜的每日警戒时间和独立警戒录像目录
- Web 端预览、筛选和删除普通、警戒及重点录像
- CPU 温度、CPU 利用率、内存、负载、运行时间和磁盘监测
- 设置修改后自动保存，无需点击保存按钮
- SMTP 邮件报警，附带事件信息和当前 JPEG 截图
- 移动检测邮件、YOLO 邮件和仅警戒时段发送可独立控制

### 内置模型

仓库包含原始 `models/yolo26n.pt` COCO 权重和五种 NCNN 导出模型。
NCNN 模型使用固定输入尺寸，Web 端修改推理尺寸时会自动选择对应目录。

| Web 设置 | 模型目录 | 树莓派 4B 实测推理时间 |
| --- | --- | --- |
| 256 | `models/yolo26n_256_ncnn_model` | 约 67-88 ms |
| 320 | `models/yolo26n_320_ncnn_model` | 约 104-140 ms |
| 416 | `models/yolo26n_ncnn_model` | 约 170-220 ms |
| 512 | `models/yolo26n_512_ncnn_model` | 约 260-280 ms |
| 640 | `models/yolo26n_640_ncnn_model` | 约 395-420 ms |

性能会受到散热、摄像头负载、检测类别和后台任务影响。树莓派 4B 在 320
输入尺寸、不限制推理帧率时，实测约为 8-9 FPS。

内置 Ultralytics 权重和导出模型受各模型目录中 `metadata.yaml` 所记录的许可约束。
重新分发或商用前请检查 Ultralytics 的许可条款。

### 树莓派安装

需要 Raspberry Pi OS、受支持的 CSI 摄像头、`rpicam-vid` 或
`libcamera-vid`、Python 3、FFmpeg、Ultralytics、OpenCV 和 NCNN 运行环境。

```bash
sudo git clone https://github.com/chenziwenhaoshuai/piWatch.git /opt/piwatch
sudo chown -R pi:pi /opt/piwatch
sudo apt-get install -y python3-venv python3-opencv python3-torch python3-torchvision ffmpeg
python3 -m venv --system-site-packages /opt/piwatch-venv
/opt/piwatch-venv/bin/pip install -r /opt/piwatch/requirements.txt
sudo install -d -o pi -g pi /var/lib/piwatch/data /var/lib/piwatch/recordings
sudo install -m 0644 /opt/piwatch/systemd/piwatch.service /etc/systemd/system/piwatch.service
sudo systemctl daemon-reload
sudo systemctl enable --now piwatch.service
```

服务从 `/opt/piwatch/models` 加载模型。安装完成后访问
`http://security-camera.local:8080/`，也可以使用树莓派 IP 地址的 8080 端口。

如果系统没有与当前 Python 版本兼容的 Torch 包，请先安装平台支持的 Torch 和
torchvision，再安装 `requirements.txt`。

### Web 设置

Web 页面可以设置摄像头分辨率和帧率、YOLO 输入尺寸和推理帧率、COCO
检测类别、录像切片时长、最大录像容量、重点录像策略、移动检测阈值、每日警戒
时间和 SMTP 邮件通知。设置变化约 600 毫秒后自动保存。

预览分辨率和推理分辨率相互独立。例如摄像头可保持 1280 x 720，同时使用
320 模型推理。不限制推理帧率会增加 CPU 占用和温度，需要低功耗运行时应设置
明确的帧率上限。

### SMTP 邮件设置

展开 Web 设置页中的“SMTP 邮件通知”，填写以下内容：

| 设置项 | 说明 |
| --- | --- |
| 启用邮件通知 | 邮件功能总开关 |
| 发送移动检测 | 移动画面达到阈值时发送事件邮件 |
| 发送 YOLO 事件 | 检测到已选择的 COCO 目标时发送邮件 |
| 只发送警戒时间内的事件 | 仅在录像设置的每日警戒开始和结束时间之间发送事件邮件 |
| SMTP 服务器 | 邮箱服务商提供的 SMTP 主机名 |
| SMTP 端口 | SSL/TLS 常用 465，STARTTLS 常用 587 |
| 连接加密 | 按服务商要求选择 SSL/TLS、STARTTLS 或无加密 |
| 发件邮箱 | 邮件显示的发件地址 |
| SMTP 用户名 | 通常为完整邮箱地址 |
| SMTP 密码/授权码 | 邮箱服务商生成的 SMTP 授权码，通常不是邮箱登录密码 |
| 收件邮箱 | 接收报警邮件的地址，可与发件邮箱相同 |
| 邮件主题前缀 | 默认 `[PiWatch]` |

等待页面显示“已自动保存”后点击“发送测试邮件”。测试邮件不受移动、YOLO
和警戒时间开关限制，并会在摄像头画面可用时附带当前截图。

QQ 邮箱示例：

```text
SMTP 服务器: smtp.qq.com
SMTP 端口: 465
连接加密: SSL/TLS
发件邮箱: 你的QQ邮箱@qq.com
SMTP 用户名: 你的QQ邮箱@qq.com
SMTP 密码/授权码: QQ邮箱生成的SMTP授权码
收件邮箱: 接收报警的邮箱地址
```

需要先在 QQ 邮箱的账号设置中启用 SMTP 服务并生成授权码。不要填写 QQ
登录密码，也不要把授权码提交到 Git。SMTP 凭据只保存在树莓派本地的 PiWatch
设置数据库中。事件邮件在后台线程发送，SMTP 服务器不可用时不会阻塞录像和检测。

### 录像存储

普通录像位于 `recordings/regular`，在每日警戒时间内开始的切片位于
`recordings/alert` 并自动标记为重点。YOLO 和移动事件也会将当前切片标记为重点。

启用“只保留重点视频”后，非重点切片在结束时删除。超过容量限制时，PiWatch
先删除最老的非重点录像，必要时再删除最老的重点录像。已完成的录像支持浏览器
拖动播放和 Web 删除，正在写入的切片不能删除。

### 本地开发与验证

```powershell
python run.py
```

访问 `http://127.0.0.1:8080/`。摄像头和 NCNN 推理需要对应的树莓派运行环境。

```bash
python -m unittest tests.test_core
```

当前版本尚未实现 USB 摄像头预览和麦克风采集。

## English

PiWatch is a Raspberry Pi CSI camera Web application with live MJPEG preview,
YOLO26n detection, motion detection, segmented recording, important-recording
management, device monitoring, and SMTP event notifications.

### Features

- CSI camera preview and snapshots
- YOLO26n NCNN detection with selectable COCO classes
- Fixed-input NCNN profiles for 256, 320, 416, 512, and 640 pixels
- Configurable inference rate from 1 FPS upward, or unlimited
- Fast detection overlays on the live view
- Continuous H.264 MP4 recording with 60-second segments by default
- Configurable storage quota with oldest-file cleanup, defaulting to 64 GB
- Motion and YOLO events automatically mark recordings as important
- Optional important-only retention
- Daily alert windows, including schedules that cross midnight
- Separate regular and alert recording areas
- Browser playback, filtering, seeking, and deletion
- CPU temperature, utilization, memory, load, uptime, and disk monitoring
- Debounced automatic settings persistence without a save button
- SMTP event messages with event details and a current JPEG snapshot
- Independent switches for motion mail, YOLO mail, and alert-window-only delivery

### Included Models

The repository includes the original `models/yolo26n.pt` COCO weight and five
fixed-input NCNN exports. PiWatch automatically selects the matching model when
the inference size is changed in the Web UI.

| Web setting | Model directory | Observed Raspberry Pi 4B inference |
| --- | --- | --- |
| 256 | `models/yolo26n_256_ncnn_model` | about 67-88 ms |
| 320 | `models/yolo26n_320_ncnn_model` | about 104-140 ms |
| 416 | `models/yolo26n_ncnn_model` | about 170-220 ms |
| 512 | `models/yolo26n_512_ncnn_model` | about 260-280 ms |
| 640 | `models/yolo26n_640_ncnn_model` | about 395-420 ms |

Performance depends on cooling, camera load, selected classes, and background
activity. A Raspberry Pi 4B processed about 8-9 FPS with the 320 model and an
unlimited target rate in testing.

The bundled Ultralytics weights and exports are subject to the license recorded
in each model's `metadata.yaml`. Review the Ultralytics terms before redistribution
or commercial use.

### Raspberry Pi Installation

PiWatch requires Raspberry Pi OS, a supported CSI camera, `rpicam-vid` or
`libcamera-vid`, Python 3, FFmpeg, Ultralytics, OpenCV, and the NCNN runtime.

```bash
sudo git clone https://github.com/chenziwenhaoshuai/piWatch.git /opt/piwatch
sudo chown -R pi:pi /opt/piwatch
sudo apt-get install -y python3-venv python3-opencv python3-torch python3-torchvision ffmpeg
python3 -m venv --system-site-packages /opt/piwatch-venv
/opt/piwatch-venv/bin/pip install -r /opt/piwatch/requirements.txt
sudo install -d -o pi -g pi /var/lib/piwatch/data /var/lib/piwatch/recordings
sudo install -m 0644 /opt/piwatch/systemd/piwatch.service /etc/systemd/system/piwatch.service
sudo systemctl daemon-reload
sudo systemctl enable --now piwatch.service
```

Models are loaded from `/opt/piwatch/models`. Open
`http://security-camera.local:8080/` or port 8080 on the Raspberry Pi IP address.

If the operating system does not provide Torch packages compatible with the
active Python version, install platform-supported Torch and torchvision packages
before installing `requirements.txt`.

### Web Settings

The Web UI controls camera resolution and FPS, YOLO input size and target rate,
COCO classes, recording segment duration and quota, retention policy, motion
thresholds, the daily alert schedule, and SMTP notifications. Changes are saved
automatically after about 600 ms.

Preview and inference resolutions are independent. For example, the camera may
remain at 1280 x 720 while YOLO uses the 320 model. Unlimited inference can raise
CPU utilization and temperature; use an explicit FPS limit for lower power use.

### SMTP Configuration

Expand "SMTP Email Notifications" on the settings page and configure:

| Field | Description |
| --- | --- |
| Enable email notifications | Master switch for event mail |
| Send motion detections | Send mail when motion exceeds the configured threshold |
| Send YOLO events | Send mail for selected COCO detections |
| Only send events during alert hours | Apply the recording alert start/end schedule to event mail |
| SMTP server | SMTP hostname provided by the mail service |
| SMTP port | Commonly 465 for SSL/TLS or 587 for STARTTLS |
| Connection security | SSL/TLS, STARTTLS, or none, as required by the provider |
| Sender address | Address displayed as the sender |
| SMTP username | Usually the complete email address |
| SMTP password/app password | Provider-generated SMTP app password, usually not the login password |
| Recipient address | Destination for alerts; it may match the sender |
| Subject prefix | Defaults to `[PiWatch]` |

Wait for the automatic-save confirmation, then select "Send Test Email". Test
messages are not restricted by the motion, YOLO, or alert-window switches. A
current camera snapshot is attached when a frame is available.

QQ Mail example:

```text
SMTP server: smtp.qq.com
SMTP port: 465
Connection security: SSL/TLS
Sender address: your-account@qq.com
SMTP username: your-account@qq.com
SMTP password/app password: the SMTP authorization code generated by QQ Mail
Recipient address: the address that should receive alerts
```

Enable SMTP in QQ Mail account settings and generate an authorization code first.
Do not use the QQ login password and never commit the authorization code to Git.
Credentials are stored only in PiWatch's local settings database on the Raspberry
Pi. Mail is sent in a background thread, so an unavailable SMTP server does not
block recording or detection.

### Recording Storage

Regular recordings are stored below `recordings/regular`. Segments started during
the daily alert window are stored below `recordings/alert` and marked important.
YOLO and motion events also mark the active segment as important.

With important-only retention enabled, unimportant segments are deleted when they
close. When the quota is exceeded, PiWatch removes the oldest non-important files
first and then the oldest important files if necessary. Completed recordings
support HTTP Range seeking and Web deletion; an active segment cannot be deleted.

### Local Development and Verification

```powershell
python run.py
```

Open `http://127.0.0.1:8080/`. Camera streaming and NCNN inference require the
corresponding Raspberry Pi runtime.

```bash
python -m unittest tests.test_core
```

USB camera preview and microphone capture are not implemented in the current
version.
