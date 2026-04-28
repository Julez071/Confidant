let chatHistory = [];

const chatHistoryEl = document.getElementById('chat-history');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const typingIndicator = document.getElementById('typing-indicator');
const fileInput = document.getElementById('file-input');
const attachBtn = document.getElementById('attach-btn');
const imagePreviewContainer = document.getElementById('image-preview-container');
const imagePreview = document.getElementById('image-preview');
const removeImageBtn = document.getElementById('remove-image-btn');

// Settings Elements
const chatInputContainer = document.getElementById('chat-input-container');
const settingsContainer = document.getElementById('settings-container');
const memoryVaultContainer = document.getElementById('memory-vault-container');
const masonryGrid = document.getElementById('masonry-grid');
const tabConversation = document.getElementById('tab-conversation');
const tabSettings = document.getElementById('tab-settings');
const tabMemory = document.getElementById('tab-memory');
const braveApiKeyInput = document.getElementById('brave-api-key');
const saveSettingsBtn = document.getElementById('save-settings-btn');

let currentBase64Image = null;
let appNeedsSetup = false;
let appNeedsOnboarding = false;

// Markdown configuration
marked.setOptions({ breaks: true });

// Resize/compress an image to keep token counts sane across all AI providers.
// Returns a promise that resolves with a smaller base64 data URL.
function resizeImage(dataUrl, maxDim = 1024, quality = 0.85) {
    return new Promise((resolve) => {
        const img = new Image();
        img.onload = function() {
            let w = img.width, h = img.height;
            if (w > maxDim || h > maxDim) {
                if (w > h) { h = Math.round(h * maxDim / w); w = maxDim; }
                else        { w = Math.round(w * maxDim / h); h = maxDim; }
            }
            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            canvas.getContext('2d').drawImage(img, 0, 0, w, h);
            resolve(canvas.toDataURL('image/jpeg', quality));
        };
        img.onerror = () => resolve(dataUrl); // Fallback: return original
        img.src = dataUrl;
    });
}

function scrollToBottom() {
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
}

function showTyping() {
    chatHistoryEl.appendChild(typingIndicator); // Move to bottom
    typingIndicator.classList.remove('hidden');
    scrollToBottom();
}

function hideTyping() {
    typingIndicator.classList.add('hidden');
}

function clearMessages() {
    const messages = chatHistoryEl.querySelectorAll('.message');
    messages.forEach(msg => msg.remove());
}

function appendMessage(role, content) {
    const msgEl = document.createElement('div');
    msgEl.className = `message ${role}`;
    
    msgEl.innerHTML = `
        <div class="text-content">${content ? marked.parse(content) : ''}</div>
    `;
    
    // Insert before the typing indicator
    chatHistoryEl.insertBefore(msgEl, typingIndicator);
    scrollToBottom();
    return msgEl.querySelector('.text-content');
}

