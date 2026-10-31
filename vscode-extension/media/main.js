(function () {
  const vscode = acquireVsCodeApi();

  const form = document.getElementById('predict-form');
  const filePathInput = document.getElementById('file-path');
  const browseButton = document.getElementById('browse-button');
  const profileSelect = document.getElementById('profile-select');
  const manualFields = document.getElementById('manual-fields');
  const bandwidthInput = document.getElementById('bandwidth-input');
  const computeInput = document.getElementById('compute-input');
  const saveProfileCheckbox = document.getElementById('save-profile-checkbox');
  const profileNameField = document.getElementById('profile-name-field');
  const profileNameInput = document.getElementById('profile-name-input');
  const slaInput = document.getElementById('sla-input');
  const formError = document.getElementById('form-error');
  const runButton = document.getElementById('run-button');
  const statusRow = document.getElementById('status-row');

  const emptyState = document.getElementById('empty-state');
  const errorState = document.getElementById('error-state');
  const resultState = document.getElementById('result-state');
  const errorQuery = document.getElementById('error-query');
  const errorMessage = document.getElementById('error-message');
  const queryName = document.getElementById('query-name');
  const verdictBadge = document.getElementById('verdict-badge');
  const timestamp = document.getElementById('timestamp');
  const p50 = document.getElementById('p50');
  const p95 = document.getElementById('p95');
  const p99 = document.getElementById('p99');
  const confidenceFill = document.getElementById('confidence-fill');
  const confidenceValue = document.getElementById('confidence-value');
  const reason = document.getElementById('reason');

  let profiles = [];

  function isPositiveNumber(value) {
    const parsed = parseFloat(value);
    return typeof value === 'string' && value.trim().length > 0 && !Number.isNaN(parsed) && parsed > 0;
  }

  function updateManualFieldsVisibility() {
    const manual = profileSelect.value === '__manual__';
    manualFields.classList.toggle('hidden', !manual);
  }

  function updateProfileNameFieldVisibility() {
    profileNameField.classList.toggle('hidden', !saveProfileCheckbox.checked);
  }

  function populateProfiles(list) {
    profiles = list || [];
    const currentValue = profileSelect.value;
    profileSelect.innerHTML = '';
    const manualOption = document.createElement('option');
    manualOption.value = '__manual__';
    manualOption.textContent = 'Enter manually';
    profileSelect.appendChild(manualOption);
    profiles.forEach((profile) => {
      const option = document.createElement('option');
      option.value = profile.name;
      option.textContent = profile.name + ' (bw ' + profile.bandwidth + ', cp ' + profile.compute + ')';
      profileSelect.appendChild(option);
    });
    const stillExists = profiles.some((profile) => profile.name === currentValue);
    profileSelect.value = stillExists ? currentValue : '__manual__';
    updateManualFieldsVisibility();
  }

  function showState(id) {
    emptyState.classList.add('hidden');
    errorState.classList.add('hidden');
    resultState.classList.add('hidden');
    document.getElementById(id).classList.remove('hidden');
  }

  function formatMs(value) {
    return typeof value === 'number' ? value.toFixed(1) + ' ms' : '--';
  }

  function renderResult(payload) {
    if (!payload) {
      showState('empty-state');
      return;
    }
    if (payload.status === 'error') {
      errorQuery.textContent = payload.query_id || '';
      errorMessage.textContent = payload.error || 'Unknown error';
      showState('error-state');
      return;
    }
    queryName.textContent = payload.query_id;
    verdictBadge.textContent = payload.verdict;
    verdictBadge.className = 'badge ' + String(payload.verdict).toLowerCase();
    timestamp.textContent = new Date(payload.timestamp).toLocaleString();
    p50.textContent = formatMs(payload.predicted_prod_ms.p50);
    p95.textContent = formatMs(payload.predicted_prod_ms.p95);
    p99.textContent = formatMs(payload.predicted_prod_ms.p99);
    const confidencePct = Math.round(payload.confidence * 100);
    confidenceFill.style.width = confidencePct + '%';
    confidenceValue.textContent = confidencePct + '%';
    reason.textContent = payload.reason;
    showState('result-state');
  }

  function setRunning(running) {
    runButton.disabled = running;
    statusRow.classList.toggle('hidden', !running);
  }

  function persistState(extra) {
    vscode.setState(
      Object.assign(
        {
          filePath: filePathInput.value,
          profileMode: profileSelect.value,
          bandwidth: bandwidthInput.value,
          compute: computeInput.value,
          slaMs: slaInput.value,
          lastResult: extra && extra.lastResult !== undefined ? extra.lastResult : (vscode.getState() || {}).lastResult,
        },
        extra || {}
      )
    );
  }

  browseButton.addEventListener('click', () => {
    vscode.postMessage({ type: 'browseFile' });
  });

  profileSelect.addEventListener('change', () => {
    updateManualFieldsVisibility();
    persistState({});
  });

  saveProfileCheckbox.addEventListener('change', updateProfileNameFieldVisibility);

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    formError.classList.add('hidden');

    const filePath = filePathInput.value.trim();
    const profileMode = profileSelect.value === '__manual__' ? 'manual' : 'saved';

    if (!filePath) {
      formError.textContent = 'select a .sql query file';
      formError.classList.remove('hidden');
      return;
    }
    if (profileMode === 'manual') {
      if (!isPositiveNumber(bandwidthInput.value)) {
        formError.textContent = 'bandwidth must be a positive number';
        formError.classList.remove('hidden');
        return;
      }
      if (!isPositiveNumber(computeInput.value)) {
        formError.textContent = 'compute score must be a positive number';
        formError.classList.remove('hidden');
        return;
      }
      if (saveProfileCheckbox.checked && profileNameInput.value.trim().length === 0) {
        formError.textContent = 'enter a name to save this profile';
        formError.classList.remove('hidden');
        return;
      }
    }
    if (!isPositiveNumber(slaInput.value)) {
      formError.textContent = 'SLA must be a positive number';
      formError.classList.remove('hidden');
      return;
    }

    const payload = {
      filePath: filePath,
      profileMode: profileMode,
      profileName: profileMode === 'saved' ? profileSelect.value : undefined,
      bandwidth: bandwidthInput.value,
      compute: computeInput.value,
      saveAsProfile: saveProfileCheckbox.checked,
      newProfileName: profileNameInput.value,
      slaMs: slaInput.value,
    };

    setRunning(true);
    persistState({});
    vscode.postMessage({ type: 'run', payload: payload });
  });

  window.addEventListener('message', (event) => {
    const message = event.data;
    if (!message || typeof message.type !== 'string') {
      return;
    }
    if (message.type === 'init') {
      const init = message.payload || {};
      populateProfiles(init.profiles);
      if (!filePathInput.value) {
        filePathInput.value = init.filePath || '';
      }
      if (!slaInput.value) {
        slaInput.value = init.defaultSlaMs !== undefined ? String(init.defaultSlaMs) : '500';
      }
      renderResult(init.lastResult);
      persistState({ lastResult: init.lastResult });
      return;
    }
    if (message.type === 'fileSelected' || message.type === 'prefillFile') {
      filePathInput.value = message.payload.path;
      persistState({});
      return;
    }
    if (message.type === 'profiles') {
      populateProfiles(message.payload);
      return;
    }
    if (message.type === 'running') {
      setRunning(true);
      return;
    }
    if (message.type === 'result') {
      setRunning(false);
      renderResult(message.payload);
      persistState({ lastResult: message.payload });
      return;
    }
  });

  const previousState = vscode.getState();
  if (previousState) {
    filePathInput.value = previousState.filePath || '';
    bandwidthInput.value = previousState.bandwidth || '';
    computeInput.value = previousState.compute || '';
    slaInput.value = previousState.slaMs || '';
    renderResult(previousState.lastResult);
  }

  updateManualFieldsVisibility();
  updateProfileNameFieldVisibility();
  vscode.postMessage({ type: 'ready' });
})();
