/**
 * Freya Quinn - Mini App JavaScript
 * Handles all user flows: IP check, verification, phone, invite generation
 */

// =============================================================================
// Configuration
// =============================================================================

const CONFIG = {
    API_BASE: '', // Same origin, will be proxied by nginx
    SUPPORT_CONTACT: '@cogitosk',
    GIVEAWAY_CHANNEL: 'https://t.me/Freya_Trades',
};

// =============================================================================
// State Management
// =============================================================================

const state = {
    tgUserId: null,
    tgUser: null,
    initData: null,
    ipCheckPassed: false,
    ipCheckBypassed: false,
    verificationData: null,
    currentScreen: 'welcome',
};

// =============================================================================
// Telegram WebApp Integration
// =============================================================================

const TG = window.Telegram?.WebApp;

function initTelegram() {
    if (!TG) {
        console.warn('Telegram WebApp not available');
        showError('Please open this app from Telegram', 'This app only works inside Telegram.');
        return false;
    }

    // Expand to full height
    TG.expand();
    
    // Apply Telegram theme (optional - we use our own dark theme)
    TG.setHeaderColor('#000000');
    TG.setBackgroundColor('#000000');
    
    // Get user data
    if (TG.initDataUnsafe?.user) {
        state.tgUser = TG.initDataUnsafe.user;
        state.tgUserId = TG.initDataUnsafe.user.id;
        state.initData = TG.initData;
        console.log('Telegram user:', state.tgUserId);
    } else {
        console.warn('No Telegram user data');
        // For testing: check URL params
        const urlParams = new URLSearchParams(window.location.search);
        const testTgId = urlParams.get('tg_id');
        if (testTgId) {
            state.tgUserId = parseInt(testTgId);
            console.log('Using test tg_id:', state.tgUserId);
        }
    }
    
    // Enable closing confirmation if needed
    TG.enableClosingConfirmation();
    
    return true;
}

function hapticFeedback(type = 'light') {
    if (TG?.HapticFeedback) {
        if (type === 'success') {
            TG.HapticFeedback.notificationOccurred('success');
        } else if (type === 'error') {
            TG.HapticFeedback.notificationOccurred('error');
        } else if (type === 'warning') {
            TG.HapticFeedback.notificationOccurred('warning');
        } else {
            TG.HapticFeedback.impactOccurred(type);
        }
    }
}

function closeMiniApp() {
    if (TG) {
        TG.close();
    }
}

// =============================================================================
// API Calls
// =============================================================================

async function apiCall(endpoint, method = 'GET', data = null) {
    const url = `${CONFIG.API_BASE}/api${endpoint}`;
    const options = {
        method,
        headers: {
            'Content-Type': 'application/json',
        },
    };
    
    // Add initData for authentication
    if (state.initData) {
        options.headers['X-Telegram-Init-Data'] = state.initData;
    }
    
    // Add tg_id as query param for GET requests
    if (method === 'GET' && state.tgUserId) {
        const urlObj = new URL(url, window.location.origin);
        urlObj.searchParams.set('tg_id', state.tgUserId);
        options.url = urlObj.toString();
    }
    
    if (data) {
        data.tg_id = state.tgUserId;
        options.body = JSON.stringify(data);
    }
    
    try {
        const finalUrl = method === 'GET' && state.tgUserId 
            ? `${url}?tg_id=${state.tgUserId}` 
            : url;
        const response = await fetch(finalUrl, options);
        const result = await response.json();
        
        if (!response.ok) {
            throw new Error(result.error || 'API request failed');
        }
        
        return result;
    } catch (error) {
        console.error('API error:', error);
        throw error;
    }
}

// =============================================================================
// Screen Management
// =============================================================================

function showScreen(screenId) {
    // Hide all screens
    document.querySelectorAll('.screen').forEach(screen => {
        screen.classList.remove('active');
    });
    
    // Show target screen
    const targetScreen = document.getElementById(`screen-${screenId}`);
    if (targetScreen) {
        targetScreen.classList.add('active');
        state.currentScreen = screenId;
        hapticFeedback('light');
    }
}

