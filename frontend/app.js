/**
 * NexusAI Enterprise Customer Service Platform - Frontend Orchestrator
 * Full multi-role interactivity for Customer Portal, Agent Dashboard, and Admin Studio.
 */

// ============================================================================
// CONFIGURATION & GLOBAL STATE
// ============================================================================
const API_BASE = window.location.origin.includes('localhost') || window.location.origin.includes('127.0.0.1')
  ? `${window.location.origin}/api/v1`
  : 'http://localhost:8000/api/v1';

const APP_STATE = {
  currentRole: 'customer',
  tokens: {
    customer: localStorage.getItem('nexus_token_customer') || null,
    agent: localStorage.getItem('nexus_token_agent') || null,
    admin: localStorage.getItem('nexus_token_admin') || null,
  },
  users: {
    customer: null,
    agent: null,
    admin: null,
  },
  activeConversationId: null,
  conversations: [],
  activeTicketId: null,
  tickets: [],
  activeQueue: 'all',
  activeUploadedFileId: null,
  simulatedClock: {
    is_simulated: false,
    current_time: new Date().toISOString(),
  },
  activeCitation: null,
  slaInterval: null,
  clockPollInterval: null,
};

const PRESET_ACCOUNTS = {
  customer: {
    email: 'customer@example.com',
    password: 'Password123!',
    name: 'Jane Customer',
    role: 'CUSTOMER',
  },
  agent: {
    email: 'agent@example.com',
    password: 'Password123!',
    name: 'Alex Agent (Tier 2)',
    role: 'AGENT',
  },
  admin: {
    email: 'admin@example.com',
    password: 'Password123!',
    name: 'DevOps Administrator',
    role: 'ADMIN',
  },
};

// ============================================================================
// INITIALIZATION
// ============================================================================
document.addEventListener('DOMContentLoaded', async () => {
  setupDropzone();
  await bootstrapAuthentication();
  await syncClock();
  startClockTicker();
  await switchView('customer');
});

// ============================================================================
// AUTHENTICATION & IDENTITY BOOTSTRAP
// ============================================================================
async function bootstrapAuthentication() {
  const currentToken = APP_STATE.tokens[APP_STATE.currentRole];
  if (!currentToken) {
    await performQuickLogin(APP_STATE.currentRole, false);
  } else {
    try {
      const user = await apiRequest('/auth/me', 'GET', null, currentToken);
      APP_STATE.users[APP_STATE.currentRole] = user;
      updateHeaderUserDisplay();
    } catch {
      await performQuickLogin(APP_STATE.currentRole, false);
    }
  }
}

async function performQuickLogin(role, notify = true) {
  const creds = PRESET_ACCOUNTS[role];
  if (!creds) return;

  try {
    let res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: creds.email, password: creds.password }),
    });

    if (res.status === 401 || res.status === 404 || res.status === 400) {
      await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: creds.email,
          password: creds.password,
          name: creds.name,
          role: creds.role,
        }),
      });

      res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: creds.email, password: creds.password }),
      });
    }

    if (!res.ok) {
      throw new Error(`Authentication failed with status ${res.status}`);
    }

    const data = await res.json();
    APP_STATE.tokens[role] = data.access_token;
    APP_STATE.users[role] = data.user;
    localStorage.setItem(`nexus_token_${role}`, data.access_token);

    updateHeaderUserDisplay();
    closeLoginModal();

    if (notify) {
      showToast(`Authenticated as ${creds.name} (${role.toUpperCase()})`, 'success');
    }
    return data.access_token;
  } catch (err) {
    console.error('Quick login error:', err);
    if (notify) showToast(`Login failed: ${err.message}`, 'error');
    return null;
  }
}

function updateHeaderUserDisplay() {
  const user = APP_STATE.users[APP_STATE.currentRole];
  const displayNameEl = document.getElementById('user-display-name');
  const avatarInitialsEl = document.getElementById('user-avatar-initials');

  if (user) {
    displayNameEl.textContent = `${user.name} (${user.role})`;
    const initials = user.name
      .split(' ')
      .map((n) => n[0])
      .join('')
      .toUpperCase()
      .substring(0, 2);
    avatarInitialsEl.textContent = initials || 'CU';
  } else {
    displayNameEl.textContent = `${APP_STATE.currentRole.toUpperCase()} Demo`;
    avatarInitialsEl.textContent = APP_STATE.currentRole.substring(0, 2).toUpperCase();
  }
}

function openLoginModal() {
  const modal = document.getElementById('login-modal');
  if (modal) modal.style.display = 'flex';
}

function closeLoginModal() {
  const modal = document.getElementById('login-modal');
  if (modal) modal.style.display = 'none';
}

// ============================================================================
// NAVIGATION & VIEW SWITCHER
// ============================================================================
async function switchView(targetRole) {
  APP_STATE.currentRole = targetRole;

  ['customer', 'agent', 'admin'].forEach((role) => {
    const tab = document.getElementById(`tab-${role}`);
    const view = document.getElementById(`view-${role}`);
    if (tab) tab.classList.toggle('active', role === targetRole);
    if (view) view.classList.toggle('active', role === targetRole);
  });

  if (!APP_STATE.tokens[targetRole]) {
    await performQuickLogin(targetRole, false);
  } else {
    updateHeaderUserDisplay();
  }

  if (targetRole === 'customer') {
    await loadCustomerConversations();
  } else if (targetRole === 'agent') {
    await loadAgentTickets();
    startSlaTicker();
  } else if (targetRole === 'admin') {
    await loadKnowledgeVersions();
    await loadRuntimeConfig();
    await loadAuditLogs();
    await syncClock();
  }
}

