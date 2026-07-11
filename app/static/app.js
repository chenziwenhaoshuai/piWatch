const $ = (selector) => document.querySelector(selector);
const form = $('#settings');
const liveView = $('#live-view');
const detectionCanvas = $('#detection-overlay');
const detectionContext = detectionCanvas.getContext('2d');
let settingsLoaded = false;
let lastDetectionUpdate = null;
let recordingFilter = 'all';
let saveTimer = null;
let saveInProgress = false;
let saveQueued = false;

const COCO_GROUPS = [
  ['人员', [['person', '人']]],
  ['交通工具', [['bicycle', '自行车'], ['car', '汽车'], ['motorcycle', '摩托车'], ['airplane', '飞机'], ['bus', '公交车'], ['train', '火车'], ['truck', '卡车'], ['boat', '船']]],
  ['道路与户外', [['traffic light', '交通灯'], ['fire hydrant', '消防栓'], ['stop sign', '停车标志'], ['parking meter', '停车计时器'], ['bench', '长椅']]],
  ['动物', [['bird', '鸟'], ['cat', '猫'], ['dog', '狗'], ['horse', '马'], ['sheep', '羊'], ['cow', '牛'], ['elephant', '大象'], ['bear', '熊'], ['zebra', '斑马'], ['giraffe', '长颈鹿']]],
  ['随身物品', [['backpack', '背包'], ['umbrella', '雨伞'], ['handbag', '手提包'], ['tie', '领带'], ['suitcase', '行李箱']]],
  ['运动用品', [['frisbee', '飞盘'], ['skis', '滑雪板'], ['snowboard', '单板滑雪板'], ['sports ball', '球'], ['kite', '风筝'], ['baseball bat', '棒球棒'], ['baseball glove', '棒球手套'], ['skateboard', '滑板'], ['surfboard', '冲浪板'], ['tennis racket', '网球拍']]],
  ['餐具与食物', [['bottle', '瓶子'], ['wine glass', '酒杯'], ['cup', '杯子'], ['fork', '叉子'], ['knife', '刀'], ['spoon', '勺子'], ['bowl', '碗'], ['banana', '香蕉'], ['apple', '苹果'], ['sandwich', '三明治'], ['orange', '橙子'], ['broccoli', '西兰花'], ['carrot', '胡萝卜'], ['hot dog', '热狗'], ['pizza', '披萨'], ['donut', '甜甜圈'], ['cake', '蛋糕']]],
  ['家具', [['chair', '椅子'], ['couch', '沙发'], ['potted plant', '盆栽'], ['bed', '床'], ['dining table', '餐桌'], ['toilet', '马桶']]],
  ['电子设备', [['tv', '电视'], ['laptop', '笔记本电脑'], ['mouse', '鼠标'], ['remote', '遥控器'], ['keyboard', '键盘'], ['cell phone', '手机']]],
  ['家居用品', [['microwave', '微波炉'], ['oven', '烤箱'], ['toaster', '烤面包机'], ['sink', '水槽'], ['refrigerator', '冰箱'], ['book', '书'], ['clock', '时钟'], ['vase', '花瓶'], ['scissors', '剪刀'], ['teddy bear', '泰迪熊'], ['hair drier', '吹风机'], ['toothbrush', '牙刷']]],
];

