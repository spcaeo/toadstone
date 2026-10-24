// Settings Management
let currentSettings = {};
let defaultSettings = {
    // Crawler settings
    maxDepth: 3,
    maxUrls: 5000000,
    crawlDelay: 1,
    followRedirects: true,
    crawlExternalLinks: false,
    ignoreTrackingParams: true,
    keepLastCrawls: 0,
    auditSitemaps: true,
    analyseLinkGraph: true,
    headerChecks: true,
    auditCms: true,
    cmsExpectedAuthor: '',
    renderedChecks: false,
    webhookUrl: '',
    mainTextIgnoreSelectors: 'header, nav, footer, aside, .cookie, .breadcrumb',
    linkClusters: '',
    staleAfterMonths: 12,
    stripQueryParams: 'utm_*, gclid, gbraid, wbraid, fbclid, msclkid, mc_cid, mc_eid, _ga, _gl, ref, igshid, yclid, dclid, ttclid, li_fat_id, s_kwcid, vero_id, hsa_*',

    // Request settings
    userAgent: 'Toadstone/1.0 (Web Crawler)',
    timeout: 10,
    retries: 3,
    acceptLanguage: 'en-US,en;q=0.9',
    respectRobotsTxt: true,
    allowCookies: true,
    discoverSitemaps: true,
    enablePageSpeed: false,
    googleApiKey: '',

    // Filter settings
    includeExtensions: 'html,htm,php,asp,aspx,jsp',
    excludeExtensions: 'pdf,doc,docx,zip,exe,dmg',
    includePatterns: '',
    excludePatterns: '',
    maxFileSize: 50,
    crawlImages: false,

    // Duplication detection settings
    enableDuplicationCheck: true,
    duplicationThreshold: 0.85,

    // Link metrics. Empty means the four authority checks do not run, which
    // is the correct behaviour for an install with no subscription: nothing
    // is guessed and nothing is reported either way.
    mozAccessId: '',
    mozSecretKey: '',
    majesticApiKey: '',
    ahrefsApiToken: '',
    linkMetricsMaxUrls: 200,

    // Export settings
    exportFormat: 'csv',
    exportFields: ['url', 'status_code', 'title', 'meta_description', 'h1', 'word_count', 'response_time', 'analytics', 'og_tags', 'json_ld', 'internal_links', 'external_links', 'images'],

    // Advanced settings
    concurrency: 5,
    memoryLimit: 512,
    logLevel: 'INFO',
    saveSession: false,
    enableProxy: false,
    proxyUrl: '',
    customHeaders: '',

    // JavaScript rendering settings
    enableJavaScript: false,
    jsWaitTime: 3,
    jsTimeout: 30,
    jsBrowser: 'chromium',
    jsHeadless: true,
    jsUserAgent: 'Toadstone/1.0 (Web Crawler with JavaScript)',
    jsViewportWidth: 1920,
    jsViewportHeight: 1080,
    jsMaxConcurrentPages: 3,

    // Custom CSS styling
    customCSS: '',

    // Issue exclusion patterns
    issueExclusionPatterns: `# WordPress admin & system paths
/wp-admin/*
/wp-content/plugins/*
/wp-content/themes/*
/wp-content/uploads/*
/wp-includes/*
/wp-login.php
/wp-cron.php
/xmlrpc.php
/wp-json/*
/wp-activate.php
/wp-signup.php
/wp-trackback.php

# Auth & user management pages
/login*
/signin*
/sign-in*
/log-in*
/auth/*
/authenticate/*
/register*
/signup*
/sign-up*
/registration/*
/logout*
/signout*
/sign-out*
/log-out*
/forgot-password*
/reset-password*
/password-reset*
/recover-password*
/change-password*
/account/password/*
/user/password/*
/activate/*
/verification/*
/verify/*
/confirm/*

# Admin panels & dashboards
/admin/*
/administrator/*
/_admin/*
/backend/*
/dashboard/*
/cpanel/*
/phpmyadmin/*
/pma/*
/webmail/*
/plesk/*
/control-panel/*
/manage/*
/manager/*

# E-commerce checkout & cart
/checkout/*
/cart/*
/basket/*
/payment/*
/billing/*
/order/*
/orders/*
/purchase/*

# User account pages
/account/*
/profile/*
/settings/*
/preferences/*
/my-account/*
/user/*
/member/*
/members/*

# CGI & server scripts
/cgi-bin/*
/cgi/*
/fcgi-bin/*

# Version control & config
/.git/*
/.svn/*
/.hg/*
/.bzr/*
/.cvs/*
/.env
/.env.*
/.htaccess
/.htpasswd
/web.config
/app.config
/composer.json
/package.json

# Development & build artifacts
/node_modules/*
/vendor/*
/bower_components/*
/jspm_packages/*
/includes/*
/lib/*
/libs/*
/src/*
/dist/*
/build/*
/builds/*
/_next/*
/.next/*
/out/*
/_nuxt/*
/.nuxt/*

# Testing & development
/test/*
/tests/*
/spec/*
/specs/*
/__tests__/*
/debug/*
/dev/*
/development/*
/staging/*

# API internal endpoints
/api/internal/*
/api/admin/*
/api/private/*

# System & internal
/private/*
/system/*
/core/*
/internal/*
/tmp/*
/temp/*
/cache/*
/logs/*
/log/*
/backup/*
/backups/*
/old/*
/archive/*
/archives/*
/config/*
/configs/*
/configuration/*

# Media upload forms
/upload/*
/uploads/*
/uploader/*
/file-upload/*

# Search & filtering (often noisy for SEO)
/search*
*/search/*
?s=*
?search=*
*/filter/*
?filter=*
*/sort/*
?sort=*

# Printer-friendly & special views
/print/*
?print=*
/preview/*
?preview=*
/embed/*
?embed=*
/amp/*
/amp

# Feed URLs
/feed/*
/feeds/*
/rss/*
*.rss
/atom/*
*.atom

# Common file types to exclude from issues
*.json
*.xml
*.yaml
*.yml
*.toml
*.ini
*.conf
*.log
*.txt
*.csv
*.sql
*.db
*.bak
*.backup
*.old
*.orig
*.tmp
*.swp
*.map
*.min.js
*.min.css`
};