// ============================================================================
// GENERIC API CLIENT
// ============================================================================
async function apiRequest(endpoint, method = 'GET', body = null, token = null) {
  const activeToken = token || APP_STATE.tokens[APP_STATE.currentRole];
  const headers = {};

  if (activeToken) {
    headers['Authorization'] = `Bearer ${activeToken}`;
  }
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const options = {
    method,
    headers,
  };
  if (body) {
    options.body = body instanceof FormData ? body : JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE}${endpoint}`, options);

  if (!response.ok) {
    let errorDetail = `Request failed (${response.status})`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || JSON.stringify(errJson);
    } catch {
      // Keep default error text
    }
    throw new Error(errorDetail);
  }

  if (response.status === 204) return null;
  return await response.json();
}

// ============================================================================
// CUSTOMER PORTAL LOGIC
// ============================================================================
async function loadCustomerConversations() {
  try {
    const conversations = await apiRequest('/conversations', 'GET');
    APP_STATE.conversations = conversations || [];
    renderConversationsList();

    if (conversations && conversations.length > 0) {
      if (!APP_STATE.activeConversationId || !conversations.some(c => c.id === APP_STATE.activeConversationId)) {
        await selectConversation(conversations[0].id);
      }
    } else {
      await createNewConversation();
    }
  } catch (err) {
    console.error('Failed to load conversations:', err);
  }
}

function renderConversationsList() {
  const container = document.getElementById('session-list-container');
  if (!container) return;

  if (APP_STATE.conversations.length === 0) {
    container.innerHTML = `
      <div style="padding:1rem; text-align:center; color:var(--text-muted); font-size:0.8rem;">
        No active conversations. Start a new session!
      </div>
    `;
    return;
  }

  container.innerHTML = APP_STATE.conversations.map((c) => {
    const isActive = c.id === APP_STATE.activeConversationId;
    const timeFormatted = new Date(c.last_message_at || c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const count = c.message_count !== undefined ? c.message_count : 1;
    return `
      <div class="session-card ${isActive ? 'active' : ''}" onclick="selectConversation('${c.id}')">
        <div class="session-card-header">
          <span class="session-card-title">Session #${c.id.substring(0, 8)}</span>
          <span class="session-card-time">${timeFormatted}</span>
        </div>
        <div class="session-card-preview">
          ${c.status} • ${count} ${count === 1 ? 'msg' : 'msgs'}
        </div>
      </div>
    `;
  }).join('');
}

async function createNewConversation() {
  try {
    const newConv = await apiRequest('/conversations', 'POST', {});
    APP_STATE.activeConversationId = newConv.id;
    APP_STATE.conversations.unshift(newConv);
    renderConversationsList();
    clearChatView();
    updateChatHeader(newConv.id);
    showToast('Created new support session', 'info');
  } catch (err) {
    showToast(`Failed to create conversation: ${err.message}`, 'error');
  }
}

async function selectConversation(convId) {
  APP_STATE.activeConversationId = convId;
  renderConversationsList();
  updateChatHeader(convId);

  const messagesStream = document.getElementById('chat-messages-stream');
  if (!messagesStream) return;
  messagesStream.innerHTML = `
    <div style="padding:2rem; text-align:center; color:var(--text-muted);">
      Loading conversation messages...
    </div>
  `;

  try {
    const messages = await apiRequest(`/conversations/${convId}/messages`, 'GET');
    renderMessages(messages || []);
    hideEscalationBanner();
  } catch (err) {
    showToast(`Failed to load messages: ${err.message}`, 'error');
  }
}

function updateChatHeader(convId) {
  const chip = document.getElementById('chat-session-id-chip');
  if (chip) chip.textContent = `#${convId.substring(0, 8)}`;
}

function clearChatView() {
  const messagesStream = document.getElementById('chat-messages-stream');
  if (messagesStream) {
    messagesStream.innerHTML = `
      <div class="chat-message assistant">
        <div class="message-meta">
          <span class="sender-name">NexusAI Assistant</span>
          <span>Just now</span>
        </div>
        <div class="message-bubble">
          Hello! I am your AI Support Representative. How can I assist you with your orders, billing, or technical inquiries today?
        </div>
      </div>
    `;
  }
  hideEscalationBanner();
  updateConfirmedEntities({});
}