function showLoading() {
    document.getElementById('loading-overlay').classList.remove('hidden');
}

function hideLoading() {
    document.getElementById('loading-overlay').classList.add('hidden');
}

function showError(title, message) {
    document.getElementById('error-title').textContent = title;
    document.getElementById('error-message').textContent = message;
    showScreen('error');
    hapticFeedback('error');
}

// =============================================================================
// Flow: Check User Status
// =============================================================================

async function checkUserStatus() {
    if (!state.tgUserId) {
        showError('Authentication Error', 'Could not identify your Telegram account. Please reopen the app.');
        return;
    }
    
    try {
        const result = await apiCall('/user/status');
        
        if (result.has_used_trial) {
            // User already used trial
            showScreen('used');
            return;
        }
        
        if (result.has_active_trial) {
            // User has active trial - show status
            document.getElementById('elapsed-time').textContent = `${result.elapsed_hours} hours`;
            document.getElementById('remaining-time').textContent = `${result.remaining_hours} hours`;
            showScreen('active');
            return;
        }
        
        // New user - start verification flow
        showScreen('welcome');
        
    } catch (error) {
        console.error('Status check error:', error);
        // On error, show welcome screen and let them try
        showScreen('welcome');
    }
}

// =============================================================================
// Flow: IP Check
// =============================================================================

async function performIPCheck() {
    showScreen('ip-check');
    
    try {
        const result = await apiCall('/verify/ip');
        
        if (result.is_vpn) {
            document.getElementById('ip-error-title').textContent = 'VPN Detected';
            document.getElementById('ip-error-message').textContent = 
                'Please turn off your VPN or proxy and try again.';
            showScreen('ip-error');
            hapticFeedback('error');
            return false;
        }
        
        if (result.is_blocked_country) {
            document.getElementById('ip-error-title').textContent = 'Region Not Supported';
            document.getElementById('ip-error-message').textContent = 
                'Sorry, this trial is not available in your region.';
            showScreen('ip-error');
            hapticFeedback('error');
            return false;
        }
        
        // IP check passed (or bypassed due to API failure)
        state.ipCheckPassed = true;
        state.ipCheckBypassed = result.bypassed || false;
        
        hapticFeedback('success');
        showScreen('verify');
        return true;
        
    } catch (error) {
        console.error('IP check error:', error);
        // Fail-open: allow user to continue but mark as bypassed
        state.ipCheckPassed = true;
        state.ipCheckBypassed = true;
        showScreen('verify');
        return true;
    }
}

// =============================================================================
// Flow: Verification Form
// =============================================================================

async function submitVerification(formData) {
    showLoading();
    
    try {
        const result = await apiCall('/verify/submit', 'POST', {
            name: formData.name,
            country: formData.country,
            email: formData.email,
            marketing_opt_in: formData.marketing,
            ip_check_bypassed: state.ipCheckBypassed,
        });
        
        if (result.success) {
            state.verificationData = formData;
            hapticFeedback('success');
            hideLoading();
            showScreen('phone');
            return true;
        } else {
            hideLoading();
            showError('Verification Failed', result.error || 'Please try again.');
            return false;
        }
        
    } catch (error) {
        console.error('Verification error:', error);
        hideLoading();
        showError('Verification Failed', 'Something went wrong. Please try again.');
        return false;
    }
}

// =============================================================================
// Flow: Phone Verification
// =============================================================================

async function requestPhoneNumber() {
    if (!TG) {
        showError('Not Available', 'Phone verification requires the Telegram app.');
        return;
    }
    
    // Use Telegram's native phone request
    TG.requestContact((sent, event) => {
        if (sent && event?.responseUnsafe?.contact) {
            const contact = event.responseUnsafe.contact;
            handlePhoneReceived(contact.phone_number);
        } else {
            // User cancelled or error
            hapticFeedback('warning');
        }
    });
}