// Initialize settings when page loads
document.addEventListener('DOMContentLoaded', function() {
    loadSettings();
    setupSettingsEventHandlers();
    applyCustomCSS();
});

function setupSettingsEventHandlers() {
    // Proxy checkbox handler
    const enableProxyCheckbox = document.getElementById('enableProxy');
    if (enableProxyCheckbox) {
        enableProxyCheckbox.addEventListener('change', function() {
            const proxySettings = document.getElementById('proxySettings');
            if (proxySettings) {
                proxySettings.style.display = this.checked ? 'block' : 'none';
            }
        });
    }

    // JavaScript checkbox handler
    const enableJavaScriptCheckbox = document.getElementById('enableJavaScript');
    if (enableJavaScriptCheckbox) {
        enableJavaScriptCheckbox.addEventListener('change', function() {
            const jsSettingsGroups = [
                'jsSettings', 'jsTimeoutGroup', 'jsBrowserGroup', 'jsHeadlessGroup',
                'jsUserAgentGroup', 'jsViewportGroup', 'jsConcurrencyGroup', 'jsWarning'
            ];

            jsSettingsGroups.forEach(groupId => {
                const group = document.getElementById(groupId);
                if (group) {
                    group.style.display = this.checked ? 'block' : 'none';
                }
            });
        });
    }
}

function resetIssueExclusions() {
    // Always use the hardcoded defaults, not current settings
    document.getElementById('issueExclusionPatterns').value = defaultSettings.issueExclusionPatterns;
    alert('Issue exclusion patterns have been reset to defaults');
}

async function openSettings() {
    // Get user tier info
    let userTier = 'guest';
    try {
        const response = await fetch('/api/user/info');
        const data = await response.json();
        if (data.success) {
            userTier = data.user.tier;
        }
    } catch (error) {
        console.error('Failed to get user tier:', error);
    }

    // Block guests from accessing settings
    if (userTier === 'guest') {
        alert('Settings are not available for guest users.\n\nPlease register for a free account to customize crawler settings, filters, and more.\n\nClick "Logout" and then "Register here" to create an account.');
        return;
    }

    // Hide tabs based on tier
    applyTierRestrictions(userTier);

    // Load current settings into form
    populateSettingsForm();

    // Show modal
    document.getElementById('settingsModal').style.display = 'flex';

    // Focus first input
    const firstInput = document.querySelector('.settings-tab-content.active input, .settings-tab-content.active select');
    if (firstInput) {
        setTimeout(() => firstInput.focus(), 100);
    }
}

