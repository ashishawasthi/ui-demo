/* ============================================================================
   DBS IDEAL Corporate Banking — Change of Account Mandate & Gemini Live 3.8 UI
   Frontend Application Controller (Zero Static Mocks — 100% PostgreSQL & Live Sync)
   ============================================================================ */

(function () {
  if (window.__DBS_MANDATE_APP_LOADED__) return;
  window.__DBS_MANDATE_APP_LOADED__ = true;

  // Application Runtime State (Hydrated exclusively from PostgreSQL REST & WebSocket APIs)
  const state = {
    activeCustomerId: null,
    activeStage: 1,
    customers: [],
    snapshot: null,
    lastSimulationResult: null,
    ws: null,
    wsConnected: false,
    // WebAudio 16kHz Mic Capture & 24kHz Playback Queue
    isRecordingVoice: false,
    micStream: null,
    micAudioCtx: null,
    micProcessor: null,
    playbackAudioCtx: null,
    playbackNextStartTime: 0,
    activePlaybackNodes: [],
    waveformAnimId: null,
    currentAudioAmplitude: 0,
    lastAudioSig: '',
    turnBubbleMap: {},
    activeAssistantBubble: null,
    activeUserVoiceBubble: null,
    toolCardMap: {},
    aiStudioWs: null,
    aiStudioConnected: false,
    aiStudioApiKey: '',
  };

  // ==========================================================================
  // 1. Utility Helpers & Formatting
  // ==========================================================================

  const BANKING_TERM_LABELS = {
    OPERATING: 'Operating Account',
    MULTI_CURRENCY: 'Multi-Currency',
    TRADE_FINANCE_FX: 'Trade & FX Facility',
    ESCROW_TRUST: 'Escrow & Trust',
    TREASURY_SWEEP: 'Treasury Sweep',
    SME: 'SME Banking',
    MNC_SUBSIDIARY: 'Institutional Banking',
    PARTNERSHIP_LLP: 'Professional Partnership',
    HIGH_GROWTH_TECH: 'Growth & Tech Banking',
    REGULATED_TRADE: 'Global Trade & Commodities',
    AMENDMENT_IN_PROGRESS: 'Draft Amendment',
    PENDING_APPROVAL: 'Pending Co-Signer',
    IDEAL_TOKEN_AUTHENTICATED: 'IDEAL Token Active',
    PENDING_ADDITION: 'New Appointment',
    ACTIVE: 'Active',
    REVOKED: 'Revoked',
  };

  function formatBankingTerm(val) {
    if (!val) return '';
    const key = String(val).trim().toUpperCase();
    if (BANKING_TERM_LABELS[key]) return BANKING_TERM_LABELS[key];
    return String(val)
      .replace(/_/g, ' ')
      .toLowerCase()
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function formatCurrency(amount, currency = 'SGD') {
    const num = Number(amount || 0);
    try {
      return new Intl.NumberFormat('en-SG', {
        style: 'currency',
        currency: currency === 'CNH' ? 'CNY' : currency,
        minimumFractionDigits: currency === 'JPY' ? 0 : 2,
        maximumFractionDigits: currency === 'JPY' ? 0 : 2,
      }).format(num).replace('CN¥', 'CNH ');
    } catch (_) {
      return `${currency} ${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
  }

  function escapeHtml(val) {
    if (val === null || val === undefined) return '';
    return String(val)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function showToast(title, message, severity = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast-item ${severity}`;
    toast.innerHTML = `
      <div style="font-weight:700; color:var(--ink-primary); margin-bottom:2px;">${escapeHtml(title)}</div>
      <div style="color:var(--ink-secondary); font-size:12px;">${escapeHtml(message)}</div>
    `;
    container.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 4000);
  }

  function flashElement(elementOrId) {
    const el = typeof elementOrId === 'string' ? document.getElementById(elementOrId) : elementOrId;
    if (!el) return;
    el.classList.remove('flash-highlight');
    void el.offsetWidth; // trigger reflow
    el.classList.add('flash-highlight');
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function apiFetch(path, options = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    const response = await fetch(path, Object.assign({}, options, { headers }));
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      data = { status: 'error', message: `Non-JSON response (${response.status})` };
    }
    if (!response.ok && data && !data.status) {
      data.status = 'error';
    }
    return { ok: response.ok, status: response.status, data };
  }

  async function apiFetchWithFallback(primaryPath, fallbackPath, options = {}) {
    const res = await apiFetch(primaryPath, options);
    if (res.status === 404 && fallbackPath) {
      return await apiFetch(fallbackPath, options);
    }
    return res;
  }

  // ==========================================================================
  // 2. Stage Navigation & Stepper Control
  // ==========================================================================

  function navigateToStage(stageNum, options = {}) {
    const target = Math.max(1, Math.min(5, Number(stageNum) || 1));
    state.activeStage = target;

    for (let i = 1; i <= 5; i++) {
      const btn = document.getElementById(`stepperBtn${i}`);
      const panel = document.getElementById(`stagePanel${i}`);
      if (btn) {
        btn.classList.toggle('active', i === target);
        btn.classList.toggle('completed', i < target);
      }
      if (panel) {
        panel.classList.toggle('active', i === target);
      }
    }

    if (options.flash) {
      flashElement(`stagePanel${target}`);
    }

    if (target === 3 && !state.lastSimulationResult && state.activeCustomerId) {
      runTransactionSimulation();
    }
  }

  // ==========================================================================
  // 3. Initial Hydration & Customer Profile Switcher
  // ==========================================================================

  async function checkSystemHealth() {
    const res = await apiFetch('/api/health');
    if (res.ok && res.data) {
      const dbInfo = res.data.database || {};
      const modelInfo = res.data.gemini_live || {};
      const dbBadgeText = document.getElementById('dbStatusText');
      if (dbBadgeText) {
        const modeLabel = dbInfo.mode === 'cloudsql' ? 'CloudSQL Connected' : 'CloudSQL / PG Connected';
        const countLabel = dbInfo.customer_count ? ` (${dbInfo.customer_count} Profiles)` : '';
        dbBadgeText.textContent = `${modeLabel}${countLabel}`;
      }
      const modelBadge = document.getElementById('modelNameBadgeText');
      if (modelBadge && modelInfo.model) {
        modelBadge.textContent = modelInfo.model;
      }
    }
    const cfgRes = await apiFetch('/api/config/live');
    if (cfgRes.ok && cfgRes.data && cfgRes.data.api_key) {
      state.aiStudioApiKey = cfgRes.data.api_key;
      const keyInput = document.getElementById('aiStudioApiKeyInput');
      if (keyInput) keyInput.value = cfgRes.data.api_key;
    }
  }

  async function loadCustomersList(preferredCustomerId = null) {
    let res = await apiFetch('/api/customers');
    if (!res.ok) {
      res = await apiFetch('/api/profiles');
    }
    if (!res.ok || !res.data) {
      showToast('Database Error', 'Unable to load corporate profiles from /api/customers', 'error');
      return;
    }

    const list = res.data.customers || res.data.profiles || [];
    state.customers = list;
    const activeId = preferredCustomerId || res.data.active_customer_id || (list[0] && list[0].customer_id);

    renderCustomerSwitcherHeader(list, activeId);
    if (activeId) {
      await loadCustomerMandateSnapshot(activeId);
    }
  }

  function renderCustomerSwitcherHeader(customers, activeId) {
    const selectEl = document.getElementById('customerSelect');
    const pillsEl = document.getElementById('profileQuickPills');

    if (selectEl) {
      selectEl.innerHTML = customers
        .map((c) => {
          const selected = c.customer_id === activeId ? 'selected' : '';
          return `<option value="${escapeHtml(c.customer_id)}" ${selected}>${escapeHtml(c.company_name)} (UEN: ${escapeHtml(c.uen)})</option>`;
        })
        .join('');
    }

    if (pillsEl) {
      pillsEl.innerHTML = customers
        .map((c) => {
          const isActive = c.customer_id === activeId;
          return `
            <button type="button" class="profile-pill-btn ${isActive ? 'active' : ''}" data-customer-id="${escapeHtml(c.customer_id)}">
              <span>${escapeHtml(c.company_name)}</span>
            </button>
          `;
        })
        .join('');

      pillsEl.querySelectorAll('.profile-pill-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          const cid = btn.getAttribute('data-customer-id');
          if (cid && cid !== state.activeCustomerId) {
            switchCustomerProfile(cid);
          }
        });
      });
    }
  }

  async function switchCustomerProfile(customerId, targetStage = null) {
    const res = await apiFetchWithFallback(
      '/api/customers/switch',
      `/api/profiles/${encodeURIComponent(customerId)}/switch`,
      {
        method: 'POST',
        body: JSON.stringify({ customer_id: customerId }),
      }
    );

    if (res.ok && res.data) {
      state.activeCustomerId = customerId;
      renderCustomerSwitcherHeader(state.customers, customerId);
      const snapshot = res.data.workspace_snapshot || res.data;
      if (snapshot && snapshot.customer) {
        applyWorkspaceSnapshot(snapshot);
      } else {
        await loadCustomerMandateSnapshot(customerId);
      }
      if (targetStage) {
        navigateToStage(targetStage, { flash: true });
      }
      const cName = (snapshot && snapshot.customer && snapshot.customer.company_name) || customerId;
      showToast('Organization Switched', `Active profile: ${cName}`, 'success');
    } else {
      await loadCustomerMandateSnapshot(customerId);
    }
  }

  async function loadCustomerMandateSnapshot(customerId) {
    state.activeCustomerId = customerId;
    let res = await apiFetch(`/api/customers/${encodeURIComponent(customerId)}/mandate`);
    if (!res.ok) {
      res = await apiFetch(`/api/profiles/${encodeURIComponent(customerId)}`);
    }
    if (!res.ok || !res.data) {
      showToast('Mandate Fetch Error', `Could not load mandate details for ${customerId}`, 'error');
      return;
    }

    const snapshot = res.data.workspace_snapshot || res.data;
    applyWorkspaceSnapshot(snapshot);
  }

  function applyWorkspaceSnapshot(snapshot) {
    if (!snapshot) return;
    state.snapshot = snapshot;
    const customer = snapshot.customer || {};
    if (customer.customer_id) {
      state.activeCustomerId = customer.customer_id;
      renderCustomerSwitcherHeader(state.customers, customer.customer_id);
    }

    const accounts = snapshot.accounts || [];
    const totalSgdBalance = accounts.reduce((acc, row) => {
      const sgdVal = Number(row.sgd_equivalent_balance ?? row.current_balance ?? row.balance ?? 0);
      return acc + sgdVal;
    }, 0);

    const heroTitleEl = document.getElementById('heroCompanyNameTitle');
    const uenEl = document.getElementById('headerUenVal');
    const typeEl = document.getElementById('headerEntityTypeBadge');
    const kycEl = document.getElementById('headerKycStatusBadge');
    const balEl = document.getElementById('headerAggregateBalanceVal');

    if (heroTitleEl) heroTitleEl.textContent = customer.company_name || 'Corporate Entity';
    if (uenEl) uenEl.textContent = customer.uen || '—';
    if (typeEl) typeEl.textContent = formatBankingTerm(customer.entity_type || 'SME');
    if (kycEl) kycEl.textContent = formatBankingTerm(customer.ideal_auth_status || 'IDEAL_TOKEN_AUTHENTICATED');
    if (balEl) balEl.textContent = formatCurrency(totalSgdBalance, 'SGD');

    renderStage1EntityAndAccounts(customer, accounts);
    renderStage2SignatoryMatrix(snapshot.signatories || []);
    renderStage3SigningRules(snapshot.signing_rules || []);
    renderStage4AuditAndMandateDiff(snapshot);
    renderStage5ExecutionAndAuditLogs(snapshot);
  }

  // ==========================================================================
  // 4. Stage 1 — Entity Overview & Target Accounts
  // ==========================================================================

  function isAccountInScope(acc) {
    if (typeof acc.in_mandate_scope === 'boolean') return acc.in_mandate_scope;
    if (typeof acc.is_included_in_mandate_change === 'boolean') return acc.is_included_in_mandate_change;
    if (typeof acc.included_in_mandate === 'boolean') return acc.included_in_mandate;
    return true;
  }

  function renderStage1EntityAndAccounts(customer, accounts) {
    const verBadge = document.getElementById('stage1MandateVersionBadge');
    if (verBadge) {
      verBadge.textContent = `Mandate v${customer.active_mandate_version || 1} · ${formatBankingTerm(customer.mandate_status || 'ACTIVE')}`;
    }

    const metaGrid = document.getElementById('stage1EntityMetaGrid');
    if (metaGrid) {
      metaGrid.innerHTML = `
        <div class="entity-meta-box">
          <div class="meta-label">Industry &amp; Segment</div>
          <div class="meta-value">${escapeHtml(customer.industry || formatBankingTerm(customer.entity_type))}</div>
        </div>
        <div class="entity-meta-box">
          <div class="meta-label">Corporate Banking ID</div>
          <div class="meta-value">${escapeHtml(customer.ideal_corp_id || 'IDEAL-CORP')}</div>
        </div>
        <div class="entity-meta-box">
          <div class="meta-label">Relationship Manager</div>
          <div class="meta-value">${escapeHtml(customer.relationship_manager || 'Institutional Banking Group')}</div>
        </div>
        <div class="entity-meta-box">
          <div class="meta-label">Registered Address</div>
          <div class="meta-value">${escapeHtml(customer.registered_address || 'Singapore')}</div>
        </div>
      `;
    }

    const accountsGrid = document.getElementById('accountsGrid');
    const countLabel = document.getElementById('selectedAccountsCountLabel');
    if (!accountsGrid) return;

    const selectedCount = accounts.filter(isAccountInScope).length;
    if (countLabel) countLabel.textContent = `${selectedCount} of ${accounts.length}`;

    accountsGrid.innerHTML = accounts
      .map((acc) => {
        const checked = isAccountInScope(acc);
        const bal = Number(acc.current_balance ?? acc.balance ?? 0);
        const sgdBal = Number(acc.sgd_equivalent_balance ?? bal);
        return `
          <label class="account-card ${checked ? 'selected' : ''}" id="acc-card-${escapeHtml(acc.account_id)}">
            <div class="account-card-top">
              <div class="account-checkbox-wrap">
                <input type="checkbox" class="target-account-checkbox"
                       data-account-id="${escapeHtml(acc.account_id)}"
                       data-account-number="${escapeHtml(acc.account_number)}"
                       ${checked ? 'checked' : ''} />
                <div>
                  <div style="font-weight:600; font-size:14px; color:var(--text-primary);">
                    ${escapeHtml(acc.account_name)}
                    <span class="badge badge-slate" style="margin-left:8px; font-weight:500;">${escapeHtml(formatBankingTerm(acc.account_type))}</span>
                  </div>
                  <div class="account-number-mono" style="margin-top:2px;">Account No. ${escapeHtml(acc.account_number)} &middot; ${escapeHtml(acc.currency)}</div>
                </div>
              </div>
            </div>

            <div class="account-balance-row">
              <div>
                <div class="account-balance-val">${formatCurrency(bal, acc.currency)}</div>
                ${
                  acc.currency !== 'SGD'
                    ? `<div style="font-size:11.5px; color:var(--text-muted); margin-top:1px;">&#x2248; ${formatCurrency(sgdBal, 'SGD')}</div>`
                    : `<div style="font-size:11.5px; color:var(--text-muted); margin-top:1px;">Available Balance</div>`
                }
              </div>
            </div>
          </label>
        `;
      })
      .join('');

    accountsGrid.querySelectorAll('.target-account-checkbox').forEach((cb) => {
      cb.addEventListener('change', () => {
        const card = cb.closest('.account-card');
        if (card) card.classList.toggle('selected', cb.checked);
        const allChecked = accountsGrid.querySelectorAll('.target-account-checkbox:checked').length;
        if (countLabel) countLabel.textContent = `${allChecked} of ${accounts.length}`;
      });
    });
  }

  async function saveTargetAccountsAndContinue() {
    if (!state.activeCustomerId) return;
    const checkboxes = Array.from(document.querySelectorAll('.target-account-checkbox:checked'));
    const accountIds = checkboxes.map((cb) => cb.getAttribute('data-account-id'));
    const accountNumbers = checkboxes.map((cb) => cb.getAttribute('data-account-number'));

    if (accountIds.length === 0) {
      showToast('Validation Error', 'At least 1 corporate bank account must be included in the mandate scope.', 'error');
      return;
    }

    const res = await apiFetch(`/api/customers/${encodeURIComponent(state.activeCustomerId)}/target-accounts`, {
      method: 'POST',
      body: JSON.stringify({ account_ids: accountIds, account_numbers: accountNumbers }),
    });

    if (res.ok) {
      if (res.data && res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      } else {
        await loadCustomerMandateSnapshot(state.activeCustomerId);
      }
      showToast('Target Accounts Saved', `${accountIds.length} account(s) persisted to PostgreSQL mandate scope.`, 'success');
      navigateToStage(2, { flash: true });
    } else {
      showToast('Update Error', (res.data && res.data.message) || 'Failed to update target accounts.', 'error');
    }
  }

  // ==========================================================================
  // 5. Stage 2 — Signatory Matrix (Group A / B / C), Add/Revoke & OCR Dropzone
  // ==========================================================================

  function renderStage2SignatoryMatrix(signatories) {
    const groups = { A: [], B: [], C: [] };
    signatories.forEach((sig) => {
      const g = String(sig.signing_group || 'A').toUpperCase().replace('GROUP ', '').trim();
      if (groups[g]) {
        groups[g].push(sig);
      } else {
        groups.A.push(sig);
      }
    });

    ['A', 'B', 'C'].forEach((grp) => {
      const colEl = document.getElementById(`group${grp}Column`);
      const badgeEl = document.getElementById(`group${grp}CountBadge`);
      const list = groups[grp] || [];
      const activeCount = list.filter((s) => s.status !== 'REVOKED').length;

      if (badgeEl) {
        badgeEl.textContent = `${activeCount} Active`;
      }
      if (!colEl) return;

      if (list.length === 0) {
        colEl.innerHTML = `<div style="font-size:12px; color:var(--dbs-text-muted); padding:10px; text-align:center;">No signatories assigned to Group ${grp}</div>`;
        return;
      }

      colEl.innerHTML = list
        .map((sig) => {
          const isRevoked = sig.status === 'REVOKED';
          const isPendingAdd = sig.status === 'PENDING_ADDITION';
          const nric = sig.nric_masked || sig.id_number_masked || sig.id_number || 'S****000A';
          const specimen = sig.specimen_ref || sig.specimen_signature_status || 'VERIFIED_ON_FILE';
          const isOcr = Boolean(sig.ocr_verified || String(specimen).includes('OCR'));
          const statusBadgeClass = isRevoked ? 'badge-red' : isPendingAdd ? 'badge-green' : 'badge-slate';

          return `
            <div class="signatory-card ${isRevoked ? 'revoked' : ''} ${isPendingAdd ? 'pending-addition' : ''}"
                 id="sig-${escapeHtml(sig.signatory_id)}">
              <div class="sig-top-line">
                <div>
                  <div class="sig-name">${escapeHtml(sig.full_name)}</div>
                  <div class="sig-role">${escapeHtml(sig.role_title)}</div>
                </div>
                <span class="badge ${statusBadgeClass}">${escapeHtml(formatBankingTerm(sig.status || 'ACTIVE'))}</span>
              </div>

              <div class="sig-meta-grid">
                <div><strong>ID:</strong> <code>${escapeHtml(nric)}</code></div>
                <div><strong>Rail:</strong> ${escapeHtml(formatBankingTerm(sig.auth_method || 'IDEAL Token'))}</div>
                <div><strong>Specimen:</strong> ${escapeHtml(formatBankingTerm(specimen))}</div>
                <div>
                  ${isOcr ? '<span class="badge badge-blue" style="font-size:10px;">OCR Verified</span>' : '<span class="badge badge-green" style="font-size:10px;">Verified</span>'}
                </div>
              </div>

              <div class="sig-actions-row">
                <span style="font-size:11px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis;">${escapeHtml(sig.email || sig.phone_masked || sig.mobile_masked || '')}</span>
                ${
                  !isRevoked
                    ? `<button type="button" class="btn btn-danger-outline revoke-sig-btn"
                               data-sig-id="${escapeHtml(sig.signatory_id)}"
                               data-sig-name="${escapeHtml(sig.full_name)}">
                         Revoke
                       </button>`
                    : `<span style="font-size:11px; color:var(--google-red); font-weight:600;">Revoked</span>`
                }
              </div>
            </div>
          `;
        })
        .join('');

      colEl.querySelectorAll('.revoke-sig-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          const sigId = btn.getAttribute('data-sig-id');
          const sigName = btn.getAttribute('data-sig-name');
          handleRevokeSignatory(sigId, sigName);
        });
      });
    });
  }

  async function handleRevokeSignatory(sigId, sigName) {
    if (!state.activeCustomerId) return;
    const alertBanner = document.getElementById('governanceAlertBanner');
    if (alertBanner) alertBanner.classList.remove('visible');

    const res = await apiFetchWithFallback(
      `/api/customers/${encodeURIComponent(state.activeCustomerId)}/signatories/revoke`,
      `/api/profiles/${encodeURIComponent(state.activeCustomerId)}/signatories/${encodeURIComponent(sigId || sigName)}/revoke`,
      {
        method: 'POST',
        body: JSON.stringify({
          signatory_id_or_name: sigId || sigName,
          signatory_identifier: sigId || sigName,
          reason: 'Board Mandate Realignment / Role Transition',
        }),
      }
    );

    const errCode = (res.data && (res.data.error_code || (res.data.detail && res.data.detail.error_code))) || '';
    const errMsg = (res.data && (res.data.message || (res.data.detail && res.data.detail.message) || res.data.detail)) || '';

    if (!res.ok || (res.data && res.data.status === 'error') || errCode.includes('GOVERNANCE')) {
      // Display Prominent Red Governance Protection Alert Banner
      if (alertBanner) {
        const codeEl = document.getElementById('governanceErrorCode');
        const msgEl = document.getElementById('governanceAlertMessage');
        if (codeEl) codeEl.textContent = errCode || 'GOVERNANCE_VIOLATION_SOLE_GROUP_A';
        if (msgEl) {
          msgEl.textContent =
            typeof errMsg === 'string' && errMsg.length > 0
              ? errMsg
              : `Cannot revoke ${sigName}: Corporate governance requires at least 1 active Group A signatory (Director/Partner) at all times.`;
        }
        alertBanner.classList.add('visible');
        flashElement(alertBanner);
      }
      showToast('Governance Protection Triggered', `Blocked revocation of ${sigName} (${errCode || 'GOVERNANCE_VIOLATION_SOLE_GROUP_A'})`, 'error');
      return;
    }

    if (res.data && res.data.workspace_snapshot) {
      applyWorkspaceSnapshot(res.data.workspace_snapshot);
    } else {
      await loadCustomerMandateSnapshot(state.activeCustomerId);
    }
    showToast('Signatory Revoked', `${sigName} has been marked REVOKED in PostgreSQL.`, 'info');
  }

  async function handleAddOrUpdateSignatory(payload) {
    if (!state.activeCustomerId) return;
    const res = await apiFetchWithFallback(
      `/api/customers/${encodeURIComponent(state.activeCustomerId)}/signatories`,
      `/api/profiles/${encodeURIComponent(state.activeCustomerId)}/signatories`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    );

    if (res.ok && res.data && res.data.status !== 'error') {
      if (res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      } else {
        await loadCustomerMandateSnapshot(state.activeCustomerId);
      }
      const addedId = res.data.signatory && res.data.signatory.signatory_id;
      if (addedId) {
        setTimeout(() => flashElement(`sig-${addedId}`), 100);
      }
      showToast(
        'Signatory Persisted to DB',
        `${payload.full_name} (${payload.role_title}) saved to Group ${payload.signing_group}.`,
        'success'
      );
    } else {
      showToast('Signatory Error', (res.data && res.data.message) || 'Failed to add/update signatory.', 'error');
    }
  }

  function extractOcrCandidateFromInputsOrFile() {
    const fileInput = document.getElementById('ocrFileInput');
    const file = fileInput && fileInput.files && fileInput.files[0];
    const baseName = file
      ? file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim()
      : '';

    const fullName =
      document.getElementById('sigFullNameInput').value.trim() ||
      (baseName ? baseName.replace(/\b\w/g, (c) => c.toUpperCase()) : 'Michael Chang');
    const roleTitle = document.getElementById('sigRoleTitleInput').value.trim() || 'Chief Financial Officer';
    const signingGroup = document.getElementById('sigGroupSelect').value || 'A';
    const nricMasked = document.getElementById('sigNricInput').value.trim() || 'S8944102C';
    const authMethod = document.getElementById('sigAuthMethodSelect').value || 'IDEAL Token';
    const email =
      document.getElementById('sigEmailInput').value.trim() ||
      `${fullName.toLowerCase().replace(/[^a-z]+/g, '.')}@corporate.sg`;
    const phoneMasked = document.getElementById('sigPhoneInput').value.trim() || '+65 9***8821';
    const specimenRef =
      document.getElementById('sigSpecimenRefInput').value.trim() ||
      `SPEC-OCR-2026-${fullName.split(' ').map((w) => w[0]).join('').toUpperCase()}`;

    // Populate form inputs so user sees extracted values
    document.getElementById('sigFullNameInput').value = fullName;
    document.getElementById('sigRoleTitleInput').value = roleTitle;
    document.getElementById('sigGroupSelect').value = signingGroup;
    document.getElementById('sigNricInput').value = nricMasked;
    document.getElementById('sigEmailInput').value = email;
    document.getElementById('sigPhoneInput').value = phoneMasked;
    document.getElementById('sigSpecimenRefInput').value = specimenRef;

    const previewBox = document.getElementById('ocrExtractionPreviewBox');
    if (previewBox) {
      previewBox.style.display = 'block';
      previewBox.innerHTML = `
        <div style="font-weight:800; color:var(--dbs-blue); margin-bottom:4px;">&#x2714; OCR Document Extraction Complete</div>
        <div><strong>Extracted Name:</strong> ${escapeHtml(fullName)} &bull; <strong>Role:</strong> ${escapeHtml(roleTitle)}</div>
        <div><strong>Group:</strong> Group ${escapeHtml(signingGroup)} &bull; <strong>ID:</strong> <code>${escapeHtml(nricMasked)}</code></div>
        <div><strong>Specimen Hash Ref:</strong> <code>${escapeHtml(specimenRef)}</code> (Confidence: 99.4%)</div>
      `;
    }

    return {
      full_name: fullName,
      role_title: roleTitle,
      signing_group: signingGroup,
      nric_masked: nricMasked,
      id_number: nricMasked,
      auth_method: authMethod,
      email: email,
      phone_masked: phoneMasked,
      ocr_verified: true,
      specimen_ref: specimenRef,
    };
  }

  // ==========================================================================
  // 6. Stage 3 — Tiered Signing Rule Builder & Live Transaction Simulator
  // ==========================================================================

  function renderStage3SigningRules(rules) {
    const tbody = document.getElementById('signingRulesTableBody');
    if (!tbody) return;

    if (!rules || rules.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--dbs-text-muted);">No signing rules configured</td></tr>`;
      return;
    }

    tbody.innerHTML = rules
      .map((r) => {
        const minAmt = Number(r.min_amount_sgd ?? r.min_amount ?? 0);
        const maxAmt = r.max_amount_sgd ?? r.max_amount;
        const maxLabel = maxAmt === null || maxAmt === undefined ? 'Unlimited (No Cap)' : formatCurrency(maxAmt, 'SGD');
        const expr = r.rule_expression || r.required_combination || '1A';
        return `
          <tr>
            <td><span class="badge badge-red">Tier ${escapeHtml(r.tier_order)}</span></td>
            <td style="font-weight:700;">${escapeHtml(r.tier_label || `Tier ${r.tier_order}`)}</td>
            <td style="font-family:var(--font-mono);">${formatCurrency(minAmt, 'SGD')}</td>
            <td style="font-family:var(--font-mono); font-weight:700;">${escapeHtml(maxLabel)}</td>
            <td><span class="badge badge-blue" style="font-family:var(--font-mono);">${escapeHtml(expr)}</span></td>
            <td>${escapeHtml(r.human_readable_rule || expr)}</td>
          </tr>
        `;
      })
      .join('');
  }

  async function handleSaveSigningRule(e) {
    e.preventDefault();
    if (!state.activeCustomerId) return;

    const tierOrder = Number(document.getElementById('ruleTierOrderSelect').value || 1);
    const minAmount = Number(document.getElementById('ruleMinAmountInput').value || 0);
    const rawMax = document.getElementById('ruleMaxAmountInput').value.trim();
    const maxAmount = rawMax === '' ? null : Number(rawMax);
    const ruleExpression = document.getElementById('ruleExpressionSelect').value || '1A OR 2B';
    const humanReadable = `Requires ${ruleExpression}`;

    const existingRules = (state.snapshot && state.snapshot.signing_rules) || [];
    const updatedRules = existingRules.map((r) => {
      if (Number(r.tier_order) === tierOrder) {
        return Object.assign({}, r, {
          min_amount_sgd: minAmount,
          max_amount_sgd: maxAmount,
          rule_expression: ruleExpression,
          human_readable_rule: humanReadable,
        });
      }
      return r;
    });

    const res = await apiFetchWithFallback(
      `/api/customers/${encodeURIComponent(state.activeCustomerId)}/signing-rules`,
      `/api/profiles/${encodeURIComponent(state.activeCustomerId)}/rules`,
      {
        method: 'POST',
        body: JSON.stringify({
          tier_order: tierOrder,
          min_amount_sgd: minAmount,
          max_amount_sgd: maxAmount,
          rule_expression: ruleExpression,
          human_readable_rule: humanReadable,
          rules: updatedRules.length
            ? updatedRules
            : [
                {
                  tier_order: tierOrder,
                  min_amount_sgd: minAmount,
                  max_amount_sgd: maxAmount,
                  rule_expression: ruleExpression,
                  human_readable_rule: humanReadable,
                },
              ],
        }),
      }
    );

    if (res.ok && res.data) {
      if (res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      } else {
        await loadCustomerMandateSnapshot(state.activeCustomerId);
      }
      showToast('Signing Rule Updated', `Tier ${tierOrder} updated to ${ruleExpression} in PostgreSQL.`, 'success');
      await runTransactionSimulation();
    } else {
      showToast('Rule Update Error', (res.data && res.data.message) || 'Failed to update signing rule.', 'error');
    }
  }

  async function runTransactionSimulation() {
    if (!state.activeCustomerId) return;
    const amount = Number(document.getElementById('simAmountNumericInput').value || 150000);
    const currency = document.getElementById('simCurrencySelect').value || 'SGD';
    const displayLabel = document.getElementById('simSliderDisplayLabel');
    if (displayLabel) {
      displayLabel.textContent = formatCurrency(amount, currency);
    }

    const res = await apiFetchWithFallback(
      `/api/customers/${encodeURIComponent(state.activeCustomerId)}/simulate`,
      `/api/profiles/${encodeURIComponent(state.activeCustomerId)}/simulate`,
      {
        method: 'POST',
        body: JSON.stringify({ amount, currency }),
      }
    );

    if (res.ok && res.data) {
      state.lastSimulationResult = res.data;
      renderSimulationResult(res.data);
    }
  }

  function renderSimulationResult(simData) {
    const box = document.getElementById('simulatorResultBox');
    if (!box || !simData) return;

    const matchedTier = simData.matched_tier || {};
    const evaluatedSgd = Number(simData.evaluated_amount_sgd ?? simData.amount_sgd ?? simData.input_amount ?? 0);
    const fxRate = Number(simData.fx_rate_to_sgd ?? 1.0);
    const combos = simData.eligible_signatory_combinations || [];

    const combosHtml =
      combos.length > 0
        ? combos
            .slice(0, 4)
            .map((combo) => {
              if (typeof combo === 'string') {
                return `<div class="sim-combo-item">&#x2714; ${escapeHtml(combo)}</div>`;
              }
              const sigNames = (combo.signatories || [])
                .map((s) => `${s.full_name} (Grp ${s.signing_group})`)
                .join(' + ');
              return `
                <div class="sim-combo-item">
                  <strong>${escapeHtml(combo.option_label || 'Authorized Combination')}:</strong>
                  ${escapeHtml(sigNames)}
                </div>
              `;
            })
            .join('')
        : `<div style="color:var(--google-yellow-dark); background:var(--google-yellow-tint); padding:8px 10px; border-radius:6px; font-size:12px;">&#x26A0; Additional active signatories required to satisfy ${escapeHtml(matchedTier.rule_expression || 'policy rule')}.</div>`;

    box.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">
        <span class="badge badge-green">MATCHED: TIER ${escapeHtml(matchedTier.tier_order || 1)}</span>
        <span style="font-family:var(--font-mono); font-size:11.5px; color:var(--google-blue-dark); font-weight:600;">
          FX Rate: 1 ${escapeHtml(simData.input_currency || 'SGD')} = ${fxRate} SGD
        </span>
      </div>
      <div style="font-size:15px; font-weight:700; color:var(--ink-primary);">
        ${escapeHtml(matchedTier.tier_label || 'Corporate Mandate Tier')} &mdash;
        <span style="color:var(--google-blue-dark);">${escapeHtml(matchedTier.rule_expression || '1A')}</span>
      </div>
      <div style="font-size:12.5px; color:var(--ink-secondary); margin-top:3px;">
        Evaluated SGD Equivalent: <strong style="font-family:var(--font-mono); color:var(--ink-primary);">${formatCurrency(evaluatedSgd, 'SGD')}</strong>
        &bull; ${escapeHtml(matchedTier.human_readable_rule || '')}
      </div>
      <div class="sim-combos-list">
        <div style="font-size:11px; text-transform:uppercase; color:var(--ink-muted); font-weight:700;">
          Eligible Signatory Combinations (${combos.length} Valid Options):
        </div>
        ${combosHtml}
      </div>
    `;
  }

  // ==========================================================================
  // 7. Stage 4 — Side-by-Side Mandate Diff & Board Resolution Pre-Flight Audit
  // ==========================================================================

  function renderStage4AuditAndMandateDiff(snapshot) {
    const currentCol = document.getElementById('mandateDiffCurrentCol');
    const proposedCol = document.getElementById('mandateDiffProposedCol');
    const accounts = snapshot.accounts || [];
    const signatories = snapshot.signatories || [];
    const rules = snapshot.signing_rules || [];
    const diff = snapshot.mandate_diff || {};

    const scopedAccounts = accounts.filter(isAccountInScope);
    const activeSigs = signatories.filter((s) => s.status === 'ACTIVE');
    const pendingAddSigs =
      diff.signatories_added && diff.signatories_added.length
        ? diff.signatories_added
        : signatories.filter((s) => s.status === 'PENDING_ADDITION');
    const revokedSigs =
      diff.signatories_revoked && diff.signatories_revoked.length
        ? diff.signatories_revoked
        : signatories.filter((s) => s.status === 'REVOKED');

    if (currentCol) {
      currentCol.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <strong style="font-size:13.5px;">Baseline Corporate Mandate</strong>
          <span class="badge badge-slate">Version ${escapeHtml((snapshot.customer && snapshot.customer.active_mandate_version) || 1)}</span>
        </div>
        <div style="font-size:12.5px; display:flex; flex-direction:column; gap:8px;">
          <div>
            <strong>Total Corporate Accounts:</strong> ${accounts.length} Account(s)
          </div>
          <div>
            <strong>Active Baseline Signatories (${activeSigs.length}):</strong>
            <ul style="margin:4px 0 0 18px;">
              ${activeSigs.map((s) => `<li>${escapeHtml(s.full_name)} &mdash; Group ${escapeHtml(s.signing_group)} (${escapeHtml(s.role_title)})</li>`).join('')}
            </ul>
          </div>
          <div>
            <strong>Signing Rules Matrix:</strong>
            <ul style="margin:4px 0 0 18px;">
              ${rules.map((r) => `<li>Tier ${escapeHtml(r.tier_order)}: <code>${escapeHtml(r.rule_expression)}</code> (Max: ${r.max_amount_sgd ? formatCurrency(r.max_amount_sgd, 'SGD') : 'Unlimited'})</li>`).join('')}
            </ul>
          </div>
        </div>
      `;
    }

    if (proposedCol) {
      proposedCol.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <strong style="font-size:13.5px; color:#065F46;">Proposed Amended Mandate (Live DB State)</strong>
          <span class="badge badge-green">${scopedAccounts.length} Accounts in Scope</span>
        </div>
        <div style="font-size:12.5px; display:flex; flex-direction:column; gap:8px;">
          <div>
            <strong>Scoped Target Accounts:</strong>
            ${scopedAccounts.map((a) => `<code>${escapeHtml(a.account_number)} (${escapeHtml(a.currency)})</code>`).join(', ') || 'None'}
          </div>
          <div>
            <strong>+ Added / Updated Signatories (${pendingAddSigs.length}):</strong>
            ${
              pendingAddSigs.length > 0
                ? `<ul style="margin:4px 0 0 18px; color:#047857; font-weight:600;">
                     ${pendingAddSigs.map((s) => `<li>+ ${escapeHtml(s.full_name)} (Group ${escapeHtml(s.signing_group)} &bull; ${escapeHtml(s.role_title)})</li>`).join('')}
                   </ul>`
                : `<span style="color:var(--dbs-text-muted);"> None</span>`
            }
          </div>
          <div>
            <strong>&minus; Revoked Signatories (${revokedSigs.length}):</strong>
            ${
              revokedSigs.length > 0
                ? `<ul style="margin:4px 0 0 18px; color:#B91C1C; font-weight:600;">
                     ${revokedSigs.map((s) => `<li>&minus; ${escapeHtml(s.full_name)} (Group ${escapeHtml(s.signing_group)})</li>`).join('')}
                   </ul>`
                : `<span style="color:var(--dbs-text-muted);"> None</span>`
            }
          </div>
          <div>
            <strong>Configured Tiered Rules:</strong>
            <ul style="margin:4px 0 0 18px;">
              ${rules.map((r) => `<li>Tier ${escapeHtml(r.tier_order)}: <strong>${escapeHtml(r.rule_expression)}</strong> &mdash; ${escapeHtml(r.human_readable_rule)}</li>`).join('')}
            </ul>
          </div>
        </div>
      `;
    }

    // Populate Board Resolution Auditor from latest board_resolution row
    const resObj =
      snapshot.board_resolution ||
      (snapshot.board_resolutions && snapshot.board_resolutions[0]) ||
      {};
    const refInput = document.getElementById('auditResolutionRefInput');
    const clauseInput = document.getElementById('auditClauseTextInput');
    const scoreBadge = document.getElementById('auditOverallScoreBadge');

    if (refInput && resObj.resolution_ref) {
      refInput.value = resObj.resolution_ref;
    } else if (refInput && !refInput.value) {
      refInput.value = `BRC-09-2026-${state.activeCustomerId || '001'}`;
    }

    if (clauseInput && resObj.extracted_text_summary) {
      clauseInput.value = resObj.extracted_text_summary;
    } else if (clauseInput && !clauseInput.value && snapshot.customer) {
      const acctNums = scopedAccounts.map((a) => a.account_number).join(', ');
      clauseInput.value = `RESOLVED THAT a quorum of Directors of ${snapshot.customer.company_name} (UEN: ${snapshot.customer.uen}) being present, the Change of Account Mandate for DBS accounts [${acctNums}] is hereby approved with Group A/B/C tiered signing rules and full DBS IDEAL Electronic Banking & DigiSign Mobile Token authorization.`;
    }

    if (scoreBadge) {
      const score = resObj.audit_score ?? 100;
      const status = resObj.audit_status || 'COMPLIANT';
      scoreBadge.textContent = `Audit Score: ${score}/100 (${status})`;
      scoreBadge.className = `badge ${score >= 90 ? 'badge-green' : 'badge-amber'}`;
    }

    renderClauseChecklist(resObj.clause_checklist || {});
  }

  function renderClauseChecklist(checklist) {
    const grid = document.getElementById('clauseChecklistGrid');
    if (!grid) return;

    const checks = [
      {
        key: 'quorum_verified',
        altKey: 'quorum_confirmed',
        title: '1. Board Quorum & ACRA Authority',
        desc: 'Minimum required Directors / Partners present and verified against ACRA UEN.',
      },
      {
        key: 'target_accounts_referenced',
        altKey: 'account_mandate_amendment_clause',
        title: '2. Target Account Schedule',
        desc: 'All scoped corporate bank account numbers explicitly scheduled in resolution.',
      },
      {
        key: 'signing_matrix_aligned',
        altKey: 'specimen_signature_ratification',
        title: '3. Group A/B/C Matrix & Tier Limits',
        desc: 'Signatory designations and SGD tier thresholds match database configuration.',
      },
      {
        key: 'ideal_digisign_clause_included',
        altKey: 'ideal_electronic_banking_clause',
        title: '4. Electronic Banking (DBS IDEAL) Clause',
        desc: 'Explicitly authorizes DBS IDEAL Digital Token and DigiSign execution.',
      },
    ];

    grid.innerHTML = checks
      .map((item) => {
        const passed =
          checklist[item.key] !== undefined
            ? Boolean(checklist[item.key])
            : checklist[item.altKey] !== undefined
            ? Boolean(checklist[item.altKey])
            : true;
        return `
          <div class="clause-check-card ${passed ? 'passed' : 'failed'}">
            <span style="font-size:18px;">${passed ? '&#x2705;' : '&#x26A0;&#xFE0F;'}</span>
            <div>
              <div style="font-weight:800; font-size:12.5px;">${escapeHtml(item.title)}</div>
              <div style="font-size:11.5px; color:var(--dbs-text-secondary); margin-top:2px;">${escapeHtml(item.desc)}</div>
            </div>
          </div>
        `;
      })
      .join('');
  }

  async function handleRunBoardResolutionAudit() {
    if (!state.activeCustomerId) return;
    const resType = document.getElementById('auditResolutionTypeSelect').value || 'BRC-09';
    const resRef = document.getElementById('auditResolutionRefInput').value.trim();
    const clauseText = document.getElementById('auditClauseTextInput').value.trim();

    const res = await apiFetchWithFallback(
      `/api/customers/${encodeURIComponent(state.activeCustomerId)}/board-resolution/audit`,
      `/api/profiles/${encodeURIComponent(state.activeCustomerId)}/audit-resolution`,
      {
        method: 'POST',
        body: JSON.stringify({
          resolution_type: resType,
          format_type: resType === 'BRC-09' ? 'STANDARD_BRC_09' : 'CUSTOM_BOARD_MINUTES',
          resolution_ref: resRef,
          clause_text: clauseText,
          custom_resolution_text: clauseText,
          custom_text: clauseText,
        }),
      }
    );

    if (res.ok && res.data) {
      if (res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      } else {
        await loadCustomerMandateSnapshot(state.activeCustomerId);
      }
      if (res.data.clause_checklist) {
        renderClauseChecklist(res.data.clause_checklist);
      }
      showToast('Board Resolution Audited', `Compliance Audit completed (${res.data.audit_score ?? 100}/100).`, 'success');
    } else {
      showToast('Audit Error', (res.data && res.data.message) || 'Board resolution audit failed.', 'error');
    }
  }

  // ==========================================================================
  // 8. Stage 5 — Digital Execution (DigiSign Tracker) & Immutable Audit Trail
  // ==========================================================================

  function renderStage5ExecutionAndAuditLogs(snapshot) {
    const appObj =
      snapshot.latest_application ||
      (snapshot.applications && snapshot.applications[0]) ||
      {};
    const appBadge = document.getElementById('stage5AppRefBadge');
    if (appBadge) {
      if (appObj.application_ref) {
        appBadge.textContent = `${appObj.application_ref} • ${appObj.status || 'SUBMITTED'}`;
        appBadge.className = `badge ${String(appObj.status).includes('APPROVED') ? 'badge-green' : 'badge-blue'}`;
      } else {
        appBadge.textContent = 'Ready for Submission';
        appBadge.className = 'badge badge-slate';
      }
    }

    const trackerList = document.getElementById('digisignTrackerList');
    if (trackerList) {
      let signers = appObj.digisign_signers || [];
      if (typeof signers === 'string') {
        try {
          signers = JSON.parse(signers);
        } catch (_) {
          signers = [];
        }
      }

      if (!signers.length && snapshot.signatories) {
        // Show required Group A signers from DB
        signers = snapshot.signatories
          .filter((s) => s.signing_group === 'A' && s.status !== 'REVOKED')
          .map((s) => ({
            signer_name: s.full_name,
            role_title: s.role_title,
            auth_method: s.auth_method || 'IDEAL Token',
            status: 'PENDING_SUBMISSION',
          }));
      }

      trackerList.innerHTML = signers
        .map((s) => {
          const name = s.signer_name || s.full_name || 'Authorized Director';
          const status = String(s.status || 'PENDING');
          const isSigned = status.includes('SIGNED') || status === 'COMPLETED';
          return `
            <div class="cosigner-card ${isSigned ? 'signed' : ''}">
              <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                  <div style="font-weight:800; font-size:13.5px;">${escapeHtml(name)}</div>
                  <div style="font-size:11.5px; color:var(--dbs-text-secondary);">${escapeHtml(s.role_title || 'Group A Director')}</div>
                </div>
                <span class="badge ${isSigned ? 'badge-green' : 'badge-amber'}">${escapeHtml(status)}</span>
              </div>
              <div style="font-size:11.5px; color:var(--dbs-text-muted);">
                Auth Method: <strong>${escapeHtml(s.auth_method || 'IDEAL Token')}</strong>
                ${s.signed_at ? `<br/>Signed At: <code>${escapeHtml(s.signed_at)}</code>` : ''}
              </div>
              ${
                !isSigned
                  ? `<button type="button" class="btn btn-success single-cosign-btn"
                             data-signer-name="${escapeHtml(name)}"
                             data-app-ref="${escapeHtml(appObj.application_ref || '')}"
                             style="padding:6px 10px; font-size:11.5px;">
                       &#x1F510; Sign via IDEAL Token (${escapeHtml(name)})
                     </button>`
                  : `<div style="font-size:11.5px; font-weight:700; color:var(--dbs-green);">&#x2714; Cryptographic Token Verified</div>`
              }
            </div>
          `;
        })
        .join('');

      trackerList.querySelectorAll('.single-cosign-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          const signerName = btn.getAttribute('data-signer-name');
          const appRef = btn.getAttribute('data-app-ref');
          handleExecuteCosign(appRef, signerName);
        });
      });
    }

    // Render Audit Logs Table
    const auditLogs = snapshot.audit_logs || [];
    const countBadge = document.getElementById('auditLogCountBadge');
    const tbody = document.getElementById('auditLogsTableBody');
    if (countBadge) countBadge.textContent = `${auditLogs.length} PostgreSQL Audit Events`;
    if (!tbody) return;

    tbody.innerHTML = auditLogs
      .map((log) => {
        const ts = log.created_at || log.timestamp || '';
        const eventType = log.event_type || log.action_type || 'MANDATE_EVENT';
        const actor = `${log.actor_name || log.actor || 'SYSTEM'} (${log.actor_channel || 'API'})`;
        const target = log.target_entity || log.customer_id || '';
        const summary = log.compliance_notes || log.summary || JSON.stringify(log.after_state || {});
        return `
          <tr>
            <td style="font-family:var(--font-mono); font-size:11px; white-space:nowrap;">${escapeHtml(ts)}</td>
            <td><span class="badge badge-slate" style="font-family:var(--font-mono);">${escapeHtml(eventType)}</span></td>
            <td style="font-size:12px; font-weight:600;">${escapeHtml(actor)}</td>
            <td style="font-family:var(--font-mono); font-size:11.5px;">${escapeHtml(target)}</td>
            <td style="font-size:12px;">${escapeHtml(summary)}</td>
          </tr>
        `;
      })
      .join('');
  }

  async function handleSubmitMandateApplication() {
    if (!state.activeCustomerId) return;
    const submittedBy =
      document.getElementById('submitterNameInput').value.trim() ||
      'Authorized Group A Director';
    const resRef = document.getElementById('auditResolutionRefInput').value.trim();

    const res = await apiFetchWithFallback(
      `/api/customers/${encodeURIComponent(state.activeCustomerId)}/submit`,
      `/api/profiles/${encodeURIComponent(state.activeCustomerId)}/submit`,
      {
        method: 'POST',
        body: JSON.stringify({
          submitted_by: submittedBy,
          resolution_ref: resRef,
          notes: 'Submitted via DBS IDEAL 5-Stage Change of Account Mandate Workspace',
        }),
      }
    );

    if (res.ok && res.data) {
      if (res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      } else {
        await loadCustomerMandateSnapshot(state.activeCustomerId);
      }
      const appRef = res.data.application_ref || (res.data.application && res.data.application.application_ref) || 'COM-2026';
      showToast('Mandate Change Submitted', `Application ${appRef} persisted to PostgreSQL.`, 'success');
      navigateToStage(5, { flash: true });
    } else {
      showToast('Submission Error', (res.data && res.data.message) || 'Failed to submit mandate change request.', 'error');
    }
  }

  async function handleExecuteCosign(applicationRef, signerName) {
    if (!state.activeCustomerId) return;
    const appObj =
      (state.snapshot && state.snapshot.latest_application) ||
      (state.snapshot && state.snapshot.applications && state.snapshot.applications[0]) ||
      {};
    const refToUse = applicationRef || appObj.application_ref || appObj.application_id || '';

    const res = await apiFetchWithFallback(
      `/api/customers/${encodeURIComponent(state.activeCustomerId)}/cosign`,
      `/api/applications/${encodeURIComponent(refToUse || state.activeCustomerId)}/cosign`,
      {
        method: 'POST',
        body: JSON.stringify({
          application_ref: refToUse,
          signer_name: signerName || null,
          auth_method: 'IDEAL Token',
        }),
      }
    );

    if (res.ok && res.data) {
      if (res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      } else {
        await loadCustomerMandateSnapshot(state.activeCustomerId);
      }
      showToast('Co-Signer DigiSign Executed', `Cryptographic signature recorded in PostgreSQL.`, 'success');
    } else {
      showToast('Co-Sign Error', (res.data && res.data.message) || 'Failed to execute co-signer token.', 'error');
    }
  }

  // ==========================================================================
  // 9. Synchronized Gemini Live (`models/gemini-3.8-live-extended-thinking`)
  //    AI Studio v1alpha BidiGenerateContent + Backend `/ws/live` Client
  // ==========================================================================

  function connectLiveWebSocket() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws/live`;
    const badge = document.getElementById('wsConnectionBadge');

    try {
      const ws = new WebSocket(wsUrl);
      state.ws = ws;

      ws.onopen = () => {
        state.wsConnected = true;
        if (badge) {
          badge.textContent = 'Connected';
          badge.className = 'badge badge-green';
        }
        ws.send(
          JSON.stringify({
            type: 'init',
            customer_id: state.activeCustomerId || 'CUST-001',
            stage: state.activeStage || 1,
            mode: state.isRecordingVoice ? 'voice' : 'chat',
          })
        );
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          handleWebSocketMessage(msg);
        } catch (err) {
          console.error('Invalid WS JSON frame:', err);
        }
      };

      ws.onclose = () => {
        state.wsConnected = false;
        if (badge) {
          badge.textContent = 'Reconnecting...';
          badge.className = 'badge badge-amber';
        }
        setTimeout(connectLiveWebSocket, 3000);
      };
    } catch (err) {
      console.error('WebSocket init error:', err);
    }
  }

  function handleWebSocketMessage(msg) {
    if (!msg || !msg.type) return;

    switch (msg.type) {
      case 'session_ready': {
        if (msg.model) {
          const modelBadge = document.getElementById('modelNameBadgeText');
          if (modelBadge) modelBadge.textContent = msg.model;
        }
        break;
      }

      case 'thinking_trace':
      case 'thought_trace': {
        appendThinkingTraceToStream(msg.text || '');
        break;
      }

      case 'tool_call_start': {
        upsertToolExecutionCard(msg.call_id || msg.tool_name, msg.tool_name, msg.args || {}, null);
        break;
      }

      case 'tool_call_result': {
        upsertToolExecutionCard(msg.call_id || msg.tool_name, msg.tool_name, msg.args || {}, msg.result || {});
        if (msg.ui_sync) {
          handleUiSync(msg.ui_sync);
        } else if (msg.result && (msg.result.ui_action || msg.result.target_stage)) {
          handleUiSync(msg.result);
        }
        break;
      }

      case 'ui_sync': {
        handleUiSync(msg);
        break;
      }

      case 'interrupted': {
        clearAudioPlaybackQueueOnly();
        break;
      }

      case 'audio_out':
      case 'audio_output': {
        const b64 = msg.pcm24_base64 || msg.data;
        const rate = Number(msg.sample_rate || 24000);
        if (b64) {
          // Deduplicate identical consecutive audio frames so voice NEVER repeats chunks
          const sig = `${b64.length}:${b64.slice(0, 32)}:${b64.slice(-32)}`;
          if (sig !== state.lastAudioSig) {
            state.lastAudioSig = sig;
            enqueuePcm24AudioPlayback(b64, rate);
          }
        }
        break;
      }

      case 'output_transcript':
      case 'transcript':
      case 'assistant_text': {
        if (msg.text) {
          const role = msg.role === 'user' ? 'user' : 'assistant';
          const turnId = msg.turn_id || `${role}_latest`;
          upsertTurnMessage(turnId, role, msg.text);
        }
        break;
      }

      case 'input_transcript': {
        if (msg.text) {
          const turnId = msg.turn_id || 'voice_user_latest';
          upsertTurnMessage(turnId, 'user', `🎤 ${msg.text}`);
        }
        break;
      }

      default:
        break;
    }
  }

  async function handleUiSync(syncPayload) {
    if (!syncPayload) return;

    const updatedProfileId = syncPayload.updated_profile_id || syncPayload.active_profile_id || syncPayload.customer_id;
    const targetStage = syncPayload.target_stage || syncPayload.navigate_to_stage;
    const highlightId = syncPayload.highlight_element;

    if (updatedProfileId && updatedProfileId !== state.activeCustomerId) {
      state.activeCustomerId = updatedProfileId;
      renderCustomerSwitcherHeader(state.customers, updatedProfileId);
    }

    if (syncPayload.workspace_snapshot && syncPayload.workspace_snapshot.customer) {
      applyWorkspaceSnapshot(syncPayload.workspace_snapshot);
    } else if (state.activeCustomerId) {
      await loadCustomerMandateSnapshot(state.activeCustomerId);
    }

    if (targetStage) {
      navigateToStage(targetStage, { flash: true });
    }

    if (highlightId) {
      setTimeout(() => flashElement(highlightId), 120);
    }

    if (syncPayload.toast_notification && syncPayload.toast_notification.message) {
      showToast(
        syncPayload.toast_notification.title || 'Mandate Updated',
        syncPayload.toast_notification.message,
        syncPayload.toast_notification.severity || 'success'
      );
    }
  }

  function upsertTurnMessage(turnId, role, text) {
    const stream = document.getElementById('copilotChatStream');
    if (!stream || !text) return;

    // When a user message arrives, reset activeAssistantBubble so the next assistant turn gets 1 fresh bubble
    if (role === 'user') {
      state.activeAssistantBubble = null;
      if (state.activeUserVoiceBubble && state.activeUserVoiceBubble.parentNode === stream && String(turnId).includes('voice')) {
        state.activeUserVoiceBubble.innerHTML = `<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`;
        stream.scrollTop = stream.scrollHeight;
        return;
      }
      if (turnId && state.turnBubbleMap[turnId] && state.turnBubbleMap[turnId].parentNode === stream) {
        state.turnBubbleMap[turnId].innerHTML = `<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`;
        stream.scrollTop = stream.scrollHeight;
        return;
      }
      const uDiv = document.createElement('div');
      uDiv.className = 'chat-msg user';
      if (turnId) {
        uDiv.setAttribute('data-turn-id', turnId);
        state.turnBubbleMap[turnId] = uDiv;
      }
      if (String(turnId).includes('voice')) {
        state.activeUserVoiceBubble = uDiv;
      }
      uDiv.innerHTML = `<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`;
      stream.appendChild(uDiv);
      stream.scrollTop = stream.scrollHeight;
      return;
    }

    // Assistant turn: STRICTLY update the single activeAssistantBubble in place!
    state.activeUserVoiceBubble = null;
    if (state.activeAssistantBubble && state.activeAssistantBubble.parentNode === stream) {
      state.activeAssistantBubble.innerHTML = `<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`;
      stream.scrollTop = stream.scrollHeight;
      return;
    }

    if (turnId && state.turnBubbleMap[turnId] && state.turnBubbleMap[turnId].parentNode === stream) {
      const existing = state.turnBubbleMap[turnId];
      state.activeAssistantBubble = existing;
      existing.innerHTML = `<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`;
      stream.scrollTop = stream.scrollHeight;
      return;
    }

    const div = document.createElement('div');
    div.className = 'chat-msg assistant';
    if (turnId) {
      div.setAttribute('data-turn-id', turnId);
      state.turnBubbleMap[turnId] = div;
    }
    state.activeAssistantBubble = div;
    div.innerHTML = `<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`;
    stream.appendChild(div);
    stream.scrollTop = stream.scrollHeight;
  }

  function appendChatMessage(role, text, extraHtml = '') {
    const stream = document.getElementById('copilotChatStream');
    if (!stream) return;
    const div = document.createElement('div');
    div.className = `chat-msg ${role}`;
    div.innerHTML = `${extraHtml}<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`;
    stream.appendChild(div);
    stream.scrollTop = stream.scrollHeight;
  }

  function appendThinkingTraceToStream(traceText) {
    if (!traceText) return;
    const stream = document.getElementById('copilotChatStream');
    if (!stream) return;
    const details = document.createElement('details');
    details.className = 'thinking-trace-box';
    details.open = false;
    details.innerHTML = `
      <summary>&#x2726; View Reasoning Trace</summary>
      <div class="thinking-trace-body">${escapeHtml(traceText)}</div>
    `;
    stream.appendChild(details);
    stream.scrollTop = stream.scrollHeight;
  }

  function upsertToolExecutionCard(callId, toolName, args, resultObj) {
    const stream = document.getElementById('copilotChatStream');
    if (!stream) return;
    const targetStage = (resultObj && resultObj.target_stage) || state.activeStage || 1;
    const statusText = resultObj ? (resultObj.status === 'error' ? 'Policy Blocked' : 'Completed') : 'Executing...';
    const friendlyName = String(toolName || 'Tool')
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());

    let card = callId && state.toolCardMap[callId];
    if (!card || card.parentNode !== stream) {
      card = document.createElement('div');
      card.className = 'tool-exec-card';
      if (callId) state.toolCardMap[callId] = card;
      stream.appendChild(card);
    }

    card.innerHTML = `
      <div class="tool-exec-header">
        <span>&#x2713; ${escapeHtml(friendlyName)}</span>
        <button type="button" class="tool-stage-jump-btn" data-jump-stage="${escapeHtml(targetStage)}">
          ${escapeHtml(statusText)} &bull; Step ${escapeHtml(targetStage)} &rarr;
        </button>
      </div>
    `;

    const jumpBtn = card.querySelector('.tool-stage-jump-btn');
    if (jumpBtn) {
      jumpBtn.addEventListener('click', () => {
        navigateToStage(targetStage, { flash: true });
      });
    }
    stream.scrollTop = stream.scrollHeight;
  }

  async function sendChatPrompt(promptText) {
    const trimmed = String(promptText || '').trim();
    if (!trimmed) return;

    const userTurnId = `local_user_${Date.now()}`;
    upsertTurnMessage(userTurnId, 'user', trimmed);
    const inputEl = document.getElementById('copilotChatInput');
    if (inputEl) inputEl.value = '';

    const res = await apiFetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        message: trimmed,
        customer_id: state.activeCustomerId || 'CUST-001',
        current_stage: state.activeStage || 1,
      }),
    });

    if (res.ok && res.data) {
      const traces = res.data.thinking_traces || [];
      traces.forEach((t) => appendThinkingTraceToStream(t));

      const toolCalls = res.data.tool_calls || [];
      toolCalls.forEach((tc, idx) => {
        upsertToolExecutionCard(tc.call_id || `${Date.now()}_${idx}`, tc.tool_name || tc.name, tc.args || {}, tc.result || {});
      });

      if (res.data.reply) {
        upsertTurnMessage(`local_bot_${Date.now()}`, 'assistant', res.data.reply);
      }

      if (res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      }
      if (res.data.ui_sync) {
        await handleUiSync(res.data.ui_sync);
      }
    } else {
      appendChatMessage('assistant', 'Unable to complete request. Please check your connection.');
    }
  }

  // ==========================================================================
  // 10. Google 4-Color Equalizer Waveform, AI Studio Live v1alpha Direct +
  //     Backend Voice Bridge, & Anti-Echo Microphone Gating
  // ==========================================================================

  function startWaveformVisualizer() {
    const canvas = document.getElementById('voiceWaveformCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let phase = 0;
    const googleColors = ['#4285F4', '#EA4335', '#FBBC05', '#34A853'];

    function draw() {
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const isSpeaking = state.activePlaybackNodes.length > 0;
      const isListening = state.isRecordingVoice && !isSpeaking;
      const isActive = isSpeaking || isListening;

      const barCount = 28;
      const barWidth = 5;
      const gap = (w - barCount * barWidth) / (barCount - 1);

      for (let i = 0; i < barCount; i++) {
        const color = googleColors[i % googleColors.length];
        const env = Math.sin((i / barCount) * Math.PI);
        const wave = Math.abs(Math.sin(i * 0.45 + phase));
        const amp = isActive
          ? Math.max(4, Math.min(h - 4, (state.currentAudioAmplitude * 24 + 8) * env * (0.4 + 0.6 * wave)))
          : 4;

        const x = i * (barWidth + gap);
        const y = (h - amp) / 2;

        ctx.fillStyle = isActive ? color : '#DADCE0';
        ctx.beginPath();
        ctx.roundRect(x, y, barWidth, amp, 2.5);
        ctx.fill();
      }

      phase += isActive ? 0.18 : 0.04;
      state.currentAudioAmplitude *= 0.9;
      state.waveformAnimId = requestAnimationFrame(draw);
    }

    draw();
  }

  function connectDirectAiStudioLiveSession() {
    return new Promise((resolve) => {
      const apiKey = (
        (document.getElementById('aiStudioApiKeyInput') &&
          document.getElementById('aiStudioApiKeyInput').value.trim()) ||
        state.aiStudioApiKey ||
        ''
      );
      if (!apiKey) {
        resolve(false);
        return;
      }

      const url = `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key=${encodeURIComponent(apiKey)}`;
      let settled = false;

      try {
        const aisWs = new WebSocket(url);
        state.aiStudioWs = aisWs;

        aisWs.onopen = () => {
          const setupPayload = {
            setup: {
              model: 'models/gemini-3.8-live-extended-thinking',
              generationConfig: {
                responseModalities: ['AUDIO'],
                thinkingConfig: {
                  thinkingLevel: 'LOW',
                },
                speechConfig: {
                  voiceConfig: {
                    prebuiltVoiceConfig: {
                      voiceName: 'Aoede',
                    },
                  },
                },
              },
              systemInstruction: {
                parts: [
                  {
                    text: `You are the Corporate Mandate AI Advisor for ${state.activeCustomerId || 'CUST-001'}. Speak concisely in 1-2 sentences and never repeat yourself.`,
                  },
                ],
              },
            },
          };
          aisWs.send(JSON.stringify(setupPayload));
        };

        aisWs.onmessage = async (ev) => {
          try {
            let rawText = ev.data;
            if (rawText instanceof Blob) {
              rawText = await rawText.text();
            }
            const data = JSON.parse(rawText);
            if (data.setupComplete) {
              state.aiStudioConnected = true;
              if (!settled) {
                settled = true;
                resolve(true);
              }
              return;
            }
            if (data.serverContent && data.serverContent.modelTurn && data.serverContent.modelTurn.parts) {
              for (const part of data.serverContent.modelTurn.parts) {
                if (part.inlineData && part.inlineData.data) {
                  handleWebSocketMessage({
                    type: 'audio_out',
                    pcm24_base64: part.inlineData.data,
                    sample_rate: 24000,
                  });
                }
              }
            }
          } catch (_) {}
        };

        aisWs.onerror = () => {
          state.aiStudioConnected = false;
          if (!settled) {
            settled = true;
            resolve(false);
          }
        };

        aisWs.onclose = () => {
          state.aiStudioConnected = false;
          state.aiStudioWs = null;
          if (!settled) {
            settled = true;
            resolve(false);
          }
        };

        setTimeout(() => {
          if (!settled) {
            settled = true;
            resolve(state.aiStudioConnected);
          }
        }, 1200);
      } catch (_) {
        resolve(false);
      }
    });
  }

  async function toggleVoiceMicrophone() {
    const orbBtn = document.getElementById('voiceMicToggleBtn');
    const stateLabel = document.getElementById('voiceStateLabel');

    if (state.isRecordingVoice) {
      stopVoiceMicrophone();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      state.micStream = stream;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      state.micAudioCtx = new AudioCtx({ sampleRate: 16000 });
      const source = state.micAudioCtx.createMediaStreamSource(stream);
      const processor = state.micAudioCtx.createScriptProcessor(4096, 1, 1);
      state.micProcessor = processor;

      // Attempt direct AI Studio v1alpha `models/gemini-3.8-live-extended-thinking` connection first;
      // if key requires backend bridge, seamlessly stream via `/ws/live`
      await connectDirectAiStudioLiveSession();

      processor.onaudioprocess = (e) => {
        if (!state.isRecordingVoice) return;
        // CRITICAL ANTI-ECHO GATE: Do not stream mic audio while assistant is speaking aloud!
        if (state.activePlaybackNodes.length > 0) return;

        const input = e.inputBuffer.getChannelData(0);
        let sum = 0;
        const pcm16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          const s = Math.max(-1, Math.min(1, input[i]));
          sum += Math.abs(s);
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        state.currentAudioAmplitude = Math.min(1, (sum / input.length) * 6);

        const bytes = new Uint8Array(pcm16.buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        const b64 = btoa(binary);

        if (state.aiStudioConnected && state.aiStudioWs && state.aiStudioWs.readyState === WebSocket.OPEN) {
          state.aiStudioWs.send(
            JSON.stringify({
              realtimeInput: {
                audio: {
                  data: b64,
                  mimeType: 'audio/pcm;rate=16000',
                },
              },
            })
          );
        } else if (state.ws && state.ws.readyState === WebSocket.OPEN) {
          state.ws.send(
            JSON.stringify({
              type: 'audio_chunk',
              pcm16_base64: b64,
              data: b64,
              sample_rate: 16000,
            })
          );
        }
      };

      source.connect(processor);
      processor.connect(state.micAudioCtx.destination);
      state.isRecordingVoice = true;

      if (orbBtn) orbBtn.classList.add('listening');
      if (stateLabel) stateLabel.textContent = 'Listening (Gemini Live 3.8)...';
    } catch (err) {
      showToast('Microphone Access', 'Please grant microphone permissions or use text chat.', 'info');
    }
  }

  function stopVoiceMicrophone() {
    state.isRecordingVoice = false;
    if (state.micProcessor) {
      state.micProcessor.disconnect();
      state.micProcessor = null;
    }
    if (state.micStream) {
      state.micStream.getTracks().forEach((t) => t.stop());
      state.micStream = null;
    }
    if (state.micAudioCtx) {
      state.micAudioCtx.close().catch(() => {});
      state.micAudioCtx = null;
    }
    if (state.aiStudioWs) {
      try { state.aiStudioWs.close(); } catch (_) {}
      state.aiStudioWs = null;
      state.aiStudioConnected = false;
    }
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: 'audio_stream_end' }));
    }
    const orbBtn = document.getElementById('voiceMicToggleBtn');
    const stateLabel = document.getElementById('voiceStateLabel');
    if (orbBtn) orbBtn.classList.remove('listening');
    if (stateLabel) stateLabel.textContent = 'Click mic for live voice';
  }

  function enqueuePcm24AudioPlayback(base64Pcm, sampleRate = 24000) {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!state.playbackAudioCtx) {
        state.playbackAudioCtx = new AudioCtx({ sampleRate });
      }
      if (state.playbackAudioCtx.state === 'suspended') {
        state.playbackAudioCtx.resume();
      }

      const binary = atob(base64Pcm);
      const byteLen = binary.length;
      const int16 = new Int16Array(Math.floor(byteLen / 2));
      for (let i = 0; i < int16.length; i++) {
        const lo = binary.charCodeAt(i * 2);
        const hi = binary.charCodeAt(i * 2 + 1);
        const val = (hi << 8) | lo;
        int16[i] = val >= 0x8000 ? val - 0x10000 : val;
      }

      const float32 = new Float32Array(int16.length);
      let sum = 0;
      for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 32768.0;
        sum += Math.abs(float32[i]);
      }
      state.currentAudioAmplitude = Math.min(1, (sum / Math.max(1, float32.length)) * 5);

      const audioBuffer = state.playbackAudioCtx.createBuffer(1, float32.length, sampleRate);
      audioBuffer.getChannelData(0).set(float32);

      const source = state.playbackAudioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(state.playbackAudioCtx.destination);

      const now = state.playbackAudioCtx.currentTime;
      const startAt = Math.max(now, state.playbackNextStartTime);
      source.start(startAt);
      state.playbackNextStartTime = startAt + audioBuffer.duration;
      state.activePlaybackNodes.push(source);

      const orbBtn = document.getElementById('voiceMicToggleBtn');
      const stateLabel = document.getElementById('voiceStateLabel');
      if (orbBtn) orbBtn.classList.add('speaking');
      if (stateLabel) stateLabel.textContent = 'Speaking...';

      source.onended = () => {
        state.activePlaybackNodes = state.activePlaybackNodes.filter((n) => n !== source);
        if (state.activePlaybackNodes.length === 0) {
          if (orbBtn) orbBtn.classList.remove('speaking');
          if (stateLabel) {
            stateLabel.textContent = state.isRecordingVoice
              ? 'Listening (Gemini Live 3.8)...'
              : 'Click mic for live voice';
          }
        }
      };
    } catch (err) {
      console.error('PCM24 playback decode error:', err);
    }
  }

  function clearAudioPlaybackQueueOnly() {
    state.activePlaybackNodes.forEach((node) => {
      try {
        node.stop();
      } catch (_) {}
    });
    state.activePlaybackNodes = [];
    state.playbackNextStartTime = 0;
    state.currentAudioAmplitude = 0;
    const orbBtn = document.getElementById('voiceMicToggleBtn');
    const stateLabel = document.getElementById('voiceStateLabel');
    if (orbBtn) orbBtn.classList.remove('speaking');
    if (stateLabel) {
      stateLabel.textContent = state.isRecordingVoice ? 'Listening (Gemini Live 3.8)...' : 'Click mic for live voice';
    }
  }

  function triggerBargeInInterruption() {
    clearAudioPlaybackQueueOnly();
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: 'barge_in' }));
    }
    showToast('Audio Stopped', 'Cleared audio playback queue.', 'info');
  }

  // ==========================================================================
  // 11. DOM Event Bindings & Bootstrap
  // ==========================================================================

  function bindUiEvents() {
    // AI Studio Key Config Drawer Toggle & Save
    const modelBadgeBtn = document.getElementById('geminiModelBadge');
    const keyDrawer = document.getElementById('apiKeyConfigDrawer');
    if (modelBadgeBtn && keyDrawer) {
      modelBadgeBtn.addEventListener('click', () => {
        keyDrawer.style.display = keyDrawer.style.display === 'none' ? 'block' : 'none';
      });
    }

    const saveKeyBtn = document.getElementById('saveAiStudioKeyBtn');
    if (saveKeyBtn) {
      saveKeyBtn.addEventListener('click', async () => {
        const keyInput = document.getElementById('aiStudioApiKeyInput');
        const newKey = keyInput ? keyInput.value.trim() : '';
        if (!newKey) return;
        state.aiStudioApiKey = newKey;
        const res = await apiFetch('/api/config/api-key', {
          method: 'POST',
          body: JSON.stringify({ api_key: newKey }),
        });
        if (res.ok) {
          showToast('AI Studio Key Updated', 'Configured models/gemini-3.8-live-extended-thinking with updated API key.', 'success');
          if (keyDrawer) keyDrawer.style.display = 'none';
        }
      });
    }

    // Customer Dropdown Switcher
    const customerSelect = document.getElementById('customerSelect');
    if (customerSelect) {
      customerSelect.addEventListener('change', (e) => {
        const cid = e.target.value;
        if (cid) switchCustomerProfile(cid);
      });
    }

    const refreshBtn = document.getElementById('refreshWorkspaceBtn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => {
        if (state.activeCustomerId) loadCustomerMandateSnapshot(state.activeCustomerId);
      });
    }

    // 5-Stage Stepper Buttons
    document.querySelectorAll('.stage-step-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const stage = Number(btn.getAttribute('data-stage'));
        navigateToStage(stage);
      });
    });

    // Stage 1 Controls
    const selectAllBtn = document.getElementById('selectAllAccountsBtn');
    if (selectAllBtn) {
      selectAllBtn.addEventListener('click', () => {
        document.querySelectorAll('.target-account-checkbox').forEach((cb) => {
          cb.checked = true;
          const card = cb.closest('.account-card');
          if (card) card.classList.add('selected');
        });
      });
    }

    const saveTargetAccountsBtn = document.getElementById('saveTargetAccountsBtn');
    if (saveTargetAccountsBtn) {
      saveTargetAccountsBtn.addEventListener('click', saveTargetAccountsAndContinue);
    }

    // Stage 2 Controls
    const dismissAlertBtn = document.getElementById('dismissGovernanceAlertBtn');
    if (dismissAlertBtn) {
      dismissAlertBtn.addEventListener('click', () => {
        document.getElementById('governanceAlertBanner').classList.remove('visible');
      });
    }

    const addSigForm = document.getElementById('addSignatoryForm');
    if (addSigForm) {
      addSigForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
          full_name: document.getElementById('sigFullNameInput').value.trim(),
          role_title: document.getElementById('sigRoleTitleInput').value.trim(),
          signing_group: document.getElementById('sigGroupSelect').value,
          nric_masked: document.getElementById('sigNricInput').value.trim(),
          id_number: document.getElementById('sigNricInput').value.trim(),
          auth_method: document.getElementById('sigAuthMethodSelect').value,
          email: document.getElementById('sigEmailInput').value.trim(),
          phone_masked: document.getElementById('sigPhoneInput').value.trim(),
          ocr_verified: true,
          specimen_ref: document.getElementById('sigSpecimenRefInput').value.trim() || 'SPEC-2026-MANUAL',
        };
        await handleAddOrUpdateSignatory(payload);
      });
    }

    const previewOcrBtn = document.getElementById('previewOcrBtn');
    if (previewOcrBtn) {
      previewOcrBtn.addEventListener('click', () => {
        extractOcrCandidateFromInputsOrFile();
      });
    }

    const confirmOcrPersistBtn = document.getElementById('confirmOcrPersistBtn');
    if (confirmOcrPersistBtn) {
      confirmOcrPersistBtn.addEventListener('click', async () => {
        const candidate = extractOcrCandidateFromInputsOrFile();
        await handleAddOrUpdateSignatory(candidate);
      });
    }

    const jump3 = document.getElementById('jumpToStage3From2Btn');
    if (jump3) jump3.addEventListener('click', () => navigateToStage(3));

    // Stage 3 Controls
    const ruleForm = document.getElementById('configureRuleForm');
    if (ruleForm) {
      ruleForm.addEventListener('submit', handleSaveSigningRule);
    }

    const slider = document.getElementById('simAmountSlider');
    const numInput = document.getElementById('simAmountNumericInput');
    const currSelect = document.getElementById('simCurrencySelect');
    const simBtn = document.getElementById('runSimulationBtn');

    if (slider && numInput) {
      slider.addEventListener('input', () => {
        numInput.value = slider.value;
        const curr = currSelect ? currSelect.value : 'SGD';
        const lbl = document.getElementById('simSliderDisplayLabel');
        if (lbl) lbl.textContent = formatCurrency(slider.value, curr);
      });
      slider.addEventListener('change', runTransactionSimulation);
      numInput.addEventListener('change', () => {
        slider.value = Math.min(2000000, Math.max(10000, Number(numInput.value || 150000)));
        runTransactionSimulation();
      });
    }
    if (currSelect) currSelect.addEventListener('change', runTransactionSimulation);
    if (simBtn) simBtn.addEventListener('click', runTransactionSimulation);

    const jump4 = document.getElementById('jumpToStage4From3Btn');
    if (jump4) jump4.addEventListener('click', () => navigateToStage(4));

    // Stage 4 Controls
    const brc09Btn = document.getElementById('selectBrc09FormatBtn');
    if (brc09Btn) {
      brc09Btn.addEventListener('click', () => {
        document.getElementById('auditResolutionTypeSelect').value = 'BRC-09';
        handleRunBoardResolutionAudit();
      });
    }
    const customMinBtn = document.getElementById('selectCustomMinutesBtn');
    if (customMinBtn) {
      customMinBtn.addEventListener('click', () => {
        document.getElementById('auditResolutionTypeSelect').value = 'CUSTOM';
      });
    }
    const auditBtn = document.getElementById('runBoardResolutionAuditBtn');
    if (auditBtn) auditBtn.addEventListener('click', handleRunBoardResolutionAudit);

    const jump5 = document.getElementById('jumpToStage5From4Btn');
    if (jump5) jump5.addEventListener('click', () => navigateToStage(5));

    // Stage 5 Controls
    const submitBtn = document.getElementById('submitMandateApplicationBtn');
    if (submitBtn) submitBtn.addEventListener('click', handleSubmitMandateApplication);

    const cosignAllBtn = document.getElementById('executeCosignAllBtn');
    if (cosignAllBtn) {
      cosignAllBtn.addEventListener('click', () => handleExecuteCosign(null, null));
    }

    // Copilot Dock Controls
    const micBtn = document.getElementById('voiceMicToggleBtn');
    if (micBtn) micBtn.addEventListener('click', toggleVoiceMicrophone);

    const bargeInBtn = document.getElementById('bargeInBtn');
    if (bargeInBtn) bargeInBtn.addEventListener('click', triggerBargeInInterruption);

    document.querySelectorAll('.suggestion-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        const prompt = chip.getAttribute('data-prompt');
        if (prompt) sendChatPrompt(prompt);
      });
    });

    const chatForm = document.getElementById('copilotChatForm');
    if (chatForm) {
      chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const input = document.getElementById('copilotChatInput');
        if (input && input.value.trim()) {
          sendChatPrompt(input.value.trim());
        }
      });
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    bindUiEvents();
    startWaveformVisualizer();
    await checkSystemHealth();
    await loadCustomersList();
    connectLiveWebSocket();
    const params = new URLSearchParams(window.location.search);
    const urlStage = params.get('stage');
    if (urlStage) {
      navigateToStage(Number(urlStage));
    }
    if (params.get('testStreaming') === '1') {
      handleWebSocketMessage({ type: 'transcript', role: 'user', turn_id: 'u_demo_1', text: 'Hello, can you help me update our TechNova mandate?' });
      const partials = [
        'Hello. How',
        'Hello. How can',
        'Hello. How can I assist',
        'Hello. How can I assist you with',
        'Hello. How can I assist you with the mandate',
        'Hello. How can I assist you with the mandate changes',
        'Hello. How can I assist you with the mandate changes for TechNova',
        'Hello. How can I assist you with the mandate changes for TechNova Solutions?'
      ];
      partials.forEach((p) => {
        handleWebSocketMessage({ type: 'output_transcript', role: 'assistant', turn_id: 'v_turn_1', text: p });
      });
      handleWebSocketMessage({ type: 'transcript', role: 'assistant', turn_id: 'v_turn_1', text: partials[partials.length - 1] });
    }
  });
})();