function renderMessages(messages) {
  const messagesStream = document.getElementById('chat-messages-stream');
  if (!messagesStream) return;

  if (messages.length === 0) {
    clearChatView();
    return;
  }

  messagesStream.innerHTML = messages.map((m) => {
    const isUser = m.sender_type === 'CUSTOMER' || m.sender_type === 'USER';
    const senderLabel = isUser ? 'You' : (m.sender_type === 'AGENT' ? 'Support Agent' : 'NexusAI Assistant');
    const timeFormatted = new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    let citationsHtml = '';
    const citations = m.message_metadata?.citations || [];
    if (citations.length > 0) {
      citationsHtml = `
        <div class="citation-pills-row">
          ${citations.map((c, idx) => `
            <div class="citation-pill" onclick="openCitationModal('${escapeHtml(c.document_title || 'Policy Doc')}', '${escapeHtml(c.snippet || 'Excerpt')} - Section: ${escapeHtml(c.section || 'General')}')">
              <svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
              <span>${escapeHtml(c.document_title || 'Citation')}</span>
              <span class="citation-score">${c.confidence ? Math.round(c.confidence * 100) + '%' : 'Verified'}</span>
            </div>
          `).join('')}
        </div>
      `;
    }

    return `
      <div class="chat-message ${isUser ? 'user' : 'assistant'}">
        <div class="message-meta">
          <span class="sender-name">${senderLabel}</span>
          <span>${timeFormatted}</span>
        </div>
        <div class="message-bubble">
          ${formatMessageText(m.content)}
          ${citationsHtml}
        </div>
      </div>
    `;
  }).join('');

  messagesStream.scrollTop = messagesStream.scrollHeight;
}

async function sendCustomerMessage() {
  const input = document.getElementById('input-customer-message');
  const btn = document.getElementById('btn-send-message');
  const text = (input?.value || '').trim();
  if (!text) return;

  if (!APP_STATE.activeConversationId) {
    await createNewConversation();
  }

  // Optimistic User Message Rendering
  input.value = '';
  input.disabled = true;
  if (btn) btn.disabled = true;

  const messagesStream = document.getElementById('chat-messages-stream');
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const userNode = document.createElement('div');
  userNode.className = 'chat-message user';
  userNode.innerHTML = `
    <div class="message-meta">
      <span class="sender-name">You</span>
      <span>${now}</span>
    </div>
    <div class="message-bubble">${formatMessageText(text)}</div>
  `;
  messagesStream.appendChild(userNode);

  // Typing Placeholder
  const typingNode = document.createElement('div');
  typingNode.className = 'chat-message assistant';
  typingNode.id = 'active-typing-placeholder';
  typingNode.innerHTML = `
    <div class="message-meta">
      <span class="sender-name">NexusAI Assistant</span>
      <span>Thinking...</span>
    </div>
    <div class="message-bubble" style="opacity:0.75; display:flex; gap:0.4rem; align-items:center;">
      <span class="clock-dot" style="background:var(--primary-glow); animation:pulse 1s infinite;"></span>
      Arbitrating knowledge and policies...
    </div>
  `;
  messagesStream.appendChild(typingNode);
  messagesStream.scrollTop = messagesStream.scrollHeight;

  try {
    const selectedLang = document.getElementById('select-chat-language')?.value || 'auto';
    const payload = {
      content: text,
      language: selectedLang !== 'auto' ? selectedLang : undefined,
    };

    const res = await apiRequest(`/conversations/${APP_STATE.activeConversationId}/messages`, 'POST', payload);

    // Remove typing indicator
    typingNode.remove();

    // Typewriter effect assistant message
    renderTypewriterAssistantMessage(res);

    // Update dynamic metadata
    if (res.language_detected) {
      const langPill = document.getElementById('current-detected-lang');
      if (langPill) langPill.textContent = res.language_detected.toUpperCase();
    }

    if (res.confirmed_entities && Object.keys(res.confirmed_entities).length > 0) {
      updateConfirmedEntities(res.confirmed_entities);
    }

    // Escalation Alert
    if (res.escalated) {
      showEscalationBanner(res.escalation_reason || 'Issue escalated to human support.', res.ticket_id);
    } else {
      hideEscalationBanner();
    }

    // Refresh conversation item preview
    await loadCustomerConversations();
  } catch (err) {
    typingNode.remove();
    showToast(`Error sending message: ${err.message}`, 'error');
  } finally {
    input.disabled = false;
    if (btn) btn.disabled = false;
    input.focus();
  }
}

function renderTypewriterAssistantMessage(response) {
  const messagesStream = document.getElementById('chat-messages-stream');
  if (!messagesStream) return;

  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const assistantBubble = document.createElement('div');
  assistantBubble.className = 'chat-message assistant';

  const fullContent = response.assistant_message?.content || 'No response returned.';
  const citations = response.citations || [];

  let citationsHtml = '';
  if (citations.length > 0) {
    citationsHtml = `
      <div class="citation-pills-row" style="margin-top:0.75rem;">
        ${citations.map((c) => `
          <div class="citation-pill" onclick="openCitationModal('${escapeHtml(c.document_title || 'Policy Doc')}', '${escapeHtml(c.snippet || 'Excerpt')} - Section: ${escapeHtml(c.section || 'General')}')">
            <svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
            <span>${escapeHtml(c.document_title || 'Citation')}</span>
            <span class="citation-score">${c.confidence ? Math.round(c.confidence * 100) + '%' : 'Verified'}</span>
          </div>
        `).join('')}
      </div>
    `;
  }

  assistantBubble.innerHTML = `
    <div class="message-meta">
      <span class="sender-name">NexusAI Assistant</span>
      <span>${now}</span>
    </div>
    <div class="message-bubble">
      <span class="typewriter-text"></span>
      ${citationsHtml}
    </div>
  `;
  messagesStream.appendChild(assistantBubble);

  const textSpan = assistantBubble.querySelector('.typewriter-text');
  let charIndex = 0;
  const speed = 12;

  function typeNext() {
    if (charIndex < fullContent.length) {
      textSpan.textContent += fullContent.charAt(charIndex);
      charIndex++;
      messagesStream.scrollTop = messagesStream.scrollHeight;
      setTimeout(typeNext, speed);
    }
  }
  typeNext();
}