async function sendMessage(text, isHidden = false) {
    if (!text.trim() && !currentBase64Image && !isHidden) return;
    
    let messagePayload;
    let uiContent = text;
    
    if (currentBase64Image) {
        messagePayload = [
            { type: "text", text: text || " " },
            { type: "image_url", image_url: { url: currentBase64Image } }
        ];
        uiContent = `![Attached Image](${currentBase64Image})\n\n${text}`;
    } else {
        messagePayload = text;
    }
    
    if (!isHidden) {
        appendMessage('user', uiContent);
    }
    chatHistory.push({ role: 'user', content: messagePayload });
    
    chatInput.value = '';
    chatInput.style.height = 'auto'; // Reset height
    
    // Clear attachment state
    currentBase64Image = null;
    fileInput.value = '';
    imagePreviewContainer.classList.add('hidden');
    
    showTyping(); // Always show typing when waiting for response
    
    let textContentEl = null;
    let fullResponse = "";
    
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: messagePayload, isHidden: isHidden })
        });
        
        if (!response.ok) {
            throw new Error(`Server Error ${response.status}: The backend failed to start the chat stream. Please check app.py terminal logs.`);
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let isFirstChunk = true;
        let isSearching = false;
        let isRecalling = false;
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.replace('data: ', '');
                    try {
                        const data = JSON.parse(dataStr);
                        if (data.status === 'searching') {
                            isSearching = true;
                            if (textContentEl) {
                                textContentEl.innerHTML = `<div class="searching-indicator">
                                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                                    Searching the web...
                                </div>`;
                                scrollToBottom();
                            }
                        } else if (data.status === 'recalling') {
                            isRecalling = true;
                            if (isFirstChunk) {
                                // Keep typing indicator showing — create the assistant bubble
                                // but use it to show the recalling status
                                hideTyping();
                                textContentEl = appendMessage('assistant', '');
                                isFirstChunk = false;
                            }
                            if (textContentEl) {
                                textContentEl.innerHTML = `<div class="searching-indicator">
                                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"></circle></svg>
                                    Recalling...
                                </div>`;
                                scrollToBottom();
                            }
                        } else if (data.content) {
                            if (isSearching || isRecalling) {
                                // Second pass started! Reset the output.
                                isSearching = false;
                                isRecalling = false;
                                fullResponse = "";
                                if (textContentEl) textContentEl.innerHTML = "";
                            }
                            
                            if (isFirstChunk) {
                                hideTyping();
                                if (!textContentEl) {
                                    textContentEl = appendMessage('assistant', '');
                                }
                                isFirstChunk = false;
                            }
                            
                            fullResponse += data.content;
                            if (textContentEl && !isSearching && !isRecalling) {
                                // Strip internal tags from display
                                let displayText = fullResponse;
                                if (displayText.includes('<update_character>')) {
                                    displayText = displayText.replace(/<update_character>[\s\S]*?(<\/update_character>|$)/g, '').trim();
                                }
                                if (displayText.includes('<vault_card')) {
                                    displayText = displayText.replace(/<vault_card[\s\S]*?(<\/vault_card>|$)/g, '').trim();
                                }
                                textContentEl.innerHTML = marked.parse(displayText);
                                scrollToBottom();
                            }
                        } else if (data.error) {
                            if (isFirstChunk) {
                                hideTyping();
                                textContentEl = appendMessage('assistant', '');
                                isFirstChunk = false;
                            }
                            fullResponse += `\n\n*Error:* \`${data.error}\``;
                            if (textContentEl) {
                                textContentEl.innerHTML = marked.parse(fullResponse);
                                scrollToBottom();
                            }
                        }
                    } catch (e) {
                        // ignore JSON parse errors from partial chunks
                    }
                }
            }
        }
        
        // If the stream completed but we never got content
        if (isFirstChunk) {
            hideTyping();
        }
        
        chatHistory.push({ role: 'assistant', content: fullResponse });
        
    } catch (e) {
        console.error("Chat error", e);
        hideTyping();
        appendMessage('assistant', `*Frontend Network Error:* \`${e.message}\`\n\nEnsure Flask is running and check the terminal for Python errors.`);
    }
}

// Event Listeners
attachBtn.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = async function(event) {
            currentBase64Image = await resizeImage(event.target.result);
            imagePreview.src = currentBase64Image;
            imagePreviewContainer.classList.remove('hidden');
        };
        reader.readAsDataURL(file);
    }
});

removeImageBtn.addEventListener('click', () => {
    currentBase64Image = null;
    fileInput.value = '';
    imagePreviewContainer.classList.add('hidden');
});

sendBtn.addEventListener('click', () => sendMessage(chatInput.value));

chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(chatInput.value);
    }
});

// Auto-resize textarea
chatInput.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = (this.scrollHeight) + 'px';
});

// Navigation Logic
let currentTabIndex = 0;
const tabs = [tabConversation, tabMemory, tabSettings];
const viewsWrapper = document.getElementById('views-wrapper');

function switchTab(activeTabBtn) {
    const newTabIndex = tabs.indexOf(activeTabBtn);
    currentTabIndex = newTabIndex;
    
    tabs.forEach(btn => btn.classList.remove('active'));
    activeTabBtn.classList.add('active');
    
    // Move the views-wrapper
    viewsWrapper.style.transform = `translateX(-${newTabIndex * 33.333}%)`;
    
    if (activeTabBtn === tabSettings) {
        loadSettings();
    } else if (activeTabBtn === tabConversation) {
        // Hide welcome banner when returning to chat
        const banner = document.getElementById('welcome-banner');
        if (banner) banner.classList.add('hidden');
        
        // Load chat if it's empty (only the typing indicator is present)
        if (chatHistoryEl.children.length <= 1) {
            loadHistory();
        }
        scrollToBottom();
    } else if (activeTabBtn === tabMemory) {
        loadVault();
    }
}

tabConversation.addEventListener('click', () => switchTab(tabConversation));
tabSettings.addEventListener('click', () => switchTab(tabSettings));
tabMemory.addEventListener('click', () => switchTab(tabMemory));

// Settings Logic
async function loadSettings() {
    try {
        const response = await fetch('/api/config');
        const config = await response.json();
        if (config.BRAVE_API_KEY) {
            braveApiKeyInput.value = config.BRAVE_API_KEY;
            updateBraveStatus(config.BRAVE_API_KEY);
        }
    } catch (e) {
        console.error("Failed to load config", e);
    }
    // Also load AI config
    loadAIConfig();
}

// AI Configuration Logic
const configureAiBtn = document.getElementById('configure-ai-btn');
const aiConfigPanel = document.getElementById('ai-config-panel');
const providerGrid = document.getElementById('provider-grid');
const aiFields = document.getElementById('ai-fields');
const aiApiKeyInput = document.getElementById('ai-api-key');
const aiModelSelect = document.getElementById('ai-model-select');
const saveAiBtn = document.getElementById('save-ai-btn');
const aiStatusProvider = document.getElementById('ai-status-provider');
const aiStatusModel = document.getElementById('ai-status-model');