function applyTierRestrictions(tier) {
    // Define which tabs each tier can see - MUST MATCH HTML TAB NAMES
    // 'google' is in every signed-in tier: the connection is stored per
    // user, so each person connects their own account rather than sharing one.
    //
    // This list is a trap and has sprung twice: a tab missing from it is not
    // restricted, it is invisible, with nothing anywhere saying why. The
    // Google tab was hidden from every tier that way this morning, and
    // Integrations went the same way the hour it was added. A browser test
    // now asserts that every tab in the markup appears in the admin list, so
    // the next omission fails a test instead of silently removing a feature.
    const tierTabs = {
        'guest': [],  // No settings tabs for guests
        'user': ['crawler', 'google', 'export', 'issues'],
        'extra': ['crawler', 'google', 'export', 'issues', 'filters', 'requests', 'customcss', 'javascript'],
        'admin': ['crawler', 'google', 'requests', 'filters', 'export', 'javascript', 'issues', 'customcss', 'integrations', 'advanced']
    };

    const allowedTabs = tierTabs[tier] || [];

    // Hide/show tab buttons based on tier
    const allTabButtons = document.querySelectorAll('.settings-tab-btn');
    allTabButtons.forEach(btn => {
        const tabName = btn.getAttribute('onclick').match(/switchSettingsTab\('(.+?)'\)/)[1];
        if (allowedTabs.includes(tabName)) {
            btn.style.display = 'inline-block';
        } else {
            btn.style.display = 'none';
        }
    });

    // If current active tab is not allowed, switch to first allowed tab
    const activeTab = document.querySelector('.settings-tab-btn.active');
    if (activeTab && activeTab.style.display === 'none' && allowedTabs.length > 0) {
        // Click the first visible tab
        const firstVisibleTab = document.querySelector('.settings-tab-btn[style*="inline-block"]');
        if (firstVisibleTab) {
            firstVisibleTab.click();
        }
    }

    // Show message for guests
    if (tier === 'guest') {
        const settingsContent = document.querySelector('.settings-tabs');
        if (settingsContent) {
            const message = document.createElement('div');
            message.style.cssText = 'padding: 40px; text-align: center; color: #9ca3af; font-size: 16px;';
            message.innerHTML = `
                <h3 style="color: #f3f4f6; margin-bottom: 16px;">Settings Access Restricted</h3>
                <p>Guest accounts cannot modify settings.</p>
                <p style="margin-top: 8px; font-size: 14px;">Please upgrade your account to access settings.</p>
            `;
            settingsContent.innerHTML = '';
            settingsContent.appendChild(message);
        }
    }
}

function closeSettings() {
    document.getElementById('settingsModal').style.display = 'none';
}

function switchSettingsTab(tabName) {
    // Remove active class from all tabs and content
    document.querySelectorAll('.settings-tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelectorAll('.settings-tab-content').forEach(content => {
        content.classList.remove('active');
    });

    // Add active class to selected tab and content (looked up by name so the
    // router can switch tabs without a click event)
    document.querySelector(`.settings-tab-btn[onclick="switchSettingsTab('${tabName}')"]`)?.classList.add('active');
    document.getElementById(tabName + '-settings').classList.add('active');

    // The MCP block shows this install's own endpoint and whether it answers,
    // so it is filled when the panel is opened rather than on page load.
    if (tabName === 'integrations' && typeof refreshMcp === 'function') {
        refreshMcp();
    }
}