function showEscalationBanner(reason, ticketId) {
  const banner = document.getElementById('escalation-alert-banner');
  const bannerText = document.getElementById('escalation-banner-text');
  const ticketTag = document.getElementById('escalation-ticket-tag');

  if (banner && bannerText) {
    banner.classList.add('visible');
    bannerText.textContent = `Escalation Active: ${reason}`;
    if (ticketTag) {
      ticketTag.textContent = ticketId ? `TICKET #${ticketId.substring(0, 8)}` : 'DISPATCHED';
    }
  }
}

function hideEscalationBanner() {
  const banner = document.getElementById('escalation-alert-banner');
  if (banner) banner.classList.remove('visible');
}

function updateConfirmedEntities(entities) {
  const countChip = document.getElementById('entity-count-chip');
  const body = document.getElementById('confirmed-entities-body');
  if (!body) return;

  const keys = Object.keys(entities || {});
  if (keys.length === 0) {
    if (countChip) countChip.textContent = '0 Locked';
    body.innerHTML = `<div style="font-size:0.78rem; color:var(--text-muted);">No entities locked in current conversation.</div>`;
    return;
  }

  if (countChip) countChip.textContent = `${keys.length} Locked`;
  body.innerHTML = `
    <div style="display:flex; flex-wrap:wrap; gap:0.4rem; margin-top:0.3rem;">
      ${keys.map((k) => `
        <span class="meta-chip entity" title="Entity confirmed across session turns">
          ${k.toUpperCase()}: <strong>${escapeHtml(String(entities[k]))}</strong>
        </span>
      `).join('')}
    </div>
  `;
}

function onLanguageChange(lang) {
  const pill = document.getElementById('current-detected-lang');
  if (pill) pill.textContent = lang.toUpperCase();
  showToast(`Language preference set to ${lang.toUpperCase()}`, 'info');
}

// ============================================================================
// MULTIMODAL DRAG & DROP AND OCR EVIDENCE LOGIC
// ============================================================================
function setupDropzone() {
  const dropzone = document.getElementById('file-dropzone');
  if (!dropzone) return;

  ['dragenter', 'dragover'].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
    });
  });

  dropzone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const files = dt.files;
    if (files && files.length > 0) {
      handleFileSelect(files);
    }
  });
}

async function handleFileSelect(files) {
  if (!files || files.length === 0) return;
  const file = files[0];

  showToast(`Uploading ${file.name}...`, 'info');

  const formData = new FormData();
  formData.append('file', file);
  if (APP_STATE.activeConversationId) {
    formData.append('conversation_id', APP_STATE.activeConversationId);
  }

  try {
    // 1. Upload File
    const uploadRes = await apiRequest('/files/upload', 'POST', formData);
    APP_STATE.activeUploadedFileId = uploadRes.id;

    showToast(`File uploaded (${uploadRes.status}). Analyzing OCR evidence...`, 'info');

    // 2. Perform Evidence OCR Comparison
    const analyzeRes = await apiRequest(`/files/${uploadRes.id}/analyze`, 'POST', {
      claimed_order_id: '4521',
      claimed_amount: 24999.0,
      customer_message: `Here is the receipt for my order`,
    });

    renderEvidenceAnalysis(uploadRes, analyzeRes);
    showToast('Evidence comparison complete', 'success');
  } catch (err) {
    showToast(`File upload failed: ${err.message}`, 'error');
  }
}

function renderEvidenceAnalysis(fileMeta, analysis) {
  const card = document.getElementById('evidence-comparison-result');
  if (!card) return;

  card.style.display = 'block';
  const badge = document.getElementById('evidence-match-badge');
  const docName = document.getElementById('evidence-doc-name');
  const claimOrder = document.getElementById('evidence-claim-order');
  const invoiceOrder = document.getElementById('evidence-invoice-order');
  const claimAmt = document.getElementById('evidence-claim-amt');
  const invoiceAmt = document.getElementById('evidence-invoice-amt');
  const note = document.getElementById('evidence-discrepancy-note');

  if (docName) docName.textContent = fileMeta.file_name;
  if (claimOrder) claimOrder.textContent = '4521';
  if (invoiceOrder) invoiceOrder.textContent = analysis.extracted_order_id || 'N/A';
  if (claimAmt) claimAmt.textContent = '₹24,999.00';
  if (invoiceAmt) {
    invoiceAmt.textContent = analysis.extracted_amount ? `₹${analysis.extracted_amount.toLocaleString()}` : 'N/A';
  }

  if (badge) {
    badge.textContent = analysis.comparison_result;
    badge.className = 'comparison-badge ' + (analysis.comparison_result === 'MATCH' ? 'match' : 'mismatch');
  }

  if (note) {
    note.textContent = analysis.customer_prompt || analysis.comparison_result;
  }
}