let providerData = {};
let selectedProvider = 'local';

configureAiBtn.addEventListener('click', () => {
    aiConfigPanel.classList.toggle('hidden');
});

// Fetch provider definitions from backend
async function loadProviderData() {
    try {
        const response = await fetch('/api/ai/providers');
        providerData = await response.json();
    } catch (e) {
        console.error("Failed to load provider data", e);
    }
}

function populateModelDropdown(provider) {
    aiModelSelect.innerHTML = '';
    const info = providerData[provider];
    if (info && info.models && info.models.length > 0) {
        for (const model of info.models) {
            const opt = document.createElement('option');
            opt.value = model;
            opt.textContent = model;
            if (model === info.default_model) opt.selected = true;
            aiModelSelect.appendChild(opt);
        }
    }
}

function selectProviderCard(provider) {
    selectedProvider = provider;
    
    // Update active state on cards
    providerGrid.querySelectorAll('.provider-card').forEach(card => {
        card.classList.toggle('active', card.dataset.provider === provider);
    });
    
    // Show/hide API key + model fields (local doesn't need them)
    if (provider === 'local') {
        aiFields.classList.add('hidden');
    } else {
        aiFields.classList.remove('hidden');
        populateModelDropdown(provider);
    }
}

// Attach click handlers to provider cards
providerGrid.querySelectorAll('.provider-card').forEach(card => {
    card.addEventListener('click', () => {
        selectProviderCard(card.dataset.provider);
    });
});

function updateStatusRow(provider, model) {
    const info = providerData[provider];
    const name = info ? info.name : (provider === 'local' ? 'Local Model' : provider);
    aiStatusProvider.textContent = name;
    aiStatusModel.textContent = model ? `Model: ${model}` : '';
}

async function loadAIConfig() {
    await loadProviderData();
    try {
        const response = await fetch('/api/ai/config');
        const config = await response.json();
        
        selectedProvider = config.provider || 'local';
        selectProviderCard(selectedProvider);
        
        if (config.api_key) {
            aiApiKeyInput.value = config.api_key;
        }
        if (config.model) {
            aiModelSelect.value = config.model;
        }
        
        updateStatusRow(selectedProvider, config.model);
    } catch (e) {
        console.error("Failed to load AI config", e);
    }
}

saveAiBtn.addEventListener('click', async () => {
    saveAiBtn.textContent = 'Saving...';
    try {
        const payload = {
            provider: selectedProvider,
            api_key: selectedProvider === 'local' ? '' : aiApiKeyInput.value,
            model: selectedProvider === 'local' ? '' : aiModelSelect.value,
        };
        await fetch('/api/ai/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        updateStatusRow(payload.provider, payload.model);
        aiConfigPanel.classList.add('hidden');
        
        saveAiBtn.textContent = 'Saved!';
        setTimeout(() => saveAiBtn.textContent = 'Save AI Configuration', 2000);
    } catch (e) {
        saveAiBtn.textContent = 'Error';
        setTimeout(() => saveAiBtn.textContent = 'Save AI Configuration', 2000);
    }
});

// Brave Search Configuration Logic
const configureBraveBtn = document.getElementById('configure-brave-btn');
const braveConfigPanel = document.getElementById('brave-config-panel');
const braveStatusDetail = document.getElementById('brave-status-detail');

configureBraveBtn.addEventListener('click', () => {
    braveConfigPanel.classList.toggle('hidden');
});

function updateBraveStatus(key) {
    if (key && key.trim()) {
        braveStatusDetail.textContent = 'Connected';
    } else {
        braveStatusDetail.textContent = 'Not configured';
    }
}

saveSettingsBtn.addEventListener('click', async () => {
    saveSettingsBtn.textContent = 'Saving...';
    try {
        // Preserve existing config keys (like AI settings)
        const existingConfig = await (await fetch('/api/config')).json();
        existingConfig.BRAVE_API_KEY = braveApiKeyInput.value;
        
        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(existingConfig)
        });
        
        updateBraveStatus(braveApiKeyInput.value);
        braveConfigPanel.classList.add('hidden');
        
        saveSettingsBtn.textContent = 'Saved!';
        setTimeout(() => saveSettingsBtn.textContent = 'Save Search Configuration', 2000);
    } catch (e) {
        saveSettingsBtn.textContent = 'Error';
        setTimeout(() => saveSettingsBtn.textContent = 'Save Search Configuration', 2000);
    }
});

// Accordion (How It Works) Logic
document.querySelectorAll('.accordion-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
        const targetId = btn.dataset.target;
        const panel = document.getElementById(targetId);
        if (panel) {
            const isOpen = !panel.classList.contains('hidden');
            panel.classList.toggle('hidden');
            btn.classList.toggle('open', !isOpen);
        }
    });
});