function populateSettingsForm() {
    // Populate all form fields with current settings
    Object.keys(currentSettings).forEach(key => {
        const element = document.getElementById(key);
        if (element) {
            if (element.type === 'checkbox') {
                element.checked = currentSettings[key];
            } else {
                element.value = currentSettings[key];
            }
        }
    });

    // Handle export fields checkboxes
    const exportFieldsCheckboxes = document.querySelectorAll('input[name="exportFields"]');
    exportFieldsCheckboxes.forEach(checkbox => {
        checkbox.checked = currentSettings.exportFields.includes(checkbox.value);
    });

    // Show/hide proxy settings
    const enableProxy = currentSettings.enableProxy;
    const proxySettings = document.getElementById('proxySettings');
    if (proxySettings) {
        proxySettings.style.display = enableProxy ? 'block' : 'none';
    }

    // Show/hide JavaScript settings
    const enableJavaScript = currentSettings.enableJavaScript;
    const jsSettingsGroups = [
        'jsSettings', 'jsTimeoutGroup', 'jsBrowserGroup', 'jsHeadlessGroup',
        'jsUserAgentGroup', 'jsViewportGroup', 'jsConcurrencyGroup', 'jsWarning'
    ];

    jsSettingsGroups.forEach(groupId => {
        const group = document.getElementById(groupId);
        if (group) {
            group.style.display = enableJavaScript ? 'block' : 'none';
        }
    });

    applyAgentMode();
}

/* The agent settings only mean anything once a mode is chosen, and the CLI
 * command only means anything in CLI mode. Showing all four at once invites
 * someone to fill in a model name and wonder why nothing happens. */
function applyAgentMode() {
    const mode = (document.getElementById('agentMode') || {}).value || 'none';
    document.querySelectorAll('.agent-setting').forEach(group => {
        group.style.display = mode === 'none' ? 'none' : '';
    });
    const cli = document.getElementById('agentCliCommandGroup');
    if (cli) cli.style.display = mode === 'cli' ? '' : 'none';

    const help = document.getElementById('agentModeHelp');
    if (help) {
        help.textContent = {
            none: 'Agent findings are skipped entirely.',
            api: 'Page text is sent to the Anthropic API. Needs ANTHROPIC_API_KEY '
                 + 'in the environment; calls are billed per token.',
            cli: 'Page text is sent to a local Claude process, which bills to that '
                 + 'installation\'s own account.'
        }[mode] || '';
    }
}

document.addEventListener('change', event => {
    if (event.target && event.target.id === 'agentMode') applyAgentMode();
});

function collectSettingsFromForm() {
    const settings = {};

    // Collect regular form fields
    const formFields = [
        'maxDepth', 'maxUrls', 'crawlDelay', 'followRedirects', 'crawlExternalLinks',
        'ignoreTrackingParams', 'stripQueryParams', 'keepLastCrawls',
        'auditSitemaps', 'analyseLinkGraph', 'auditCms', 'cmsExpectedAuthor',
        'headerChecks',
        'renderedChecks', 'webhookUrl', 'staleAfterMonths',
        'mainTextIgnoreSelectors', 'linkClusters',
        'userAgent', 'timeout', 'retries', 'acceptLanguage', 'respectRobotsTxt', 'allowCookies', 'discoverSitemaps', 'enablePageSpeed', 'googleApiKey',
        'includeExtensions', 'excludeExtensions', 'includePatterns', 'excludePatterns', 'maxFileSize', 'crawlImages',
        'enableDuplicationCheck', 'duplicationThreshold',
        'exportFormat', 'concurrency', 'memoryLimit', 'logLevel', 'saveSession',
        'enableProxy', 'proxyUrl', 'customHeaders',
        'enableJavaScript', 'jsWaitTime', 'jsTimeout', 'jsBrowser', 'jsHeadless', 'jsUserAgent', 'jsViewportWidth', 'jsViewportHeight', 'jsMaxConcurrentPages',
        'customCSS', 'issueExclusionPatterns',
        'agentMode', 'agentModel', 'agentMaxPages', 'agentCliCommand',
        'mozAccessId', 'mozSecretKey', 'majesticApiKey', 'ahrefsApiToken',
        'linkMetricsMaxUrls'
    ];

    formFields.forEach(fieldId => {
        const element = document.getElementById(fieldId);
        if (element) {
            if (element.type === 'checkbox') {
                settings[fieldId] = element.checked;
            } else if (element.type === 'number') {
                settings[fieldId] = parseFloat(element.value) || 0;
            } else {
                settings[fieldId] = element.value;
            }
        }
    });

    // Collect export fields
    const exportFieldsCheckboxes = document.querySelectorAll('input[name="exportFields"]:checked');
    settings.exportFields = Array.from(exportFieldsCheckboxes).map(cb => cb.value);

    return settings;
}

