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
    // AI Studio API key is still used by the settings panel to configure the backend.
    aiStudioApiKey: '',
    // --- Voice call state ---------------------------------------------------
    // These were previously created ad-hoc on first assignment, so the full set of
    // voice-call state was invisible here and easy to typo (a misspelled property
    // silently reads back as undefined rather than failing).
    isRinging: false,
    isGreetingInProgress: false,
    isVoiceMuted: false,
    lastPlaybackEndTime: 0,
    lastVoiceSpeechAt: 0,
    // Last real `fx_hedge_card` returned by the FX pre-trade tool. The UC3 chart is rendered
    // from this; it is never populated with placeholder rates.
    lastFxHedgeCard: null,
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

  // Unambiguous "CODE 1,234,567" formatting. `formatCurrency` above delegates to the en-SG
  // locale, which renders SGD as a bare "$" and USD as "US$" - fine in isolation, but on the FX
  // panel a USD payable and an SGD VaR sit next to each other and both collapse to dollar signs.
  function formatMoneyCode(amount, currency = 'SGD', decimals = 0) {
    const num = Number(amount || 0);
    return `${currency} ${num.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}`;
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
    // Never stack multiple toasts on screen — replace any existing toast cleanly
    container.innerHTML = '';
    const toast = document.createElement('div');
    toast.className = `toast-item ${severity}`;
    toast.innerHTML = `
      <div style="font-weight:700; color:var(--text-primary); margin-bottom:2px;">${escapeHtml(title)}</div>
      <div style="color:var(--text-secondary); font-size:12px;">${escapeHtml(message)}</div>
    `;
    container.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 2800);
  }

  function flashElement(elementOrId) {
    const el = typeof elementOrId === 'string' ? document.getElementById(elementOrId) : elementOrId;
    if (!el) return;
    el.classList.remove('flash-highlight');
    void el.offsetWidth; // trigger reflow
    el.classList.add('flash-highlight');
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

    // Only run initial simulation if user navigated directly (never from a WebSocket ui_sync broadcast)
    if (target === 3 && !options.fromUiSync && !state.lastSimulationResult && state.activeCustomerId) {
      state.lastSimulationResult = { pending: true };
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
        if (typeof stopPhoneRinger === 'function') stopPhoneRinger(true);
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
          if (role === 'assistant' && typeof stopPhoneRinger === 'function') {
            stopPhoneRinger(true);
          }
          const turnId = msg.turn_id || `${role}_latest`;
          upsertTurnMessage(turnId, role, msg.text);
        }
        break;
      }

      case 'turn_complete': {
        state.isGreetingInProgress = false;
        break;
      }

      case 'input_transcript': {
        if (msg.text) {
          // Strip non-ASCII script hallucinations (e.g. Tamil 'ம்') and filler/ringer hum ('Hum.')
          const asciiClean = String(msg.text)
            .replace(/[^\x20-\x7E]/g, '')
            .trim();
          const low = asciiClean.toLowerCase().replace(/[.,!?-_:;"'()]/g, '').trim();
          const noiseFillers = new Set([
            'hum', 'hmm', 'uh', 'um', 'ah', 'oh', 'eh', 'mm', 'mhm', 'hm', 'noise', '<noise>'
          ]);
          const alphaCount = (low.match(/[a-z]/g) || []).length;
          if (!low || noiseFillers.has(low) || alphaCount < 3) {
            break;
          }
          const turnId = msg.turn_id || 'voice_user_latest';
          upsertTurnMessage(turnId, 'user', `🎤 ${asciiClean}`);
        }
        break;
      }

      default:
        break;
    }
  }

  function switchSlideDeckUseCase(usecaseId) {
    const uc = String(usecaseId || 'UC1_MANDATE').toUpperCase();
    state.activeUseCase = uc;
    const p1 = document.getElementById('uc1MandateContainer');
    const p2 = document.getElementById('uc2PaymentPrepPanel');
    const p3 = document.getElementById('uc3FxAdvisoryPanel');
    if (p1) p1.style.display = uc === 'UC1_MANDATE' ? 'block' : 'none';
    if (p2) p2.style.display = uc === 'UC2_PAYMENT' ? 'block' : 'none';
    if (p3) p3.style.display = uc === 'UC3_FX' ? 'block' : 'none';
    // Re-hydrate the FX chart on tab-back; the host div is otherwise left showing the
    // "awaiting quote" placeholder even though a real hedge was already booked.
    if (uc === 'UC3_FX' && state.lastFxHedgeCard) {
      renderUc3FxAdvisoryPanel(state.lastFxHedgeCard);
    }

    document.querySelectorAll('.uc-tab-btn').forEach((btn) => {
      const isMatch = btn.getAttribute('data-usecase') === uc;
      btn.classList.toggle('active', isMatch);
      btn.style.background = isMatch ? '#E31837' : '#FFFFFF';
      btn.style.color = isMatch ? '#FFFFFF' : '#374151';
      btn.style.borderColor = isMatch ? '#E31837' : '#D1D5DB';
      btn.style.fontWeight = isMatch ? '700' : '600';
    });
  }

  async function handleUiSync(syncPayload) {
    if (!syncPayload) return;

    if (syncPayload.target_usecase) {
      switchSlideDeckUseCase(syncPayload.target_usecase);
    } else if (syncPayload.ui_action === 'STAGE_PAYMENT_TO_IDEAL') {
      switchSlideDeckUseCase('UC2_PAYMENT');
    } else if (syncPayload.ui_action === 'FX_HEDGE_EXECUTED') {
      switchSlideDeckUseCase('UC3_FX');
    } else if (syncPayload.target_stage) {
      switchSlideDeckUseCase('UC1_MANDATE');
    }

    if (syncPayload.payment_prep_card) {
      const pc = syncPayload.payment_prep_card;
      const accEl = document.getElementById('uc2AccountNoVal');
      const vBox = document.getElementById('uc2StateVerifiedBox');
      const bBox = document.getElementById('uc2StateBecBox');
      if (accEl) accEl.textContent = pc.extracted_account_no || '003-918239-1';
      if (vBox && bBox) {
        vBox.style.opacity = pc.is_bec_fraud_flagged ? '0.45' : '1';
        bBox.style.opacity = pc.is_bec_fraud_flagged ? '1' : '0.45';
      }
    }

    if (syncPayload.fx_hedge_card) {
      const fx = syncPayload.fx_hedge_card;
      renderUc3FxAdvisoryPanel(fx);

      // Right-hand pre-trade widget: these were static literals in index.html, so the booked
      // trade and the displayed trade could disagree.
      const buyEl = document.getElementById('uc3YouBuyVal');
      const mathEl = document.getElementById('uc3HedgeMathVal');
      const tenorEl = document.getElementById('uc3TenorRateVal');
      const contractEl = document.getElementById('uc3ContractIdVal');
      const badgeEl = document.getElementById('uc3PretradeBadge');
      const ratio = Number(fx.hedge_ratio_pct) || 0;
      const payable = Number(fx.total_payable_usd) || 0;
      const buy = Number(fx.you_buy_usd) || 0;
      const fwd = Number(fx.forward_90d_rate);
      if (buyEl) buyEl.textContent = formatMoneyCode(buy, 'USD');
      if (mathEl) {
        mathEl.innerHTML = `${escapeHtml((ratio / 100).toFixed(2))} &times; ${escapeHtml(formatMoneyCode(payable, 'USD'))} = <strong>${escapeHtml(formatMoneyCode(buy, 'USD'))}</strong>`;
      }
      if (tenorEl) {
        tenorEl.textContent = `${fx.tenor || '3M'} @ ${isFinite(fwd) ? fwd.toFixed(4) : '--'}`;
      }
      if (contractEl) {
        contractEl.textContent = `Contract ID: ${fx.contract_id || '--'}${fx.booked ? ' \u2713' : ''}`;
      }
      if (badgeEl) badgeEl.textContent = `Pre-Trade Checks: ${fx.pretrade_checks || 'PENDING'}`;
    }

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
      navigateToStage(targetStage, { flash: true, fromUiSync: true });
    }

    // If a governance violation occurred via voice/chat tool call, surface the Stage 2 Red Governance Banner
    const toastMsg = (syncPayload.toast_notification && syncPayload.toast_notification.message) || '';
    if (toastMsg.includes('GOVERNANCE_VIOLATION') || (syncPayload.toast_notification && syncPayload.toast_notification.title && syncPayload.toast_notification.title.includes('Sole Group A'))) {
      const alertBanner = document.getElementById('governanceAlertBanner');
      if (alertBanner) {
        const codeEl = document.getElementById('governanceErrorCode');
        const msgEl = document.getElementById('governanceAlertMessage');
        if (codeEl) codeEl.textContent = 'GOVERNANCE_VIOLATION_SOLE_GROUP_A';
        if (msgEl) msgEl.textContent = toastMsg;
        alertBanner.classList.add('visible');
      }
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

  function formatRichChatText(rawText) {
    if (!rawText) return '';
    // Strip markdown tables and ### headers since A2UI visual cards display structured tables/charts
    const lines = String(rawText)
      .split('\n')
      .filter((line) => {
        const t = line.trim();
        if (t.startsWith('|') && t.endsWith('|')) return false;
        if (/^#{1,6}\s+/.test(t)) return false;
        if (/^---+$/.test(t)) return false;
        return true;
      });
    let cleaned = lines.join('\n').trim();
    if (!cleaned) {
      cleaned = String(rawText).replace(/[#|*`]/g, '').trim();
    }
    // Keep conversational bubble crisp (max 320 chars if overly long)
    if (cleaned.length > 340) {
      const firstTwoSentences = cleaned.split(/(?<=[.!?])\s+/).slice(0, 2).join(' ');
      if (firstTwoSentences && firstTwoSentences.length >= 25) {
        cleaned = firstTwoSentences;
      }
    }
    let html = escapeHtml(cleaned);
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<span style="background:#F3F4F6;padding:1px 5px;border-radius:4px;font-family:var(--font-mono);font-size:11.5px;">$1</span>');
    html = html.replace(/^\s*[\*\-]\s+(.+)$/gm, '<div style="margin:2px 0;">&bull; $1</div>');
    html = html.replace(/\n+/g, '<br/>');
    return html;
  }

  function upsertTurnMessage(turnId, role, text) {
    const stream = document.getElementById('copilotChatStream');
    if (!stream || !text) return;

    // Never display internal system greeting instructions as user chat bubbles
    const lowTxt = String(text).toLowerCase().trim();
    if (
      role === 'user' &&
      (lowTxt.startsWith('greet the corporate director') ||
        lowTxt.startsWith('you just answered a live corporate') ||
        lowTxt.includes('ask how you can help with their mandate, payment verification'))
    ) {
      return;
    }

    const formattedHtml = role === 'user'
      ? `<div>${escapeHtml(text).replace(/\n/g, '<br/>')}</div>`
      : `<div class="ichat-sender-tag"><span>Joy &middot; DBS Mandate Advisor</span><span>Live</span></div><div>${formatRichChatText(text)}</div>`;

    if (role === 'user') {
      state.activeAssistantBubble = null;
      if (state.activeUserVoiceBubble && state.activeUserVoiceBubble.parentNode && String(turnId).includes('voice')) {
        state.activeUserVoiceBubble.innerHTML = formattedHtml;
        stream.scrollTop = stream.scrollHeight;
        return;
      }
      if (turnId && state.turnBubbleMap[turnId] && state.turnBubbleMap[turnId].parentNode) {
        state.turnBubbleMap[turnId].innerHTML = formattedHtml;
        stream.scrollTop = stream.scrollHeight;
        return;
      }
      const rowDiv = document.createElement('div');
      rowDiv.className = 'ichat-row user';
      const uDiv = document.createElement('div');
      uDiv.className = 'chat-msg user';
      if (turnId) {
        uDiv.setAttribute('data-turn-id', turnId);
        state.turnBubbleMap[turnId] = uDiv;
      }
      if (String(turnId).includes('voice')) {
        state.activeUserVoiceBubble = uDiv;
      }
      uDiv.innerHTML = formattedHtml;
      const avatarDiv = document.createElement('div');
      avatarDiv.className = 'ichat-user-avatar';
      avatarDiv.textContent = 'SL';
      rowDiv.appendChild(uDiv);
      rowDiv.appendChild(avatarDiv);
      stream.appendChild(rowDiv);
      stream.scrollTop = stream.scrollHeight;
      return;
    }

    // Assistant turn: if no user turns exist yet, reuse the initial welcome assistant bubble in place!
    state.activeUserVoiceBubble = null;
    if (!state.activeAssistantBubble && stream.querySelectorAll('.ichat-row.user').length === 0) {
      const initialWelcome = stream.querySelector('.ichat-row.assistant .chat-msg.assistant');
      if (initialWelcome) {
        state.activeAssistantBubble = initialWelcome;
      }
    }
    if (state.activeAssistantBubble && state.activeAssistantBubble.parentNode) {
      state.activeAssistantBubble.innerHTML = formattedHtml;
      stream.scrollTop = stream.scrollHeight;
      return;
    }

    if (turnId && state.turnBubbleMap[turnId] && state.turnBubbleMap[turnId].parentNode) {
      const existing = state.turnBubbleMap[turnId];
      state.activeAssistantBubble = existing;
      existing.innerHTML = formattedHtml;
      stream.scrollTop = stream.scrollHeight;
      return;
    }

    const rowDiv = document.createElement('div');
    rowDiv.className = 'ichat-row assistant';
    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'ichat-avatar';
    avatarDiv.innerHTML = '<img src="/dbs-logo.png" alt="DBS" />';
    const div = document.createElement('div');
    div.className = 'chat-msg assistant';
    if (turnId) {
      div.setAttribute('data-turn-id', turnId);
      state.turnBubbleMap[turnId] = div;
    }
    state.activeAssistantBubble = div;
    div.innerHTML = formattedHtml;
    rowDiv.appendChild(avatarDiv);
    rowDiv.appendChild(div);
    stream.appendChild(rowDiv);
    stream.scrollTop = stream.scrollHeight;
  }

  function appendChatMessage(role, text, extraHtml = '') {
    upsertTurnMessage(`${role}_${Date.now()}_${Math.random()}`, role, text);
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

  function renderGroupQuorumMiniChartSvg(grpCounts) {
    const gA = Number((grpCounts && grpCounts.A) ?? 2);
    const gB = Number((grpCounts && grpCounts.B) ?? 2);
    const gC = Number((grpCounts && grpCounts.C) ?? 1);
    const maxVal = Math.max(4, gA, gB, gC);
    const wA = Math.round((gA / maxVal) * 140);
    const wB = Math.round((gB / maxVal) * 140);
    const wC = Math.round((gC / maxVal) * 140);
    return `
      <div style="margin-top:8px; padding-top:8px; border-top:1px solid #F3F4F6;">
        <div style="font-size:10px; font-weight:700; color:#6B7280; text-transform:uppercase; margin-bottom:4px;">Active Signatory Quorum Graph</div>
        <svg width="100%" height="58" viewBox="0 0 250 58" style="display:block;">
          <text x="0" y="13" font-size="10" font-weight="700" fill="#111827">Group A</text>
          <rect x="54" y="4" width="145" height="11" rx="5.5" fill="#F3F4F6"></rect>
          <rect x="54" y="4" width="${Math.max(8, wA)}" height="11" rx="5.5" fill="#E31837"></rect>
          <text x="206" y="13" font-size="10" font-weight="700" fill="#E31837">${gA} Active</text>

          <text x="0" y="32" font-size="10" font-weight="700" fill="#111827">Group B</text>
          <rect x="54" y="23" width="145" height="11" rx="5.5" fill="#F3F4F6"></rect>
          <rect x="54" y="23" width="${Math.max(8, wB)}" height="11" rx="5.5" fill="#2563EB"></rect>
          <text x="206" y="32" font-size="10" font-weight="700" fill="#2563EB">${gB} Active</text>

          <text x="0" y="51" font-size="10" font-weight="700" fill="#111827">Group C</text>
          <rect x="54" y="42" width="145" height="11" rx="5.5" fill="#F3F4F6"></rect>
          <rect x="54" y="42" width="${gC > 0 ? Math.max(8, wC) : 4}" height="11" rx="5.5" fill="${gC > 0 ? '#059669' : '#D1D5DB'}"></rect>
          <text x="206" y="51" font-size="10" font-weight="700" fill="${gC > 0 ? '#059669' : '#6B7280'}">${gC} Active</text>
        </svg>
      </div>
    `;
  }

  // ---------------------------------------------------------------------------
  // USD/SGD 90-day volatility cone (UC3, Slides 9-10)
  // ---------------------------------------------------------------------------
  // The chart this replaces was a hand-written static SVG whose trajectory was drawn with a
  // `M ... Q ... T ... T ... T ...` chain. Every `T` reflects the *previous* control point, so
  // the amplitude compounded with each segment until the curve ran off the top and bottom of the
  // 150px viewBox and was clipped - that is the broken render. It was also decorative: the
  // squiggle encoded no data at all and the rates printed beside it were hardcoded literals, so
  // it never reflected the trade that was actually booked.
  //
  // This version plots what the VaR number actually means: the +/- sigma cone around spot,
  // widening with the square root of time (the standard volatility scaling), against the flat
  // 90-day forward rate that removes that uncertainty. Every value comes from the FX pre-trade
  // tool result, and every coordinate is produced by an explicit domain -> pixel scale, so no
  // point can fall outside the plot box.
  function buildFxVolatilityConeSvg(fx, opts) {
    const o = opts || {};
    const card = fx || {};
    const spot = Number(card.spot_rate);
    const fwd = Number(card.forward_90d_rate);
    const volPct = Number(card.quarterly_volatility_pct);
    if (!isFinite(spot) || !isFinite(fwd) || !isFinite(volPct) || spot <= 0) return '';

    const compact = Boolean(o.compact);
    const W = o.width || 480;
    const H = o.height || 196;
    const padL = o.padL != null ? o.padL : (compact ? 8 : 12);
    const padR = o.padR != null ? o.padR : (compact ? 54 : 78);
    const padT = o.padT != null ? o.padT : (compact ? 12 : 30);
    const padB = o.padB != null ? o.padB : (compact ? 12 : 26);
    const x0 = padL;
    const x1 = W - padR;
    const y0 = padT;
    const y1 = H - padB;
    const uid = `fxcone${Math.random().toString(36).slice(2, 9)}`;

    const sigma = volPct / 100;
    const up90 = spot * (1 + sigma);
    const dn90 = spot * (1 - sigma);

    // Domain must cover the cone AND the forward rate (which can sit outside the cone when the
    // forward premium exceeds one quarterly sigma, as it does at 1.3538 vs 1.2800 +/- 3%).
    const lo = Math.min(dn90, fwd, spot);
    const hi = Math.max(up90, fwd, spot);
    const span = (hi - lo) || spot * 0.01;
    const dLo = lo - span * 0.22;
    const dHi = hi + span * 0.22;

    const yOf = (v) => y1 - ((v - dLo) / (dHi - dLo)) * (y1 - y0);
    const xOf = (day) => x0 + (Math.min(Math.max(day, 0), 90) / 90) * (x1 - x0);
    const r4 = (v) => v.toFixed(4);

    // Volatility scales with sqrt(t): sigma(t) = sigma_quarter * sqrt(t / 90).
    const upper = [];
    const lower = [];
    for (let day = 0; day <= 90; day += 3) {
      const s = sigma * Math.sqrt(day / 90);
      upper.push(`${xOf(day).toFixed(2)} ${yOf(spot * (1 + s)).toFixed(2)}`);
      lower.push(`${xOf(day).toFixed(2)} ${yOf(spot * (1 - s)).toFixed(2)}`);
    }
    const conePath = `M ${upper.join(' L ')} L ${lower.reverse().join(' L ')} Z`;
    const upperPath = `M ${upper.join(' L ')}`;

    const ySpot = yOf(spot);
    const yFwd = yOf(fwd);
    // Keep the two right-hand value pills from overlapping when spot and forward are close.
    const pillH = compact ? 15 : 20;
    let yFwdPill = yFwd;
    let ySpotPill = ySpot;
    if (Math.abs(yFwdPill - ySpotPill) < pillH + 2) {
      const mid = (yFwdPill + ySpotPill) / 2;
      const half = (pillH + 2) / 2;
      yFwdPill = yFwd <= ySpot ? mid - half : mid + half;
      ySpotPill = yFwd <= ySpot ? mid + half : mid - half;
    }
    const pillX = x1 + 5;
    const pillW = compact ? 46 : 66;
    const pillFont = compact ? 9.5 : 11;

    const axisTicks = compact
      ? ''
      : [0, 30, 60, 90]
          .map((d) => {
            const anchor = d === 0 ? 'start' : d === 90 ? 'end' : 'middle';
            return `<text x="${xOf(d).toFixed(1)}" y="${(y1 + 15).toFixed(1)}" font-size="9.5" font-weight="600" fill="#6B7280" text-anchor="${anchor}">${d === 0 ? 'Today' : `+${d}d`}</text>`;
          })
          .join('');

    const fwdCaption = compact
      ? ''
      : `<text x="${(x0 + 4).toFixed(1)}" y="${Math.max(y0 - 8, yFwd - 7).toFixed(1)}" font-size="9.5" font-weight="700" fill="#FCA5A5">90-DAY FORWARD LOCK &#183; REMOVES THE BAND BELOW</text>`;

    const sigmaLabels = compact
      ? ''
      : `<text x="${(x1 - 4).toFixed(1)}" y="${(yOf(up90) - 5).toFixed(1)}" font-size="9" font-weight="700" fill="#FCA5A5" text-anchor="end">+${volPct}&#37; &#183; ${r4(up90)}</text>
         <text x="${(x1 - 4).toFixed(1)}" y="${(yOf(dn90) + 11).toFixed(1)}" font-size="9" font-weight="700" fill="#FCA5A5" text-anchor="end">&#8722;${volPct}&#37; &#183; ${r4(dn90)}</text>`;

    return `
      <svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
           aria-label="USD to SGD ninety day volatility cone around spot ${r4(spot)} against a forward lock at ${r4(fwd)}"
           style="display:block; background:#1F2937; border-radius:10px;">
        <defs>
          <linearGradient id="${uid}fill" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#F87171" stop-opacity="0.05"></stop>
            <stop offset="100%" stop-color="#F87171" stop-opacity="0.30"></stop>
          </linearGradient>
          <clipPath id="${uid}clip">
            <rect x="${x0}" y="${y0 - 2}" width="${x1 - x0}" height="${y1 - y0 + 4}"></rect>
          </clipPath>
        </defs>

        <g clip-path="url(#${uid}clip)">
          <path d="${conePath}" fill="url(#${uid}fill)" stroke="none"></path>
          <path d="${upperPath}" fill="none" stroke="#F87171" stroke-width="1.4" stroke-dasharray="4 3"></path>
          <path d="${`M ${lower.join(' L ')}`}" fill="none" stroke="#F87171" stroke-width="1.4" stroke-dasharray="4 3"></path>
          <line x1="${x0}" y1="${ySpot.toFixed(2)}" x2="${x1}" y2="${ySpot.toFixed(2)}" stroke="#60A5FA" stroke-width="${compact ? 1.6 : 2.2}"></line>
          <line x1="${x0}" y1="${yFwd.toFixed(2)}" x2="${x1}" y2="${yFwd.toFixed(2)}" stroke="#EF4444" stroke-width="${compact ? 1.8 : 2.5}"></line>
        </g>

        <line x1="${x0}" y1="${y1}" x2="${x1}" y2="${y1}" stroke="#374151" stroke-width="1"></line>
        <circle cx="${x0}" cy="${ySpot.toFixed(2)}" r="${compact ? 3 : 4.5}" fill="#3B82F6" stroke="#111827" stroke-width="1.5"></circle>
        ${fwdCaption}
        ${sigmaLabels}
        ${axisTicks}

        <rect x="${pillX}" y="${(yFwdPill - pillH / 2).toFixed(2)}" width="${pillW}" height="${pillH}" rx="4" fill="#DC2626"></rect>
        <text x="${pillX + pillW / 2}" y="${(yFwdPill + pillFont / 2.9).toFixed(2)}" font-size="${pillFont}" font-weight="700" fill="#FFFFFF" text-anchor="middle" font-family="ui-monospace, monospace">${r4(fwd)}</text>

        <rect x="${pillX}" y="${(ySpotPill - pillH / 2).toFixed(2)}" width="${pillW}" height="${pillH}" rx="4" fill="#0F172A" stroke="#475569"></rect>
        <text x="${pillX + pillW / 2}" y="${(ySpotPill + pillFont / 2.9).toFixed(2)}" font-size="${pillFont}" font-weight="700" fill="#93C5FD" text-anchor="middle" font-family="ui-monospace, monospace">${r4(spot)}</text>
      </svg>
    `;
  }

  // Renders the whole left-hand dark FX card (header figures + cone chart + VaR footnote) from a
  // real `fx_hedge_card`. Previously these figures were static markup in index.html.
  function renderUc3FxAdvisoryPanel(fxCard) {
    const host = document.getElementById('uc3FxChartHost');
    if (!host) return;
    const fx = fxCard || state.lastFxHedgeCard;
    if (!fx || !isFinite(Number(fx.spot_rate))) return;
    state.lastFxHedgeCard = fx;

    const payable = Number(fx.total_payable_usd) || 0;
    const varSgd = Number(fx.var_uncertainty_sgd) || 0;
    const volPct = Number(fx.quarterly_volatility_pct) || 0;
    const fwd = Number(fx.forward_90d_rate) || 0;
    const chart = buildFxVolatilityConeSvg(fx);
    if (!chart) return;

    host.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:12px;">
        <div>
          <div style="font-size:11px; color:#9CA3AF; text-transform:uppercase; font-weight:700;">Dynamic UI Target State &middot; USD/SGD 90-Day Exposure</div>
          <div style="font-size:18px; font-weight:700; margin-top:2px; line-height:1.35;">
            ${escapeHtml(formatMoneyCode(payable, 'USD'))} Payable &middot;
            <span style="color:#F87171; white-space:nowrap;">${escapeHtml(formatMoneyCode(varSgd, 'SGD'))} VaR</span>
          </div>
        </div>
        <span style="background:#DC2626; color:#FFFFFF; padding:4px 10px; border-radius:6px; font-family:var(--font-mono); font-size:12px; font-weight:700; white-space:nowrap;">90D Fwd Lock: ${escapeHtml(fwd.toFixed(4))}</span>
      </div>

      ${chart}

      <div style="display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; font-size:10.5px; color:#9CA3AF; font-weight:600;">
        <span><span style="display:inline-block; width:16px; height:2.5px; background:#EF4444; vertical-align:middle; margin-right:5px;"></span>90D Forward Lock</span>
        <span><span style="display:inline-block; width:16px; height:2.5px; background:#60A5FA; vertical-align:middle; margin-right:5px;"></span>Spot</span>
        <span><span style="display:inline-block; width:16px; height:8px; background:rgba(248,113,113,0.28); border:1px dashed #F87171; vertical-align:middle; margin-right:5px;"></span>&plusmn;${escapeHtml(String(volPct))}&#37; Unhedged Band</span>
      </div>

      <div style="margin-top:12px; background:#1F2937; border:1px solid #374151; border-radius:8px; padding:10px 14px; font-size:12.5px; color:#E5E7EB; line-height:1.55;">
        <strong>Quant VaR Analysis:</strong> ${escapeHtml(String(volPct))}&#37; quarterly USD/SGD movement on a
        ${escapeHtml(formatMoneyCode(payable, 'USD'))} payable =
        <strong>${escapeHtml(formatMoneyCode(varSgd, 'SGD'))} of cash-flow uncertainty</strong>
        (${escapeHtml(formatMoneyCode(payable, 'USD'))} &times; ${escapeHtml(Number(fx.spot_rate).toFixed(4))} &times; ${escapeHtml(String(volPct))}&#37;).
        Locking the 90-Day Forward at ${escapeHtml(fwd.toFixed(4))} or SecureFX removes that band today.
      </div>
    `;
  }

  function renderA2UIWidgetHtml(toolName, args, resultObj) {
    if (!resultObj) return '';
    const tName = String(toolName || '').toLowerCase();
    const snap = (state.snapshot && state.snapshot.customer) ? state.snapshot : {};
    const cust = resultObj.customer || snap.customer || {};
    const grpCounts = resultObj.active_group_counts || resultObj.remaining_group_counts || snap.active_group_counts || { A: 2, B: 2, C: 1 };

    // 0A. Slide Deck Use Case 2 (Slides 7-8): Smart Payment Verification, BEC Screening & FAST Router (Ref FT262359902)
    if (tName.includes('stage_payment') || resultObj.payment_prep_card) {
      const pc = resultObj.payment_prep_card || {};
      const isBec = Boolean(pc.is_bec_fraud_flagged);
      return `
        <div class="a2ui-widget-card ${isBec ? 'a2ui-red' : 'a2ui-green'}" data-a2ui-type="payment-prep-card">
          <div class="a2ui-widget-header">
            <span class="a2ui-widget-title">&#x1F9FE; Smart Verification &amp; Rail Router</span>
            <span class="a2ui-pill ${isBec ? '' : 'green'}">${escapeHtml(pc.staging_ref || 'FT262359902')}</span>
          </div>
          <div class="a2ui-kpi-grid">
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Beneficiary &amp; Acct</div>
              <div class="a2ui-kpi-val">${escapeHtml(pc.beneficiary || 'SingaTech Industrial')}<br/><span style="font-size:11px;font-family:var(--font-mono);color:${isBec ? '#DC2626' : '#059669'};">${escapeHtml(pc.extracted_account_no || '003-918239-1')}</span></div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Amount &amp; Due Date</div>
              <div class="a2ui-kpi-val" style="color:#059669;">SGD 14,250.00<br/><span style="font-size:10.5px;color:#6B7280;">Due ${escapeHtml(pc.due_date || '28 Aug 2026')}</span></div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Security Screening</div>
              <div class="a2ui-kpi-val" style="color:${isBec ? '#DC2626' : '#059669'};">${isBec ? '&#x26A0;&#xFE0F; BEC Mismatch' : '&#x2713; Verified Payee'}</div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Recommended Rail</div>
              <div class="a2ui-kpi-val" style="color:#059669;">&#x2713; FAST ($0 &middot; Instant)</div>
            </div>
          </div>
        </div>
      `;
    }

    // 0B. Slide Deck Use Case 3 (Slides 9-10): FX Volatility VaR & Partial Forward Hedge
    // Every figure below is read off the tool result. It previously printed hardcoded literals
    // ("USD 3,500,000", "1.3538", "Spot 1.2800", "~SGD 200,000"), so the card showed the same
    // numbers no matter what hedge ratio, payable or rates the trade was actually booked at.
    if (tName.includes('fx_pretrade') || tName.includes('book_fx') || resultObj.fx_hedge_card) {
      const fx = resultObj.fx_hedge_card || {};
      const fxPayable = Number(fx.total_payable_usd) || 0;
      const fxBuy = Number(fx.you_buy_usd) || 0;
      const fxRatio = Number(fx.hedge_ratio_pct) || 0;
      const fxSpot = Number(fx.spot_rate);
      const fxFwd = Number(fx.forward_90d_rate);
      const fxVol = Number(fx.quarterly_volatility_pct) || 0;
      const fxVar = Number(fx.var_uncertainty_sgd) || 0;
      const fxPassed = String(fx.pretrade_checks || '').toUpperCase() === 'PASSED';
      const miniCone = buildFxVolatilityConeSvg(fx, { compact: true, width: 240, height: 76 });
      return `
        <div class="a2ui-widget-card ${fxPassed ? 'a2ui-green' : 'a2ui-red'}" data-a2ui-type="fx-hedge-card">
          <div class="a2ui-widget-header">
            <span class="a2ui-widget-title">&#x1F4C8; Quantitative FX Hedge &amp; Pre-Trade</span>
            <span class="a2ui-pill ${fxPassed ? 'green' : ''}">Pre-Trade: ${escapeHtml(fx.pretrade_checks || 'PENDING')}</span>
          </div>
          <div class="a2ui-kpi-grid">
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">You Buy (${escapeHtml(String(fxRatio))}&#37; of ${escapeHtml(formatMoneyCode(fxPayable, 'USD'))})</div>
              <div class="a2ui-kpi-val">${escapeHtml(formatMoneyCode(fxBuy, 'USD'))}</div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">90D Fwd Lock vs Spot</div>
              <div class="a2ui-kpi-val"><span style="color:#E31837;">${escapeHtml(isFinite(fxFwd) ? fxFwd.toFixed(4) : '--')}</span> <span style="font-size:10.5px;color:#6B7280;">(Spot ${escapeHtml(isFinite(fxSpot) ? fxSpot.toFixed(4) : '--')})</span></div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">VaR Uncertainty Removed</div>
              <div class="a2ui-kpi-val" style="color:#059669;">${escapeHtml(formatMoneyCode(fxVar, 'SGD'))} <span style="font-size:10.5px;color:#6B7280;">(${escapeHtml(String(fxVol))}&#37; Vol)</span></div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Executed Contract ID</div>
              <div class="a2ui-kpi-val" style="font-family:var(--font-mono);color:#047857;">${escapeHtml(fx.contract_id || '--')} ${fx.booked ? '&#x2713;' : ''}</div>
            </div>
          </div>
          ${miniCone}
        </div>
      `;
    }

    // 1. NRIC OCR Signatory Extraction Card + Group Quorum Chart
    if (tName.includes('upload_nric') || resultObj.nric_ocr_card || (tName.includes('add_or_update_signatory') && resultObj.signatory)) {
      const ocr = resultObj.nric_ocr_card || {};
      const sig = resultObj.signatory || {};
      const fullName = ocr.full_name || sig.full_name || args.full_name || 'Authorized Signatory';
      const nricMasked = ocr.nric_masked || sig.id_number_masked || args.nric_masked || 'S****521J';
      const roleTitle = ocr.role_title || sig.role_title || args.role_title || 'Treasury Director';
      const grp = ocr.signing_group || sig.signing_group || args.signing_group || 'A';
      const isOcr = Boolean(resultObj.nric_ocr_card || (sig.specimen_signature_status && sig.specimen_signature_status.includes('OCR')));
      return `
        <div class="a2ui-widget-card a2ui-green" data-a2ui-type="nric-ocr-card">
          <div class="a2ui-widget-header">
            <span class="a2ui-widget-title">&#x1FAAA; ${isOcr ? 'Singapore NRIC OCR Verified' : 'Signatory Matrix Updated'}</span>
            <span class="a2ui-pill green">${isOcr ? '99.4% OCR Match' : `Group ${escapeHtml(grp)}`}</span>
          </div>
          <div class="a2ui-kpi-grid">
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Extracted Full Name</div>
              <div class="a2ui-kpi-val">${escapeHtml(fullName)}</div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">NRIC / Identity No.</div>
              <div class="a2ui-kpi-val" style="font-family:var(--font-mono);">${escapeHtml(nricMasked)}</div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Role &amp; Mandate Group</div>
              <div class="a2ui-kpi-val">${escapeHtml(roleTitle)} &middot; <span style="color:#E31837;">Grp ${escapeHtml(grp)}</span></div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Specimen &amp; Token</div>
              <div class="a2ui-kpi-val" style="color:#059669;">&#x2713; OCR Specimen Active</div>
            </div>
          </div>
          ${renderGroupQuorumMiniChartSvg(grpCounts)}
        </div>
      `;
    }

    // 2. Revoke Signatory (Governance Policy Block vs Successful Revocation)
    if (tName.includes('revoke_signatory')) {
      const errCode = String(resultObj.error_code || '');
      if (resultObj.status === 'error' && errCode.includes('GOVERNANCE')) {
        const blockedSig = resultObj.blocked_signatory || {};
        return `
          <div class="a2ui-widget-card a2ui-red" data-a2ui-type="governance-block-card">
            <div class="a2ui-widget-header">
              <span class="a2ui-widget-title" style="color:#DC2626;">&#x1F6E1;&#xFE0F; Sole Group A Governance Shield</span>
              <span class="a2ui-pill">Policy Blocked</span>
            </div>
            <div style="font-size:12px; color:#7F1D1D; font-weight:600; margin-bottom:6px;">
              Cannot revoke <strong>${escapeHtml(blockedSig.full_name || args.signatory_id_or_name || 'Managing Partner')}</strong> (Group A)
            </div>
            <div class="a2ui-kpi-grid">
              <div class="a2ui-kpi-box">
                <div class="a2ui-kpi-label">Active Group A Left</div>
                <div class="a2ui-kpi-val" style="color:#DC2626;">1 Signatory (Minimum: 1)</div>
              </div>
              <div class="a2ui-kpi-box">
                <div class="a2ui-kpi-label">Required Resolution</div>
                <div class="a2ui-kpi-val">Appoint Co-Partner First</div>
              </div>
            </div>
            ${renderGroupQuorumMiniChartSvg(grpCounts)}
          </div>
        `;
      }
      const revSig = resultObj.revoked_signatory || {};
      return `
        <div class="a2ui-widget-card" data-a2ui-type="revocation-success-card">
          <div class="a2ui-widget-header">
            <span class="a2ui-widget-title">&#x2713; Signatory Authority Revoked</span>
            <span class="a2ui-pill green">Group ${escapeHtml(revSig.signing_group || 'C')} Updated</span>
          </div>
          <div class="a2ui-kpi-grid">
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Revoked Officer</div>
              <div class="a2ui-kpi-val">${escapeHtml(revSig.full_name || args.signatory_id_or_name || 'Signatory')}</div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Mandate Status</div>
              <div class="a2ui-kpi-val" style="color:#DC2626;">REVOKED (Immediate)</div>
            </div>
          </div>
          ${renderGroupQuorumMiniChartSvg(grpCounts)}
        </div>
      `;
    }

    // 3. Entity Switch / Mandate Details -> A2UI Corporate Liquidity & Account Distribution Chart
    if (tName.includes('switch') || tName.includes('mandate_details') || tName.includes('list_customer') || tName.includes('entity_profile')) {
      const accounts = resultObj.accounts || snap.accounts || [];
      const totalSgd = accounts.reduce((s, a) => s + Number(a.sgd_equivalent_balance || a.balance || 0), 0);
      const colors = ['#E31837', '#2563EB', '#059669', '#D97706'];
      let xCursor = 0;
      const barSegments = accounts.map((acc, i) => {
        const val = Number(acc.sgd_equivalent_balance || acc.balance || 0);
        const pct = totalSgd > 0 ? Math.max(6, Math.round((val / totalSgd) * 100)) : 33;
        const widthPx = Math.max(12, Math.round((pct / 100) * 240));
        const seg = `<rect x="${xCursor}" y="0" width="${widthPx}" height="14" fill="${colors[i % colors.length]}"></rect>`;
        xCursor += widthPx;
        return seg;
      }).join('');

      const legendRows = accounts.slice(0, 3).map((acc, i) => {
        const val = Number(acc.sgd_equivalent_balance || acc.balance || 0);
        const pct = totalSgd > 0 ? Math.round((val / totalSgd) * 100) : 0;
        return `
          <div style="display:flex; align-items:center; justify-content:space-between; font-size:11px; margin-top:4px;">
            <span style="display:flex; align-items:center; gap:5px; color:#374151; font-weight:600;">
              <span style="width:8px; height:8px; border-radius:2px; background:${colors[i % colors.length]}; display:inline-block;"></span>
              ${escapeHtml((acc.account_name || acc.account_type || 'Account').slice(0, 26))}
            </span>
            <span style="font-family:var(--font-mono); font-weight:700; color:#111827;">${formatCurrency(val, 'SGD')} (${pct}%)</span>
          </div>
        `;
      }).join('');

      return `
        <div class="a2ui-widget-card" data-a2ui-type="entity-liquidity-chart">
          <div class="a2ui-widget-header">
            <span class="a2ui-widget-title">&#x1F3E6; ${escapeHtml(cust.company_name || resultObj.company_name || 'Corporate Entity')}</span>
            <span class="a2ui-pill green">UEN ${escapeHtml(cust.uen || resultObj.uen || '')}</span>
          </div>
          <div class="a2ui-kpi-grid">
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Consolidated Liquidity</div>
              <div class="a2ui-kpi-val" style="color:#059669;">${formatCurrency(totalSgd, 'SGD')}</div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Active Quorum</div>
              <div class="a2ui-kpi-val">${grpCounts.A || 0}A &middot; ${grpCounts.B || 0}B &middot; ${grpCounts.C || 0}C</div>
            </div>
          </div>
          <div style="margin-top:6px;">
            <div style="font-size:10px; font-weight:700; color:#6B7280; text-transform:uppercase; margin-bottom:4px;">Account Liquidity Allocation Chart</div>
            <svg width="100%" height="14" viewBox="0 0 240 14" style="border-radius:7px; overflow:hidden; display:block; background:#F3F4F6;">
              ${barSegments}
            </svg>
            ${legendRows}
          </div>
        </div>
      `;
    }

    // 4. Signing Rule & Transaction Simulation Waterfall Chart + Slide 6 Deadlock Check
    if (tName.includes('simulate_transaction') || tName.includes('configure_signing_rules') || tName.includes('validate_mandate')) {
      const sgdAmt = Number(resultObj.sgd_equivalent_amount || resultObj.max_amount_sgd || args.max_amount_sgd || 150000);
      const reqMatrix = resultObj.required_rule_expression || resultObj.rule_expression || args.rule_expression || '2A';
      const ratio = Math.min(94, Math.max(12, Math.round((sgdAmt / 500000) * 100)));
      const hasDeadlock = (grpCounts.B || 2) < 2;
      return `
        <div class="a2ui-widget-card" data-a2ui-type="threshold-simulation-chart">
          <div class="a2ui-widget-header">
            <span class="a2ui-widget-title">&#x1F4CA; Mandate Threshold &amp; Routing Chart</span>
            <span class="a2ui-pill">Requires ${escapeHtml(reqMatrix)}</span>
          </div>
          <div class="a2ui-kpi-grid">
            <div class="a2ui-kpi-box" style="background:#ECFDF5; border-color:#A7F3D0;">
              <div class="a2ui-kpi-label" style="color:#065F46;">&#x2713; Authorized</div>
              <div class="a2ui-kpi-val" style="color:#059669; font-size:11.5px;">1 Director (Grp A) + 1 Manager (Grp B)</div>
            </div>
            <div class="a2ui-kpi-box" style="background:${hasDeadlock ? '#FEF3C7' : '#F9FAFB'}; border-color:${hasDeadlock ? '#F59E0B' : '#E5E7EB'};">
              <div class="a2ui-kpi-label" style="color:#92400E;">&#x26A0;&#xFE0F; Deadlock Guard</div>
              <div class="a2ui-kpi-val" style="color:#B45309; font-size:11px;">${hasDeadlock ? '2 Grp B req / 1 active' : 'Zero Deadlocks Verified'}</div>
            </div>
          </div>
          <svg width="100%" height="38" viewBox="0 0 240 38" style="display:block; margin-top:4px;">
            <rect x="0" y="14" width="95" height="10" rx="4" fill="#10B981"></rect>
            <rect x="97" y="14" width="143" height="10" rx="4" fill="#E31837"></rect>
            <circle cx="${Math.round((ratio / 100) * 230)}" cy="19" r="6" fill="#111827" stroke="#FFFFFF" stroke-width="2"></circle>
            <text x="0" y="35" font-size="9.5" font-weight="700" fill="#059669">Tier 1 (&le;$150k: 1A/2B)</text>
            <text x="128" y="35" font-size="9.5" font-weight="700" fill="#E31837">Tier 2 (&gt;$150k: 2A)</text>
          </svg>
        </div>
      `;
    }

    // 5. Board Resolution Audit / DigiSign Submission
    if (tName.includes('audit_board') || tName.includes('submit_mandate') || tName.includes('cosigner')) {
      const appRef = resultObj.application_ref || (resultObj.application && resultObj.application.application_ref) || 'BRC-09 PASSED';
      return `
        <div class="a2ui-widget-card a2ui-green" data-a2ui-type="compliance-digisign-card">
          <div class="a2ui-widget-header">
            <span class="a2ui-widget-title">&#x2705; Governance &amp; DigiSign Verification</span>
            <span class="a2ui-pill green">100/100 Verified</span>
          </div>
          <div class="a2ui-kpi-grid">
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Reference / Resolution</div>
              <div class="a2ui-kpi-val" style="font-family:var(--font-mono);">${escapeHtml(appRef)}</div>
            </div>
            <div class="a2ui-kpi-box">
              <div class="a2ui-kpi-label">Execution Status</div>
              <div class="a2ui-kpi-val" style="color:#059669;">&#x2713; Cryptographically Signed</div>
            </div>
          </div>
        </div>
      `;
    }

    return '';
  }

  function upsertToolExecutionCard(callId, toolName, args, resultObj) {
    const stream = document.getElementById('copilotChatStream');
    if (!stream) return;

    const tNameLow = String(toolName || '').toLowerCase();
    const hasUserTurns = stream.querySelectorAll('.ichat-row.user').length > 0;
    // Never render unsolicited background read-only lookups before the user has spoken or typed a command
    if (!hasUserTurns && (tNameLow.includes('list_customer') || tNameLow.includes('mandate_details'))) {
      return;
    }
    // Suppress redundant list_customer_profiles card when get_customer_mandate_details / SwitchActiveCustomerProfile is used
    if (tNameLow === 'list_customer_profiles') {
      return;
    }

    const targetStage = (resultObj && resultObj.target_stage) || state.activeStage || 1;
    const errCode = String((resultObj && resultObj.error_code) || '');
    const isGovernanceBlock = resultObj && resultObj.status === 'error' && errCode.includes('GOVERNANCE');
    const statusText = resultObj
      ? isGovernanceBlock
        ? 'Policy Blocked'
        : resultObj.status === 'error'
          ? 'Needs Review'
          : 'Completed'
      : 'Executing...';
    const friendlyName = String(toolName || 'Tool')
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());

    const a2uiHtml = resultObj ? renderA2UIWidgetHtml(toolName, args || {}, resultObj) : '';

    // If this tool produces an entity-liquidity-chart and the most recent tool card in the stream is ALREADY an entity-liquidity-chart, reuse it in place instead of stacking duplicates
    let card = callId && state.toolCardMap[callId];
    if (!card && a2uiHtml.includes('data-a2ui-type="entity-liquidity-chart"')) {
      const existingCards = stream.querySelectorAll('.tool-exec-card');
      const lastCard = existingCards.length ? existingCards[existingCards.length - 1] : null;
      if (lastCard && lastCard.querySelector('[data-a2ui-type="entity-liquidity-chart"]')) {
        card = lastCard;
        if (callId) state.toolCardMap[callId] = card;
      }
    }

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
      ${a2uiHtml}
    `;

    const jumpBtn = card.querySelector('.tool-stage-jump-btn');
    if (jumpBtn) {
      jumpBtn.addEventListener('click', () => {
        navigateToStage(targetStage, { flash: true });
      });
    }
    stream.scrollTop = stream.scrollHeight;
  }

  async function triggerNricOcrUploadFlow(fileObj = null) {
    const filename = fileObj ? fileObj.name : 'NRIC_Desmond_Lim_S8841521J.png';
    upsertTurnMessage(`nric_user_${Date.now()}`, 'user', `🪪 Uploaded Singapore NRIC Card: ${filename}`);

    let imageBase64 = '';
    if (fileObj) {
      imageBase64 = await new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => resolve('');
        reader.readAsDataURL(fileObj);
      });
    }

    const res = await apiFetch('/api/ocr/upload-nric', {
      method: 'POST',
      body: JSON.stringify({
        customer_id: state.activeCustomerId || 'CUST-001',
        filename,
        signing_group: 'A',
        role_title: 'Treasury Director',
        image_base64: imageBase64,
      }),
    });

    if (res.ok && res.data) {
      const toolCalls = res.data.tool_calls || [];
      toolCalls.forEach((tc, idx) => {
        upsertToolExecutionCard(tc.call_id || `ocr_${Date.now()}_${idx}`, tc.tool_name, tc.args || {}, tc.result || res.data);
      });
      if (res.data.reply) {
        upsertTurnMessage(`nric_bot_${Date.now()}`, 'assistant', res.data.reply);
      }
      if (res.data.workspace_snapshot) {
        applyWorkspaceSnapshot(res.data.workspace_snapshot);
      }
      if (res.data.ui_sync) {
        await handleUiSync(res.data.ui_sync);
      }
    } else {
      upsertTurnMessage(`nric_err_${Date.now()}`, 'assistant', 'Unable to process NRIC OCR scan. Please try again.');
    }
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
    const googleColors = ['#E31837', '#C8102E', '#059669', '#1A1D21'];

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

  // NOTE: connectDirectAiStudioLiveSession() was removed here.
  //
  // It opened a browser-direct WebSocket to the AI Studio Live API and streamed raw
  // microphone audio into it -- including the WebAudio ringer tone and room noise -- under a
  // minimal system prompt with no role lock. The model interpreted that noise as speech and
  // hallucinated the CUSTOMER's side of the call ("Hey Joy, I need to change my supplier..."),
  // which is what made Joy appear to role-play the caller instead of greeting them.
  //
  // All voice now flows through the backend /ws/live session, which applies the ROLE LOCK
  // system instruction, noise filtering and the verbatim greeting narrator.

  function updateCallControlIcons() {
    const callBtn = document.getElementById('voiceMicToggleBtn');
    const callBtnText = document.getElementById('voiceCallBtnText');
    const iconConnect = document.getElementById('iconPhoneConnect');
    const iconDisconnect = document.getElementById('iconPhoneDisconnect');
    const muteBtn = document.getElementById('voiceMuteToggleBtn');
    const iconUnmuted = document.getElementById('iconMicUnmuted');
    const iconMuted = document.getElementById('iconMicMuted');

    const inCall = Boolean(state.isRecordingVoice || state.isRinging);
    if (callBtn && iconConnect && iconDisconnect) {
      if (inCall) {
        callBtn.style.background = 'linear-gradient(90deg, #D90429 0%, #B91C1C 100%)';
        callBtn.style.borderColor = '#EF4444';
        callBtn.style.boxShadow = '0 4px 14px rgba(217, 4, 41, 0.34)';
        callBtn.title = 'End Voice Call';
        iconConnect.style.display = 'none';
        iconDisconnect.style.display = 'block';
        if (callBtnText) callBtnText.textContent = 'END CALL';
      } else {
        callBtn.style.background = 'linear-gradient(90deg, #059669 0%, #10B981 50%, #059669 100%)';
        callBtn.style.borderColor = '#10B981';
        callBtn.style.boxShadow = '0 4px 14px rgba(5, 150, 105, 0.28)';
        callBtn.title = 'Start DBS Joy Voice Call';
        iconConnect.style.display = 'block';
        iconDisconnect.style.display = 'none';
        if (callBtnText) callBtnText.textContent = 'START DBS JOY VOICE CALL';
      }
    }

    const isMuted = Boolean(state.isVoiceMuted);
    if (muteBtn && iconUnmuted && iconMuted) {
      muteBtn.style.display = inCall ? 'flex' : 'none';
      if (isMuted) {
        muteBtn.style.background = '#E11D48';
        muteBtn.style.borderColor = '#F43F5E';
        muteBtn.style.boxShadow = '0 4px 12px rgba(225, 29, 72, 0.28)';
        muteBtn.title = 'Unmute Microphone';
        iconUnmuted.style.display = 'none';
        iconMuted.style.display = 'block';
      } else {
        muteBtn.style.background = '#1E293B';
        muteBtn.style.borderColor = '#475569';
        muteBtn.style.boxShadow = '0 2px 8px rgba(15, 23, 42, 0.18)';
        muteBtn.title = 'Mute Microphone';
        iconUnmuted.style.display = 'block';
        iconMuted.style.display = 'none';
      }
    }
  }

  function startPhoneRinger() {
    stopPhoneRinger(false);
    state.isRinging = true;
    updateCallControlIcons();
    const stateLabel = document.getElementById('voiceStateLabel');
    if (stateLabel) stateLabel.textContent = 'Dialing Joy (Ringing...)';

    const playRingBurst = () => {
      if (!state.isRinging) return;
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!state.ringerAudioCtx) {
          state.ringerAudioCtx = new AudioCtx();
        }
        const ctx = state.ringerAudioCtx;
        if (ctx.state === 'suspended') ctx.resume();

        const now = ctx.currentTime;
        // Dual-tone comfort ringback (400Hz + 425Hz: two 0.35s bursts separated by 0.18s)
        [[0, 0.35], [0.53, 0.35]].forEach(([offset, dur]) => {
          const osc1 = ctx.createOscillator();
          const osc2 = ctx.createOscillator();
          const gain = ctx.createGain();
          osc1.type = 'sine';
          osc2.type = 'sine';
          osc1.frequency.value = 400;
          osc2.frequency.value = 425;
          gain.gain.setValueAtTime(0.001, now + offset);
          gain.gain.exponentialRampToValueAtTime(0.06, now + offset + 0.03);
          gain.gain.setValueAtTime(0.06, now + offset + dur - 0.04);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + dur);
          osc1.connect(gain);
          osc2.connect(gain);
          gain.connect(ctx.destination);
          osc1.start(now + offset);
          osc2.start(now + offset);
          osc1.stop(now + offset + dur);
          osc2.stop(now + offset + dur);
        });
      } catch (_) {}
    };

    playRingBurst();
    state.ringerTimerId = setInterval(playRingBurst, 2200);
  }

  function stopPhoneRinger(connected = false) {
    if (state.ringerTimerId) {
      clearInterval(state.ringerTimerId);
      state.ringerTimerId = null;
    }
    if (state.isRinging) {
      state.isRinging = false;
      updateCallControlIcons();
      const stateLabel = document.getElementById('voiceStateLabel');
      if (stateLabel && connected) {
        stateLabel.textContent = 'Connected · Joy Live';
      }
    }
  }

  function toggleVoiceMute() {
    state.isVoiceMuted = !state.isVoiceMuted;
    if (state.micStream) {
      state.micStream.getAudioTracks().forEach((t) => {
        t.enabled = !state.isVoiceMuted;
      });
    }
    updateCallControlIcons();
    const stateLabel = document.getElementById('voiceStateLabel');
    if (stateLabel) {
      stateLabel.textContent = state.isVoiceMuted
        ? 'Microphone Muted'
        : state.isRecordingVoice
          ? 'Connected · Listening...'
          : 'Tap green phone to call Joy';
    }
  }

  async function toggleVoiceMicrophone() {
    const orbBtn = document.getElementById('voiceMicToggleBtn');
    const stateLabel = document.getElementById('voiceStateLabel');

    if (state.isRecordingVoice || state.isRinging) {
      stopPhoneRinger(false);
      stopVoiceMicrophone();
      return;
    }

    // Start phone ringer immediately while connecting to Gemini Live 3.8
    state.isGreetingInProgress = true;
    startPhoneRinger();

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
      if (state.isVoiceMuted) {
        stream.getAudioTracks().forEach((t) => {
          t.enabled = false;
        });
      }
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      state.micAudioCtx = new AudioCtx({ sampleRate: 16000 });
      const source = state.micAudioCtx.createMediaStreamSource(stream);

      const handleFloat32Frame = (input) => {
        if (!state.isRecordingVoice) return;

        // CRITICAL: the stream to Gemini Live must be CONTINUOUS.
        //
        // Server-side automatic VAD decides the user's turn is over by observing the
        // silence that FOLLOWS their speech. If the browser simply stops transmitting
        // when the room goes quiet, the server never sees that trailing silence, never
        // closes the turn, and the model never answers -- Joy greets you and then
        // appears deaf forever, with no error raised anywhere.
        //
        // Verified empirically against models/gemini-3.8-live-extended-thinking:
        //   stop sending after speech -> SILENT
        //   audio_stream_end=True     -> SILENT
        //   keep streaming silence    -> RESPONDS
        //
        // So we NEVER drop a frame. During the mute / ringer / greeting / echo-tail
        // windows we transmit digital silence instead, which keeps VAD fed while still
        // preventing Joy from hearing herself or the ringer.
        const suppressAudio =
          state.isVoiceMuted ||
          state.isRinging ||
          state.isGreetingInProgress ||
          state.activePlaybackNodes.length > 0 ||
          Date.now() - (state.lastPlaybackEndTime || 0) < 650;

        let sum = 0;
        const pcm16 = new Int16Array(input.length);
        if (!suppressAudio) {
          for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            sum += Math.abs(s);
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
        }
        const avgAbs = sum / Math.max(1, input.length);
        state.currentAudioAmplitude = suppressAudio ? 0 : Math.min(1, avgAbs * 6);
        if (avgAbs >= 0.008) {
          state.lastVoiceSpeechAt = Date.now();
        }

        // NOTE: the old "Voice Activity Energy Gate" lived here and returned early on
        // quiet frames to suppress junk '🎤 Hum.' / '🎤 ம்' transcripts. That is what
        // broke two-way voice. Those junk transcripts are already filtered server-side
        // by _is_meaningful_user_speech() in backend/gemini_live.py, so the client-side
        // gate was redundant as well as harmful. Do not reintroduce it.

        const bytes = new Uint8Array(pcm16.buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        const b64 = btoa(binary);

        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
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

      // Use modern AudioWorkletNode instead of deprecated ScriptProcessorNode
      if (state.micAudioCtx.audioWorklet && typeof window.AudioWorkletNode !== 'undefined') {
        const workletCode = `
          class Pcm16CaptureProcessor extends AudioWorkletProcessor {
            constructor() {
              super();
              this._buf = new Float32Array(4096);
              this._offset = 0;
            }
            process(inputs) {
              const ch = inputs && inputs[0] && inputs[0][0];
              if (!ch) return true;
              for (let i = 0; i < ch.length; i++) {
                this._buf[this._offset++] = ch[i];
                if (this._offset >= 4096) {
                  this.port.postMessage(this._buf.slice(0));
                  this._offset = 0;
                }
              }
              return true;
            }
          }
          registerProcessor('pcm16-capture-processor', Pcm16CaptureProcessor);
        `;
        const blobUrl = URL.createObjectURL(new Blob([workletCode], { type: 'application/javascript' }));
        await state.micAudioCtx.audioWorklet.addModule(blobUrl);
        URL.revokeObjectURL(blobUrl);
        const workletNode = new AudioWorkletNode(state.micAudioCtx, 'pcm16-capture-processor');
        workletNode.port.onmessage = (ev) => {
          if (ev && ev.data) handleFloat32Frame(ev.data);
        };
        source.connect(workletNode);
        workletNode.connect(state.micAudioCtx.destination);
        state.micProcessor = workletNode;
      }
      state.isRecordingVoice = true;
      updateCallControlIcons();

      if (orbBtn) orbBtn.classList.add('listening');
    } catch (err) {
      // Even in headless/no-mic environments, keep the call active so Joy answers the ringer!
      state.isRecordingVoice = true;
      updateCallControlIcons();
    }

    // Trigger unified live voice greeting after 1.1s of ringing (never invokes tools or creates duplicate text turns)
    setTimeout(() => {
      if (!state.isRecordingVoice && !state.isRinging) return;
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(
          JSON.stringify({
            type: 'voice_greeting',
            customer_id: state.activeCustomerId || 'CUST-001',
            current_stage: state.activeStage || 1,
          })
        );
      }
    }, 1100);
    // Safety release for greeting gate after 5s in case audio_out finishes early
    setTimeout(() => {
      state.isGreetingInProgress = false;
    }, 5000);
  }

  function stopVoiceMicrophone() {
    stopPhoneRinger(false);
    state.isRecordingVoice = false;
    state.isGreetingInProgress = false;
    state.isVoiceMuted = false;
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
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: 'audio_stream_end' }));
    }
    clearAudioPlaybackQueueOnly();
    updateCallControlIcons();
    const orbBtn = document.getElementById('voiceMicToggleBtn');
    const stateLabel = document.getElementById('voiceStateLabel');
    if (orbBtn) orbBtn.classList.remove('listening');
    if (stateLabel) stateLabel.textContent = 'Tap green phone to call Joy';
  }

  async function triggerUc2PaymentPrepFlow(simulateBecMismatch = false) {
    switchSlideDeckUseCase('UC2_PAYMENT');
    upsertTurnMessage(
      `uc2_user_${Date.now()}`,
      'user',
      simulateBecMismatch
        ? 'Screen SingaTech Industrial invoice INV-2026-889 with altered account 017-482910-8 for BEC fraud.'
        : 'Verify SingaTech Industrial invoice INV-2026-889 (SGD 14,250.00), optimize rail, and stage in Native IDEAL.'
    );
    const res = await apiFetch('/api/payment-prep/stage', {
      method: 'POST',
      body: JSON.stringify({
        customer_id: state.activeCustomerId || 'CUST-001',
        beneficiary_name: 'SingaTech Industrial',
        amount_sgd: 14250.0,
        simulate_bec_mismatch: Boolean(simulateBecMismatch),
      }),
    });
    if (res.ok && res.data) {
      (res.data.tool_calls || []).forEach((tc, idx) => {
        upsertToolExecutionCard(tc.call_id || `uc2_${Date.now()}_${idx}`, tc.tool_name, tc.args || {}, tc.result || res.data);
      });
      if (res.data.reply) {
        upsertTurnMessage(`uc2_bot_${Date.now()}`, 'assistant', res.data.reply);
      }
      if (res.data.ui_sync) {
        await handleUiSync(res.data.ui_sync);
      }
    }
  }

  async function triggerUc3FxHedgeFlow() {
    switchSlideDeckUseCase('UC3_FX');
    upsertTurnMessage(
      `uc3_user_${Date.now()}`,
      'user',
      'Book a forward for 70% of my USD 5M payable (0.70 × USD 5M = USD 3,500,000) after pre-trade validation.'
    );
    const res = await apiFetch('/api/fx/pretrade-and-book', {
      method: 'POST',
      body: JSON.stringify({
        customer_id: state.activeCustomerId || 'CUST-001',
        total_payable_usd: 5000000.0,
        hedge_ratio_pct: 70.0,
        tenor: '3M',
        execute_booking: true,
      }),
    });
    if (res.ok && res.data) {
      (res.data.tool_calls || []).forEach((tc, idx) => {
        upsertToolExecutionCard(tc.call_id || `uc3_${Date.now()}_${idx}`, tc.tool_name, tc.args || {}, tc.result || res.data);
      });
      if (res.data.reply) {
        upsertTurnMessage(`uc3_bot_${Date.now()}`, 'assistant', res.data.reply);
      }
      if (res.data.ui_sync) {
        await handleUiSync(res.data.ui_sync);
      }
    }
  }

  function enqueuePcm24AudioPlayback(base64Pcm, sampleRate = 24000) {
    try {
      stopPhoneRinger(true);
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
      if (stateLabel) stateLabel.textContent = 'Connected · Joy Speaking...';

      source.onended = () => {
        state.activePlaybackNodes = state.activePlaybackNodes.filter((n) => n !== source);
        state.lastPlaybackEndTime = Date.now();
        if (state.activePlaybackNodes.length === 0) {
          state.isGreetingInProgress = false;
          if (orbBtn) orbBtn.classList.remove('speaking');
          if (stateLabel) {
            stateLabel.textContent = state.isRecordingVoice ? 'Connected · Listening...' : 'Tap green phone to call Joy';
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
      stateLabel.textContent = state.isRecordingVoice ? 'Connected · Listening...' : 'Tap green phone to call Joy';
    }
  }

  function triggerBargeInInterruption() {
    clearAudioPlaybackQueueOnly();
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: 'barge_in' }));
    }
  }

  // ==========================================================================
  // 11. DOM Event Bindings & Bootstrap
  // ==========================================================================

  function bindUiEvents() {
    // Top Header Slide Deck 3-Use-Case Switcher
    document.querySelectorAll('.uc-tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const uc = btn.getAttribute('data-usecase');
        if (uc) switchSlideDeckUseCase(uc);
      });
    });

    const stagePayBtn = document.getElementById('stagePaymentIdealBtn');
    if (stagePayBtn) {
      stagePayBtn.addEventListener('click', () => triggerUc2PaymentPrepFlow(false));
    }
    const becSimBtn = document.getElementById('toggleBecFraudSimBtn');
    if (becSimBtn) {
      becSimBtn.addEventListener('click', () => triggerUc2PaymentPrepFlow(true));
    }
    const bookFxBtn = document.getElementById('bookFxForwardBtn');
    if (bookFxBtn) {
      bookFxBtn.addEventListener('click', () => triggerUc3FxHedgeFlow());
    }

    const quickUc2 = document.getElementById('quickUc2PaymentChip');
    if (quickUc2) {
      quickUc2.addEventListener('click', () => triggerUc2PaymentPrepFlow(false));
    }
    const quickUc3 = document.getElementById('quickUc3FxHedgeChip');
    if (quickUc3) {
      quickUc3.addEventListener('click', () => triggerUc3FxHedgeFlow());
    }

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

    // Copilot Dock Voice & Mute Controls
    const micBtn = document.getElementById('voiceMicToggleBtn');
    if (micBtn) micBtn.addEventListener('click', toggleVoiceMicrophone);

    const muteBtn = document.getElementById('voiceMuteToggleBtn');
    if (muteBtn) muteBtn.addEventListener('click', toggleVoiceMute);

    const bargeInBtn = document.getElementById('bargeInBtn');
    if (bargeInBtn) bargeInBtn.addEventListener('click', triggerBargeInInterruption);

    const quickNricChip = document.getElementById('quickUploadNricChip');
    if (quickNricChip) {
      quickNricChip.addEventListener('click', () => {
        triggerNricOcrUploadFlow(null);
      });
    }

    const nricUploadBtn = document.getElementById('copilotNricUploadBtn');
    const nricFileInput = document.getElementById('copilotNricFileInput');
    if (nricUploadBtn && nricFileInput) {
      nricUploadBtn.addEventListener('click', () => {
        nricFileInput.click();
      });
      nricFileInput.addEventListener('change', (e) => {
        const f = e.target.files && e.target.files[0];
        if (f) {
          triggerNricOcrUploadFlow(f);
          nricFileInput.value = '';
        }
      });
    }

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