// --- Security / Passphrase / WebAuthn ---

const lockScreen = document.getElementById('lock-screen');
const lockSubtitle = document.getElementById('lock-subtitle');
const lockPassphraseInput = document.getElementById('lock-passphrase');
const lockUnlockBtn = document.getElementById('lock-unlock-btn');
const lockError = document.getElementById('lock-error');
const lockBiometric = document.getElementById('lock-biometric');
const lockBiometricBtn = document.getElementById('lock-biometric-btn');
const lockDivider = document.getElementById('lock-divider');
const passphraseStatus = document.getElementById('passphrase-status');
const setPassphraseBtn = document.getElementById('set-passphrase-btn');
const passphraseConfig = document.getElementById('passphrase-config');
const savePassphraseBtn = document.getElementById('save-passphrase-btn');
const passphraseError = document.getElementById('passphrase-error');
const changePassphraseConfig = document.getElementById('change-passphrase-config');
const saveChangePassphraseBtn = document.getElementById('save-change-passphrase-btn');
const changePassphraseError = document.getElementById('change-passphrase-error');
const biometricStatusRow = document.getElementById('biometric-status-row');
const biometricStatus = document.getElementById('biometric-status');
const enableBiometricBtn = document.getElementById('enable-biometric-btn');

// WebAuthn helpers: base64url <-> ArrayBuffer
function base64urlToBuffer(base64url) {
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
    const pad = base64.length % 4 === 0 ? '' : '='.repeat(4 - (base64.length % 4));
    const binary = atob(base64 + pad);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
}

function bufferToBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function showPassphraseEnabled() {
    passphraseStatus.textContent = 'Enabled';
    setPassphraseBtn.textContent = 'Change';
    setPassphraseBtn.disabled = false;
    setPassphraseBtn.style.opacity = '';
    setPassphraseBtn.style.cursor = '';
    setPassphraseBtn.dataset.mode = 'change';
}

function showBiometricEnabled() {
    biometricStatusRow.classList.remove('hidden');
    biometricStatus.textContent = 'Enabled';
    enableBiometricBtn.textContent = 'Configured';
    enableBiometricBtn.disabled = true;
    enableBiometricBtn.style.opacity = '0.4';
    enableBiometricBtn.style.cursor = 'default';
}

async function checkAuthStatus() {
    try {
        const response = await fetch('/api/auth/status');
        const status = await response.json();

        // Store setup state for post-auth redirect
        appNeedsSetup = status.needs_setup || false;
        appNeedsOnboarding = status.needs_onboarding || false;

        // Update settings security status
        if (status.has_passphrase) {
            showPassphraseEnabled();
            biometricStatusRow.classList.remove('hidden');
        }
        if (status.has_webauthn) {
            showBiometricEnabled();
        }

        // Show lock screen if server locked OR session not authenticated
        if (status.has_passphrase && (status.is_locked || !status.session_authenticated)) {
            lockScreen.classList.remove('hidden');
            lockError.classList.add('hidden');
            lockBiometric.classList.add('hidden');
            lockDivider.classList.add('hidden');

            // If WebAuthn is available, show biometric button and auto-trigger
            if (status.has_webauthn && !status.is_locked) {
                lockBiometric.classList.remove('hidden');
                lockDivider.classList.remove('hidden');
                // Auto-trigger biometric after a brief delay
                setTimeout(() => attemptBiometricAuth(), 300);
            } else {
                lockPassphraseInput.focus();
            }
            return false;
        }

        return true;
    } catch (e) {
        console.error('Auth check failed', e);
        return true;
    }
}

// --- Biometric (WebAuthn) Authentication ---