function saveSettings() {
    // Collect settings from form
    const newSettings = collectSettingsFromForm();

    // Validate settings
    const validation = validateSettings(newSettings);
    if (!validation.valid) {
        alert('Settings validation failed: ' + validation.errors.join(', '));
        return;
    }

    // Save to localStorage first (primary storage for persistence)
    try {
        localStorage.setItem('toadstone_settings', JSON.stringify(newSettings));
        console.log('Settings saved to localStorage');
    } catch (error) {
        console.error('Failed to save to localStorage:', error);
        showNotification('Warning: Settings may not persist', 'warning');
    }

    // Update current settings
    currentSettings = { ...newSettings };

    // Apply custom CSS immediately
    applyCustomCSS();

    // Close settings modal
    closeSettings();
    showNotification('Settings saved successfully', 'success');

    // Sync to backend for crawler configuration
    fetch('/api/save_settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(newSettings)
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            // The server copy drives crawls started later, so a failed sync
            // must not hide behind the "saved" notification above.
            const reason = data.message || data.error || 'unknown error';
            console.warn('Backend sync failed:', reason);
            showNotification('Settings saved in this browser, but the server did not accept them: ' + reason, 'warning');
        }

        // Update crawler with new settings if it's running
        if (window.crawlState && window.crawlState.isRunning) {
            updateCrawlerSettings();
        }
    })
    .catch(error => {
        console.error('Error syncing settings to backend:', error);
        showNotification('Settings saved in this browser, but syncing them to the server failed', 'warning');
    });
}

function resetSettings() {
    if (confirm('Are you sure you want to reset all settings to their default values?')) {
        currentSettings = { ...defaultSettings };

        // Clear localStorage
        try {
            localStorage.removeItem('toadstone_settings');
            console.log('Settings cleared from localStorage');
        } catch (error) {
            console.error('Failed to clear localStorage:', error);
        }

        populateSettingsForm();
        applyCustomCSS(); // Remove any custom CSS
        showNotification('Settings reset to defaults', 'info');

        // Sync reset to backend
        syncSettingsToBackend();
    }
}

function validateSettings(settings) {
    const errors = [];

    // Validate numeric ranges
    if (settings.maxDepth < 1 || settings.maxDepth > 10) {
        errors.push('Max depth must be between 1 and 10');
    }

    if (settings.maxUrls < 1 || settings.maxUrls > 5000000) {
        errors.push('Max URLs must be between 1 and 5,000,000');
    }

    if (settings.crawlDelay < 0 || settings.crawlDelay > 60) {
        errors.push('Crawl delay must be between 0 and 60 seconds');
    }

    if (settings.timeout < 1 || settings.timeout > 120) {
        errors.push('Timeout must be between 1 and 120 seconds');
    }

    if (settings.retries < 0 || settings.retries > 10) {
        errors.push('Retries must be between 0 and 10');
    }

    if (settings.maxFileSize < 1 || settings.maxFileSize > 1000) {
        errors.push('Max file size must be between 1 and 1000 MB');
    }

    if (settings.concurrency < 1 || settings.concurrency > 50) {
        errors.push('Concurrency must be between 1 and 50');
    }

    if (settings.memoryLimit < 64 || settings.memoryLimit > 4096) {
        errors.push('Memory limit must be between 64 and 4096 MB');
    }

    // Validate duplication detection settings
    if (settings.duplicationThreshold < 0 || settings.duplicationThreshold > 1) {
        errors.push('Duplication threshold must be between 0.0 and 1.0');
    }

    // Validate JavaScript settings if enabled
    if (settings.enableJavaScript) {
        if (settings.jsWaitTime < 0 || settings.jsWaitTime > 30) {
            errors.push('JavaScript wait time must be between 0 and 30 seconds');
        }

        if (settings.jsTimeout < 5 || settings.jsTimeout > 120) {
            errors.push('JavaScript timeout must be between 5 and 120 seconds');
        }

        if (settings.jsViewportWidth < 800 || settings.jsViewportWidth > 4000) {
            errors.push('JavaScript viewport width must be between 800 and 4000 pixels');
        }

        if (settings.jsViewportHeight < 600 || settings.jsViewportHeight > 3000) {
            errors.push('JavaScript viewport height must be between 600 and 3000 pixels');
        }

        if (settings.jsMaxConcurrentPages < 1 || settings.jsMaxConcurrentPages > 10) {
            errors.push('JavaScript concurrent pages must be between 1 and 10');
        }

        if (!settings.jsUserAgent.trim()) {
            errors.push('JavaScript user agent cannot be empty');
        }
    }

    // Validate proxy URL if proxy is enabled
    if (settings.enableProxy && settings.proxyUrl) {
        try {
            new URL(settings.proxyUrl);
        } catch (e) {
            errors.push('Invalid proxy URL format');
        }
    }

    // Validate user agent
    if (!settings.userAgent.trim()) {
        errors.push('User agent cannot be empty');
    }

    // Validate export fields
    if (settings.exportFields.length === 0) {
        errors.push('At least one export field must be selected');
    }

    return {
        valid: errors.length === 0,
        errors: errors
    };
}