const COMMON_CLASSES = new Set(['person', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe']);

function message(text, error = false) {
  $('#message').textContent = text;
  $('#message').className = error ? 'error-text' : 'success-text';
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw Error(data.error?.message || '请求失败');
  return data;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '--';
  const gib = bytes / (1024 ** 3);
  return `${gib.toFixed(gib >= 10 ? 1 : 2)} GB`;
}

function formatDuration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

function formatDate(value) {
  if (!value) return '--';
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function formatUptime(seconds) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `已运行 ${days ? `${days} 天 ` : ''}${hours} 小时 ${minutes} 分`;
}

function setMeter(selector, value, warning = 75, danger = 90) {
  const meter = $(selector);
  const bounded = Math.max(0, Math.min(100, Number(value) || 0));
  meter.style.width = `${bounded}%`;
  meter.className = bounded >= danger ? 'danger' : bounded >= warning ? 'warning' : '';
}

function renderStatus(data) {
  const camera = data.camera || {};
  const storage = data.storage || {};
  const system = data.system || {};
  const memory = system.memory || {};
  const rows = [
    ['摄像头', camera.connected ? '已连接' : '未连接'],
    ['接口', `${camera.source_type || '-'} / ${camera.device || '-'}`],
    ['画面', camera.streaming ? '正在传输' : '等待访问'],
    ['规格', `${camera.width || '-'} × ${camera.height || '-'} · ${camera.fps || '-'} FPS`],
    ['存储占用', `${storage.used_percent ?? '-'}%`],
    ['录像空间', `${formatBytes(storage.recording_bytes)} / ${data.recording?.max_storage_gb ?? '-'} GB`],
    ['持续录像', data.recording?.active ? `切片 #${data.recording.recording_id || '-'}` : data.recording?.enabled ? '启动中' : '已关闭'],
    ['移动检测', data.recording?.last_motion_at ? `${data.recording.motion_score}%` : '等待变化'],
  ];
  $('#status').innerHTML = rows.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join('');
  const health = $('#health');
  health.textContent = camera.connected ? '设备在线' : '摄像头离线';
  health.className = `badge ${camera.connected ? 'online' : 'offline'}`;

  const temperature = system.cpu_temperature_c;
  $('#cpu-temp').textContent = temperature == null ? '--' : `${temperature.toFixed(1)} °C`;
  $('#cpu-temp-note').textContent = temperature == null ? '温度传感器不可用' : temperature >= 80 ? '温度过高' : temperature >= 70 ? '温度偏高' : '温度正常';
  setMeter('#cpu-temp-bar', temperature == null ? 0 : temperature, 70, 80);
  $('#cpu-usage').textContent = `${system.cpu_percent ?? 0}%`;
  $('#load-average').textContent = `负载 ${(system.load_average || []).join(' / ') || '--'}`;
  setMeter('#cpu-usage-bar', system.cpu_percent, 70, 90);
  $('#memory-usage').textContent = `${memory.used_percent ?? 0}%`;
  $('#memory-detail').textContent = `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}`;
  setMeter('#memory-usage-bar', memory.used_percent, 75, 90);
  $('#uptime').textContent = formatUptime(system.uptime_seconds || 0);
  renderYoloStatus(data.yolo || {});
}

function renderYoloStatus(yolo) {
  renderDetections(yolo);
  const yoloBadge = $('#yolo-badge');
  const active = yolo.enabled && yolo.running && !yolo.error;
  yoloBadge.textContent = active ? '运行中' : yolo.error ? '异常' : yolo.enabled ? '启动中' : '未启用';
  yoloBadge.className = `badge ${active ? 'online' : yolo.error ? 'offline' : 'neutral'}`;
  const inference = yolo.last_inference_ms == null ? '--' : `${yolo.last_inference_ms} ms`;
  const inputSize = yolo.imgsz ? ` · 输入 ${yolo.imgsz}` : '';
  const actualFps = yolo.actual_fps == null ? '' : ` · ${yolo.actual_fps} FPS`;
  $('#yolo-detail').textContent = `${yolo.model_path?.split('/').pop() || 'YOLO'}${inputSize} · 推理 ${inference}${actualFps} · 当前 ${yolo.last_detections?.length || 0} 个目标`;
}

function renderDetections(yolo) {
  const frameSize = yolo.frame_size;
  const detections = yolo.last_detections || [];
  const width = liveView.clientWidth;
  const height = liveView.clientHeight;
  if (!width || !height) return;
  if (detectionCanvas.width !== width || detectionCanvas.height !== height) {
    detectionCanvas.width = width;
    detectionCanvas.height = height;
  }
  detectionContext.clearRect(0, 0, width, height);
  if (!frameSize || !detections.length) return;
  const [sourceWidth, sourceHeight] = frameSize;
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const offsetX = (width - sourceWidth * scale) / 2;
  const offsetY = (height - sourceHeight * scale) / 2;
  detectionContext.font = '600 13px "Segoe UI", sans-serif';
  detectionContext.lineWidth = 2;
  detections.forEach((detection, index) => {
    const [x1, y1, x2, y2] = detection.box;
    const x = offsetX + x1 * scale;
    const y = offsetY + y1 * scale;
    const boxWidth = (x2 - x1) * scale;
    const boxHeight = (y2 - y1) * scale;
    const color = ['#24d17e', '#38bdf8', '#f59e0b', '#f472b6'][index % 4];
    const label = `${detection.label} ${Math.round(detection.confidence * 100)}%`;
    detectionContext.strokeStyle = color;
    detectionContext.fillStyle = color;
    detectionContext.strokeRect(x, y, boxWidth, boxHeight);
    const labelWidth = detectionContext.measureText(label).width + 12;
    const labelY = Math.max(0, y - 23);
    detectionContext.fillRect(x, labelY, labelWidth, 23);
    detectionContext.fillStyle = '#071014';
    detectionContext.fillText(label, x + 6, labelY + 16);
  });
}

function buildClassSelector() {
  $('#class-groups').innerHTML = COCO_GROUPS.map(([group, classes]) => `
    <fieldset class="class-group">
      <legend>${group}</legend>
      <div class="class-options">
        ${classes.map(([value, label]) => `<label class="class-option"><input type="checkbox" name="target_class" value="${value}"><span>${label}<small>${value}</small></span></label>`).join('')}
      </div>
    </fieldset>`).join('');
  document.querySelectorAll('input[name="target_class"]').forEach((input) => input.addEventListener('change', updateSelectedCount));
}

function selectedClasses() {
  return [...document.querySelectorAll('input[name="target_class"]:checked')].map((input) => input.value);
}

function setSelectedClasses(values) {
  const selected = new Set(values || []);
  document.querySelectorAll('input[name="target_class"]').forEach((input) => { input.checked = selected.has(input.value); });
  updateSelectedCount();
}

function updateSelectedCount() {
  $('#selected-count').textContent = selectedClasses().length;
}

function fill(settings) {
  const camera = settings.camera || {};
  const yolo = settings.yolo || {};
  const recording = settings.recording || {};
  const motion = settings.motion || {};
  const notifications = settings.notifications || {};
  form.elements.source_type.value = camera.source_type || 'csi';
  form.elements.device.value = camera.device || 'csi:0';
  const resolution = `${camera.width || 1280}x${camera.height || 720}`;
  if ([...form.elements.resolution.options].some((option) => option.value === resolution)) form.elements.resolution.value = resolution;
  form.elements.fps.value = camera.fps || 15;
  form.elements.yolo_imgsz.value = String(yolo.imgsz || 416);
  form.elements.yolo_enabled.checked = yolo.enabled !== false;
  const unlimited = Number(yolo.sample_fps) === 0;
  form.elements.yolo_unlimited.checked = unlimited;
  form.elements.yolo_sample_fps.disabled = unlimited;
  form.elements.yolo_sample_fps.value = String(unlimited ? 2 : Math.max(1, Number(yolo.sample_fps) || 2));
  form.elements.recording_enabled.checked = recording.enabled !== false;
  form.elements.segment_seconds.value = recording.segment_seconds || 60;
  form.elements.max_storage_gb.value = recording.max_storage_gb || 64;
  form.elements.important_only.checked = !!recording.important_only;
  form.elements.alert_schedule_enabled.checked = !!recording.alert_schedule_enabled;
  form.elements.alert_start.value = recording.alert_start || '22:00';
  form.elements.alert_end.value = recording.alert_end || '06:00';
  form.elements.motion_enabled.checked = motion.enabled !== false;
  form.elements.motion_pixel_threshold.value = motion.pixel_threshold || 25;
  form.elements.motion_trigger_percent.value = motion.trigger_percent || 8;
  form.elements.motion_cooldown_seconds.value = motion.cooldown_seconds ?? 5;
  form.elements.notification_enabled.checked = !!notifications.enabled;
  form.elements.smtp_host.value = notifications.smtp_host || '';
  form.elements.smtp_port.value = notifications.smtp_port || 465;
  form.elements.smtp_security.value = notifications.security || 'ssl';
  form.elements.smtp_sender.value = notifications.sender || '';
  form.elements.smtp_username.value = notifications.username || '';
  form.elements.smtp_password.value = notifications.password || '';
  form.elements.smtp_recipient.value = notifications.recipient || '';
  form.elements.smtp_subject_prefix.value = notifications.subject_prefix || '[PiWatch]';
  setSelectedClasses(yolo.target_classes || []);
  settingsLoaded = true;
}

async function refresh() {
  try {
    const [status, settings] = await Promise.all([api('/api/v1/status'), api('/api/v1/settings')]);
    renderStatus(status);
    fill(settings);
  } catch (error) {
    $('#health').textContent = '服务不可用';
    $('#health').className = 'badge offline';
    message(error.message, true);
  }
}

function reconnectStream() {
  $('#stream-error').hidden = true;
  liveView.src = `/api/v1/stream.mjpg?t=${Date.now()}`;
}

liveView.onload = () => { $('#stream-error').hidden = true; };
liveView.onerror = () => { $('#stream-error').hidden = false; };

async function saveSettings() {
  if (!settingsLoaded) {
    return;
  }
  if (saveInProgress) {
    saveQueued = true;
    return;
  }
  saveInProgress = true;
  message('保存中');
  const fields = new FormData(form);
  const [width, height] = String(fields.get('resolution')).split('x').map(Number);
  try {
    await api('/api/v1/settings', {
      method: 'PUT',
      body: JSON.stringify({
        camera: { source_type: fields.get('source_type'), device: fields.get('device'), width, height, fps: Number(fields.get('fps')) },
        yolo: {
          enabled: form.elements.yolo_enabled.checked,
          target_classes: selectedClasses(),
          imgsz: Number(fields.get('yolo_imgsz')),
          sample_fps: form.elements.yolo_unlimited.checked ? 0 : Math.max(1, Number(fields.get('yolo_sample_fps')) || 1),
        },
        recording: {
          enabled: form.elements.recording_enabled.checked,
          segment_seconds: Math.max(10, Number(fields.get('segment_seconds')) || 60),
          max_storage_gb: Math.max(0.1, Number(fields.get('max_storage_gb')) || 64),
          important_only: form.elements.important_only.checked,
          alert_schedule_enabled: form.elements.alert_schedule_enabled.checked,
          alert_start: fields.get('alert_start'),
          alert_end: fields.get('alert_end'),
        },
        motion: {
          enabled: form.elements.motion_enabled.checked,
          pixel_threshold: Math.max(1, Number(fields.get('motion_pixel_threshold')) || 25),
          trigger_percent: Math.max(0.1, Number(fields.get('motion_trigger_percent')) || 8),
          cooldown_seconds: Math.max(0, Number(fields.get('motion_cooldown_seconds')) || 0),
        },
        notifications: {
          enabled: form.elements.notification_enabled.checked,
          smtp_host: String(fields.get('smtp_host') || '').trim(),
          smtp_port: Math.max(1, Number(fields.get('smtp_port')) || 465),
          security: fields.get('smtp_security'),
          sender: String(fields.get('smtp_sender') || '').trim(),
          username: String(fields.get('smtp_username') || '').trim(),
          password: String(fields.get('smtp_password') || ''),
          recipient: String(fields.get('smtp_recipient') || '').trim(),
          subject_prefix: String(fields.get('smtp_subject_prefix') || '[PiWatch]').trim(),
        },
      }),
    });
    message('已自动保存');
  } catch (error) {
    message(error.message, true);
  } finally {
    saveInProgress = false;
    if (saveQueued) {
      saveQueued = false;
      saveSettings();
    }
  }
}

function scheduleSave() {
  if (!settingsLoaded) return;
  window.clearTimeout(saveTimer);
  message('等待保存');
  saveTimer = window.setTimeout(saveSettings, 600);
}

$('#refresh').onclick = refresh;
$('#select-common').onclick = () => { setSelectedClasses(COMMON_CLASSES); scheduleSave(); };
$('#select-all').onclick = () => { setSelectedClasses(COCO_GROUPS.flatMap(([, classes]) => classes.map(([value]) => value))); scheduleSave(); };
$('#clear-all').onclick = () => { setSelectedClasses([]); scheduleSave(); };
form.elements.yolo_unlimited.onchange = () => {
  form.elements.yolo_sample_fps.disabled = form.elements.yolo_unlimited.checked;
};
$('#test-email').onclick = async () => {
  const status = $('#email-message');
  status.textContent = '正在保存配置并发送...';
  try {
    window.clearTimeout(saveTimer);
    await saveSettings();
    await api('/api/v1/notifications/test-email', { method: 'POST', body: '{}' });
    status.textContent = '测试邮件已发送';
    status.className = 'success-text';
  } catch (error) {
    status.textContent = error.message;
    status.className = 'error-text';
  }
};
form.addEventListener('input', scheduleSave);
form.addEventListener('change', scheduleSave);
window.addEventListener('resize', () => detectionContext.clearRect(0, 0, detectionCanvas.width, detectionCanvas.height));

buildClassSelector();
refresh();
setInterval(async () => {
  try { renderStatus(await api('/api/v1/status')); } catch (_) { /* The next full refresh reports connection errors. */ }
}, 3000);
setInterval(refresh, 30000);

async function pollDetections() {
  try {
    const yolo = await api('/api/v1/detections');
    if (yolo.updated_at !== lastDetectionUpdate) {
      lastDetectionUpdate = yolo.updated_at;
      renderYoloStatus(yolo);
    }
  } catch (_) { /* Full status refresh reports persistent connection errors. */ }
  window.setTimeout(pollDetections, 50);
}

pollDetections();

async function refreshRecordings() {
  try {
    const query = recordingFilter === 'important' ? '?important=1' : recordingFilter === 'regular' || recordingFilter === 'alert' ? `?zone=${recordingFilter}` : '';
    const data = await api(`/api/v1/recordings${query}`);
    const items = data.items || [];
    const filterNames = { all: '全部', regular: '普通区', alert: '警戒区', important: '重点' };
    $('#recording-summary').textContent = `${filterNames[recordingFilter]} · ${items.length} 个切片`;
    $('#recording-list').innerHTML = items.length ? items.map((item) => {
      const reasons = (item.important_reasons || []).map((reason) => reason === 'yolo' ? 'YOLO 目标' : reason === 'motion' ? '移动检测' : reason === 'alert_schedule' ? '警戒时段' : reason);
      const zone = item.storage_zone === 'alert' ? '警戒区' : '普通区';
      return `<article class="recording-item">
        <video controls preload="metadata" src="/api/v1/recordings/${item.id}/video"></video>
        <div class="recording-meta">
          <div class="recording-title"><strong>${formatDate(item.started_at)}</strong>${item.important ? '<span class="important-badge">重点</span>' : ''}</div>
          <p>${zone} · ${formatDuration(item.duration_seconds)} · ${formatBytes(item.size_bytes)} · ${item.status === 'recording' ? '录制中' : '已完成'}</p>
          ${reasons.length ? `<div class="reason-list">${reasons.map((reason) => `<span>${reason}</span>`).join('')}</div>` : ''}
          ${item.status === 'recording' ? '' : `<button type="button" class="delete-recording" data-recording-id="${item.id}">删除视频</button>`}
        </div>
      </article>`;
    }).join('') : '<div class="empty-state">暂无录像</div>';
  } catch (error) {
    $('#recording-list').innerHTML = `<div class="empty-state error-text">${error.message}</div>`;
  }
}

document.querySelectorAll('[data-recording-filter]').forEach((button) => {
  button.onclick = () => {
    recordingFilter = button.dataset.recordingFilter;
    document.querySelectorAll('[data-recording-filter]').forEach((item) => item.classList.toggle('active', item === button));
    refreshRecordings();
  };
});

refreshRecordings();
setInterval(refreshRecordings, 15000);

$('#recording-list').onclick = async (event) => {
  const button = event.target.closest('[data-recording-id]');
  if (!button || !window.confirm('确定删除这个视频？此操作无法撤销。')) return;
  button.disabled = true;
  try {
    await api(`/api/v1/recordings/${button.dataset.recordingId}`, { method: 'DELETE' });
    await refreshRecordings();
  } catch (error) {
    button.disabled = false;
    message(error.message, true);
  }
};