async function attemptBiometricAuth() {
    lockError.classList.add('hidden');
    lockBiometricBtn.textContent = 'Authenticating...';
    lockBiometricBtn.disabled = true;

    try {
        // Get authentication options from server
        const optRes = await fetch('/api/auth/webauthn/auth/options', { method: 'POST' });
        if (!optRes.ok) throw new Error('Failed to get options');
        const options = await optRes.json();

        // Convert base64url fields to ArrayBuffers for WebAuthn API
        const publicKey = {
            challenge: base64urlToBuffer(options.challenge),
            rpId: options.rpId,
            timeout: options.timeout,
            userVerification: options.userVerification,
            allowCredentials: (options.allowCredentials || []).map(c => ({
                type: c.type,
                id: base64urlToBuffer(c.id),
            })),
        };

        // Trigger Windows Hello / TouchID
        const credential = await navigator.credentials.get({ publicKey });

        // Serialize response for server
        const body = {
            id: credential.id,
            rawId: bufferToBase64url(credential.rawId),
            response: {
                authenticatorData: bufferToBase64url(credential.response.authenticatorData),
                clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
                signature: bufferToBase64url(credential.response.signature),
                userHandle: credential.response.userHandle ? bufferToBase64url(credential.response.userHandle) : null,
            },
            type: credential.type,
            authenticatorAttachment: credential.authenticatorAttachment || null,
        };

        // Verify with server
        const verifyRes = await fetch('/api/auth/webauthn/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const result = await verifyRes.json();

        if (result.success) {
            lockScreen.classList.add('hidden');
            showPassphraseEnabled();
            showBiometricEnabled();
            // Re-check setup state after auth
            const statusRes = await fetch('/api/auth/status');
            const statusData = await statusRes.json();
            appNeedsSetup = statusData.needs_setup || false;
            appNeedsOnboarding = statusData.needs_onboarding || false;
            handlePostAuth();
            return;
        }
    } catch (e) {
        console.log('Biometric auth failed or cancelled:', e.message);
    }

    // Reset button
    lockBiometricBtn.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Unlock with Biometrics`;
    lockBiometricBtn.disabled = false;
    lockPassphraseInput.focus();
}

lockBiometricBtn.addEventListener('click', attemptBiometricAuth);

// --- Biometric (WebAuthn) Registration ---

async function registerBiometric() {
    enableBiometricBtn.textContent = 'Registering...';
    enableBiometricBtn.disabled = true;

    try {
        // Get registration options from server
        const optRes = await fetch('/api/auth/webauthn/register/options', { method: 'POST' });
        if (!optRes.ok) throw new Error('Failed to get options');
        const options = await optRes.json();

        // Convert base64url fields to ArrayBuffers
        const publicKey = {
            challenge: base64urlToBuffer(options.challenge),
            rp: options.rp,
            user: {
                ...options.user,
                id: base64urlToBuffer(options.user.id),
            },
            pubKeyCredParams: options.pubKeyCredParams,
            timeout: options.timeout,
            attestation: options.attestation,
            authenticatorSelection: options.authenticatorSelection,
            excludeCredentials: (options.excludeCredentials || []).map(c => ({
                type: c.type,
                id: base64urlToBuffer(c.id),
            })),
        };

        // Trigger Windows Hello / TouchID registration
        const credential = await navigator.credentials.create({ publicKey });

        // Serialize response for server
        const body = {
            id: credential.id,
            rawId: bufferToBase64url(credential.rawId),
            response: {
                attestationObject: bufferToBase64url(credential.response.attestationObject),
                clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
            },
            type: credential.type,
            authenticatorAttachment: credential.authenticatorAttachment || null,
        };

        // Verify with server
        const verifyRes = await fetch('/api/auth/webauthn/register/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const result = await verifyRes.json();

        if (result.success) {
            showBiometricEnabled();
            return;
        } else {
            alert(result.error || 'Registration failed');
        }
    } catch (e) {
        console.error('Biometric registration failed:', e);
        alert('Biometric registration failed: ' + (e.name === 'NotAllowedError' ? 'The operation was cancelled or not allowed. Make sure Windows Hello is configured in Windows Settings.' : e.message));
    }

    enableBiometricBtn.textContent = 'Enable';
    enableBiometricBtn.disabled = false;
}

enableBiometricBtn.addEventListener('click', registerBiometric);

// Lock screen unlock
lockUnlockBtn.addEventListener('click', async () => {
    const passphrase = lockPassphraseInput.value;
    if (!passphrase) return;
    
    lockUnlockBtn.textContent = 'Unlocking...';
    lockUnlockBtn.disabled = true;
    lockError.classList.add('hidden');
    
    try {
        const response = await fetch('/api/auth/unlock', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ passphrase })
        });
        const result = await response.json();
        
        if (result.success) {
            lockScreen.classList.add('hidden');
            showPassphraseEnabled();
            // Re-check setup state after auth
            const statusRes = await fetch('/api/auth/status');
            const statusData = await statusRes.json();
            appNeedsSetup = statusData.needs_setup || false;
            appNeedsOnboarding = statusData.needs_onboarding || false;
            handlePostAuth();
        } else {
            lockError.classList.remove('hidden');
            lockScreen.classList.add('shake');
            setTimeout(() => lockScreen.classList.remove('shake'), 500);
            lockPassphraseInput.value = '';
            lockPassphraseInput.focus();
        }
    } catch (e) {
        lockError.classList.remove('hidden');
    }
    
    lockUnlockBtn.textContent = 'Unlock';
    lockUnlockBtn.disabled = false;
});

// Enter key on lock screen
lockPassphraseInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') lockUnlockBtn.click();
});

// Settings: Set passphrase / Change passphrase toggle
setPassphraseBtn.addEventListener('click', () => {
    if (setPassphraseBtn.dataset.mode === 'change') {
        changePassphraseConfig.classList.toggle('hidden');
        passphraseConfig.classList.add('hidden');
    } else {
        passphraseConfig.classList.toggle('hidden');
        changePassphraseConfig.classList.add('hidden');
    }
});

savePassphraseBtn.addEventListener('click', async () => {
    const passphrase = document.getElementById('new-passphrase').value;
    const confirm = document.getElementById('confirm-passphrase').value;
    
    passphraseError.classList.add('hidden');
    
    if (!passphrase || passphrase.length < 4) {
        passphraseError.textContent = 'Passphrase must be at least 4 characters.';
        passphraseError.classList.remove('hidden');
        return;
    }
    if (passphrase !== confirm) {
        passphraseError.textContent = 'Passphrases do not match.';
        passphraseError.classList.remove('hidden');
        return;
    }
    
    savePassphraseBtn.textContent = 'Encrypting...';
    savePassphraseBtn.disabled = true;
    
    try {
        const response = await fetch('/api/auth/set-passphrase', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ passphrase })
        });
        const result = await response.json();
        
        if (result.success) {
            showPassphraseEnabled();
            passphraseConfig.classList.add('hidden');
            document.getElementById('new-passphrase').value = '';
            document.getElementById('confirm-passphrase').value = '';
            // First-time setup: always go to settings for AI provider config
            appNeedsSetup = true;
            appNeedsOnboarding = true;
            handlePostAuth();
        } else {
            passphraseError.textContent = result.error || 'Failed to set passphrase.';
            passphraseError.classList.remove('hidden');
        }
    } catch (e) {
        passphraseError.textContent = 'Connection error.';
        passphraseError.classList.remove('hidden');
    }
    
    savePassphraseBtn.textContent = 'Enable Encryption';
    savePassphraseBtn.disabled = false;
});

saveChangePassphraseBtn.addEventListener('click', async () => {
    const oldPass = document.getElementById('old-passphrase').value;
    const newPass = document.getElementById('change-new-passphrase').value;
    const confirmPass = document.getElementById('change-confirm-passphrase').value;
    
    changePassphraseError.classList.add('hidden');
    
    if (!oldPass) {
        changePassphraseError.textContent = 'Enter your current passphrase.';
        changePassphraseError.classList.remove('hidden');
        return;
    }
    if (!newPass || newPass.length < 4) {
        changePassphraseError.textContent = 'New passphrase must be at least 4 characters.';
        changePassphraseError.classList.remove('hidden');
        return;
    }
    if (newPass !== confirmPass) {
        changePassphraseError.textContent = 'New passphrases do not match.';
        changePassphraseError.classList.remove('hidden');
        return;
    }
    
    saveChangePassphraseBtn.textContent = 'Re-encrypting...';
    saveChangePassphraseBtn.disabled = true;
    
    try {
        const response = await fetch('/api/auth/change-passphrase', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_passphrase: oldPass, new_passphrase: newPass })
        });
        const result = await response.json();
        
        if (result.success) {
            changePassphraseConfig.classList.add('hidden');
            document.getElementById('old-passphrase').value = '';
            document.getElementById('change-new-passphrase').value = '';
            document.getElementById('change-confirm-passphrase').value = '';
            saveChangePassphraseBtn.textContent = 'Changed!';
            setTimeout(() => { saveChangePassphraseBtn.textContent = 'Change Passphrase'; }, 2000);
        } else {
            changePassphraseError.textContent = result.error || 'Failed to change passphrase.';
            changePassphraseError.classList.remove('hidden');
        }
    } catch (e) {
        changePassphraseError.textContent = 'Connection error.';
        changePassphraseError.classList.remove('hidden');
    }
    
    saveChangePassphraseBtn.textContent = 'Change Passphrase';
    saveChangePassphraseBtn.disabled = false;
});

// Memory Vault Logic
async function loadVault() {
    try {
        const response = await fetch('/api/vault');
        const cards = await response.json();
        
        masonryGrid.innerHTML = '';
        
        for (const card of cards) {
            const cardEl = document.createElement('div');
            cardEl.className = `vault-card ${card.type}`;
            
            // Format date string (e.g. "2026-04-18 21:00:00" -> "APRIL 18, 2026 • 21:00")
            let dateStr = card.timestamp;
            try {
                const d = new Date(card.timestamp + 'Z');
                const months = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];
                dateStr = `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()} • ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
            } catch (e) {}
            if (card.type === 'image' && card.content.startsWith('IMAGE:')) {
                cardEl.classList.add('image-card');
                const msgId = card.content.split(':')[1];
                const imgEl = document.createElement('img');
                imgEl.alt = card.title || 'Memory';
                imgEl.style.opacity = '0';
                imgEl.style.transition = 'opacity 0.3s';
                imgEl.onload = function() { imgEl.style.opacity = '1'; };
                imgEl.onerror = function() { cardEl.remove(); };
                
                fetch('/api/vault/image/' + msgId)
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.image_url) {
                            imgEl.src = data.image_url;
                        } else {
                            cardEl.remove();
                        }
                    })
                    .catch(function() { cardEl.remove(); });
                
                cardEl.appendChild(imgEl);
            } else {
                let html = '';
                html += '<div class="card-meta">' + dateStr + '</div>';
                
                let contentText = card.content;
                
                if (card.type === 'quote') {
                    // Quotes: NO title, just the quoted text
                    if (!contentText.startsWith('"')) {
                        contentText = '"' + contentText + '"';
                    }
                    html += '<div class="card-content card-quote">' + marked.parse(contentText) + '</div>';
                } else {
                    // Summaries: ALWAYS show title if present
                    if (card.title) {
                        html += '<div class="card-title">' + card.title + '</div>';
                    }
                    html += '<div class="card-content">' + marked.parse(contentText) + '</div>';
                }
                
                cardEl.innerHTML = html;
            }
            
            // Add delete button (top-right)
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'vault-delete-btn';
            deleteBtn.innerHTML = '&times;';
            deleteBtn.title = 'Remove memory';
            deleteBtn.addEventListener('click', async function(e) {
                e.stopPropagation();
                try {
                    await fetch('/api/vault/' + card.id, { method: 'DELETE' });
                    cardEl.style.opacity = '0';
                    cardEl.style.transform = 'scale(0.9)';
                    setTimeout(function() { cardEl.remove(); }, 200);
                } catch (err) {
                    console.error('Failed to delete card', err);
                }
            });
            cardEl.appendChild(deleteBtn);

            // Add "talk about this memory" button (bottom-left)
            if (card.type !== 'image') {
                const talkBtn = document.createElement('button');
                talkBtn.className = 'vault-talk-btn';
                talkBtn.title = 'Talk about this memory';
                talkBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
                talkBtn.addEventListener('click', function(e) {
                    e.stopPropagation();

                    let memoryDescription = '';
                    if (card.type === 'quote') {
                        memoryDescription = `a quote: ${card.content}`;
                    } else if (card.type === 'dream') {
                        memoryDescription = `a dream snippet: ${card.content}`;
                    } else {
                        const titlePart = card.title ? `"${card.title}" — ` : '';
                        memoryDescription = `a memory titled ${titlePart}${card.content}`;
                    }

                    const hiddenPrompt = `(The Partner has opened a memory from their Memory Vault and wishes to talk about it. The memory card is ${memoryDescription}. Please use your recall tool to find the full context behind this memory, then warmly open the conversation — knowing it was the Partner who specifically chose to revisit this moment.)`;

                    switchTab(tabConversation);
                    setTimeout(() => sendMessage(hiddenPrompt, true), 150);
                });
                cardEl.appendChild(talkBtn);
            } else {
                // Image cards: fetch the image and send it as a multimodal message
                const talkBtn = document.createElement('button');
                talkBtn.className = 'vault-talk-btn';
                talkBtn.title = 'Talk about this memory';
                talkBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
                talkBtn.addEventListener('click', async function(e) {
                    e.stopPropagation();

                    const msgId = card.content.split(':')[1];
                    try {
                        const r = await fetch('/api/vault/image/' + msgId);
                        const data = await r.json();
                        if (!data.image_url) return;

                        // Resize/compress the image before sending to avoid token overflow
                        const resized = await resizeImage(data.image_url);

                        // Build a multimodal hidden message: image + text instruction
                        const titleContext = card.title ? `titled "${card.title}"` : 'a meaningful moment';
                        const hiddenText = `(The Partner has selected an image from their Memory Vault — ${titleContext}. Please look at this image carefully and use your recall tool to find the memories and conversations surrounding it, then warmly open the conversation — knowing it was the Partner who chose to revisit this moment.)`;

                        // Switch tab and send with image
                        switchTab(tabConversation);
                        setTimeout(() => {
                            currentBase64Image = resized;
                            sendMessage(hiddenText, true);
                        }, 150);
                    } catch (err) {
                        console.error('Failed to fetch vault image', err);
                    }
                });
                cardEl.appendChild(talkBtn);
            }

            masonryGrid.appendChild(cardEl);

        }
        
    } catch (e) {
        console.error("Failed to load vault", e);
        masonryGrid.innerHTML = '<p style="color: #8E8E93">Failed to load memories.</p>';
    }
}