function loadSettings() {
    // Try to load from localStorage first (browser-specific persistence)
    try {
        const savedSettings = localStorage.getItem('toadstone_settings');
        if (savedSettings) {
            const parsed = JSON.parse(savedSettings);
            currentSettings = { ...defaultSettings, ...parsed };
            console.log('Settings loaded from localStorage');

            // Apply custom CSS after loading settings
            applyCustomCSS();

            // Sync to backend for crawler configuration
            syncSettingsToBackend();
            return;
        }
    } catch (error) {
        console.warn('Failed to load settings from localStorage:', error);
    }

    // Fallback: Load from backend (legacy support)
    fetch('/api/get_settings')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                currentSettings = { ...defaultSettings, ...data.settings };
                // Save to localStorage for future loads
                localStorage.setItem('toadstone_settings', JSON.stringify(currentSettings));
                // Apply custom CSS after loading settings
                applyCustomCSS();
            } else {
                console.warn('Failed to load settings, using defaults');
                currentSettings = { ...defaultSettings };
            }
        })
        .catch(error => {
            console.error('Error loading settings:', error);
            currentSettings = { ...defaultSettings };
        });
}

function syncSettingsToBackend() {
    // Send settings to backend without waiting for response
    // This ensures crawler gets the right config
    fetch('/api/save_settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(currentSettings)
    }).catch(error => {
        console.warn('Failed to sync settings to backend:', error);
    });
}

function updateCrawlerSettings() {
    // Send updated settings to crawler
    fetch('/api/update_crawler_settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(currentSettings)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log('Crawler settings updated');
        } else {
            console.warn('Failed to update crawler settings:', data.error);
        }
    })
    .catch(error => {
        console.error('Error updating crawler settings:', error);
    });
}

function exportSettings() {
    // Create downloadable settings file
    const settingsBlob = new Blob([JSON.stringify(currentSettings, null, 2)], {
        type: 'application/json'
    });

    const url = URL.createObjectURL(settingsBlob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'toadstone-settings.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function importSettings(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const importedSettings = JSON.parse(e.target.result);

            // Validate imported settings
            const validation = validateSettings(importedSettings);
            if (!validation.valid) {
                alert('Invalid settings file: ' + validation.errors.join(', '));
                return;
            }

            // Merge with defaults to ensure all fields are present
            currentSettings = { ...defaultSettings, ...importedSettings };
            populateSettingsForm();
            showNotification('Settings imported successfully', 'success');

        } catch (error) {
            alert('Invalid settings file format');
        }
    };
    reader.readAsText(file);
}

// ---- Toasts ---------------------------------------------------------------
//
// One element per message, stacked bottom-right, announced through
// role="status", dismissed automatically (errors linger) or by the close
// button. The class names `notification` and `notification-<type>` are the
// contract with the stylesheet and are kept, but nothing here writes colours
// or a position inline any more: inline styles beat every stylesheet rule,
// which is exactly why the old gradients could not be restyled.

