const taskList = document.querySelector('#task-list');
const dropzone = document.querySelector('#upload-dropzone');
const fileInput = document.querySelector('#pdf-input');
const uploadError = document.querySelector('#upload-error');
const drawer = document.querySelector('#console-drawer');
const consoleOutput = document.querySelector('#console-output');
const consoleTitle = document.querySelector('#console-title');
const connection = document.querySelector('#console-connection');
const autoscroll = document.querySelector('#console-autoscroll');
let logSource = null;
let activeTaskId = null;
let activeFilename = '';
let logMessages = [];
let lastLogId = 0;

function actionButton(label, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.action = action;
  button.textContent = label;
  return button;
}

function renderTasks(tasks) {
  taskList.replaceChildren();
  let running = 0;
  let queued = 0;
  for (const task of tasks) {
    running += task.status === 'running' ? 1 : 0;
    queued += task.status === 'queued' ? 1 : 0;
    const row = document.createElement('li');
    row.className = 'task-row';
    row.dataset.taskId = task.id;
    row.dataset.filename = task.original_filename;
    const identity = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = task.original_filename;
    const detail = document.createElement('small');
    detail.textContent =
      `${new Date(task.created_at).toLocaleString()} · 第 ${task.attempt_count} 次执行`;
    const taskId = document.createElement('small');
    taskId.className = 'task-id';
    const shortId = document.createElement('code');
    shortId.title = task.id;
    shortId.textContent = `任务 UUID：${task.id.slice(0, 8)}`;
    const copyId = actionButton('复制', 'copy-id');
    copyId.setAttribute('aria-label', '复制完整任务 UUID');
    taskId.append(shortId, copyId);
    identity.append(name, detail, taskId);
    const status = document.createElement('span');
    status.className = `status status-${task.status}`;
    status.textContent = task.status;
    const actions = document.createElement('div');
    actions.className = 'task-actions';
    actions.append(actionButton('Console', 'console'));
    if (['failed', 'interrupted'].includes(task.status)) {
      actions.append(actionButton('继续', 'resume'));
    }
    if (task.status === 'succeeded') {
      const view = document.createElement('a');
      view.className = 'button primary';
      view.href = `/tasks/${task.id}/view`;
      view.target = '_blank';
      view.rel = 'noopener';
      view.textContent = '查看';
      actions.append(view);
    }
    row.append(identity, status, actions);
    taskList.append(row);
  }
  document.querySelector('#queue-summary').textContent =
    `${running} 个运行中 · ${queued} 个等待中`;
}

function appendLog(log) {
  lastLogId = Math.max(lastLogId, log.id);
  logMessages.push(`[${log.level}] ${log.message}`);
  const line = document.createElement('span');
  line.className = `log-${log.level.toLowerCase()}`;
  line.textContent = `[${log.level}] ${log.message}\n`;
  consoleOutput.append(line);
  if (autoscroll.checked) {
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
  }
}

async function loadHistory(taskId) {
  let cursor = 0;
  while (true) {
    const response = await fetch(`/tasks/${taskId}/logs?after_id=${cursor}`);
    if (!response.ok) throw new Error('无法读取历史日志');
    const logs = await response.json();
    logs.forEach(appendLog);
    if (logs.length < 500) return;
    cursor = logs[logs.length - 1].id;
  }
}

function connectLogs(taskId) {
  logSource = new EventSource(
    `/tasks/${taskId}/logs/events?after_id=${lastLogId}`
  );
  logSource.addEventListener('open', () => {
    connection.textContent = '● 实时连接';
  });
  logSource.addEventListener('log', (event) => {
    appendLog(JSON.parse(event.data));
  });
  logSource.addEventListener('error', () => {
    connection.textContent = '正在重连';
  });
}

async function openConsole(taskId, filename) {
  if (logSource) logSource.close();
  activeTaskId = taskId;
  activeFilename = filename;
  logMessages = [];
  lastLogId = 0;
  consoleOutput.replaceChildren();
  consoleTitle.textContent = filename;
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  connection.textContent = '正在加载';
  try {
    await loadHistory(taskId);
    connectLogs(taskId);
  } catch (error) {
    connection.textContent = error.message;
  }
}

function closeConsole() {
  if (logSource) logSource.close();
  logSource = null;
  activeTaskId = null;
  activeFilename = '';
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
}

async function resumeTask(taskId) {
  const response = await fetch(`/tasks/${taskId}/resume`, { method: 'POST' });
  if (!response.ok) {
    const payload = await response.json();
    uploadError.textContent = payload.detail || '无法继续任务';
  }
}

taskList.addEventListener('click', (event) => {
  const action = event.target.closest('[data-action]');
  const row = event.target.closest('[data-task-id]');
  if (!action || !row) return;
  if (action.dataset.action === 'console') {
    openConsole(row.dataset.taskId, row.dataset.filename || '');
  } else if (action.dataset.action === 'resume') {
    resumeTask(row.dataset.taskId);
  } else if (action.dataset.action === 'copy-id') {
    navigator.clipboard.writeText(row.dataset.taskId);
    action.textContent = '已复制';
    setTimeout(() => {
      action.textContent = '复制';
    }, 1200);
  }
});

async function upload(file) {
  uploadError.textContent = '';
  const body = new FormData();
  body.append('pdf', file);
  const response = await fetch('/tasks', { method: 'POST', body });
  if (!response.ok) {
    const payload = await response.json();
    uploadError.textContent = payload.detail || '上传失败';
  }
}

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) upload(fileInput.files[0]);
  fileInput.value = '';
});

for (const name of ['dragenter', 'dragover']) {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add('drag-active');
  });
}

for (const name of ['dragleave', 'drop']) {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove('drag-active');
  });
}

dropzone.addEventListener('drop', (event) => {
  if (event.dataTransfer.files[0]) upload(event.dataTransfer.files[0]);
});

consoleOutput.addEventListener('scroll', () => {
  const distance =
    consoleOutput.scrollHeight -
    consoleOutput.scrollTop -
    consoleOutput.clientHeight;
  if (distance > 32) autoscroll.checked = false;
});

document.querySelector('#console-close').addEventListener('click', closeConsole);
document.querySelector('#console-copy').addEventListener('click', () => {
  navigator.clipboard.writeText(logMessages.join('\n'));
});

const taskEvents = new EventSource('/tasks/events');
taskEvents.addEventListener('tasks', (event) => {
  renderTasks(JSON.parse(event.data));
});