// Initial Setup
const ONBOARDING_PROMPT = `This is your very first conversation with your new Partner. They have just finished setting up the app and are meeting you for the first time.

Welcome them warmly and naturally. Introduce yourself briefly. Then:
1. Ask what they would like to call you (you can suggest a few names, or they can pick their own)
2. Ask how they would like to be addressed
3. Get to know them - ask about their interests, what they are looking for in a companion
4. Ask if they have any preferences for how you communicate (tone, formality, humor level)

Be warm, curious, and genuine. Don't rush - this is the beginning of something meaningful. Ask one or two questions at a time, not all at once.`;

async function loadHistory() {
    try {
        const response = await fetch('/api/history');
        const history = await response.json();
        
        if (history.length === 0) {
            // No history — check server if onboarding is needed
            try {
                const statusRes = await fetch('/api/auth/status');
                const status = await statusRes.json();
                if (status.needs_onboarding) {
                    sendMessage(ONBOARDING_PROMPT, true);
                } else {
                    sendMessage("(The Partner has just arrived. Please initiate the conversation.)", true);
                }
            } catch {
                sendMessage("(The Partner has just arrived. Please initiate the conversation.)", true);
            }
        } else {
            for (const msg of history) {
                if (msg.role === 'user') {
                    if (msg.content !== "(The Partner has just arrived. Please initiate the conversation.)" &&
                        !String(msg.content).startsWith('This is your very first conversation')) {
                        let uiContent = msg.content;
                        if (Array.isArray(msg.content)) {
                            const textObj = msg.content.find(c => c.type === 'text');
                            const imgObj = msg.content.find(c => c.type === 'image_url');
                            uiContent = imgObj ? `![Attached Image](${imgObj.image_url.url})\n\n${textObj ? textObj.text : ''}` : (textObj ? textObj.text : '');
                        }
                        appendMessage('user', uiContent);
                    }
                } else if (msg.role === 'assistant') {
                    appendMessage('assistant', msg.content);
                }
            }
            scrollToBottom();
        }
    } catch (e) {
        console.error("Failed to load history", e);
    }
}