const TOAST_TIMEOUTS = { info: 4000, success: 4000, warning: 6000, error: 8000 };
// Names from icons.js; the tint comes from .notification-<type>, never from
// the icon itself
const TOAST_ICONS = { info: 'info', success: 'check-circle', warning: 'alert-triangle', error: 'alert-circle' };

// icons.js draws these; the text fallback keeps the toast readable if it is
// ever missing
function addToastIcon(element, name, fallback) {
    const node = typeof iconElement === 'function' ? iconElement(name, { size: 14 }) : null;
    if (node) element.appendChild(node);
    else if (fallback) element.textContent = fallback;
}

function toastContainer() {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        document.body.appendChild(container);
    }
    // .notification-stack is what the stylesheet positions (and what turns the
    // toasts from fixed elements stacked on one spot into a column);
    // .toast-container is the name the redesign spec uses for the same element.
    // Added here rather than only on creation, because the shell's markup may
    // ship the container with just one of them.
    container.classList.add('notification-stack', 'toast-container');
    // Safety net for a stylesheet that has no .toast-container rule yet -
    // without it the toasts would pile up at the bottom of the document.
    // Once the rule exists, nothing is written inline.
    if (getComputedStyle(container).position !== 'fixed') {
        container.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:1001;' +
            'display:flex;flex-direction:column;gap:8px;align-items:flex-end;pointer-events:none;';
    }
    return container;
}

function dismissNotification(toast) {
    if (!toast || toast.dataset.dismissing === 'true') return;
    toast.dataset.dismissing = 'true';
    toast.classList.remove('notification-visible');
    toast.classList.add('notification-leaving');
    setTimeout(() => toast.remove(), 220);
}

function showNotification(message, type = 'info') {
    const kind = Object.prototype.hasOwnProperty.call(TOAST_TIMEOUTS, type) ? type : 'info';
    const container = toastContainer();

    const toast = document.createElement('div');
    toast.className = `notification notification-${kind} toast`;
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');

    const glyph = document.createElement('span');
    glyph.className = 'notification-icon toast-icon';
    glyph.setAttribute('aria-hidden', 'true');
    addToastIcon(glyph, TOAST_ICONS[kind]);
    toast.appendChild(glyph);

    const text = document.createElement('span');
    text.className = 'notification-message toast-message';
    text.textContent = message;
    toast.appendChild(text);

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'notification-close toast-close';
    close.setAttribute('aria-label', 'Dismiss notification');
    addToastIcon(close, 'x', '\u00d7');
    close.addEventListener('click', () => dismissNotification(toast));
    toast.appendChild(close);

    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('notification-visible'));

    let timer = setTimeout(() => dismissNotification(toast), TOAST_TIMEOUTS[kind]);
    toast.addEventListener('mouseenter', () => clearTimeout(timer));
    toast.addEventListener('mouseleave', () => {
        timer = setTimeout(() => dismissNotification(toast), 1500);
    });

    return toast;
}

// Close modal when clicking outside
document.addEventListener('click', function(event) {
    const modal = document.getElementById('settingsModal');
    if (event.target === modal) {
        closeSettings();
    }
});

// Close modal with Escape key
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        const modal = document.getElementById('settingsModal');
        if (modal.style.display === 'flex') {
            closeSettings();
        }
    }
});

// Export current settings object for use by other modules
window.getCurrentSettings = function() {
    return currentSettings;
};

// Apply custom CSS to the page
function applyCustomCSS() {
    // Remove existing custom CSS if present
    const existingStyle = document.getElementById('custom-user-styles');
    if (existingStyle) {
        existingStyle.remove();
    }

    // Get custom CSS from settings
    const customCSS = currentSettings.customCSS || '';

    // Only inject if there's CSS to apply
    if (customCSS.trim()) {
        const styleElement = document.createElement('style');
        styleElement.id = 'custom-user-styles';
        styleElement.textContent = customCSS;
        document.head.appendChild(styleElement);
        console.log('Custom CSS applied');
    }
}