async function handlePhoneReceived(phoneNumber) {
    showLoading();
    
    try {
        const result = await apiCall('/verify/phone', 'POST', {
            phone: phoneNumber,
        });
        
        if (result.success) {
            hapticFeedback('success');
            hideLoading();
            
            // Get invite link
            await generateInviteLink();
        } else {
            hideLoading();
            
            if (result.blocked) {
                showError('Phone Not Eligible', 'Your phone number is not eligible for this trial.');
            } else {
                showError('Verification Failed', result.error || 'Please try again.');
            }
        }
        
    } catch (error) {
        console.error('Phone verification error:', error);
        hideLoading();
        showError('Verification Failed', 'Something went wrong. Please try again.');
    }
}

// =============================================================================
// Flow: Generate Invite Link
// =============================================================================

async function generateInviteLink() {
    showLoading();
    
    try {
        const result = await apiCall('/trial/invite', 'POST', {});
        
        if (result.success && result.invite_link) {
            hideLoading();
            
            // Set the invite link
            const inviteBtn = document.getElementById('invite-link');
            inviteBtn.href = result.invite_link;
            inviteBtn.onclick = (e) => {
                // Open invite link in Telegram
                if (TG) {
                    TG.openTelegramLink(result.invite_link);
                    e.preventDefault();
                }
            };
            
            hapticFeedback('success');
            showScreen('success');
        } else if (result.already_has_link) {
            hideLoading();
            
            // User already has a valid link
            const inviteBtn = document.getElementById('invite-link');
            inviteBtn.href = result.invite_link;
            inviteBtn.onclick = (e) => {
                if (TG) {
                    TG.openTelegramLink(result.invite_link);
                    e.preventDefault();
                }
            };
            
            showScreen('success');
        } else {
            hideLoading();
            showError('Failed to Generate Link', result.error || 'Please try again.');
        }
        
    } catch (error) {
        console.error('Invite generation error:', error);
        hideLoading();
        showError('Failed to Generate Link', 'Something went wrong. Please try again.');
    }
}

// =============================================================================
// Event Handlers
// =============================================================================

function setupEventHandlers() {
    // Start Trial button
    document.getElementById('btn-start-trial')?.addEventListener('click', () => {
        hapticFeedback('medium');
        performIPCheck();
    });
    
    // Retry IP check
    document.getElementById('btn-retry-ip')?.addEventListener('click', () => {
        hapticFeedback('light');
        performIPCheck();
    });
    
    // Verification form
    document.getElementById('verify-form')?.addEventListener('submit', (e) => {
        e.preventDefault();
        
        const formData = {
            name: document.getElementById('input-name').value.trim(),
            country: document.getElementById('input-country').value,
            email: document.getElementById('input-email').value.trim(),
            marketing: document.getElementById('input-marketing').checked,
        };
        
        if (!formData.name || !formData.country) {
            hapticFeedback('error');
            return;
        }
        
        hapticFeedback('medium');
        submitVerification(formData);
    });
    
    // Share phone button
    document.getElementById('btn-share-phone')?.addEventListener('click', () => {
        hapticFeedback('medium');
        requestPhoneNumber();
    });
    
    // Close app button
    document.getElementById('btn-close-app')?.addEventListener('click', () => {
        closeMiniApp();
    });
    
    // Retry button on error screen
    document.getElementById('btn-retry')?.addEventListener('click', () => {
        hapticFeedback('light');
        showScreen('welcome');
    });
}

// =============================================================================
// Initialization
// =============================================================================

async function init() {
    console.log('Initializing Freya Quinn Mini App...');
    
    // Initialize Telegram WebApp
    if (!initTelegram()) {
        return;
    }
    
    // Setup event handlers
    setupEventHandlers();
    
    // Check user status and show appropriate screen
    await checkUserStatus();
    
    // Signal ready to Telegram
    if (TG) {
        TG.ready();
    }
    
    console.log('Mini App initialized');
}

// Start the app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