function handlePostAuth() {
    loadSettings();
    if (appNeedsSetup) {
        // First run: redirect to settings for AI provider configuration
        switchTab(tabSettings);
        const banner = document.getElementById('welcome-banner');
        if (banner) banner.classList.remove('hidden');
    } else {
        loadHistory();
    }
}

async function initApp() {
    const unlocked = await checkAuthStatus();
    if (unlocked) {
        handlePostAuth();
    }
}

initApp();

// Theme Switcher Logic
const themeSelect = document.getElementById('theme-select');
if (themeSelect) {
    // Set initial value from localStorage
    const currentTheme = localStorage.getItem('theme') || 'system';
    themeSelect.value = currentTheme;

    themeSelect.addEventListener('change', (e) => {
        const theme = e.target.value;
        localStorage.setItem('theme', theme);
        
        if (theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
            document.documentElement.setAttribute('data-theme', 'dark');
        } else {
            document.documentElement.setAttribute('data-theme', 'light');
        }
    });

    // Listen for OS theme changes if 'system' is selected
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
        if (localStorage.getItem('theme') === 'system' || !localStorage.getItem('theme')) {
            if (e.matches) {
                document.documentElement.setAttribute('data-theme', 'dark');
            } else {
                document.documentElement.setAttribute('data-theme', 'light');
            }
        }
    });
}