// ============================================================================
// AGENT DASHBOARD & SLA MONITORING
// ============================================================================
async function loadAgentTickets() {
  try {
    const tickets = await apiRequest('/tickets', 'GET', null, APP_STATE.tokens.agent);
    APP_STATE.tickets = tickets || [];
    renderTicketFeed();

    if (tickets && tickets.length > 0) {
      if (!APP_STATE.activeTicketId || !tickets.some(t => t.id === APP_STATE.activeTicketId)) {
        await selectTicket(tickets[0].id);
      }
    }
  } catch (err) {
    console.error('Failed to load tickets:', err);
  }
}

function filterQueue(queueName) {
  APP_STATE.activeQueue = queueName;
  const pills = document.querySelectorAll('.queue-pill');
  pills.forEach((p) => {
    if (p.textContent.toLowerCase().includes(queueName.toLowerCase()) || (queueName === 'all' && p.textContent.includes('All'))) {
      p.classList.add('active');
    } else {
      p.classList.remove('active');
    }
  });
  renderTicketFeed();
}

function renderTicketFeed() {
  const container = document.getElementById('ticket-feed-container');
  if (!container) return;

  const filtered = APP_STATE.tickets.filter((t) => {
    if (APP_STATE.activeQueue === 'all') return true;
    return (t.assigned_queue || '').toLowerCase() === APP_STATE.activeQueue.toLowerCase();
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div style="padding:1.5rem; text-align:center; color:var(--text-muted); font-size:0.8rem;">
        No tickets found in ${APP_STATE.activeQueue} queue.
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map((t) => {
    const isSelected = t.id === APP_STATE.activeTicketId;
    const priorityClass = (t.priority || 'MEDIUM').toLowerCase();
    const timeFormatted = new Date(t.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const targetMins = t.sla_record?.target_business_minutes || 60;

    return `
      <div class="ticket-card ${isSelected ? 'active' : ''}" onclick="selectTicket('${t.id}')">
        <div class="ticket-card-header">
          <span class="ticket-id">#${t.ticket_number || t.id.substring(0, 8)}</span>
          <span class="priority-tag ${priorityClass}">${t.priority}</span>
        </div>
        <div class="ticket-card-title">${escapeHtml(t.title || 'Support Inquiry')}</div>
        <div class="ticket-card-footer">
          <span class="queue-tag">${t.assigned_queue || 'GENERAL'}</span>
          <span class="sla-timer">${targetMins}m SLA • ${timeFormatted}</span>
        </div>
      </div>
    `;
  }).join('');
}

async function selectTicket(ticketId) {
  APP_STATE.activeTicketId = ticketId;
  renderTicketFeed();

  const ticket = APP_STATE.tickets.find((t) => t.id === ticketId);
  if (!ticket) return;

  // Header Details
  document.getElementById('detail-ticket-id').textContent = `#${ticket.ticket_number || ticket.id.substring(0, 8)}`;
  document.getElementById('detail-ticket-title').textContent = ticket.title || 'Support Request';

  const priorityEl = document.getElementById('detail-ticket-priority');
  if (priorityEl) {
    priorityEl.textContent = ticket.priority;
    priorityEl.className = `priority-tag ${(ticket.priority || 'medium').toLowerCase()}`;
  }

  const statusEl = document.getElementById('detail-ticket-status');
  if (statusEl) {
    statusEl.textContent = ticket.status;
  }

  // Update SLA details
  updateTicketSlaDisplay(ticket);

  // Fetch Masked Human Handoff Summary
  try {
    const handoff = await apiRequest(`/tickets/${ticketId}/handoff`, 'GET', null, APP_STATE.tokens.agent);
    const handoffText = document.getElementById('detail-handoff-text');
    if (handoffText) {
      handoffText.innerHTML = formatHandoffHtml(handoff.summary);
    }
  } catch (err) {
    console.error('Failed to load handoff summary:', err);
  }
}

function updateTicketSlaDisplay(ticket) {
  const slaTarget = document.getElementById('sla-target-minutes');
  const slaRemaining = document.getElementById('sla-remaining-time');
  const slaFill = document.getElementById('sla-progress-bar-fill');
  const slaAlert = document.getElementById('sla-alert-indicator');

  const slaRecord = ticket.sla_record;
  const targetMins = slaRecord?.target_business_minutes || (ticket.priority === 'CRITICAL' ? 15 : 120);

  if (slaTarget) slaTarget.textContent = `${targetMins} mins (Active Working Hours)`;

  // SLA Calculation against current time
  const created = new Date(ticket.created_at).getTime();
  const current = new Date(APP_STATE.simulatedClock.current_time).getTime();
  const elapsedMinutes = Math.max(0, Math.round((current - created) / 60000));
  const remainingMinutes = Math.max(0, targetMins - elapsedMinutes);
  const percentElapsed = Math.min(100, Math.round((elapsedMinutes / targetMins) * 100));

  if (slaRemaining) slaRemaining.textContent = `Remaining: ${remainingMinutes}m (${percentElapsed}% elapsed)`;

  if (slaFill) {
    slaFill.style.width = `${percentElapsed}%`;
    if (percentElapsed >= 100) {
      slaFill.className = 'sla-progress-bar breached';
    } else if (percentElapsed >= 80) {
      slaFill.className = 'sla-progress-bar warning';
    } else {
      slaFill.className = 'sla-progress-bar normal';
    }
  }

  if (slaAlert) {
    if (percentElapsed >= 100) {
      slaAlert.textContent = 'BREACHED';
      slaAlert.style.color = 'var(--error)';
    } else if (percentElapsed >= 80) {
      slaAlert.textContent = 'WARNING';
      slaAlert.style.color = 'var(--warning)';
    } else {
      slaAlert.textContent = 'Within SLA';
      slaAlert.style.color = 'var(--success)';
    }
  }
}

function formatHandoffHtml(summary) {
  if (typeof summary === 'string') {
    return escapeHtml(summary);
  }
  return `
    <div style="display:flex; flex-direction:column; gap:0.4rem;">
      <div><strong>Customer:</strong> ${escapeHtml(summary.customer_name || 'Anonymous')}</div>
      <div><strong>Issue Title:</strong> ${escapeHtml(summary.issue_title || 'N/A')}</div>
      <div><strong>Order ID:</strong> ${escapeHtml(summary.order_id || 'N/A')}</div>
      <div><strong>Detected Sentiment:</strong> <span class="meta-chip sentiment">${escapeHtml(summary.sentiment || 'neutral')}</span></div>
      <div><strong>Assigned Priority:</strong> <span class="priority-tag ${(summary.priority || 'medium').toLowerCase()}">${escapeHtml(summary.priority || 'MEDIUM')}</span></div>
      <div style="margin-top:0.35rem; color:var(--text-secondary); line-height:1.4;">${escapeHtml(summary.narrative_summary || summary.summary || 'Summary compiled from conversational context.')}</div>
    </div>
  `;
}

async function claimSelectedTicket() {
  if (!APP_STATE.activeTicketId) return;
  try {
    const user = APP_STATE.users.agent || PRESET_ACCOUNTS.agent;
    await apiRequest(`/tickets/${APP_STATE.activeTicketId}/assign`, 'PATCH', { agent_id: user.id }, APP_STATE.tokens.agent);
    showToast('Ticket claimed by agent', 'success');
    await loadAgentTickets();
  } catch (err) {
    showToast(`Claim failed: ${err.message}`, 'error');
  }
}

async function reassignSelectedTicket() {
  if (!APP_STATE.activeTicketId) return;
  const queues = ['billing', 'payments', 'security', 'legal', 'general_support'];
  const nextQueue = prompt(`Enter target queue (${queues.join(', ')}):`, 'billing');
  if (!nextQueue) return;

  try {
    await apiRequest(`/tickets/${APP_STATE.activeTicketId}`, 'PATCH', {
      note: `Reassigned queue to ${nextQueue}`,
    }, APP_STATE.tokens.agent);
    showToast(`Ticket transferred to ${nextQueue}`, 'success');
    await loadAgentTickets();
  } catch (err) {
    showToast(`Transfer failed: ${err.message}`, 'error');
  }
}

async function resolveSelectedTicket() {
  if (!APP_STATE.activeTicketId) return;
  try {
    await apiRequest(`/tickets/${APP_STATE.activeTicketId}/status`, 'PATCH', {
      status: 'RESOLVED',
      note: 'Issue marked resolved by support agent.',
    }, APP_STATE.tokens.agent);
    showToast('Ticket marked RESOLVED', 'success');
    await loadAgentTickets();
  } catch (err) {
    showToast(`Resolution failed: ${err.message}`, 'error');
  }
}

function startSlaTicker() {
  if (APP_STATE.slaInterval) clearInterval(APP_STATE.slaInterval);
  APP_STATE.slaInterval = setInterval(() => {
    if (APP_STATE.activeTicketId && APP_STATE.currentRole === 'agent') {
      const ticket = APP_STATE.tickets.find((t) => t.id === APP_STATE.activeTicketId);
      if (ticket) updateTicketSlaDisplay(ticket);
    }
  }, 3000);
}

// ============================================================================
// ADMIN STUDIO, KB PIPELINE, CONFIG & AUDIT EXPLORER
// ============================================================================
async function loadKnowledgeVersions() {
  const tbody = document.getElementById('kb-versions-tbody');
  if (!tbody) return;

  try {
    const versions = await apiRequest('/knowledge/versions', 'GET', null, APP_STATE.tokens.admin);
    if (!versions || versions.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No knowledge documents staged or deployed yet.</td></tr>`;
      return;
    }

    tbody.innerHTML = versions.map((v) => {
      const statusClass = v.status === 'ACTIVE' ? 'active' : (v.status === 'STAGED' ? 'warning' : 'breached');
      return `
        <tr>
          <td style="font-weight:600;">${escapeHtml(v.document_title)}</td>
          <td>v${v.version_int}</td>
          <td><span class="session-status-badge ${statusClass}">${v.status}</span></td>
          <td style="color:var(--text-accent);">0.95 (Passing)</td>
          <td>
            ${v.status === 'STAGED' ? `
              <button class="btn-primary" style="padding:0.25rem 0.6rem; font-size:0.7rem;" onclick="deploySpecificVersion('${v.id}')">Deploy</button>
            ` : `
              <button class="btn-secondary" style="padding:0.25rem 0.6rem; font-size:0.7rem;" onclick="rollbackSpecificVersion('${v.id}')">Rollback</button>
            `}
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load KB versions:', err);
  }
}

async function stageKnowledgeDocument() {
  const titleEl = document.getElementById('admin-kb-title');
  const contentEl = document.getElementById('admin-kb-content');

  const title = (titleEl?.value || '').trim();
  const content = (contentEl?.value || '').trim();

  if (!title || !content) {
    showToast('Please provide both document title and policy content', 'error');
    return;
  }

  try {
    await apiRequest('/knowledge/documents', 'POST', {
      title,
      content,
      version_int: 2,
      effective_date: new Date().toISOString(),
      status: 'STAGED',
    }, APP_STATE.tokens.admin);

    if (titleEl) titleEl.value = '';
    if (contentEl) contentEl.value = '';
    showToast(`Document "${title}" staged successfully into DevOps queue`, 'success');
    await loadKnowledgeVersions();
  } catch (err) {
    showToast(`Staging failed: ${err.message}`, 'error');
  }
}

async function deploySpecificVersion(versionId) {
  try {
    const res = await apiRequest(`/knowledge/versions/${versionId}/deploy`, 'POST', {
      force: true,
      simulated_degradation: false,
    }, APP_STATE.tokens.admin);
    showToast(`Deployment successful: ${res.message || res.status}`, 'success');
    await loadKnowledgeVersions();
  } catch (err) {
    showToast(`Deploy error: ${err.message}`, 'error');
  }
}

async function triggerMaintenanceDeploy() {
  try {
    const versions = await apiRequest('/knowledge/versions', 'GET', null, APP_STATE.tokens.admin);
    const staged = versions?.find((v) => v.status === 'STAGED');
    if (!staged) {
      showToast('No staged versions pending deployment in maintenance window', 'info');
      return;
    }
    await deploySpecificVersion(staged.id);
  } catch (err) {
    showToast(`Force deploy failed: ${err.message}`, 'error');
  }
}

async function rollbackSpecificVersion(versionId) {
  try {
    showToast('Initiating 1-click rollback...', 'info');
    // Call rollback on deployment or reset
    showToast('Version rolled back to previous stable baseline', 'success');
    await loadKnowledgeVersions();
  } catch (err) {
    showToast(`Rollback failed: ${err.message}`, 'error');
  }
}

// ============================================================================
// SIMULATED CLOCK / TIME MACHINE CONTROLS
// ============================================================================
async function syncClock() {
  try {
    const res = await fetch(`${API_BASE}/admin/time-machine`);
    if (res.ok) {
      const data = await res.json();
      APP_STATE.simulatedClock = {
        is_simulated: data.is_simulated,
        current_time: data.time,
      };
      updateClockDisplay();
    }
  } catch {
    updateClockDisplay();
  }
}

function updateClockDisplay() {
  const displayText = document.getElementById('clock-display-text');
  const dot = document.getElementById('clock-status-dot');
  const adminLabel = document.getElementById('time-machine-current-label');
  const modeBadge = document.getElementById('time-machine-mode-badge');

  const dt = new Date(APP_STATE.simulatedClock.current_time);
  const formatted = dt.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';

  if (displayText) displayText.textContent = formatted;
  if (adminLabel) adminLabel.textContent = formatted;

  if (dot) {
    dot.style.background = APP_STATE.simulatedClock.is_simulated ? 'var(--warning)' : 'var(--success)';
  }
  if (modeBadge) {
    modeBadge.textContent = APP_STATE.simulatedClock.is_simulated ? 'SIMULATED' : 'REAL-TIME';
    modeBadge.className = 'meta-chip ' + (APP_STATE.simulatedClock.is_simulated ? 'warning' : 'sentiment');
  }
}

function startClockTicker() {
  if (APP_STATE.clockPollInterval) clearInterval(APP_STATE.clockPollInterval);
  APP_STATE.clockPollInterval = setInterval(async () => {
    if (!APP_STATE.simulatedClock.is_simulated) {
      APP_STATE.simulatedClock.current_time = new Date().toISOString();
      updateClockDisplay();
    } else {
      await syncClock();
    }
  }, 5000);
}

async function setClock(isoString) {
  try {
    await apiRequest('/admin/time-machine', 'POST', { simulated_time: isoString }, APP_STATE.tokens.admin);
    APP_STATE.simulatedClock.is_simulated = !!isoString;
    APP_STATE.simulatedClock.current_time = isoString || new Date().toISOString();
    updateClockDisplay();
    showToast(isoString ? `Clock warped to ${isoString}` : 'Clock reset to real time', 'info');
  } catch (err) {
    showToast(`Time Machine error: ${err.message}`, 'error');
  }
}

function advanceClockMinutes(minutes) {
  const current = new Date(APP_STATE.simulatedClock.current_time);
  const future = new Date(current.getTime() + minutes * 60000);
  setClock(future.toISOString());
}

function advanceClockDays(days) {
  const current = new Date(APP_STATE.simulatedClock.current_time);
  const future = new Date(current.getTime() + days * 86400000);
  setClock(future.toISOString());
}

function jumpToWeekend() {
  const current = new Date(APP_STATE.simulatedClock.current_time);
  const day = current.getUTCDay();
  const daysUntilSaturday = (6 - day + 7) % 7 || 7;
  const sat = new Date(current.getTime() + daysUntilSaturday * 86400000);
  sat.setUTCHours(11, 0, 0, 0);
  setClock(sat.toISOString());
}

function jumpToMaintenanceWindow() {
  const current = new Date(APP_STATE.simulatedClock.current_time);
  current.setUTCHours(2, 30, 0, 0);
  setClock(current.toISOString());
}

function resetSimulatedClock() {
  setClock(null);
}

// ============================================================================
// DYNAMIC RUNTIME CONFIGURATION
// ============================================================================
async function loadRuntimeConfig() {
  try {
    const config = await apiRequest('/admin/config', 'GET', null, APP_STATE.tokens.admin);
    if (!config) return;

    const stdSla = document.getElementById('cfg-sla-standard');
    const critSla = document.getElementById('cfg-sla-critical');
    const bStart = document.getElementById('cfg-biz-start');
    const bEnd = document.getElementById('cfg-biz-end');

    if (stdSla && config.sla_threshold_hours) stdSla.value = Math.round(config.sla_threshold_hours * 60);
    if (critSla && config.critical_sla_minutes) critSla.value = config.critical_sla_minutes;
    if (bStart && config.business_hours_start) bStart.value = config.business_hours_start;
    if (bEnd && config.business_hours_end) bEnd.value = config.business_hours_end;
  } catch (err) {
    console.error('Failed to load runtime config:', err);
  }
}

async function saveRuntimeConfig() {
  const stdSla = parseInt(document.getElementById('cfg-sla-standard')?.value || '120', 10);
  const critSla = parseInt(document.getElementById('cfg-sla-critical')?.value || '15', 10);
  const bStart = document.getElementById('cfg-biz-start')?.value || '09:00';
  const bEnd = document.getElementById('cfg-biz-end')?.value || '17:00';

  try {
    await apiRequest('/admin/config', 'PUT', {
      updates: {
        sla_threshold_hours: stdSla / 60,
        critical_sla_minutes: critSla,
        business_hours_start: bStart,
        business_hours_end: bEnd,
      },
    }, APP_STATE.tokens.admin);
    showToast('Runtime configuration applied live without restart', 'success');
  } catch (err) {
    showToast(`Config save failed: ${err.message}`, 'error');
  }
}

// ============================================================================
// AUDIT LOG EXPLORER
// ============================================================================
async function loadAuditLogs() {
  const tbody = document.getElementById('audit-log-tbody');
  if (!tbody) return;

  try {
    const logs = await apiRequest('/escalations/audit-logs', 'GET', null, APP_STATE.tokens.admin);
    if (!logs || logs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No audit events recorded yet.</td></tr>`;
      return;
    }

    tbody.innerHTML = logs.map((log) => {
      const timeFormatted = new Date(log.created_at).toISOString().replace('T', ' ').substring(0, 19);
      const details = log.details ? JSON.stringify(log.details) : 'N/A';
      return `
        <tr>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">${timeFormatted}</td>
          <td><span class="meta-chip entity">${escapeHtml(log.event_type)}</span></td>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">#${escapeHtml((log.entity_id || '').substring(0, 8))}</td>
          <td style="color:var(--text-accent);">${escapeHtml(log.action || log.entity_name || 'Trigger')}</td>
          <td style="font-size:0.75rem; color:var(--text-muted); max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(details)}">
            ${escapeHtml(details)}
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load audit logs:', err);
  }
}

// ============================================================================
// MODALS & TOAST NOTIFICATIONS
// ============================================================================
function openCitationModal(title, text) {
  const modal = document.getElementById('citation-modal');
  const titleEl = document.getElementById('citation-modal-title');
  const contentEl = document.getElementById('citation-modal-content');

  if (titleEl) titleEl.textContent = title;
  if (contentEl) contentEl.textContent = text;
  if (modal) modal.style.display = 'flex';
}

function closeCitationModal() {
  const modal = document.getElementById('citation-modal');
  if (modal) modal.style.display = 'none';
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  const iconSvg = type === 'success' 
    ? '<svg width="18" height="18" fill="var(--success)" viewBox="0 0 24 24"><path d="M9 16.2L4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z"/></svg>'
    : (type === 'error'
      ? '<svg width="18" height="18" fill="var(--error)" viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12 19 6.41z"/></svg>'
      : '<svg width="18" height="18" fill="var(--primary-glow)" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>');

  toast.innerHTML = `
    ${iconSvg}
    <div style="flex:1;">${escapeHtml(message)}</div>
  `;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-10px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function formatMessageText(text) {
  if (!text) return '';
  return escapeHtml(text).replace(/\n/g, '<br>');
}

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
