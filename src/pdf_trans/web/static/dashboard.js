const taskList = document.querySelector('#task-list');
const dropzone = document.querySelector('#upload-dropzone');
const fileInput = document.querySelector('#pdf-input');
const uploadError = document.querySelector('#upload-error');
const drawer = document.querySelector('#console-drawer');
const consoleOutput = document.querySelector('#console-output');
const consoleTitle = document.querySelector('#console-title');
const connection = document.querySelector('#console-connection');
const autoscroll = document.querySelector('#console-autoscroll');
const consoleDownload = document.querySelector('#console-download');
const MAX_CONSOLE_LOGS = 200;
const deletableStatuses = new Set([
  'succeeded',
  'failed',
  'interrupted',
]);
let logSource = null;
let historyController = null;
let renderFrame = null;
let consoleGeneration = 0;
let activeTaskId = null;
let activeFilename = '';
let pendingLogs = [];
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
    if (deletableStatuses.has(task.status)) {
      const deleteButton = actionButton('删除', 'delete');
      deleteButton.classList.add('danger');
      actions.append(deleteButton);
    }
    row.append(identity, status, actions);
    taskList.append(row);
  }
  document.querySelector('#queue-summary').textContent =
    `${running} 个运行中 · ${queued} 个等待中`;
}

function flushLogs() {
  renderFrame = null;
  if (pendingLogs.length === 0) return;

  const logs = pendingLogs;
  pendingLogs = [];
  const overflow = Math.max(
    0,
    logMessages.length + logs.length - MAX_CONSOLE_LOGS
  );
  if (overflow > 0) {
    logMessages.splice(0, overflow);
    for (let index = 0; index < overflow; index += 1) {
      if (consoleOutput.firstChild) consoleOutput.firstChild.remove();
    }
  }

  const fragment = document.createDocumentFragment();
  for (const log of logs) {
    const message = `[${log.level}] ${log.message}`;
    logMessages.push(message);
    const line = document.createElement('span');
    line.className = `log-${log.level.toLowerCase()}`;
    line.textContent = `${message}\n`;
    fragment.append(line);
  }
  consoleOutput.append(fragment);
  if (autoscroll.checked) {
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
  }
}

function enqueueLog(log) {
  if (log.id <= lastLogId) return;
  lastLogId = log.id;
  pendingLogs.push(log);
  if (pendingLogs.length > MAX_CONSOLE_LOGS) {
    pendingLogs.splice(
      0,
      pendingLogs.length - MAX_CONSOLE_LOGS
    );
  }
  if (renderFrame === null) {
    renderFrame = requestAnimationFrame(flushLogs);
  }
}

async function loadHistory(taskId, signal) {
  const response = await fetch(`/tasks/${taskId}/logs/recent`, { signal });
  if (!response.ok) throw new Error('无法读取历史日志');
  return response.json();
}

function connectLogs(taskId, generation) {
  const source = new EventSource(
    `/tasks/${taskId}/logs/events?after_id=${lastLogId}`
  );
  logSource = source;
  source.addEventListener('open', () => {
    if (generation === consoleGeneration) {
      connection.textContent = '● 实时连接';
    }
  });
  source.addEventListener('log', (event) => {
    if (generation === consoleGeneration) {
      enqueueLog(JSON.parse(event.data));
    }
  });
  source.addEventListener('error', () => {
    if (generation === consoleGeneration) {
      connection.textContent = '正在重连';
    }
  });
}

function resetConsoleResources() {
  consoleGeneration += 1;
  if (historyController) historyController.abort();
  historyController = null;
  if (logSource) logSource.close();
  logSource = null;
  if (renderFrame !== null) cancelAnimationFrame(renderFrame);
  renderFrame = null;
  pendingLogs = [];
  logMessages = [];
  lastLogId = 0;
  consoleOutput.replaceChildren();
}

async function openConsole(taskId, filename) {
  resetConsoleResources();
  const generation = consoleGeneration;
  const controller = new AbortController();
  historyController = controller;
  activeTaskId = taskId;
  activeFilename = filename;
  consoleTitle.textContent = filename;
  consoleDownload.href =
    `/tasks/${encodeURIComponent(taskId)}/logs/download`;
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  connection.textContent = '正在加载';
  try {
    const logs = await loadHistory(taskId, controller.signal);
    if (generation !== consoleGeneration) return;
    logs.forEach(enqueueLog);
    connectLogs(taskId, generation);
  } catch (error) {
    if (
      generation === consoleGeneration &&
      error.name !== 'AbortError'
    ) {
      connection.textContent = error.message;
    }
  } finally {
    if (generation === consoleGeneration) {
      historyController = null;
    }
  }
}

function closeConsole() {
  resetConsoleResources();
  activeTaskId = null;
  activeFilename = '';
  consoleTitle.textContent = '';
  consoleDownload.removeAttribute('href');
  connection.textContent = '未连接';
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

async function deleteTask(row, button) {
  const taskId = row.dataset.taskId;
  const filename = row.dataset.filename || '该文件';
  const confirmed = window.confirm(
    `确定删除“${filename}”吗？\n\n` +
    '这会永久删除原始 PDF、日志和全部解析产物，无法恢复。'
  );
  if (!confirmed) return;

  uploadError.textContent = '';
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = '删除中…';
  try {
    const response = await fetch(`/tasks/${taskId}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      let message = '无法删除任务';
      try {
        const payload = await response.json();
        message = payload.detail || message;
      } catch {
        // Keep the generic message for a non-JSON server error.
      }
      throw new Error(message);
    }
    if (activeTaskId === taskId) {
      closeConsole();
    }
    row.remove();
  } catch (error) {
    uploadError.textContent =
      error instanceof Error ? error.message : '无法删除任务';
    button.disabled = false;
    button.textContent = originalLabel;
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
  } else if (action.dataset.action === 'delete') {
    deleteTask(row, action);
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