// Factory Reset Logic
const resetBtn = document.getElementById('reset-confidant-btn');
const resetDialog = document.getElementById('reset-dialog');
const resetStep1 = document.getElementById('reset-step-1');
const resetStep2 = document.getElementById('reset-step-2');
const resetCancel1 = document.getElementById('reset-cancel-1');
const resetNext = document.getElementById('reset-next');
const resetCancel2 = document.getElementById('reset-cancel-2');
const resetConfirm = document.getElementById('reset-confirm');

function openResetDialog() {
    // Always start at step 1
    resetStep1.classList.remove('hidden');
    resetStep2.classList.add('hidden');
    resetConfirm.disabled = false;
    resetDialog.showModal();
}

function closeResetDialog() {
    resetDialog.close();
}

if (resetBtn) {
    resetBtn.addEventListener('click', openResetDialog);
}
if (resetCancel1) {
    resetCancel1.addEventListener('click', closeResetDialog);
}
if (resetNext) {
    resetNext.addEventListener('click', () => {
        resetStep1.classList.add('hidden');
        resetStep2.classList.remove('hidden');
    });
}
if (resetCancel2) {
    resetCancel2.addEventListener('click', closeResetDialog);
}
if (resetConfirm) {
    resetConfirm.addEventListener('click', async () => {
        resetConfirm.disabled = true;
        resetConfirm.textContent = 'Resetting...';
        try {
            const res = await fetch('/api/factory-reset', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                // Reload the page — it will come back as a fresh first-run setup
                window.location.reload();
            } else {
                resetConfirm.disabled = false;
                resetConfirm.textContent = 'Reset Everything';
                alert('Reset failed: ' + (data.error || 'Unknown error'));
            }
        } catch (e) {
            resetConfirm.disabled = false;
            resetConfirm.textContent = 'Reset Everything';
            alert('Reset failed: ' + e.message);
        }
    });
}

// Close dialog if backdrop is clicked
if (resetDialog) {
    resetDialog.addEventListener('click', (e) => {
        if (e.target === resetDialog) closeResetDialog();
    });
}
