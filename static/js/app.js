// Application State
let crawlState = {
    isRunning: false,
    isPaused: false,
    startTime: null,
    baseUrl: null,
    urls: [],
    links: [],
    issues: [],
    stats: {
        discovered: 0,
        crawled: 0,
        depth: 0,
        speed: 0
    },
    filters: {
        // active is the comma-joined form of activeList ("4xx", "4xx,html",
        // null) and stays a string: the router and the URL both read it
        active: null,
        activeList: [],
        issueFilter: 'all',
        linksFilter: {
            internalStatusCode: 'all',
            externalStatusCode: 'all',
            internalSearch: '',
            externalSearch: ''
        }
    }
};

// Incremental polling instance
let incrementalPoller = null;

// Virtual Scrollers
let virtualScrollers = {
    overview: null,
    internal: null,
    external: null,
    internalLinks: null,
    externalLinks: null,
    issues: null
};

// Initialize application
document.addEventListener('DOMContentLoaded', async function() {
    await initializeApp();
});

async function initializeApp() {
    // Load plugins first (before tabs are initialized)
    if (window.ToadstonePlugin && window.ToadstonePlugin.loader) {
        await window.ToadstonePlugin.loader.loadAllPlugins();
        window.ToadstonePlugin.loader.initializePlugins();
    }

    // Setup event listeners
    setupEventListeners();

    // Theme / density controls and the active-filter chip row
    setupPreferenceControls();

    // The Export button's menu
    setupExportMenu();

    // Initialize tables
    initializeTables();

    // Load user info
    loadUserInfo();

    // Check if we just loaded a crawl from dashboard
    if (sessionStorage.getItem('force_ui_refresh') === 'true') {
        sessionStorage.removeItem('force_ui_refresh');
        await hydrateFromServer();
    }

    // Restore the view described by the URL (crawl, tab, filters, modals)
    if (window.Router) {
        await window.Router.init();
    }

    // The Issues tab's grouping and filter controls, and the view the URL asks for
    initIssuesView();

    updateAllTableStates();
    renderFilterChips();

    // Set initial focus
    document.getElementById('urlInput')?.focus();

    console.log('Toadstone initialized');
}

// Rebuild the UI from the session's current crawl on the server (a crawl loaded
// from the dashboard, a deep link, or a running crawl after a page reload)
async function hydrateFromServer() {
    try {
        // Skeleton rows while the crawl is on the wire: a spinner says less
        // than the shape of the table that is coming
        setTablesLoading(true);

        // Fetch the loaded data immediately with FULL refresh (no incremental)
        const response = await fetch('/api/crawl_status');
        const data = await response.json();

        // Clear existing data first
        clearAllTables();
        resetStats();

        // Force populate all data
        crawlState.urls = [];
        crawlState.links = data.links || [];
        crawlState.issues = data.issues || [];
        crawlState.stats = data.stats || {};
        crawlState.baseUrl = data.stats?.baseUrl || '';

        // Set URL input
        if (crawlState.baseUrl) {
            document.getElementById('urlInput').value = crawlState.baseUrl;
        }

        // Add each URL to tables
        if (data.urls && data.urls.length > 0) {
            data.urls.forEach(url => addUrlToTable(url));
        }

        // Load links if present
        if (data.links && data.links.length > 0) {
            crawlState.pendingLinks = data.links;
            // If links tab is active, load them immediately
            if (isLinksTabActive()) {
                updateLinksTable(data.links);
            }
        }

        // Load issues if present
        if (data.issues && data.issues.length > 0) {
            crawlState.pendingIssues = data.issues;
            // If issues tab is active, load them immediately
            if (isIssuesTabActive()) {
                updateIssuesTable(data.issues);
            } else {
                // Update badge count even if tab not active
                updateIssuesTabBadge(data.issues);
            }
        }

        // Update all displays. The data-size panel is otherwise only filled
        // by the live poller, so a loaded crawl reported 0 MB for its own rows.
        updateStatsDisplay();
        updateMemoryDisplay(data.memory, data.memory_data);
        updateFilterCounts();
        updateStatusCodesTable();
        updateCrawlButtons();

        // Check if the crawl is currently running (resumed from dashboard)
        if (data.status === 'running') {
            // Set crawl state to running
            crawlState.isRunning = true;
            crawlState.isPaused = false;
            crawlState.startTime = new Date(); // Set start time to now for timer

            // Show progress UI
            showProgress();

            // Update buttons for running state
            updateCrawlButtons();

            // Start polling for updates
            updateStatus('Crawl resumed - updating...');
            pollCrawlProgress();
        } else {
            // Crawl is not running, just loaded data
            updateStatus(`Loaded crawl: ${data.stats.crawled} URLs, ${data.links?.length || 0} links, ${data.issues?.length || 0} issues`);
        }

        console.log('Loaded crawl from database:', {
            urls: data.urls?.length || 0,
            links: data.links?.length || 0,
            issues: data.issues?.length || 0,
            stats: data.stats,
            status: data.status,
            isRunning: crawlState.isRunning
        });
    } catch (error) {
        console.error('Error loading crawl data:', error);
        updateStatus('Error loading crawl data');
    } finally {
        setTablesLoading(false);
        updateAllTableStates();
    }
}

function setupEventListeners() {
    // URL input enter key
    document.getElementById('urlInput').addEventListener('keypress', handleUrlKeypress);

    // Update timer every second when crawling
    setInterval(updateTimer, 1000);
}

function handleUrlKeypress(event) {
    if (event.key === 'Enter' && !crawlState.isRunning) {
        toggleCrawl();
    }
}

function toggleCrawl() {
    if (!crawlState.isRunning) {
        startCrawl();
    } else if (crawlState.isPaused) {
        resumeCrawl();
    } else {
        pauseCrawl();
    }
}

function startCrawl() {
    const urlInput = document.getElementById('urlInput');
    let url = urlInput.value.trim();

    if (!url) {
        alert('Please enter a URL to crawl');
        urlInput.focus();
        return;
    }

    // Normalize the URL - add protocol if missing
    url = normalizeUrl(url);

    if (!isValidUrl(url)) {
        alert('Please enter a valid URL or domain');
        urlInput.focus();
        return;
    }

    // Update the input field with the normalized URL
    urlInput.value = url;

    crawlState.isRunning = true;
    crawlState.isPaused = false;
    crawlState.startTime = new Date();
    crawlState.baseUrl = url;

    // Initialize incremental poller for new crawl
    if (!incrementalPoller) {
        incrementalPoller = new IncrementalPoller();
    }
    incrementalPoller.reset();

    // Update UI
    updateCrawlButtons();
    showProgress();
    updateStatus('Starting crawl...');

    // Clear previous data
    clearAllTables();
    resetStats();

    // Start the actual crawling via Python backend
    startPythonCrawl(url);
}

function pauseCrawl() {
    crawlState.isPaused = true;
    updateCrawlButtons();
    updateStatus('Crawl paused');

    // Pause Python crawler
    fetch('/api/pause_crawl', {
        method: 'POST'
    }).catch(error => {
        console.error('Error pausing crawl:', error);
    });
}

function resumeCrawl() {
    crawlState.isPaused = false;
    updateCrawlButtons();
    updateStatus('Resuming crawl...');

    // Resume Python crawler
    fetch('/api/resume_crawl', {
        method: 'POST'
    }).catch(error => {
        console.error('Error resuming crawl:', error);
    });
}

function stopCrawl() {
    crawlState.isRunning = false;
    crawlState.isPaused = false;

    // Update UI
    updateCrawlButtons();
    hideProgress();
    updateStatus('Crawl stopped');

    // Stop Python crawler
    stopPythonCrawl();
}

function clearCrawlData() {
    if (crawlState.isRunning) {
        if (!confirm('A crawl is currently running. Stop the crawl and clear all data?')) {
            return;
        }
        stopCrawl();
    }

    // Clear all data
    clearAllTables();
    resetStats();
    crawlState.urls = [];
    crawlState.links = [];
    crawlState.issues = [];
    crawlState.baseUrl = null;
    crawlState.filters.active = null;
    crawlState.pendingLinks = null;
    crawlState.pendingIssues = null;
    updateStatusCodesTable();

    // Clear issues and reset badge
    window.currentIssues = [];
    updateIssuesTable([]);  // This will also clear the badge

    // Reset issue filter counts
    document.getElementById('issues-all-count').textContent = '(0)';
    document.getElementById('issues-error-count').textContent = '(0)';
    document.getElementById('issues-warning-count').textContent = '(0)';
    document.getElementById('issues-info-count').textContent = '(0)';

    // Clear visualization
    if (typeof window.clearVisualization === 'function') {
        window.clearVisualization();
    }

    // Notify plugins of data clear (send empty data)
    if (window.ToadstonePlugin && window.ToadstonePlugin.loader) {
        window.ToadstonePlugin.loader.notifyDataUpdate({
            urls: [],
            links: [],
            issues: [],
            stats: { discovered: 0, crawled: 0, depth: 0, speed: 0 }
        });
    }

    // Clear filter states
    document.querySelectorAll('.filter-item').forEach(item => {
        item.classList.remove('active');
    });

    // Reset the "All Issues" filter to active
    document.querySelector('[data-filter="all"]')?.classList.add('active');

    // Update UI
    updateStatus('Data cleared');
    hideProgress();
    updateCrawlButtons(); // Update save/load button states

    // Reset URL input
    document.getElementById('urlInput').value = '';
    document.getElementById('urlInput').focus();
}

function startPythonCrawl(url) {
    // Call Python backend to start crawling
    fetch('/api/start_crawl', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: url })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            updateStatus('Crawling in progress...');
            window.Router?.setCrawl(data.crawl_id);
            // Refresh user info to update crawl count
            loadUserInfo();
            // Start polling for updates
            pollCrawlProgress();
        } else {
            updateStatus('Error: ' + data.error);
            stopCrawl();
        }
    })
    .catch(error => {
        console.error('Error starting crawl:', error);
        updateStatus('Error starting crawl');
        stopCrawl();
    });
}

function stopPythonCrawl() {
    fetch('/api/stop_crawl', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        console.log('Crawl stopped:', data);
    })
    .catch(error => {
        console.error('Error stopping crawl:', error);
    });
}

function pollCrawlProgress() {
    if (!crawlState.isRunning) return;

    // The poller carries its own cursor/epoch; on a fresh page it starts at
    // zero and the server replays the running crawl's events from scratch
    if (!incrementalPoller) {
        incrementalPoller = new IncrementalPoller();
    }

    incrementalPoller.fetchUpdate()
        .then(data => {
            updateCrawlData(data);

            // Update bottom status bar based on current state
            if (data.is_running_pagespeed) {
                updateStatus('Running PageSpeed analysis...');
            } else if (data.status === 'running') {
                updateStatus('Crawling in progress...');
            }

            // Update visualization if visualization tab is active
            const vizTab = document.getElementById('visualization-tab');
            if (vizTab && vizTab.classList.contains('active') && typeof loadVisualizationData === 'function') {
                loadVisualizationData();
            }

            if (data.status === 'demo_stopped' || data.demo_stopped) {
                stopCrawl();
                updateStatus('Demo limit reached — crawl data saved');
                showDemoLimitNotification();
                if (typeof loadVisualizationData === 'function') {
                    loadVisualizationData();
                }
            } else if (crawlState.isRunning && data.status !== 'completed') {
                setTimeout(pollCrawlProgress, 1000); // Poll every second
            } else if (data.status === 'completed') {
                stopCrawl();
                updateStatus('Crawl completed');
                // Update visualization one final time when crawl completes
                if (typeof loadVisualizationData === 'function') {
                    loadVisualizationData();
                }
                // Notify plugins that crawl is complete
                if (window.ToadstonePlugin && window.ToadstonePlugin.loader) {
                    window.ToadstonePlugin.loader.notifyCrawlComplete({
                        urls: crawlState.urls,
                        links: crawlState.links,
                        issues: crawlState.issues,
                        stats: crawlState.stats
                    });
                }
            }
        })
        .catch(error => {
            console.error('Error polling crawl status:', error);
            // Continue polling even if there's an error (common on large crawls)
            if (crawlState.isRunning) {
                setTimeout(pollCrawlProgress, 1000);
            }
        });
}

function updateCrawlData(data) {
    // Update statistics
    crawlState.stats = data.stats || crawlState.stats;
    updateStatsDisplay();

    // Update memory statistics
    if (data.memory && data.memory_data) {
        updateMemoryDisplay(data.memory, data.memory_data);
    }

    // The poller reports which collections actually changed this poll, so
    // untouched tables are left alone (a plain snapshot changes everything)
    const changed = data.changed || { urls: true, links: true, issues: true };

    // Sync URL tables from the accumulated array. Updates to existing rows
    // (linked_from, image statuses) come through as replaced objects, so the
    // tables are rebuilt from state rather than append-only.
    if (data.urls && changed.urls) {
        crawlState.urls = data.urls;
        refreshUrlTables();
    }

    // Update links tables only if Links tab is active to improve performance
    if (data.links && changed.links) {
        // Always store links data in crawlState
        crawlState.links = data.links;
        if (isLinksTabActive()) {
            updateLinksTable(data.links);
        } else {
            // Store in pendingLinks for lazy loading when switching to tab
            crawlState.pendingLinks = data.links;
        }
    }

    // Update issues table only if Issues tab is active
    if (data.issues && changed.issues) {
        // Always store issues data in crawlState
        crawlState.issues = data.issues;
        if (isIssuesTabActive()) {
            updateIssuesTable(data.issues);
        } else {
            // Store in pendingIssues for lazy loading when switching to tab
            crawlState.pendingIssues = data.issues;
        }
    }

    if (changed.urls) {
        // Update filter counts
        updateFilterCounts();

        // Update status codes table (respecting active filter)
        updateStatusCodesTable(crawlState.filters.active);
    }

    // Update progress and status text
    updateProgress(data.progress || 0);
    updateProgressText(data);

    // Update PageSpeed results if available
    if (data.stats && data.stats.pagespeed_results) {
        displayPageSpeedResults(data.stats.pagespeed_results);
    }

    // Notify plugins of data update
    if ((changed.urls || changed.links || changed.issues) &&
        window.ToadstonePlugin && window.ToadstonePlugin.loader) {
        window.ToadstonePlugin.loader.notifyDataUpdate({
            urls: crawlState.urls,
            links: crawlState.links,
            issues: crawlState.issues,
            stats: crawlState.stats
        });
    }
}

// Rebuild the URL virtual scrollers from crawlState.urls, honoring the
// active sidebar filter if one is set
function refreshUrlTables() {
    if (crawlState.filters.active) {
        applyFilter(crawlState.filters.active);
        return;
    }

    setScrollerData('overview', crawlState.urls);
    setScrollerData('internal', crawlState.urls.filter(url => url.is_internal));
    setScrollerData('external', crawlState.urls.filter(url => !url.is_internal));
}

function updateProgressText(data) {
    const progressText = document.getElementById('progressText');
    if (!progressText) return;

    if (data.is_running_pagespeed) {
        progressText.textContent = 'Running PageSpeed analysis...';
    } else if (data.status === 'completed') {
        progressText.textContent = 'Crawl completed';
    } else if (data.status === 'running') {
        const stats = data.stats || crawlState.stats;
        if (stats.crawled === 0) {
            progressText.textContent = 'Starting crawl...';
        } else if (stats.discovered > stats.crawled) {
            progressText.textContent = `Crawling... (${stats.crawled}/${stats.discovered} URLs)`;
        } else {
            progressText.textContent = `Finishing up... (${stats.crawled} URLs crawled)`;
        }
    } else {
        progressText.textContent = 'Initializing...';
    }
}

function updateStatsDisplay() {
    document.getElementById('discoveredCount').textContent = crawlState.stats.discovered;
    document.getElementById('crawledCount').textContent = crawlState.stats.crawled;
    document.getElementById('crawlDepth').textContent = crawlState.stats.depth;
    document.getElementById('crawlSpeed').textContent = crawlState.stats.speed + ' URLs/sec';
}

function updateMemoryDisplay(memoryData, memoryDataSizes) {
    if (!memoryData || !memoryDataSizes) return;

    // Actual data size (deep measurement)
    const dataMB = memoryDataSizes.total_deep_mb || 0;
    document.getElementById('memCurrent').textContent = dataMB.toFixed(1) + ' MB';

    // KB per URL (actual data)
    const kbPerUrl = memoryDataSizes.avg_per_url_kb || 0;
    document.getElementById('memPeak').textContent = kbPerUrl.toFixed(1) + ' KB/URL';

    // Estimate for 1M URLs (data only)
    const estimate1M = (kbPerUrl * 1000000) / 1024; // Convert to MB
    const estimate1MDisplay = estimate1M > 1024
        ? (estimate1M / 1024).toFixed(1) + ' GB'
        : estimate1M.toFixed(0) + ' MB';
    document.getElementById('memEstimate1M').textContent = estimate1MDisplay;

    // System available
    const availableMB = memoryData.system?.available_mb || 0;
    document.getElementById('memAvailable').textContent = availableMB.toFixed(0) + ' MB';
}

function updateCrawlButtons() {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const clearBtn = document.getElementById('clearBtn');
    const saveCrawlBtn = document.getElementById('saveCrawlBtn');
    const loadCrawlBtn = document.getElementById('loadCrawlBtn');

    if (crawlState.isRunning) {
        // icons.js, not a hand-drawn filled SVG: this function rewrites the
        // button on every render, so whatever the template shipped is gone
        // after the first one
        startBtn.innerHTML = crawlState.isPaused
            ? `${iconMarkup('play', { size: 14 })} Resume`
            : `${iconMarkup('pause', { size: 14 })} Pause`;
        startBtn.disabled = false;
        stopBtn.disabled = false;
        clearBtn.disabled = false;
        saveCrawlBtn.disabled = true; // Disable during crawl
        loadCrawlBtn.disabled = true; // Disable during crawl
    } else {
        startBtn.innerHTML = `${iconMarkup('play', { size: 14 })} Start`;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        clearBtn.disabled = false;

        // Save button: only enabled if crawl is completed and has data
        const hasData = crawlState.stats.crawled > 0;
        saveCrawlBtn.disabled = !hasData || isSavingCrawl;

        // Load button: only enabled if no current crawl data
        loadCrawlBtn.disabled = hasData;
    }
}

function showProgress() {
    document.getElementById('progressContainer').style.display = 'flex';
}

function hideProgress() {
    document.getElementById('progressContainer').style.display = 'none';
}

function updateProgress(percentage) {
    document.getElementById('progressFill').style.width = percentage + '%';
}

function updateStatus(message) {
    document.getElementById('statusText').textContent = message;
}

function showDemoLimitNotification() {
    // Remove existing demo notification if any
    const existing = document.getElementById('demoLimitNotification');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'demoLimitNotification';
    overlay.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.6); z-index: 10000;
        display: flex; align-items: center; justify-content: center;
    `;

    const box = document.createElement('div');
    box.style.cssText = `
        background: #1a1a2e; border: 1px solid #f59e0b; border-radius: 12px;
        padding: 32px 40px; max-width: 480px; text-align: center;
        color: #e0e0e0; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    `;
    box.innerHTML = `
        <div style="font-size: 28px; margin-bottom: 12px;">&#9888;</div>
        <h2 style="color: #f59e0b; margin: 0 0 12px; font-size: 18px;">Demo Memory Limit Reached</h2>
        <p style="margin: 0 0 16px; line-height: 1.5; font-size: 14px;">
            This user has reached the 1.5 GB per-user memory limit.<br>
            Your crawl data has been saved automatically.<br><br>
            <strong>This is a free demo and is not intended for production use.</strong>
        </p>
        <button id="demoLimitDismiss" style="
            background: #f59e0b; color: #000; border: none; padding: 10px 28px;
            border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 14px;
        ">OK</button>
    `;

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    document.getElementById('demoLimitDismiss').addEventListener('click', function() {
        overlay.remove();
    });
}

function updateTimer() {
    if (crawlState.isRunning && crawlState.startTime) {
        const elapsed = new Date() - crawlState.startTime;
        const minutes = Math.floor(elapsed / 60000);
        const seconds = Math.floor((elapsed % 60000) / 1000);
        document.getElementById('crawlTime').textContent =
            `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    }
}

// Table Management
function initializeTables() {
    // Clear any existing data first
    clearAllTables();
    // Initialize virtual scrollers for all tables
    initializeVirtualScrollers();
    // Sortable column headers
    initTableSorting();
    // Initialize column resizers after virtual scrollers
    setTimeout(() => {
        if (window.initializeColumnResizers) {
            initializeColumnResizers();
        }
    }, 100);
}

function initializeVirtualScrollers() {
    try {
        // Overview table
        const overviewContainer = document.querySelector('#overview-tab .table-container');
        if (overviewContainer && overviewContainer.querySelector('tbody')) {
            virtualScrollers.overview = new VirtualScroller(overviewContainer, {
                rowHeight: rowHeightFor('overview', cssRowHeight()),
                buffer: 25,
                renderRow: renderOverviewRow
            });
            console.log('Overview virtual scroller initialized');
        }

        // Internal URLs table
        const internalContainer = document.querySelector('#internal-tab .table-container');
        if (internalContainer && internalContainer.querySelector('tbody')) {
            virtualScrollers.internal = new VirtualScroller(internalContainer, {
                rowHeight: rowHeightFor('internal', cssRowHeight()),
                buffer: 25,
                renderRow: renderInternalRow
            });
            console.log('Internal virtual scroller initialized');
        }

        // External URLs table
        const externalContainer = document.querySelector('#external-tab .table-container');
        if (externalContainer && externalContainer.querySelector('tbody')) {
            virtualScrollers.external = new VirtualScroller(externalContainer, {
                rowHeight: rowHeightFor('external', cssRowHeight()),
                buffer: 25,
                renderRow: renderExternalRow
            });
            console.log('External virtual scroller initialized');
        }

        // Internal Links table
        const internalLinksContainer = document.querySelector('#links-tab .internal-links-container');
        if (internalLinksContainer && internalLinksContainer.querySelector('tbody')) {
            virtualScrollers.internalLinks = new VirtualScroller(internalLinksContainer, {
                rowHeight: rowHeightFor('internalLinks', cssRowHeight()),
                buffer: 25,
                renderRow: renderInternalLinkRow
            });
            console.log('Internal links virtual scroller initialized');
        }

        // External Links table
        const externalLinksContainer = document.querySelector('#links-tab .external-links-container');
        if (externalLinksContainer && externalLinksContainer.querySelector('tbody')) {
            virtualScrollers.externalLinks = new VirtualScroller(externalLinksContainer, {
                rowHeight: rowHeightFor('externalLinks', cssRowHeight()),
                buffer: 25,
                renderRow: renderExternalLinkRow
            });
            console.log('External links virtual scroller initialized');
        }

        // Issues table
        const issuesContainer = document.querySelector('#issues-tab .table-container');
        if (issuesContainer && issuesContainer.querySelector('tbody')) {
            virtualScrollers.issues = new VirtualScroller(issuesContainer, {
                rowHeight: rowHeightFor('issues', cssRowHeight()),
                buffer: 25,
                renderRow: renderIssueRow
            });
            console.log('Issues virtual scroller initialized');
        }
    } catch (error) {
        console.error('Error initializing virtual scrollers:', error);
    }
}

function isLinksTabActive() {
    const linksTab = document.getElementById('links-tab');
    return linksTab && linksTab.classList.contains('active');
}

function isIssuesTabActive() {
    const issuesTab = document.getElementById('issues-tab');
    return issuesTab && issuesTab.classList.contains('active');
}

function updateLinksTable(links) {
    // Create a lookup map of URL statuses from crawled URLs
    const urlStatusMap = new Map();
    if (crawlState.urls && crawlState.urls.length > 0) {
        crawlState.urls.forEach(url => {
            urlStatusMap.set(url.url, url.status_code);
        });
    }

    // Remove duplicates from links array (extra safety check)
    const uniqueLinks = [];
    const seenLinks = new Set();
    links.forEach(link => {
        const key = `${link.source_url}|${link.target_url}`;
        if (!seenLinks.has(key)) {
            seenLinks.add(key);

            // Update target status with actual crawled status if available
            const crawledStatus = urlStatusMap.get(link.target_url);
            if (crawledStatus) {
                link.target_status = crawledStatus;
            }

            uniqueLinks.push(link);
        }
    });

    // Store unfiltered links in crawlState
    crawlState.links = uniqueLinks;

    // Apply filters and update virtual scrollers
    applyLinksFilter();

    console.log(`Links loaded: ${crawlState.links.filter(l => l.is_internal).length} internal, ${crawlState.links.filter(l => !l.is_internal).length} external`);
}

function applyLinksFilter() {
    if (!crawlState.links || crawlState.links.length === 0) {
        renderFilterChips();
        return;
    }

    // Separate internal and external links
    let internalLinks = crawlState.links.filter(link => link.is_internal);
    let externalLinks = crawlState.links.filter(link => !link.is_internal);

    // Apply status code filter for internal links
    const internalStatusFilter = crawlState.filters.linksFilter.internalStatusCode;
    if (internalStatusFilter && internalStatusFilter !== 'all') {
        internalLinks = internalLinks.filter(link => {
            if (!link.target_status) return false;
            const status = parseInt(link.target_status);
            switch (internalStatusFilter) {
                case '2xx': return status >= 200 && status < 300;
                case '3xx': return status >= 300 && status < 400;
                case '4xx': return status >= 400 && status < 500;
                case '5xx': return status >= 500;
                default: return true;
            }
        });
    }

    // Apply search filter for internal links
    const internalSearch = crawlState.filters.linksFilter.internalSearch.toLowerCase();
    if (internalSearch) {
        internalLinks = internalLinks.filter(link =>
            link.source_url.toLowerCase().includes(internalSearch) ||
            link.target_url.toLowerCase().includes(internalSearch) ||
            (link.anchor_text && link.anchor_text.toLowerCase().includes(internalSearch))
        );
    }

    // Apply status code filter for external links
    const externalStatusFilter = crawlState.filters.linksFilter.externalStatusCode;
    if (externalStatusFilter && externalStatusFilter !== 'all') {
        externalLinks = externalLinks.filter(link => {
            if (!link.target_status) return false;
            const status = parseInt(link.target_status);
            switch (externalStatusFilter) {
                case '2xx': return status >= 200 && status < 300;
                case '3xx': return status >= 300 && status < 400;
                case '4xx': return status >= 400 && status < 500;
                case '5xx': return status >= 500;
                default: return true;
            }
        });
    }

    // Apply search filter for external links
    const externalSearch = crawlState.filters.linksFilter.externalSearch.toLowerCase();
    if (externalSearch) {
        externalLinks = externalLinks.filter(link =>
            link.source_url.toLowerCase().includes(externalSearch) ||
            link.target_url.toLowerCase().includes(externalSearch) ||
            (link.target_domain && link.target_domain.toLowerCase().includes(externalSearch))
        );
    }

    // Update virtual scrollers with filtered data
    setScrollerData('internalLinks', internalLinks);
    setScrollerData('externalLinks', externalLinks);

    renderFilterChips();
}

function filterInternalLinks(filterType) {
    crawlState.filters.linksFilter.internalStatusCode = filterType;
    applyLinksFilter();
}

function filterExternalLinks(filterType) {
    crawlState.filters.linksFilter.externalStatusCode = filterType;
    applyLinksFilter();
}

function searchInternalLinks(searchText) {
    crawlState.filters.linksFilter.internalSearch = searchText;
    applyLinksFilter();
}

function searchExternalLinks(searchText) {
    crawlState.filters.linksFilter.externalSearch = searchText;
    applyLinksFilter();
}

function updateIssuesTable(issues) {
    if (!issues || !Array.isArray(issues)) {
        issues = [];
    }

    // Store issues globally for filtering
    window.currentIssues = issues;

    // A server-filtered view (status, "new since crawl N") has to be asked
    // again when the crawl's issues change under it.
    if (issueServerFiltersActive() && !issueView.loading) {
        scheduleIssueServerRefresh(1000);
    }

    // Counts, badge, grouping, severity filter and the empty state all happen
    // in one place now; see the Issues view section below.
    renderIssueList();
}

// The Issues tab's count badge. Tinted by class (the spec's .badge scale)
// rather than by an inline background colour.
function updateIssuesTabBadge(issues) {
    const button = Array.from(document.querySelectorAll('.tab-btn')).find(btn => btn.textContent.includes('Issues'));
    if (!button) return;

    const total = issues.length;
    if (!total) {
        button.textContent = 'Issues';
        return;
    }

    const errors = issues.filter(issue => issue.type === 'error').length;
    const warnings = issues.filter(issue => issue.type === 'warning').length;
    const tone = errors > 0 ? 'badge-error' : warnings > 0 ? 'badge-warning' : 'badge-info';

    button.textContent = 'Issues ';
    const badge = document.createElement('span');
    badge.className = `tab-badge badge ${tone}`;
    badge.textContent = String(total);
    button.appendChild(badge);
}

function clearAllTables() {
    // Clear virtual scrollers if they exist
    if (virtualScrollers.overview) {
        virtualScrollers.overview.clear();
    }
    if (virtualScrollers.internal) {
        virtualScrollers.internal.clear();
    }
    if (virtualScrollers.external) {
        virtualScrollers.external.clear();
    }
    if (virtualScrollers.internalLinks) {
        virtualScrollers.internalLinks.clear();
    }
    if (virtualScrollers.externalLinks) {
        virtualScrollers.externalLinks.clear();
    }
    if (virtualScrollers.issues) {
        virtualScrollers.issues.clear();
    }

    // Clear status codes table (not virtualized)
    const statusCodesBody = document.getElementById('statusCodesTableBody');
    if (statusCodesBody) statusCodesBody.innerHTML = '';

    crawlState.urls = [];

    console.log('All tables cleared');
}

function formatAnalyticsInfo(analytics) {
    const detected = [];
    if (analytics.gtag || analytics.ga4_id) detected.push('GA4');
    if (analytics.google_analytics) detected.push('GA');
    if (analytics.gtm_id) detected.push('GTM');
    if (analytics.facebook_pixel) detected.push('FB');
    if (analytics.hotjar) detected.push('HJ');
    if (analytics.mixpanel) detected.push('MP');

    return detected.length > 0 ? detected.join(', ') : '';
}

function addUrlToTable(urlData) {
    // Check if URL already exists to prevent duplicates
    const existingUrl = crawlState.urls.find(u => u.url === urlData.url);
    if (existingUrl) {
        return; // Skip duplicate
    }

    crawlState.urls.push(urlData);

    // Update virtual scrollers with new data
    if (virtualScrollers.overview) {
        virtualScrollers.overview.appendData([urlData]);
    }

    if (urlData.is_internal && virtualScrollers.internal) {
        virtualScrollers.internal.appendData([urlData]);
    } else if (!urlData.is_internal && virtualScrollers.external) {
        virtualScrollers.external.appendData([urlData]);
    }

    // Reapply current filter if one is active
    if (crawlState.filters.active) {
        applyFilter(crawlState.filters.active);
    }
}

function addRowToTable(tableBodyId, rowData) {
    const tbody = document.getElementById(tableBodyId);
    const row = tbody.insertRow();

    rowData.forEach(cellData => {
        const cell = row.insertCell();
        // Check if cellData contains HTML (specifically our button)
        if (typeof cellData === 'string' && cellData.includes('<button')) {
            cell.innerHTML = cellData;
        } else {
            cell.textContent = cellData;
        }
    });
}

// Tab Management
function switchTab(tabName) {
    // Remove active class from all tabs and panes
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));

    // Add active class to selected tab and pane. Look the button up by name so
    // the router can switch tabs without a click event.
    tabButtonFor(tabName)?.classList.add('active');
    document.getElementById(tabName + '-tab').classList.add('active');

    // Load pending links data if switching to Links tab
    if (tabName === 'links' && crawlState.pendingLinks) {
        updateLinksTable(crawlState.pendingLinks);
        crawlState.pendingLinks = null; // Clear pending data
    }

    if (tabName === 'issues') {
        initIssuesView();
    }

    // Load pending issues data if switching to Issues tab
    if (tabName === 'issues' && crawlState.pendingIssues) {
        updateIssuesTable(crawlState.pendingIssues);
        crawlState.pendingIssues = null; // Clear pending data
    }

    // The report tabs read the API for the crawl that is open
    if (tabName === 'sitemap' && typeof loadSitemapReport === 'function') {
        loadSitemapReport();
    }
    if (tabName === 'compare' && typeof loadCompareOptions === 'function') {
        loadCompareOptions();
    }
    if (tabName === 'rules' && typeof loadRules === 'function') {
        loadRules();
    }

    // Initialize visualization if switching to Visualization tab
    if (tabName === 'visualization' && typeof initVisualization === 'function') {
        // Small delay to ensure the tab is visible before initializing
        setTimeout(() => {
            initVisualization();
        }, 100);
    }

    // Handle plugin tabs
    const pluginTab = document.getElementById(`${tabName}-tab`);
    if (pluginTab && pluginTab.classList.contains('plugin-tab')) {
        handlePluginTabSwitch(tabName);
    }
}

function tabButtonFor(tabName) {
    return document.querySelector(`.tab-btn[onclick="switchTab('${tabName}')"]`) ||
        document.querySelector(`.tab-btn[data-plugin-id="${CSS.escape(tabName)}"]`);
}

// Handle plugin tab activation
function handlePluginTabSwitch(tabName) {
    if (!window.ToadstonePlugin || !window.ToadstonePlugin.loader) {
        return;
    }

    const loader = window.ToadstonePlugin.loader;

    // Deactivate previously active plugin
    if (loader.activePluginId && loader.activePluginId !== tabName) {
        loader.deactivatePlugin(loader.activePluginId);
    }

    // Activate the new plugin
    loader.activatePlugin(tabName, {
        urls: crawlState.urls,
        links: crawlState.links,
        issues: crawlState.issues,
        stats: crawlState.stats
    });
}

// Issue Filtering
function filterIssues(filterType) {
    const severity = ISSUE_SEVERITIES.includes(filterType) ? filterType : 'all';
    crawlState.filters.issueFilter = severity;

    // Class only. This used to write ~20 inline colours onto the four pills,
    // which beat every stylesheet rule and made them impossible to restyle.
    document.querySelectorAll('#issues-tab .filter-item').forEach(btn => {
        const on = btn.getAttribute('data-filter') === severity;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', String(on));
    });

    // Grouping, the section/status filters and the empty state are applied
    // together with the severity pick.
    renderIssueList();
}

// ---- Filter set -----------------------------------------------------------
//
// The rail is a filter *set*: several facets can be on at once. Facets within
// a group are ORed (4xx OR 5xx), groups are ANDed (html AND 4xx), which is what
// the checkbox rail promises. crawlState.filters.activeList is the ordered set;
// crawlState.filters.active stays the comma-joined string ("4xx", "4xx,html",
// null) that the router writes to ?filter= and the rest of the app reads.

const ISSUE_SEVERITIES = ['all', 'error', 'warning', 'info'];

const FILTER_GROUPS = {
    scope: ['internal', 'external'],
    status: ['2xx', '3xx', '4xx', '5xx', 'no_response'],
    content: ['html', 'css', 'js', 'images']
};

const FILTER_LABELS = {
    internal: 'Internal', external: 'External',
    '2xx': '2xx', '3xx': '3xx', '4xx': '4xx', '5xx': '5xx', no_response: 'No response',
    html: 'HTML', css: 'CSS', js: 'JavaScript', images: 'Images'
};

// The name a rail facet filters on, or null if this is not a rail facet
function filterNameOf(item) {
    const match = item.getAttribute('onclick')?.match(/toggleFilter\('([^']+)'\)/);
    const name = match ? match[1] : item.dataset.filter;
    return name && Object.prototype.hasOwnProperty.call(FILTER_LABELS, name) ? name : null;
}

// Rail facets only. The Issues severity pills carry the same .filter-item
// class, so an unscoped querySelectorAll here blanks the active severity pill
// every time a rail facet is clicked.
function railFilterItems() {
    const candidates = document.querySelectorAll(
        '#filterRail .filter-item, .sidebar .filter-item, .filter-item[onclick^="toggleFilter("]'
    );
    return Array.from(candidates).filter(el => !el.closest('#issues-tab') && filterNameOf(el));
}

// Accepts a string ("4xx" or "4xx,html"), an array, a Set or null and returns
// an ordered list of known facet names with duplicates removed.
function parseFilters(value) {
    if (!value) return [];
    const raw = Array.isArray(value) ? value
        : value instanceof Set ? Array.from(value)
        : String(value).split(',');
    const names = [];
    raw.forEach(entry => {
        const name = String(entry).trim();
        if (Object.prototype.hasOwnProperty.call(FILTER_LABELS, name) && !names.includes(name)) {
            names.push(name);
        }
    });
    return names;
}

function activeFilterList() {
    return crawlState.filters.activeList || [];
}

// Replace the whole filter set (used by the router when it restores a URL)
function setActiveFilters(value) {
    applyFilter(parseFilters(value));
}

function syncRailFilterUI() {
    const active = activeFilterList();
    railFilterItems().forEach(item => {
        const on = active.includes(filterNameOf(item));
        item.classList.toggle('active', on);
        item.setAttribute('aria-pressed', String(on));
        const box = item.querySelector('input[type="checkbox"]');
        if (box) box.checked = on;
    });
}

// Filter Management
function toggleFilter(filterType) {
    const name = parseFilters(filterType)[0];
    if (!name) return;
    const active = activeFilterList();
    applyFilter(active.includes(name)
        ? active.filter(entry => entry !== name)
        : [...active, name]);
}

function applyFilter(value) {
    const filters = parseFilters(value);
    crawlState.filters.activeList = filters;
    crawlState.filters.active = filters.length ? filters.join(',') : null;

    syncRailFilterUI();

    // Filter the data arrays and update virtual scrollers
    filterVirtualScrollerData('overview', filters);
    filterVirtualScrollerData('internal', filters);
    filterVirtualScrollerData('external', filters);

    // Update Status Codes table with filtered data
    updateStatusCodesTable(filters);

    renderFilterChips();
    console.log('Applied filter:', crawlState.filters.active);
}

function clearActiveFilters() {
    applyFilter([]);
}

// The subbar's "Clear all": rail facets, Issues severity and the four Links
// controls, in one history entry rather than six.
function clearAllFilters() {
    const reset = () => {
        clearActiveFilters();
        filterIssues('all');
        resetLinksFilters();
    };
    if (window.Router && typeof window.Router.batch === 'function') {
        window.Router.batch(reset);
    } else {
        reset();
    }
    renderFilterChips();
}

function setControlValue(selector, value) {
    const el = document.querySelector(selector);
    if (el) el.value = value;
}

function resetLinksFilters() {
    Object.assign(crawlState.filters.linksFilter, {
        internalStatusCode: 'all',
        externalStatusCode: 'all',
        internalSearch: '',
        externalSearch: ''
    });
    setControlValue('select[onchange="filterInternalLinks(this.value)"]', 'all');
    setControlValue('input[oninput="searchInternalLinks(this.value)"]', '');
    setControlValue('select[onchange="filterExternalLinks(this.value)"]', 'all');
    setControlValue('input[oninput="searchExternalLinks(this.value)"]', '');
    applyLinksFilter();
}

function matchesFilterName(url, name) {
    switch (name) {
        case 'internal':
            return isInternalURL(url.url);
        case 'external':
            return !isInternalURL(url.url);
        case '2xx':
            return url.status_code >= 200 && url.status_code < 300;
        case '3xx':
            return url.status_code >= 300 && url.status_code < 400;
        case '4xx':
            return url.status_code >= 400 && url.status_code < 500;
        case '5xx':
            return url.status_code >= 500;
        case 'no_response':
            return (url.status_code === 0 || url.status_code === null || url.status_code === undefined)
                && url.error_type !== 'file_too_large';
        case 'html':
            return (url.content_type || '').toLowerCase().includes('html');
        case 'css':
            return (url.content_type || '').toLowerCase().includes('css');
        case 'js':
            return (url.content_type || '').toLowerCase().includes('javascript');
        case 'images':
            return (url.content_type || '').toLowerCase().includes('image');
        default:
            return true;
    }
}

// OR within a group, AND across groups
function matchesFilters(url, filters) {
    if (!filters.length) return true;
    return Object.values(FILTER_GROUPS).every(group => {
        const chosen = filters.filter(name => group.includes(name));
        return !chosen.length || chosen.some(name => matchesFilterName(url, name));
    });
}

function filterVirtualScrollerData(scrollerName, value) {
    const scroller = virtualScrollers[scrollerName];
    if (!scroller) return;

    const filters = parseFilters(value);
    let filteredData = crawlState.urls;

    // Apply base filter for internal/external tables
    if (scrollerName === 'internal') {
        filteredData = filteredData.filter(url => url.is_internal);
    } else if (scrollerName === 'external') {
        filteredData = filteredData.filter(url => !url.is_internal);
    }

    // Apply the user-selected filter set
    if (filters.length) {
        filteredData = filteredData.filter(url => matchesFilters(url, filters));
    }

    setScrollerData(scrollerName, filteredData);
}

// Legacy function - kept for compatibility but no longer used
function filterTable(tableBodyId, filterType) {
    // This function is deprecated in favor of filterVirtualScrollerData
    // Kept for backwards compatibility only
}

function isInternalURL(url) {
    if (!url || !crawlState.baseUrl) return false;
    try {
        const urlObj = new URL(url);
        const baseObj = new URL(crawlState.baseUrl);

        // Normalize domains by removing www prefix for comparison
        const urlDomain = urlObj.hostname.replace('www.', '');
        const baseDomain = baseObj.hostname.replace('www.', '');

        return urlDomain === baseDomain;
    } catch (e) {
        return false;
    }
}

function isStatusCodeRange(statusText, min, max) {
    const status = parseInt(statusText);
    return status >= min && status <= max;
}

function isContentType(contentType, type) {
    if (!contentType) return false;
    return contentType.toLowerCase().includes(type.toLowerCase());
}

function updateFilterCounts() {
    // Count URLs by type and update filter counts
    const counts = {
        internal: 0,
        external: 0,
        '2xx': 0,
        '3xx': 0,
        '4xx': 0,
        '5xx': 0,
        no_response: 0,
        html: 0,
        css: 0,
        js: 0,
        images: 0
    };

    crawlState.urls.forEach(url => {
        // Count by internal/external using corrected logic
        if (isInternalURL(url.url)) counts.internal++;
        else counts.external++;

        // Count by status code
        const statusCode = parseInt(url.status_code);
        if (statusCode >= 200 && statusCode < 300) counts['2xx']++;
        else if (statusCode >= 300 && statusCode < 400) counts['3xx']++;
        else if (statusCode >= 400 && statusCode < 500) counts['4xx']++;
        else if (statusCode >= 500) counts['5xx']++;
        else if ((url.status_code === 0 || isNaN(statusCode)) && url.error_type !== 'file_too_large') counts.no_response++;

        // Count by content type
        const contentType = url.content_type || '';
        if (contentType.includes('html')) counts.html++;
        else if (contentType.includes('css')) counts.css++;
        else if (contentType.includes('javascript')) counts.js++;
        else if (contentType.includes('image')) counts.images++;
    });

    // Update DOM
    Object.keys(counts).forEach(key => {
        const element = document.getElementById(key + '-count');
        if (element) {
            element.textContent = counts[key];
        }
    });
}

function updateStatusCodesTable(filterType = null) {
    const tbody = document.getElementById('statusCodesTableBody');
    if (!tbody) return;

    // Count status codes, respecting the current filter set
    const statusCounts = {};
    const filters = parseFilters(filterType);
    const filteredUrls = filters.length
        ? crawlState.urls.filter(url => matchesFilters(url, filters))
        : crawlState.urls;

    let totalUrls = filteredUrls.length;

    filteredUrls.forEach(url => {
        // Group status_code=0 rows by error_type so DNS / timeout / refused
        // show as distinct rows instead of collapsing into a single "0".
        const isZero = url.status_code === 0 || url.status_code === null || url.status_code === undefined;
        const key = isZero ? `0|${url.error_type || 'unknown'}` : String(url.status_code);
        statusCounts[key] = (statusCounts[key] || 0) + 1;
    });

    // Clear existing rows
    tbody.innerHTML = '';

    // Sort: real HTTP codes first (numeric), then no-response variants
    const keys = Object.keys(statusCounts).sort((a, b) => {
        const aIsZero = a.startsWith('0|');
        const bIsZero = b.startsWith('0|');
        if (aIsZero && !bIsZero) return 1;
        if (!aIsZero && bIsZero) return -1;
        if (aIsZero && bIsZero) return a.localeCompare(b);
        return parseInt(a) - parseInt(b);
    });

    keys.forEach(key => {
        const count = statusCounts[key];
        const percentage = totalUrls > 0 ? ((count / totalUrls) * 100).toFixed(1) : 0;
        let displayCode, statusText;
        if (key.startsWith('0|')) {
            const errorType = key.slice(2);
            displayCode = '0';
            statusText = getStatusCodeText(0, errorType);
        } else {
            displayCode = key;
            statusText = getStatusCodeText(parseInt(key));
        }

        addRowToTable('statusCodesTableBody', [
            displayCode,
            statusText,
            count,
            percentage + '%'
        ]);
    });
}

function getStatusCodeText(statusCode, errorType) {
    if (statusCode >= 200 && statusCode < 300) {
        return 'Success';
    } else if (statusCode >= 300 && statusCode < 400) {
        return 'Redirect';
    } else if (statusCode >= 400 && statusCode < 500) {
        return 'Client Error';
    } else if (statusCode >= 500) {
        return 'Server Error';
    } else if (statusCode === 0) {
        switch (errorType) {
            case 'dns_not_found':       return 'DNS Not Found';
            case 'connection_refused':  return 'Connection Refused';
            case 'timeout':             return 'Timeout';
            case 'ssl_error':           return 'SSL/TLS Error';
            case 'connection_error':    return 'Connection Error';
            case 'file_too_large':      return 'Skipped (file too large)';
            default:                    return 'No Response';
        }
    } else {
        return 'Unknown';
    }
}

function resetStats() {
    crawlState.stats = {
        discovered: 0,
        crawled: 0,
        depth: 0,
        speed: 0
    };
    updateStatsDisplay();
}

// Utility Functions
function normalizeUrl(input) {
    // Remove any whitespace
    input = input.trim();

    // If it already has a protocol, return as-is
    if (input.match(/^https?:\/\//i)) {
        return input;
    }

    // If it looks like a domain or IP, add https://
    if (input.match(/^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.([a-zA-Z]{2,}|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/) ||
        input.match(/^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/) ||
        input.match(/^localhost(:[0-9]+)?$/i) ||
        input.match(/^[a-zA-Z0-9-]+\.(com|org|net|edu|gov|mil|int|co|io|dev|app|tech|info|biz|name|pro|museum|aero|coop|travel|jobs|mobi|tel|asia|cat|post|xxx|local|test)$/i)) {
        return 'https://' + input;
    }

    // If it doesn't match common patterns, try adding https:// anyway
    return 'https://' + input;
}

function isValidUrl(string) {
    try {
        const url = new URL(string);
        // Check if it has a valid protocol and hostname
        return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname.length > 0;
    } catch (_) {
        return false;
    }
}

// This is defined in settings.js - no need to redefine here

async function logout() {
    try {
        const response = await fetch('/api/logout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.success) {
            // Redirect to login page
            window.location.href = '/login';
        } else {
            console.error('Logout failed:', data.message);
            // Still redirect even if logout fails
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Logout error:', error);
        // Redirect anyway
        window.location.href = '/login';
    }
}

async function loadUserInfo() {
    try {
        const response = await fetch('/api/user/info');
        const data = await response.json();

        if (data.success && data.user) {
            const user = data.user;
            const userInfoElement = document.getElementById('userInfo');

            if (user.tier === 'guest') {
                // Show crawls remaining for guests
                const remaining = user.crawls_remaining;
                userInfoElement.textContent = `Guest (${remaining}/3 crawls remaining)`;
                userInfoElement.style.color = remaining === 0 ? '#dc2626' : '#6b7280';
            } else {
                // Show username and tier for registered users
                userInfoElement.textContent = `${user.username} (${user.tier})`;
                userInfoElement.style.color = '#6b7280';
            }
        }
    } catch (error) {
        console.error('Error loading user info:', error);
    }
}

// The original export: the format and the 22 fields chosen in Settings,
// for the crawl on screen. Now one row of the export menu rather than the
// only thing the Export button can do.
async function exportWithSettings() {
    const exportBtn = document.getElementById('exportBtn');
    // Keep the button's own markup (it may carry an icon) and put it back
    // afterwards, rather than flattening it to text
    const exportBtnMarkup = exportBtn ? exportBtn.innerHTML : '';
    if (exportBtn) {
        if (exportBtn.disabled) return; // Already exporting
        exportBtn.disabled = true;
        exportBtn.setAttribute('aria-busy', 'true');
        exportBtn.innerHTML = `${iconMarkup('loader', { size: 14 })} Exporting…`;
    }
    try {
        // Export with the settings this browser is showing. settings.js keeps
        // them in localStorage and syncs to the server in the background; the
        // server copy can lag (a failed sync, a busy database), which used to
        // make exports ignore the format picked in Settings. Only fall back to
        // the server copy when nothing is loaded here.
        let settings = (typeof currentSettings !== 'undefined' && currentSettings
                        && Object.keys(currentSettings).length) ? currentSettings : null;
        if (!settings) {
            const settingsResponse = await fetch('/api/get_settings');
            const settingsData = await settingsResponse.json();

            if (!settingsData.success) {
                showNotification('Failed to get export settings', 'error');
                return;
            }
            settings = settingsData.settings;
        }

        const exportFormat = settings.exportFormat || 'csv';
        const exportFields = settings.exportFields || ['url', 'status_code', 'title', 'meta_description', 'h1'];

        // Check where the exportable data lives: the server (active or resumed
        // crawl) or only this browser (crawl loaded from a saved file)
        let serverHasData = false;
        try {
            // Lightweight check — stats only, no data arrays
            const status = await fetch('/api/crawl_status?stats_only=1');
            const statusData = await status.json();
            serverHasData = !!(statusData.stats && statusData.stats.crawled > 0);
        } catch (e) {
            console.log('Status check failed, assuming local-only data:', e);
        }
        const localHasData = crawlState.urls && crawlState.urls.length > 0;

        if (!serverHasData && !localHasData) {
            showNotification('No crawl data to export', 'error');
            return;
        }

        showNotification('Preparing export...', 'info');

        if (!serverHasData) {
            // File-loaded crawls exist only in the browser — the streaming
            // endpoint can't see them, so use the legacy export endpoint
            await legacyExportData(exportFormat, exportFields);
            return;
        }

        // Determine which data types to export based on settings
        const hasIssues = exportFields.includes('issues_detected');
        const hasLinks = exportFields.includes('links_detailed');
        const regularFields = exportFields.filter(f => f !== 'issues_detected' && f !== 'links_detailed');

        // Use streaming endpoint — downloads directly, no memory blow-up
        const downloads = [];

        if (regularFields.length > 0) {
            const params = new URLSearchParams({
                format: exportFormat,
                fields: regularFields.join(','),
                type: 'urls'
            });
            downloads.push('/api/export_stream?' + params.toString());
        }

        if (hasLinks) {
            const params = new URLSearchParams({
                format: exportFormat,
                type: 'links'
            });
            downloads.push('/api/export_stream?' + params.toString());
        }

        if (hasIssues) {
            const params = new URLSearchParams({
                format: exportFormat,
                type: 'issues'
            });
            downloads.push('/api/export_stream?' + params.toString());
        }

        if (downloads.length === 0) {
            showNotification('No fields selected for export', 'error');
            return;
        }

        // Trigger each download
        downloads.forEach((url, index) => {
            setTimeout(() => {
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = '';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            }, index * 500);
        });

        const fileWord = downloads.length === 1 ? 'file' : 'files';
        showNotification('Exporting ' + downloads.length + ' ' + fileWord + '...', 'success');

    } catch (error) {
        console.error('Export error:', error);
        showNotification('Export failed', 'error');
    } finally {
        if (exportBtn) {
            exportBtn.disabled = false;
            exportBtn.innerHTML = exportBtnMarkup;
            exportBtn.removeAttribute('aria-busy');
        }
    }
}

// Legacy in-memory export via POST /api/export_data — used for crawls loaded
// from a saved file, where the data exists only in browser state
async function legacyExportData(exportFormat, exportFields) {
    const exportResponse = await fetch('/api/export_data', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            format: exportFormat,
            fields: exportFields,
            localData: {
                urls: crawlState.urls || [],
                links: crawlState.links || [],
                issues: crawlState.issues || window.currentIssues || []
            }
        })
    });

    const exportData = await exportResponse.json();

    if (!exportData.success) {
        showNotification(exportData.error || 'Export failed', 'error');
        return;
    }

    // Binary formats (xlsx) arrive base64-encoded; text formats as-is
    const toBlob = (file) => {
        if (file.encoding === 'base64') {
            const bytes = Uint8Array.from(atob(file.content), c => c.charCodeAt(0));
            return new Blob([bytes], { type: file.mimetype });
        }
        return new Blob([file.content], { type: file.mimetype });
    };

    if (exportData.multiple_files && exportData.files) {
        exportData.files.forEach((file, index) => {
            setTimeout(() => {
                const blob = toBlob(file);
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = file.filename;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            }, index * 500); // Delay between downloads to avoid browser blocking
        });

        showNotification(`Exporting ${exportData.files.length} files...`, 'success');
    } else {
        const blob = toBlob(exportData);
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = exportData.filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showNotification(`Export complete: ${exportData.filename}`, 'success');
    }
}

// Helper function to escape HTML for safe display
function escapeHtml(text) {
    if (!text) return text;
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showUrlDetails(url) {
    // When a crawl is open, the API knows more about this page than the browser
    // does - its issues, and what changed since the previous crawl.
    if (window.PageDetail?.open(url)) return;

    // Find the URL data
    const urlData = crawlState.urls.find(u => u.url === url);
    if (!urlData) {
        showNotification('URL data not found', 'error');
        return;
    }

    // Escape all user-controlled text fields to prevent HTML injection
    const safeUrl = escapeHtml(url);
    const safeTitle = escapeHtml(urlData.title) || 'N/A';
    const safeH1 = escapeHtml(urlData.h1) || 'N/A';
    const safeMetaDesc = escapeHtml(urlData.meta_description) || 'N/A';
    const safeLang = escapeHtml(urlData.lang) || 'N/A';
    const safeCharset = escapeHtml(urlData.charset) || 'N/A';
    const safeCanonical = escapeHtml(urlData.canonical_url) || 'N/A';
    const safeRobots = escapeHtml(urlData.robots) || 'N/A';
    const safeContentType = escapeHtml(urlData.content_type) || 'N/A';
    const safeGa4Id = escapeHtml(urlData.analytics?.ga4_id) || 'N/A';
    const safeGtmId = escapeHtml(urlData.analytics?.gtm_id) || 'N/A';

    // Create modal content
    const modalContent = `
        <div class="details-modal-overlay" onclick="closeUrlDetails()">
            <div class="details-modal" onclick="event.stopPropagation()">
                <div class="details-header">
                    <h3>Comprehensive URL Analysis</h3>
                    <button class="close-btn" onclick="closeUrlDetails()">${iconMarkup('x', { size: 16, title: 'Close' })}</button>
                </div>
                <div class="details-content">
                    <div class="details-url">${safeUrl}</div>

                    <div class="details-sections">
                        <div class="details-section">
                            <h4>${iconMarkup('search')} Basic SEO</h4>
                            <div class="details-grid">
                                <div><strong>Title:</strong> ${safeTitle}</div>
                                <div><strong>H1:</strong> ${safeH1}</div>
                                <div><strong>Meta Description:</strong> ${safeMetaDesc}</div>
                                <div><strong>Keywords:</strong> ${escapeHtml(urlData.keywords) || 'N/A'}</div>
                                <div><strong>Word Count:</strong> ${urlData.word_count || 0}</div>
                                <div><strong>Language:</strong> ${safeLang}</div>
                                <div><strong>Charset:</strong> ${safeCharset}</div>
                                <div><strong>Canonical URL:</strong> ${safeCanonical}</div>
                                <div><strong>Robots Meta:</strong> ${safeRobots}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>${iconMarkup('bar-chart')} Analytics &amp; Tracking</h4>
                            <div class="details-grid">
                                <div><strong>Google Analytics:</strong> ${yesNo(urlData.analytics?.google_analytics)}</div>
                                <div><strong>GA4/Gtag:</strong> ${yesNo(urlData.analytics?.gtag)}</div>
                                <div><strong>GA4 ID:</strong> ${safeGa4Id}</div>
                                <div><strong>GTM ID:</strong> ${safeGtmId}</div>
                                <div><strong>Facebook Pixel:</strong> ${yesNo(urlData.analytics?.facebook_pixel)}</div>
                                <div><strong>Hotjar:</strong> ${yesNo(urlData.analytics?.hotjar)}</div>
                                <div><strong>Mixpanel:</strong> ${yesNo(urlData.analytics?.mixpanel)}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>${iconMarkup('share')} Social Media</h4>
                            <div class="details-grid">
                                <div><strong>OpenGraph Tags:</strong> ${Object.keys(urlData.og_tags || {}).length} found</div>
                                <div><strong>Twitter Cards:</strong> ${Object.keys(urlData.twitter_tags || {}).length} found</div>
                            </div>
                            ${Object.keys(urlData.og_tags || {}).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>OpenGraph Tags:</h5>
                                    ${Object.entries(urlData.og_tags || {}).map(([key, value]) =>
                                        `<div><strong>og:${escapeHtml(key)}:</strong> ${escapeHtml(value)}</div>`
                                    ).join('')}
                                </div>
                            ` : ''}
                            ${Object.keys(urlData.twitter_tags || {}).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>Twitter Cards:</h5>
                                    ${Object.entries(urlData.twitter_tags || {}).map(([key, value]) =>
                                        `<div><strong>twitter:${escapeHtml(key)}:</strong> ${escapeHtml(value)}</div>`
                                    ).join('')}
                                </div>
                            ` : ''}
                        </div>

                        <div class="details-section">
                            <h4>${iconMarkup('link')} Links &amp; Structure</h4>
                            <div class="details-grid">
                                <div><strong>Internal Links:</strong> ${urlData.internal_links || 0}</div>
                                <div><strong>External Links:</strong> ${urlData.external_links || 0}</div>
                                <div><strong>Images:</strong> ${(urlData.images || []).length}</div>
                                <div><strong>H2 Tags:</strong> ${(urlData.h2 || []).length}</div>
                                <div><strong>H3 Tags:</strong> ${(urlData.h3 || []).length}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>${iconMarkup('zap')} Performance</h4>
                            <div class="details-grid">
                                <div><strong>Status Code:</strong> ${urlData.status_code}${urlData.error_type ? ' (' + escapeHtml(getStatusCodeText(parseInt(urlData.status_code) || 0, urlData.error_type)) + ')' : ''}</div>
                                <div><strong>Response Time:</strong> ${urlData.response_time || 0}ms</div>
                                ${urlData.render_time ? `<div><strong>Render Time:</strong> ${urlData.render_time}ms <span class="text-muted">(includes the JS render wait)</span></div>` : ''}
                                <div><strong>Content Type:</strong> ${safeContentType}</div>
                                <div><strong>Size:</strong> ${urlData.size || 0} bytes</div>
                                ${urlData.error ? `<div><strong>Error:</strong> ${escapeHtml(urlData.error)}</div>` : ''}
                            </div>
                        </div>

                        ${(urlData.linked_from && urlData.linked_from.length > 0) ? `
                        <div class="details-section">
                            <h4>${iconMarkup('git-branch')} Linked From</h4>
                            <div class="details-grid">
                                <div><strong>Found on ${urlData.linked_from.length} page${urlData.linked_from.length !== 1 ? 's' : ''}:</strong></div>
                            </div>
                            <div class="details-subsection">
                                <ul class="details-link-list">
                                    ${urlData.linked_from.slice(0, 20).map(sourceUrl => {
                                        const escapedUrl = escapeHtml(sourceUrl);
                                        return `<li class="cell-url"><a href="${escapedUrl}" target="_blank" class="url-link">${escapedUrl}</a></li>`;
                                    }).join('')}
                                    ${urlData.linked_from.length > 20 ? `<li class="text-muted">… and ${urlData.linked_from.length - 20} more</li>` : ''}
                                </ul>
                            </div>
                        </div>
                        ` : ''}

                        <div class="details-section">
                            <h4>${iconMarkup('code')} Structured Data</h4>
                            <div class="details-grid">
                                <div><strong>JSON-LD Scripts:</strong> ${(urlData.json_ld || []).length}</div>
                                <div><strong>Schema.org Items:</strong> ${(urlData.schema_org || []).length}</div>
                            </div>
                            ${(urlData.json_ld || []).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>JSON-LD Data:</h5>
                                    <pre class="json-preview">${escapeHtml(JSON.stringify(urlData.json_ld, null, 2))}</pre>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Add modal to page
    document.body.insertAdjacentHTML('beforeend', modalContent);
    // icons.js only makes one automatic pass, on DOMContentLoaded, so anything
    // built later has to ask for its <i data-icon> placeholders to be drawn
    paintIcons(document.querySelector('.details-modal-overlay'));
}

function closeUrlDetails() {
    const modal = document.querySelector('.details-modal-overlay');
    if (modal) {
        modal.remove();
    }
}

function displayPageSpeedResults(results) {
    const container = document.getElementById('pagespeedResults');
    if (!container || !results || results.length === 0) {
        return;
    }

    container.innerHTML = '';

    paintIcons(container);

    results.forEach(pageResult => {
        const pageCard = document.createElement('div');
        pageCard.className = 'pagespeed-page-card';

        const mobile = pageResult.mobile || {};
        const desktop = pageResult.desktop || {};

        pageCard.innerHTML = `
            <div class="pagespeed-page-header">
                <h4 class="pagespeed-page-url">${pageResult.url}</h4>
                <span class="pagespeed-analysis-date">Analyzed: ${pageResult.analysis_date}</span>
            </div>

            <div class="pagespeed-results-grid">
                <div class="pagespeed-device-result">
                    <h5>${iconMarkup('mobile')} Mobile</h5>
                    ${mobile.success ? `
                        <div class="pagespeed-score ${getScoreClass(mobile.performance_score)}">
                            ${mobile.performance_score || 'N/A'}
                        </div>
                        <div class="pagespeed-metrics">
                            <div class="metric">
                                <span class="metric-label">FCP:</span>
                                <span class="metric-value">${mobile.metrics?.first_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">LCP:</span>
                                <span class="metric-value">${mobile.metrics?.largest_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">CLS:</span>
                                <span class="metric-value">${mobile.metrics?.cumulative_layout_shift || 'N/A'}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">SI:</span>
                                <span class="metric-value">${mobile.metrics?.speed_index || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">TTI:</span>
                                <span class="metric-value">${mobile.metrics?.time_to_interactive || 'N/A'}s</span>
                            </div>
                        </div>
                    ` : `
                        <div class="pagespeed-error">
                            Error: ${mobile.error || 'Analysis failed'}
                        </div>
                    `}
                </div>

                <div class="pagespeed-device-result">
                    <h5>${iconMarkup('desktop')} Desktop</h5>
                    ${desktop.success ? `
                        <div class="pagespeed-score ${getScoreClass(desktop.performance_score)}">
                            ${desktop.performance_score || 'N/A'}
                        </div>
                        <div class="pagespeed-metrics">
                            <div class="metric">
                                <span class="metric-label">FCP:</span>
                                <span class="metric-value">${desktop.metrics?.first_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">LCP:</span>
                                <span class="metric-value">${desktop.metrics?.largest_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">CLS:</span>
                                <span class="metric-value">${desktop.metrics?.cumulative_layout_shift || 'N/A'}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">SI:</span>
                                <span class="metric-value">${desktop.metrics?.speed_index || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">TTI:</span>
                                <span class="metric-value">${desktop.metrics?.time_to_interactive || 'N/A'}s</span>
                            </div>
                        </div>
                    ` : `
                        <div class="pagespeed-error">
                            Error: ${desktop.error || 'Analysis failed'}
                        </div>
                    `}
                </div>
            </div>
        `;

        container.appendChild(pageCard);
    });
}

function getScoreClass(score) {
    if (!score) return 'score-unknown';
    if (score >= 90) return 'score-good';
    if (score >= 50) return 'score-needs-improvement';
    return 'score-poor';
}

// Save/Load Crawl Functions
let isSavingCrawl = false;

async function saveCrawl() {
    const saveCrawlBtn = document.getElementById('saveCrawlBtn');
    if (isSavingCrawl || (saveCrawlBtn && saveCrawlBtn.disabled)) return;
    isSavingCrawl = true;
    if (saveCrawlBtn) {
        saveCrawlBtn.disabled = true;
        saveCrawlBtn.textContent = 'Saving...';
    }
    try {
        if (crawlState.stats.crawled === 0) {
            showNotification('No crawl data to save', 'error');
            return;
        }

        // Get current crawl data from backend or use local state
        let urls = crawlState.urls;
        let links = crawlState.links;
        let issues = crawlState.issues;
        let stats = crawlState.stats;

        // Try to get fresh data from backend if available
        try {
            const status = await fetch('/api/crawl_status');
            const crawlData = await status.json();
            if (crawlData.urls && crawlData.urls.length > 0) {
                urls = crawlData.urls;
                links = crawlData.links || links;
                issues = crawlData.issues || issues;
                // Update stats to include latest PageSpeed results if available
                if (crawlData.stats) {
                    stats = crawlData.stats;
                }
            }
        } catch (e) {
            console.log('Using local state for save:', e);
        }

        // Add metadata
        const saveData = {
            timestamp: new Date().toISOString(),
            baseUrl: crawlState.baseUrl,
            stats: stats,
            urls: urls,
            links: links,
            issues: issues,
            version: '1.1'
        };

        // Create and download the file
        const blob = new Blob([JSON.stringify(saveData, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;

        // Generate filename with domain and timestamp
        const domain = crawlState.baseUrl ? new URL(crawlState.baseUrl).hostname : 'crawl';
        const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
        a.download = `toadstone_${domain}_${timestamp}.json`;

        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showNotification('Crawl saved successfully', 'success');

    } catch (error) {
        console.error('Save error:', error);
        showNotification('Failed to save crawl', 'error');
    } finally {
        isSavingCrawl = false;
        if (saveCrawlBtn) {
            saveCrawlBtn.textContent = 'Save Crawl';
        }
        updateCrawlButtons(); // Restore correct enabled/disabled state
    }
}

function loadCrawl() {
    // Create file input
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.json';
    fileInput.style.display = 'none';

    fileInput.addEventListener('change', async function(event) {
        const file = event.target.files[0];
        if (!file) return;

        try {
            const text = await file.text();
            const saveData = JSON.parse(text);

            // Validate save data
            if (!saveData.version || !saveData.urls || !saveData.stats) {
                showNotification('Invalid crawl file format', 'error');
                return;
            }

            // Clear current data
            clearAllTables();
            resetStats();

            // Load the data
            crawlState.baseUrl = saveData.baseUrl;
            crawlState.stats = saveData.stats;
            crawlState.urls = [];
            crawlState.links = saveData.links || [];
            crawlState.issues = saveData.issues || [];

            // Update UI
            document.getElementById('urlInput').value = saveData.baseUrl || '';
            updateStatsDisplay();

            // Populate tables with loaded data
            if (saveData.urls && saveData.urls.length > 0) {
                console.log(`Loading ${saveData.urls.length} URLs...`);

                // Clear crawlState.urls first to avoid duplicate check issues
                crawlState.urls = [];

                // Add URLs to tables (addUrlToTable will handle adding to crawlState.urls)
                saveData.urls.forEach(url => {
                    // Debug: check if url has is_internal flag
                    if (url.is_internal === undefined) {
                        console.warn('URL missing is_internal flag:', url.url);
                        // Try to determine is_internal based on domain
                        if (crawlState.baseUrl) {
                            try {
                                const urlDomain = new URL(url.url).hostname.replace('www.', '');
                                const baseDomain = new URL(crawlState.baseUrl).hostname.replace('www.', '');
                                url.is_internal = urlDomain === baseDomain;
                            } catch (e) {
                                url.is_internal = false;
                            }
                        }
                    }
                    addUrlToTable(url);
                });

                console.log(`Added ${crawlState.urls.length} URLs to state`);
                console.log('Sample URL data:', crawlState.urls[0]);
            }

            // Load links data
            if (saveData.links && saveData.links.length > 0) {
                console.log(`Loading ${saveData.links.length} links...`);
                crawlState.pendingLinks = saveData.links;
                // If Links tab is currently active, load them immediately
                if (isLinksTabActive()) {
                    updateLinksTable(saveData.links);
                }
            }

            // Load issues data if present - filter them based on current exclusion settings
            if (saveData.issues && saveData.issues.length > 0) {
                console.log(`Loading ${saveData.issues.length} issues...`);

                // Filter issues using current exclusion patterns
                try {
                    const filterResponse = await fetch('/api/filter_issues', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ issues: saveData.issues })
                    });
                    const filterData = await filterResponse.json();

                    const filteredIssues = filterData.success ? filterData.issues : saveData.issues;
                    console.log(`Filtered to ${filteredIssues.length} issues after exclusions`);

                    crawlState.issues = filteredIssues;
                    crawlState.pendingIssues = filteredIssues;

                    // If Issues tab is currently active, load them immediately
                    if (isIssuesTabActive()) {
                        updateIssuesTable(filteredIssues);
                    } else {
                        // Update the badge count even if tab is not active
                        const issuesTabButton = Array.from(document.querySelectorAll('.tab-btn')).find(btn => btn.textContent.includes('Issues'));
                        if (issuesTabButton) {
                            const errorCount = filteredIssues.filter(i => i.type === 'error').length;
                            const warningCount = filteredIssues.filter(i => i.type === 'warning').length;
                            let badgeColor = '#3b82f6';
                            if (errorCount > 0) badgeColor = '#ef4444';
                            else if (warningCount > 0) badgeColor = '#f59e0b';
                            issuesTabButton.innerHTML = `Issues <span style="background: ${badgeColor}; color: white; padding: 2px 6px; border-radius: 12px; font-size: 12px;">${filteredIssues.length}</span>`;
                        }
                    }
                } catch (error) {
                    console.error('Failed to filter issues:', error);
                    // Fall back to unfiltered issues if filtering fails
                    crawlState.issues = saveData.issues;
                    crawlState.pendingIssues = saveData.issues;
                    if (isIssuesTabActive()) {
                        updateIssuesTable(saveData.issues);
                    }
                }
            }

            // Update all secondary data
            updateFilterCounts();
            updateStatusCodesTable();
            updateCrawlButtons();

            // Display PageSpeed results if available
            if (saveData.stats && saveData.stats.pagespeed_results) {
                console.log(`Loading ${saveData.stats.pagespeed_results.length} PageSpeed results...`);
                displayPageSpeedResults(saveData.stats.pagespeed_results);
            }

            // Force refresh of all tables
            setTimeout(() => {
                console.log('Force refreshing tables...');
                const overviewCount = document.getElementById('overviewTableBody').children.length;
                const internalCount = document.getElementById('internalTableBody').children.length;
                const externalCount = document.getElementById('externalTableBody').children.length;
                console.log(`Table counts - Overview: ${overviewCount}, Internal: ${internalCount}, External: ${externalCount}`);
            }, 100);

            // Update visualization if it exists and has been initialized
            if (typeof window.updateVisualizationFromLoadedData === 'function') {
                window.updateVisualizationFromLoadedData(saveData.urls, saveData.links);
            }

            // Notify plugins of loaded data
            if (window.ToadstonePlugin && window.ToadstonePlugin.loader) {
                window.ToadstonePlugin.loader.notifyDataUpdate({
                    urls: crawlState.urls,
                    links: crawlState.links,
                    issues: crawlState.issues,
                    stats: crawlState.stats
                });
            }

            showNotification(`Crawl loaded: ${saveData.stats.crawled} URLs from ${new Date(saveData.timestamp).toLocaleDateString()}`, 'success');

        } catch (error) {
            console.error('Load error:', error);
            showNotification('Failed to load crawl file', 'error');
        }
    });

    // Trigger file selection
    document.body.appendChild(fileInput);
    fileInput.click();
    document.body.removeChild(fileInput);
}

// ========================================
// Virtual Scroller Render Functions
// ========================================

function renderOverviewRow(row, urlData, index) {
    const analyticsInfo = formatAnalyticsInfo(urlData.analytics || {});
    const ogTagsCount = Object.keys(urlData.og_tags || {}).length;
    const jsonLdCount = (urlData.json_ld || []).length;
    const linksInfo = `${urlData.internal_links || 0}/${urlData.external_links || 0}`;
    const imagesCount = (urlData.images || []).length;
    const jsRendered = urlData.javascript_rendered ? `${iconMarkup('code', { size: 14 })} JS` : '';

    const keywordsInfo = urlData.keywords || '';

    const cells = [
        urlData.url,
        urlData.status_code,
        urlData.title || '',
        (urlData.meta_description || '').substring(0, 50) + (urlData.meta_description && urlData.meta_description.length > 50 ? '...' : ''),
        urlData.h1 || '',
        keywordsInfo,
        urlData.word_count || 0,
        urlData.response_time || 0,
        analyticsInfo,
        ogTagsCount > 0 ? `${ogTagsCount} tags` : '',
        jsonLdCount > 0 ? `${jsonLdCount} scripts` : '',
        linksInfo,
        imagesCount > 0 ? `${imagesCount} images` : '',
        jsRendered,
        `<button class="details-btn btn btn-ghost btn-sm" onclick="showUrlDetails('${urlData.url.replace(/'/g, "\\'")}')">${iconMarkup('eye', { size: 14 })} Details</button>`
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        // Only the cells this function builds itself (the Details button, the
        // rendered-JS icon) are markup; crawled page content stays textContent
        if (typeof cellData === 'string' && (cellData.includes('<button') || cellData.includes('<svg'))) {
            cell.innerHTML = cellData;
        } else {
            cell.textContent = cellData;
        }
        row.appendChild(cell);
    });
}

function renderInternalRow(row, urlData, index) {
    const cells = [
        urlData.url,
        urlData.status_code,
        urlData.content_type || '',
        urlData.size || 0,
        urlData.title || ''
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        cell.textContent = cellData;
        row.appendChild(cell);
    });
}

function renderExternalRow(row, urlData, index) {
    const cells = [
        urlData.url,
        urlData.status_code,
        urlData.content_type || '',
        urlData.size || 0,
        urlData.title || ''
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        cell.textContent = cellData;
        row.appendChild(cell);
    });
}

// Build a <td> with text set via textContent so crawled-page content
// (anchor text, URLs, issue details) can never inject markup
function makeTextCell(text, wordBreak) {
    const cell = document.createElement('td');
    if (wordBreak) cell.style.wordBreak = wordBreak;
    cell.textContent = text;
    return cell;
}

function makeStatusBadgeCell(targetStatus) {
    const cell = document.createElement('td');
    if (targetStatus) {
        const badge = document.createElement('span');
        badge.className = `status-badge status-${Math.floor(targetStatus / 100)}xx`;
        badge.textContent = targetStatus;
        cell.appendChild(badge);
    }
    return cell;
}

function renderInternalLinkRow(row, link, index) {
    const placement = link.placement ? link.placement.charAt(0).toUpperCase() + link.placement.slice(1) : 'Unknown';

    row.appendChild(makeTextCell(link.source_url, 'break-all'));
    row.appendChild(makeTextCell(link.target_url, 'break-all'));
    row.appendChild(makeStatusBadgeCell(link.target_status));
    row.appendChild(makeTextCell(link.anchor_text || ''));
    row.appendChild(makeTextCell(placement));
}

function renderExternalLinkRow(row, link, index) {
    const placement = link.placement ? link.placement.charAt(0).toUpperCase() + link.placement.slice(1) : 'Unknown';

    row.appendChild(makeTextCell(link.source_url, 'break-all'));
    row.appendChild(makeTextCell(link.target_url, 'break-all'));
    row.appendChild(makeStatusBadgeCell(link.target_status));
    row.appendChild(makeTextCell(link.target_domain || ''));
    row.appendChild(makeTextCell(placement));
}

// ===========================================================================
// Issues view: grouping, section / status / "new since" filters, evidence
// ===========================================================================
//
// Section 10.3 asks the issue list to group by type and by page, count by
// severity, filter by section (URL prefix), by "new since crawl N" and by
// status, and to copy an evidence snippet in one click.
//
// The severity segments, the five-column table and every global the markup
// calls inline (filterIssues, updateIssuesTable, ...) stay exactly as they
// were. Everything new is built here and injected into the tab's subbar.
//
// Grouping does not fork the renderer: the table is still one windowed
// scroller, it is just fed a row stream of {kind:'group'|'issue'|'detail'}
// items, so a 500k-row crawl costs what it cost before. Group headers and the
// expanded evidence block are rows in that same stream.

const ISSUE_GROUPINGS = ['none', 'check', 'page', 'category'];
const ISSUE_GROUP_DEFAULT = 'check';
const ISSUE_GROUP_LABELS = { none: 'None', check: 'Check', page: 'Page', category: 'Category' };
const ISSUE_STATUS_FILTERS = ['open', 'accepted', 'all'];
const ISSUE_STATUS_LABELS = { open: 'Open', accepted: 'Accepted', all: 'All' };
const ISSUE_GROUP_PREF_KEY = 'toadstone.issues.group';
const ISSUE_URL_PARAMS = ['group', 'issue_status', 'since_crawl', 'issue_prefix'];
const ISSUE_RELATED_URL_LIMIT = 25;

const issueView = {
    group: 'check',          // grouping mode, the default a person fixes a site by
    status: 'open',          // open | accepted | all
    sinceCrawl: null,        // "new since crawl N", compared server-side by stable id
    urlPrefix: '',           // section filter
    collapsed: new Set(),    // group keys that are folded shut
    expanded: new Set(),     // issue keys whose evidence is open
    serverIssues: null,      // rows from /api/crawls/<id>/issues, or null for the session's
    crawls: [],              // other crawls of this site, for the "new since" control
    baseDomain: null,
    token: 0,                // request token: a slow answer never overwrites a newer one
    loading: false,
    initialised: false,
    routerOwnsParams: false
};

// --- state the rest of the file asks about ---------------------------------

function issueKeyOf(issue) {
    return issue.id || `${issue.url || ''}|${issue.check || issue.issue || ''}|${issue.details || ''}`;
}

function issueSeverityRank(type) {
    return Object.prototype.hasOwnProperty.call(SEVERITY_ORDER, type) ? SEVERITY_ORDER[type] : 3;
}

// since_crawl and the status filter can only be answered by the database, so
// they need a saved crawl; the section filter is applied in the browser too.
function issueServerFiltersActive() {
    return Boolean(issueView.sinceCrawl) || issueView.status !== 'open';
}

function issueCrawlId() {
    return (window.Router && typeof window.Router.currentCrawlId === 'function')
        ? window.Router.currentCrawlId()
        : null;
}

function issueSourceList() {
    return issueView.serverIssues || window.currentIssues || [];
}

// Section and status are also applied here, not only on the server: during a
// live crawl there is no saved crawl to query, and an issue just accepted has
// to leave the open list without a round trip.
function scopeIssues(list) {
    let rows = Array.isArray(list) ? list : [];
    if (issueView.urlPrefix) {
        rows = rows.filter(issue => String(issue.url || '').startsWith(issueView.urlPrefix));
    }
    if (issueView.status === 'open') {
        rows = rows.filter(issue => issue.status !== 'accepted');
    } else if (issueView.status === 'accepted') {
        rows = rows.filter(issue => issue.status === 'accepted');
    }
    return rows;
}

// --- rendering -------------------------------------------------------------

function updateIssueCounts(issues) {
    let errors = 0;
    let warnings = 0;
    let info = 0;
    issues.forEach(issue => {
        if (issue.type === 'error') errors++;
        else if (issue.type === 'warning') warnings++;
        else if (issue.type === 'info') info++;
    });

    const write = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = `(${value})`;
    };
    write('issues-all-count', issues.length);
    write('issues-error-count', errors);
    write('issues-warning-count', warnings);
    write('issues-info-count', info);
}

// The one place the Issues table is drawn from. Everything that changes what
// is on screen — a poll, a severity pill, a grouping, a column sort, an
// expanded row — ends up here.
function renderIssueList() {
    const base = scopeIssues(issueSourceList());
    updateIssueCounts(base);
    updateIssuesTabBadge(base);

    const severity = crawlState.filters.issueFilter;
    const visible = sortRows('issues', severity && severity !== 'all'
        ? base.filter(issue => issue.type === severity)
        : base);

    if (virtualScrollers.issues) {
        virtualScrollers.issues.setData(buildIssueRows(visible));
    }
    updateTableState('issues', visible.length);
    syncIssueControls();
    renderFilterChips();
}

function groupIssues(list) {
    const groups = new Map();
    list.forEach(issue => {
        const key = issueGroupKeyOf(issue);
        let group = groups.get(key);
        if (!group) {
            group = {
                kind: 'group',
                key,
                label: issueGroupLabelOf(issue),
                check: issueView.group === 'check' ? (issue.check || '') : '',
                severity: issue.type,
                count: 0,
                issues: []
            };
            groups.set(key, group);
        }
        group.issues.push(issue);
        group.count++;
        if (issueSeverityRank(issue.type) < issueSeverityRank(group.severity)) {
            group.severity = issue.type;
        }
    });

    // Worst first, then the biggest defect class: the order someone fixing a
    // site works in.
    return Array.from(groups.values()).sort((a, b) =>
        issueSeverityRank(a.severity) - issueSeverityRank(b.severity) ||
        b.count - a.count ||
        String(a.label).localeCompare(String(b.label)));
}

function issueGroupKeyOf(issue) {
    switch (issueView.group) {
        case 'page': return issue.url || '(no URL)';
        case 'category': return issue.category || 'Uncategorised';
        default: return issue.check || `issue:${issue.issue || 'Unnamed check'}`;
    }
}

function issueGroupLabelOf(issue) {
    switch (issueView.group) {
        case 'page': return issue.url || '(no URL)';
        case 'category': return issue.category || 'Uncategorised';
        default: return issue.issue || issue.check || 'Unnamed check';
    }
}

function buildIssueRows(list) {
    const rows = [];
    const pushIssue = issue => {
        rows.push({ kind: 'issue', issue });
        if (issueView.expanded.has(issueKeyOf(issue))) {
            rows.push({ kind: 'detail', issue });
        }
    };

    if (issueView.group === 'none') {
        list.forEach(pushIssue);
        return rows;
    }

    groupIssues(list).forEach(group => {
        const collapsed = issueView.collapsed.has(group.key);
        rows.push(Object.assign({}, group, { collapsed }));
        if (!collapsed) group.issues.forEach(pushIssue);
    });
    return rows;
}

function issueColumnCount() {
    return document.querySelectorAll('#issuesTable thead th').length || 5;
}

// The scroller's row renderer. It takes the three row kinds the grouped list
// is made of, and a bare issue object as well, so anything still calling it
// with plain data keeps working.
function renderIssueRow(row, item, index) {
    if (item && item.kind === 'group') {
        renderIssueGroupRow(row, item);
        return;
    }
    if (item && item.kind === 'detail') {
        renderIssueDetailRow(row, item.issue);
        return;
    }
    renderIssueDataRow(row, item && item.kind === 'issue' ? item.issue : item);
}

function renderIssueGroupRow(row, group) {
    row.className = 'issue-group-row';
    row.dataset.groupKey = group.key;

    const cell = document.createElement('td');
    cell.className = 'issue-group-cell';
    cell.colSpan = issueColumnCount();

    const toggle = document.createElement('button');
    toggle.type = 'button';
    // .chip-row is the stylesheet's flex+gap row; it keeps the header legible
    // until .issue-group-toggle has rules of its own.
    toggle.className = 'issue-group-toggle chip-row';
    toggle.setAttribute('aria-expanded', String(!group.collapsed));
    appendIcon(toggle, group.collapsed ? 'chevron-right' : 'chevron-down', { size: 12 },
        group.collapsed ? '+' : '-');

    const severity = ISSUE_SEVERITIES.includes(group.severity) && group.severity !== 'all'
        ? group.severity : 'info';
    const badge = document.createElement('span');
    badge.className = `badge badge-${severity}`;
    badge.textContent = severity;
    toggle.appendChild(badge);

    const label = document.createElement('span');
    label.className = 'issue-group-label';
    label.textContent = group.label;
    toggle.appendChild(label);

    if (group.check) {
        const slug = document.createElement('span');
        slug.className = 'issue-group-check mono text-tertiary';
        slug.textContent = group.check;
        toggle.appendChild(slug);
    }

    const count = document.createElement('span');
    count.className = 'issue-group-count seg-count mono';
    count.textContent = `${group.count}`;
    toggle.appendChild(count);

    toggle.addEventListener('click', () => toggleIssueGroup(group.key));
    cell.appendChild(toggle);
    row.appendChild(cell);
}

function renderIssueDataRow(row, issue) {
    if (!issue) return;

    // Severity travels on the row as data, and is drawn by a .badge in the
    // type cell: the tinted row background and the emoji both broke the
    // 26px row grid and could not be themed.
    const severity = ISSUE_SEVERITIES.includes(issue.type) && issue.type !== 'all' ? issue.type : 'info';
    row.setAttribute('data-issue-type', issue.type);

    const key = issueKeyOf(issue);
    const expanded = issueView.expanded.has(key);
    row.classList.add('issue-row');
    if (expanded) row.classList.add('is-expanded');
    if (issue.status === 'accepted') row.classList.add('issue-accepted');
    row.dataset.issueKey = key;

    const urlCell = makeTextCell(issue.url, 'break-all');
    urlCell.classList.add('issue-url-cell');
    urlCell.title = issue.status === 'accepted' && issue.accepted_reason
        ? `Accepted: ${issue.accepted_reason}`
        : issue.url;

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'issue-expander';
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? 'Hide evidence' : 'Show evidence');
    toggle.title = expanded ? 'Hide evidence' : 'Show evidence';
    appendIcon(toggle, expanded ? 'chevron-down' : 'chevron-right', { size: 12 }, expanded ? '-' : '+');
    toggle.addEventListener('click', event => {
        event.stopPropagation();
        toggleIssueExpanded(key);
    });
    urlCell.insertBefore(toggle, urlCell.firstChild);
    row.appendChild(urlCell);

    const typeCell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = `badge badge-${severity}`;
    badge.textContent = issue.type;
    typeCell.appendChild(badge);
    row.appendChild(typeCell);

    row.appendChild(makeTextCell(issue.category));
    row.appendChild(makeTextCell(issue.issue));

    const detailsCell = makeTextCell(issue.details, 'break-word');
    detailsCell.title = issue.details;
    row.appendChild(detailsCell);

    row.addEventListener('click', () => toggleIssueExpanded(key));
}

function renderIssueDetailRow(row, issue) {
    row.className = 'issue-detail-row';
    const cell = document.createElement('td');
    cell.className = 'issue-detail-cell';
    cell.colSpan = issueColumnCount();
    cell.appendChild(buildIssueEvidence(issue));
    row.appendChild(cell);
}

// --- evidence --------------------------------------------------------------

// The evidence block arrives as an object from the reporting API and can
// arrive as a JSON string from a session payload; a plain string that is not
// JSON is treated as the snippet itself.
function issueEvidenceOf(issue) {
    const raw = issue && issue.evidence;
    if (!raw) return {};
    if (typeof raw === 'string') {
        try {
            const parsed = JSON.parse(raw);
            return parsed && typeof parsed === 'object' ? parsed : { snippet: raw };
        } catch (error) {
            return { snippet: raw };
        }
    }
    return typeof raw === 'object' ? raw : {};
}

function issueSnippetText(issue) {
    const evidence = issueEvidenceOf(issue);
    if (evidence.snippet) return String(evidence.snippet);

    // No snippet on this check: copy what there is, so the button is never a
    // no-op — the developer still gets something they can paste into a ticket.
    const lines = [`${issue.issue || issue.check || 'Issue'} — ${issue.url || ''}`];
    if (evidence.selector) lines.push(`selector: ${evidence.selector}`);
    if (evidence.line) lines.push(`line: ${evidence.line}`);
    if (evidence.expected !== undefined && evidence.expected !== null) lines.push(`expected: ${evidence.expected}`);
    if (evidence.found !== undefined && evidence.found !== null) lines.push(`found: ${evidence.found}`);
    if (issue.details) lines.push(String(issue.details));
    return lines.join('\n');
}

function buildIssueEvidence(issue) {
    const evidence = issueEvidenceOf(issue);
    const box = document.createElement('div');
    box.className = 'issue-evidence';

    const actions = document.createElement('div');
    actions.className = 'issue-evidence-actions chip-row';

    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn btn-secondary btn-sm issue-copy-btn';
    appendIcon(copy, 'copy', { size: 14 });
    copy.appendChild(document.createTextNode(evidence.snippet ? 'Copy snippet' : 'Copy evidence'));
    copy.addEventListener('click', event => {
        event.stopPropagation();
        copyIssueSnippet(issue);
    });
    actions.appendChild(copy);

    if (issue.status !== 'accepted') {
        const accept = document.createElement('button');
        accept.type = 'button';
        accept.className = 'btn btn-ghost btn-sm issue-accept-btn';
        appendIcon(accept, 'check-circle', { size: 14 });
        accept.appendChild(document.createTextNode('Accept…'));
        accept.title = 'Mute this issue with a reason. Un-mute it in the Rules tab.';
        accept.addEventListener('click', event => {
            event.stopPropagation();
            acceptIssueFromList(issue);
        });
        actions.appendChild(accept);
    }

    if (issue.docs_url) {
        const docs = document.createElement('a');
        docs.className = 'url-link issue-docs-link';
        docs.href = issue.docs_url;
        docs.target = '_blank';
        docs.rel = 'noopener noreferrer';
        docs.textContent = 'Documentation';
        actions.appendChild(docs);
    }
    box.appendChild(actions);

    if (issue.status === 'accepted') {
        const muted = document.createElement('div');
        muted.className = 'callout issue-accepted-note';
        muted.textContent = issue.accepted_reason
            ? `Accepted: ${issue.accepted_reason}`
            : 'Accepted (no reason recorded)';
        box.appendChild(muted);
    }

    if (evidence.snippet) {
        const snippet = document.createElement('pre');
        snippet.className = 'issue-snippet mono';
        snippet.textContent = String(evidence.snippet);
        box.appendChild(snippet);
    }

    const facts = [
        ['Selector', evidence.selector],
        ['Line', evidence.line],
        ['Expected', evidence.expected],
        ['Found', evidence.found],
        ['First seen', issue.first_seen_crawl ? `crawl #${issue.first_seen_crawl}` : null]
    ].filter(([, value]) => value !== undefined && value !== null && value !== '');

    if (facts.length) {
        const list = document.createElement('dl');
        list.className = 'issue-evidence-facts';
        facts.forEach(([term, value]) => {
            const dt = document.createElement('dt');
            dt.className = 'issue-evidence-term';
            dt.textContent = term;
            const dd = document.createElement('dd');
            dd.className = 'issue-evidence-value mono';
            dd.textContent = String(value);
            list.appendChild(dt);
            list.appendChild(dd);
        });
        box.appendChild(list);
    }

    if (issue.how_to_fix) {
        const fix = document.createElement('div');
        fix.className = 'callout callout-info issue-how-to-fix';
        appendIcon(fix, 'help-circle', { size: 14 });
        fix.appendChild(document.createTextNode(String(issue.how_to_fix)));
        box.appendChild(fix);
    }

    const related = Array.isArray(evidence.related_urls) ? evidence.related_urls : [];
    if (related.length) {
        const wrap = document.createElement('div');
        wrap.className = 'issue-related';

        const heading = document.createElement('span');
        heading.className = 'issue-evidence-term';
        heading.textContent = `Related URLs (${related.length})`;
        wrap.appendChild(heading);

        const list = document.createElement('ul');
        list.className = 'details-link-list issue-related-list';
        related.slice(0, ISSUE_RELATED_URL_LIMIT).forEach(url => {
            const item = document.createElement('li');
            const link = document.createElement('a');
            link.className = 'url-link';
            link.href = url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = url;
            item.appendChild(link);
            list.appendChild(item);
        });
        wrap.appendChild(list);

        if (related.length > ISSUE_RELATED_URL_LIMIT) {
            const more = document.createElement('p');
            more.className = 'issue-related-more text-tertiary';
            more.textContent = `and ${related.length - ISSUE_RELATED_URL_LIMIT} more — export the issues for the full list`;
            wrap.appendChild(more);
        }
        box.appendChild(wrap);
    }

    return box;
}

async function copyIssueSnippet(issue) {
    const text = issueSnippetText(issue);
    const copied = await copyTextToClipboard(text);
    showNotification(copied ? 'Evidence copied to the clipboard' : 'The evidence could not be copied',
        copied ? 'success' : 'error');
}

// The clipboard API needs a secure context, which a LAN host on plain http is
// not, so the old selection-based copy stays as the fallback.
async function copyTextToClipboard(text) {
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch (error) {
        console.warn('Clipboard API refused the copy, falling back', error);
    }

    try {
        const area = document.createElement('textarea');
        area.className = 'sr-only';
        area.setAttribute('readonly', 'readonly');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        area.setSelectionRange(0, text.length);
        const copied = document.execCommand('copy');
        area.remove();
        return copied;
    } catch (error) {
        console.error('Evidence could not be copied', error);
        return false;
    }
}

// --- expanding, collapsing, accepting --------------------------------------

function toggleIssueExpanded(key) {
    if (issueView.expanded.has(key)) issueView.expanded.delete(key);
    else issueView.expanded.add(key);
    renderIssueList();
}

function toggleIssueGroup(key) {
    if (issueView.collapsed.has(key)) issueView.collapsed.delete(key);
    else issueView.collapsed.add(key);
    renderIssueList();
}

// Muting an issue with a reason. Un-muting lives in the Rules tab, which is
// where the whole muted set is listed.
async function acceptIssueFromList(issue) {
    const id = issue && issue.id;
    if (!id) {
        showNotification('This issue has no stable id yet — open the saved crawl to accept it', 'warning');
        return;
    }

    const reason = window.prompt(`Why is "${issue.issue || issue.check}" acceptable?`, '');
    if (reason === null) return;
    if (!reason.trim()) {
        showNotification('A reason is required to accept an issue', 'warning');
        return;
    }

    try {
        const response = await fetch(`/api/issues/${encodeURIComponent(id)}/accept`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: reason.trim(), base_domain: issueView.baseDomain || undefined })
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.error || `HTTP ${response.status}`);
        }

        markIssueAccepted(id, reason.trim());
        issueView.expanded.delete(issueKeyOf(issue));
        renderIssueList();
        showNotification('Issue accepted. It is muted until you un-mute it in the Rules tab.', 'success');
    } catch (error) {
        showNotification(`The issue could not be accepted: ${error.message}`, 'error');
    }
}

// Mark it in every list that holds it, so the open view drops it immediately
// and "Accepted"/"All" can show it with its reason without a reload.
function markIssueAccepted(id, reason) {
    [window.currentIssues, crawlState.issues, crawlState.pendingIssues, issueView.serverIssues]
        .forEach(list => {
            if (!Array.isArray(list)) return;
            list.forEach(issue => {
                if (issue.id === id) {
                    issue.status = 'accepted';
                    issue.accepted_reason = reason;
                }
            });
        });
}

// --- server-side filters ---------------------------------------------------

let issueServerRefreshTimer = null;

function scheduleIssueServerRefresh(delay = 400) {
    clearTimeout(issueServerRefreshTimer);
    issueServerRefreshTimer = setTimeout(() => refreshIssuesFromServer(), delay);
}

// "New since crawl N" and the status filter are answered by the API, which
// compares the stable issue ids: /api/crawls/<id>/issues?since_crawl=<n>.
// Diffing two lists in the browser would compare the wrong thing.
async function refreshIssuesFromServer() {
    clearTimeout(issueServerRefreshTimer);

    if (!issueServerFiltersActive()) {
        issueView.serverIssues = null;
        renderIssueList();
        return;
    }

    const crawlId = issueCrawlId();
    if (!crawlId) {
        issueView.serverIssues = null;
        showNotification('Open a saved crawl to filter by status or by "new since"', 'warning');
        renderIssueList();
        return;
    }

    const params = new URLSearchParams();
    if (issueView.urlPrefix) params.set('url_prefix', issueView.urlPrefix);
    if (issueView.sinceCrawl) params.set('since_crawl', String(issueView.sinceCrawl));
    if (issueView.status !== 'open') params.set('include_accepted', 'true');
    if (issueView.status === 'accepted') params.set('status', 'accepted');

    const token = ++issueView.token;
    issueView.loading = true;
    try {
        const response = await fetch(`/api/crawls/${crawlId}/issues?${params.toString()}`);
        const data = await response.json();
        if (token !== issueView.token) return;
        if (!response.ok || data.success === false) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        issueView.serverIssues = Array.isArray(data.issues) ? data.issues : [];
    } catch (error) {
        if (token !== issueView.token) return;
        issueView.serverIssues = null;
        showNotification(`The issue list could not be filtered: ${error.message}`, 'error');
    } finally {
        if (token === issueView.token) {
            issueView.loading = false;
            renderIssueList();
        }
    }
}

async function loadIssueCrawlOptions() {
    try {
        const response = await fetch('/api/crawls/list?limit=50');
        const data = await response.json();
        if (!data || !data.success) return;

        const crawls = Array.isArray(data.crawls) ? data.crawls : [];
        const current = issueCrawlId();
        const currentCrawl = crawls.find(crawl => crawl.id === current);
        issueView.baseDomain = currentCrawl ? currentCrawl.base_domain : null;
        issueView.crawls = crawls.filter(crawl =>
            crawl.id !== current &&
            (!issueView.baseDomain || crawl.base_domain === issueView.baseDomain));
        syncIssueControls();
    } catch (error) {
        console.warn('Issues: the crawl list could not be loaded', error);
    }
}

function issueCrawlLabel(crawl) {
    const when = crawl.completed_at || crawl.started_at || '';
    const stamp = when ? String(when).replace('T', ' ').slice(0, 16) : 'unknown date';
    return `Crawl #${crawl.id} · ${stamp}`;
}

// --- the controls in the subbar -------------------------------------------

function ensureIssueControls() {
    const subbar = document.querySelector('#issues-tab .pane-subbar');
    if (!subbar) return null;

    const existing = document.getElementById('issueViewControls');
    if (existing) return existing;

    const bar = document.createElement('div');
    bar.id = 'issueViewControls';
    bar.className = 'issue-controls chip-row';
    bar.setAttribute('role', 'group');
    bar.setAttribute('aria-label', 'Issue grouping and filters');

    const group = issueSelect('issueGroupBy', ISSUE_GROUPINGS.map(value => [value, ISSUE_GROUP_LABELS[value]]));
    group.addEventListener('change', () => setIssueGrouping(group.value));
    bar.appendChild(issueControl('Group by', group));

    const since = issueSelect('issueSinceCrawl', [['', 'Any crawl']]);
    since.addEventListener('change', () => setIssueSinceCrawl(since.value));
    bar.appendChild(issueControl('New since', since));

    const status = issueSelect('issueStatusFilter',
        ISSUE_STATUS_FILTERS.map(value => [value, ISSUE_STATUS_LABELS[value]]));
    status.addEventListener('change', () => setIssueStatusFilter(status.value));
    bar.appendChild(issueControl('Status', status));

    const field = document.createElement('div');
    field.className = 'search-field issue-prefix-field';
    appendIcon(field, 'search', { size: 14 });
    const prefix = document.createElement('input');
    prefix.type = 'search';
    prefix.id = 'issueUrlPrefix';
    prefix.className = 'issue-prefix-input';
    prefix.placeholder = 'Section: URL prefix…';
    prefix.setAttribute('aria-label', 'Filter issues by URL prefix');
    prefix.addEventListener('input', () => scheduleIssuePrefix(prefix.value));
    prefix.addEventListener('change', () => setIssueUrlPrefix(prefix.value));
    field.appendChild(prefix);
    bar.appendChild(field);

    subbar.appendChild(bar);
    return bar;
}

function issueControl(labelText, control) {
    const wrap = document.createElement('label');
    wrap.className = 'issue-control chip-row';
    wrap.setAttribute('for', control.id);

    const label = document.createElement('span');
    label.className = 'issue-control-label';
    label.textContent = labelText;
    wrap.appendChild(label);
    wrap.appendChild(control);
    return wrap;
}

function issueSelect(id, options) {
    const select = document.createElement('select');
    select.id = id;
    select.className = 'select';
    options.forEach(([value, label]) => select.appendChild(new Option(label, value)));
    return select;
}

function syncIssueControls() {
    const bar = document.getElementById('issueViewControls');
    if (!bar) return;

    const group = document.getElementById('issueGroupBy');
    if (group && group.value !== issueView.group) group.value = issueView.group;

    const status = document.getElementById('issueStatusFilter');
    if (status && status.value !== issueView.status) status.value = issueView.status;

    const prefix = document.getElementById('issueUrlPrefix');
    if (prefix && document.activeElement !== prefix && prefix.value !== issueView.urlPrefix) {
        prefix.value = issueView.urlPrefix;
    }

    const since = document.getElementById('issueSinceCrawl');
    if (!since) return;

    // Rebuilding the options under the pointer on every poll would close the
    // open dropdown, so it is only done when the crawl list actually changed.
    const signature = issueView.crawls.map(crawl => crawl.id).join(',');
    if (since.dataset.signature !== signature) {
        since.dataset.signature = signature;
        since.textContent = '';
        since.appendChild(new Option('Any crawl', ''));
        issueView.crawls.forEach(crawl => since.appendChild(new Option(issueCrawlLabel(crawl), String(crawl.id))));
    }
    const wanted = issueView.sinceCrawl ? String(issueView.sinceCrawl) : '';
    if (since.value !== wanted) {
        if (wanted && !Array.from(since.options).some(option => option.value === wanted)) {
            since.appendChild(new Option(`Crawl #${wanted}`, wanted));
        }
        since.value = wanted;
    }
}

function setIssueGrouping(value) {
    const group = ISSUE_GROUPINGS.includes(value) ? value : 'check';
    if (group === issueView.group) return;
    issueView.group = group;
    issueView.collapsed.clear();
    writePref(ISSUE_GROUP_PREF_KEY, group);
    renderIssueList();
    scheduleIssueUrlWrite('push');
}

function setIssueStatusFilter(value) {
    const status = ISSUE_STATUS_FILTERS.includes(value) ? value : 'open';
    if (status === issueView.status) return;
    issueView.status = status;
    refreshIssuesFromServer();
    scheduleIssueUrlWrite('push');
}

function setIssueSinceCrawl(value) {
    const id = Number(value) || null;
    if (id === issueView.sinceCrawl) return;
    issueView.sinceCrawl = id;
    refreshIssuesFromServer();
    scheduleIssueUrlWrite('push');
}

let issuePrefixTimer = null;

function scheduleIssuePrefix(value) {
    clearTimeout(issuePrefixTimer);
    issuePrefixTimer = setTimeout(() => setIssueUrlPrefix(value), 250);
}

function setIssueUrlPrefix(value) {
    clearTimeout(issuePrefixTimer);
    const prefix = String(value || '').trim();
    if (prefix === issueView.urlPrefix) return;
    issueView.urlPrefix = prefix;
    if (issueServerFiltersActive()) scheduleIssueServerRefresh();
    else renderIssueList();
    scheduleIssueUrlWrite('replace');
}

// --- the view in the address bar ------------------------------------------

function issueUrlValues() {
    // Only meaningful while the Issues tab is the one on screen.
    if (!isIssuesTabActive()) return {};
    return {
        // Only a non-default grouping is worth carrying: otherwise every
        // shared link would read ?group=check for no reason.
        group: issueView.group !== ISSUE_GROUP_DEFAULT ? issueView.group : null,
        issue_status: issueView.status !== 'open' ? issueView.status : null,
        since_crawl: issueView.sinceCrawl ? String(issueView.sinceCrawl) : null,
        issue_prefix: issueView.urlPrefix || null
    };
}

function applyIssueUrlValues(query) {
    const group = query.get('group');
    if (group && ISSUE_GROUPINGS.includes(group)) issueView.group = group;

    const status = query.get('issue_status');
    issueView.status = status && ISSUE_STATUS_FILTERS.includes(status) ? status : 'open';

    issueView.sinceCrawl = Number(query.get('since_crawl')) || null;
    issueView.urlPrefix = query.get('issue_prefix') || '';
}

function readIssueViewFromUrl() {
    applyIssueUrlValues(new URLSearchParams(location.search));
}

// router.js owns the address bar. If it grows a registration point, these
// params become part of the view it builds; until then they are merged in
// here and re-merged after the router has written its own URL.
function registerIssueUrlParams() {
    const router = window.Router;
    if (!router || typeof router.registerParam !== 'function') return false;
    ISSUE_URL_PARAMS.forEach(name => {
        router.registerParam(name, {
            read: value => {
                const query = new URLSearchParams(location.search);
                query.set(name, value == null ? '' : String(value));
                applyIssueUrlValues(query);
            },
            write: () => {
                const value = issueUrlValues()[name];
                return value == null ? null : String(value);
            }
        });
    });
    return true;
}

function writeIssueUrl(mode = 'replace') {
    if (issueView.routerOwnsParams) return;

    const url = new URL(location.href);
    const values = issueUrlValues();
    ISSUE_URL_PARAMS.forEach(name => {
        const value = values[name];
        if (value === null || value === undefined || value === '') url.searchParams.delete(name);
        else url.searchParams.set(name, value);
    });

    const query = url.searchParams.toString();
    const next = url.pathname + (query ? `?${query}` : '');
    if (next === location.pathname + location.search) return;
    history[mode === 'push' ? 'pushState' : 'replaceState'](null, '', next);
}

// The router rebuilds the query string from the params it knows about, so a
// write has to land after its own. The second, later write covers its async
// restore (a Back/Forward step reloads the crawl before it normalises the URL).
function scheduleIssueUrlWrite(mode = 'replace') {
    setTimeout(() => writeIssueUrl(mode), 0);
    setTimeout(() => writeIssueUrl('replace'), 320);
}

// Wrap the globals the router syncs on, outside router.js, so the issue view
// survives a tab switch or a severity click.
function keepIssueParamsInUrl() {
    if (issueView.routerOwnsParams) return;
    ['switchTab', 'filterIssues', 'toggleFilter', 'showUrlDetails', 'closeUrlDetails']
        .forEach(name => {
            const original = window[name];
            if (typeof original !== 'function' || original.issueParamsWrapped) return;
            const wrapped = function (...args) {
                const result = original.apply(this, args);
                if (result && typeof result.then === 'function') {
                    return result.then(value => { scheduleIssueUrlWrite('replace'); return value; });
                }
                scheduleIssueUrlWrite('replace');
                return result;
            };
            wrapped.issueParamsWrapped = true;
            window[name] = wrapped;
        });
}

// --- setup ----------------------------------------------------------------

function initIssuesView() {
    if (issueView.initialised) return;
    if (!ensureIssueControls()) return;
    issueView.initialised = true;

    issueView.group = readPref(ISSUE_GROUP_PREF_KEY, ISSUE_GROUPINGS, 'check');
    issueView.routerOwnsParams = registerIssueUrlParams();
    readIssueViewFromUrl();
    keepIssueParamsInUrl();

    window.addEventListener('popstate', () => {
        readIssueViewFromUrl();
        if (issueServerFiltersActive()) refreshIssuesFromServer();
        else renderIssueList();
        scheduleIssueUrlWrite('replace');
    });

    loadIssueCrawlOptions();
    syncIssueControls();

    if (issueServerFiltersActive()) refreshIssuesFromServer();
    else renderIssueList();
    scheduleIssueUrlWrite('replace');
}

// ===========================================================================
// Viewer preferences: theme and density
// ===========================================================================
//
// Both are per-viewer, stored in localStorage and applied as attributes on
// <html> so the stylesheet does the work. Every localStorage access is wrapped:
// it throws outright in a private window or when site data is blocked, and a
// theme toggle is not worth taking the app down for.

const PREF_KEYS = { theme: 'toadstone.theme', density: 'toadstone.density' };
const THEMES = ['light', 'dark', 'system'];
const DENSITIES = ['comfortable', 'compact'];
const COMPACT_MIN_WIDTH = 768;   // below this, touch targets win: comfortable

function readPref(key, allowed, fallback) {
    try {
        const value = localStorage.getItem(key);
        return allowed.includes(value) ? value : fallback;
    } catch (error) {
        return fallback;
    }
}

function writePref(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (error) {
        console.warn('Preference could not be stored:', error);
    }
}

function getTheme() {
    return readPref(PREF_KEYS.theme, THEMES, 'system');
}

function getDensity() {
    return readPref(PREF_KEYS.density, DENSITIES, 'comfortable');
}

// "system" means no data-theme attribute at all, so prefers-color-scheme wins
function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === 'light' || theme === 'dark') {
        root.dataset.theme = theme;
    } else {
        delete root.dataset.theme;
    }
    syncThemeControls(theme);
}

function setTheme(theme) {
    const value = THEMES.includes(theme) ? theme : 'system';
    writePref(PREF_KEYS.theme, value);
    applyTheme(value);
    return value;
}

function cycleTheme() {
    return setTheme(THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length]);
}

// The stored preference is kept, but narrow viewports are always comfortable
function effectiveDensity(density) {
    return density === 'compact' && window.innerWidth >= COMPACT_MIN_WIDTH ? 'compact' : 'comfortable';
}

function applyDensity(density) {
    document.documentElement.dataset.density = effectiveDensity(density);
    syncDensityControls(density);
    applyDensityToScrollers();
}

function setDensity(density) {
    const value = DENSITIES.includes(density) ? density : 'comfortable';
    writePref(PREF_KEYS.density, value);
    applyDensity(value);
    return value;
}

function toggleDensity() {
    return setDensity(getDensity() === 'compact' ? 'comfortable' : 'compact');
}

// The row height the stylesheet is currently using. The scrollers take it as
// their initial estimate, so the first paint is not a jump from a hard-coded
// guess to the real height.
function cssRowHeight() {
    const raw = getComputedStyle(document.documentElement).getPropertyValue('--row-h');
    const value = parseFloat(raw);
    return Number.isFinite(value) && value > 0 ? value : 34;
}

function rowHeightFor(name, base) {
    // Overview rows carry a Details button, so they sit a little taller
    return name === 'overview' ? base + 6 : base;
}

// Rows got taller or shorter. Hand every scroller the new height and force a
// rebuild: the ResizeObserver watches the *container*, whose height has not
// changed, and render() is a no-op while the visible window has not moved, so
// without this the spacer math keeps the old estimate until the next scroll.
function applyDensityToScrollers() {
    const base = cssRowHeight();
    Object.entries(virtualScrollers).forEach(([name, scroller]) => {
        if (!scroller) return;
        scroller.setRowHeight(rowHeightFor(name, base));
        scroller.scheduleRender();
    });
}

function syncThemeControls(theme) {
    const host = document.getElementById('themeToggle');
    if (!host) return;
    const options = host.querySelectorAll('[data-theme]');
    if (options.length) {
        options.forEach(el => el.setAttribute('aria-pressed', String(el.dataset.theme === theme)));
        return;
    }
    host.dataset.themeState = theme;
    host.setAttribute('aria-label', `Theme: ${theme}`);
    host.title = `Theme: ${theme} — click to change`;
    const label = host.querySelector('.theme-toggle-label');
    if (label) label.textContent = theme;
}

// Two markups are supported: a segmented group whose buttons carry
// data-density, and the shell's pair of #densityComfortable / #densityCompact
// buttons.
function densityControls() {
    const controls = [];
    document.querySelectorAll('#densityToggle [data-density]').forEach(el => {
        controls.push([el, el.dataset.density]);
    });
    const comfortable = document.getElementById('densityComfortable');
    const compact = document.getElementById('densityCompact');
    if (comfortable) controls.push([comfortable, 'comfortable']);
    if (compact) controls.push([compact, 'compact']);
    return controls;
}

function syncDensityControls(density) {
    densityControls().forEach(([el, value]) => {
        el.setAttribute('aria-pressed', String(value === density));
        el.classList.toggle('active', value === density);
    });
}

// The shell markup owns these controls; this is the fallback that keeps the
// preferences reachable if they are not in the page yet.
function ensurePreferenceControls() {
    const hasDensity = densityControls().length > 0;
    if (hasDensity && document.getElementById('themeToggle')) return;

    const host = document.querySelector('#subbar, .subbar, #prefControls, .pref-controls')
        || createPreferenceHost();
    if (!host) return;

    if (!hasDensity) {
        const group = document.createElement('div');
        group.id = 'densityToggle';
        group.className = 'segmented';
        group.setAttribute('role', 'group');
        group.setAttribute('aria-label', 'Row density');
        [['comfortable', '≡', 'Comfortable rows'], ['compact', '≣', 'Compact rows']]
            .forEach(([value, glyph, title]) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'segmented-btn';
                button.dataset.density = value;
                button.title = title;
                button.setAttribute('aria-label', title);
                button.textContent = glyph;
                group.appendChild(button);
            });
        host.appendChild(group);
    }

    if (!document.getElementById('themeToggle')) {
        const button = document.createElement('button');
        button.type = 'button';
        button.id = 'themeToggle';
        button.className = 'btn btn-ghost btn-sm';
        button.innerHTML = `${iconMarkup('theme')} <span class="theme-toggle-label"></span>`;
        host.appendChild(button);
    }
}

function createPreferenceHost() {
    const parent = document.querySelector('.main-content, .content-area, main') || document.body;
    if (!parent) return null;
    const host = document.createElement('div');
    host.id = 'prefControls';
    host.className = 'pref-controls';
    parent.insertBefore(host, parent.firstChild);
    return host;
}

function setupPreferenceControls() {
    ensurePreferenceControls();
    applyTheme(getTheme());
    applyDensity(getDensity());

    document.addEventListener('click', event => {
        const density = event.target.closest('#densityToggle [data-density]');
        if (density) {
            setDensity(density.dataset.density);
            return;
        }
        if (event.target.closest('#densityComfortable')) {
            setDensity('comfortable');
            return;
        }
        if (event.target.closest('#densityCompact')) {
            setDensity('compact');
            return;
        }
        const themeOption = event.target.closest('#themeToggle [data-theme]');
        if (themeOption) {
            setTheme(themeOption.dataset.theme);
            return;
        }
        if (event.target.closest('#themeToggle')) cycleTheme();
    });

    // The rail facets are <div role="button" tabindex="0"> with an onclick,
    // so a click works but Enter/Space would not without this.
    document.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const facet = event.target.closest && event.target.closest('.filter-item[onclick^="toggleFilter("]');
        if (!facet) return;
        event.preventDefault();
        const name = filterNameOf(facet);
        if (name) toggleFilter(name);
    });

    // Crossing 768px re-evaluates the forced-comfortable rule
    const wide = window.matchMedia(`(min-width: ${COMPACT_MIN_WIDTH}px)`);
    const onChange = () => applyDensity(getDensity());
    if (typeof wide.addEventListener === 'function') wide.addEventListener('change', onChange);
    else if (typeof wide.addListener === 'function') wide.addListener(onChange);
}

// Apply the stored preferences as early as this file runs, so the first paint
// is already in the right theme and density. index.html also carries an inline
// head script that does this before the stylesheet loads.
try {
    applyTheme(getTheme());
    document.documentElement.dataset.density = effectiveDensity(getDensity());
} catch (error) {
    console.warn('Could not apply stored preferences:', error);
}

// ===========================================================================
// Active-filter chips
// ===========================================================================

function activeChips() {
    const chips = activeFilterList().map(name => ({
        kind: 'filter', value: name, label: FILTER_LABELS[name]
    }));

    const severity = crawlState.filters.issueFilter;
    if (severity && severity !== 'all') {
        chips.push({ kind: 'severity', value: severity, label: `severity: ${severity}` });
    }

    const links = crawlState.filters.linksFilter;
    if (links.internalStatusCode && links.internalStatusCode !== 'all') {
        chips.push({ kind: 'links_status', value: links.internalStatusCode, label: `links: ${links.internalStatusCode}` });
    }
    if (links.internalSearch) {
        chips.push({ kind: 'links_q', value: links.internalSearch, label: `q: "${links.internalSearch}"` });
    }
    if (links.externalStatusCode && links.externalStatusCode !== 'all') {
        chips.push({ kind: 'ext_status', value: links.externalStatusCode, label: `external: ${links.externalStatusCode}` });
    }
    if (links.externalSearch) {
        chips.push({ kind: 'ext_q', value: links.externalSearch, label: `external q: "${links.externalSearch}"` });
    }

    return chips;
}

function ensureChipBar() {
    // The shell's subbar owns the container (#filterChips, or #activeFilterChips
    // where that is what the markup calls it). Only when neither exists does
    // this create one, so the chips always sit where the design puts them.
    const existing = document.getElementById('activeFilterChips') || document.getElementById('filterChips');
    if (existing) return existing;

    const host = document.querySelector('.subbar-left, #subbar, .subbar, #prefControls, .pref-controls');
    if (!host) return null;

    const bar = document.createElement('div');
    bar.id = 'activeFilterChips';
    bar.className = 'filter-chips';
    bar.setAttribute('aria-live', 'polite');
    host.appendChild(bar);
    return bar;
}

function renderFilterChips() {
    const bar = ensureChipBar();
    if (!bar) return;

    const chips = activeChips();

    // Rebuild only when the set actually changed: applyLinksFilter() runs on
    // every poll while a crawl is live, and re-creating the chips under the
    // pointer for no reason makes them flicker.
    const signature = chips.map(chip => `${chip.kind}=${chip.value}`).join('|');
    const upToDate = bar.dataset.chipSignature === signature
        && bar.querySelectorAll('.filter-chip').length === chips.length;
    if (upToDate) return;
    bar.dataset.chipSignature = signature;

    bar.textContent = '';
    // Only a chip row this file created is hidden outright; the shell's own
    // container is left visible so nothing else that writes into it disappears
    if (bar.id === 'activeFilterChips') bar.hidden = chips.length === 0;

    chips.forEach(chip => {
        // .chip / .chip-label / .chip-remove are the stylesheet's names; the
        // filter-* aliases carry the data this file reads back.
        const el = document.createElement('span');
        el.className = 'chip filter-chip';
        el.dataset.chipKind = chip.kind;
        el.dataset.chipValue = chip.value;

        const label = document.createElement('span');
        label.className = 'chip-label filter-chip-label';
        label.textContent = chip.label;
        el.appendChild(label);

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'chip-remove filter-chip-remove';
        remove.setAttribute('aria-label', `Remove filter ${chip.label}`);
        remove.title = `Remove filter ${chip.label}`;
        appendIcon(remove, 'x', { size: 12 }, '\u00d7');
        remove.addEventListener('click', () => removeChip(chip));
        el.appendChild(remove);

        bar.appendChild(el);
    });

    // "Clear all": the shell's button when it is in the markup, otherwise one
    // appended to the chip row
    const shellClear = document.getElementById('clearFiltersBtn');
    if (shellClear) {
        if (shellClear.dataset.wired !== 'true') {
            shellClear.dataset.wired = 'true';
            shellClear.addEventListener('click', () => clearAllFilters());
        }
        shellClear.hidden = chips.length === 0;
    } else if (chips.length) {
        const clear = document.createElement('button');
        clear.type = 'button';
        clear.className = 'filter-chip-clear btn btn-ghost btn-sm';
        clear.textContent = 'Clear all';
        clear.addEventListener('click', () => clearAllFilters());
        bar.appendChild(clear);
    }

    // The rail's "Filters (n)" badge counts the same set
    const railCount = document.getElementById('railToggleCount');
    if (railCount) railCount.textContent = String(chips.length);
}

// Removing a chip goes through the same global function the control itself
// calls, so the router records the change exactly once.
function removeChip(chip) {
    switch (chip.kind) {
        case 'filter':
            toggleFilter(chip.value);
            break;
        case 'severity':
            filterIssues('all');
            break;
        case 'links_status':
            setControlValue('select[onchange="filterInternalLinks(this.value)"]', 'all');
            filterInternalLinks('all');
            break;
        case 'links_q':
            setControlValue('input[oninput="searchInternalLinks(this.value)"]', '');
            searchInternalLinks('');
            break;
        case 'ext_status':
            setControlValue('select[onchange="filterExternalLinks(this.value)"]', 'all');
            filterExternalLinks('all');
            break;
        case 'ext_q':
            setControlValue('input[oninput="searchExternalLinks(this.value)"]', '');
            searchExternalLinks('');
            break;
    }
}

// ===========================================================================
// Table state: sorting, empty states, loading skeletons
// ===========================================================================

// The one scrolling element per virtualised table (spec §8, invariant 3)
const TABLE_CONTAINERS = {
    overview: '#overview-tab .table-container',
    internal: '#internal-tab .table-container',
    external: '#external-tab .table-container',
    issues: '#issues-tab .table-container',
    internalLinks: '#links-tab .internal-links-container',
    externalLinks: '#links-tab .external-links-container'
};

// Column index -> the field it sorts on. null = not sortable (derived cells)
const SORT_FIELDS = {
    overview: ['url', 'status_code', 'title', 'meta_description', 'h1', 'keywords',
        'word_count', 'response_time', null, null, null, null, null, null, null],
    internal: ['url', 'status_code', 'content_type', 'size', 'title'],
    external: ['url', 'status_code', 'content_type', 'size', 'title'],
    internalLinks: ['source_url', 'target_url', 'target_status', 'anchor_text', 'placement'],
    externalLinks: ['source_url', 'target_url', 'target_status', 'target_domain', 'placement'],
    issues: ['url', 'type', 'category', 'issue', 'details']
};

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

const tableSort = {};   // scroller name -> { index, direction }

// Every write into a virtual scroller goes through here, so the active sort
// and the empty state are applied in one place.
function setScrollerData(name, rows) {
    const scroller = virtualScrollers[name];
    if (!scroller) return;
    const data = Array.isArray(rows) ? rows : [];
    scroller.setData(sortRows(name, data));
    updateTableState(name, data.length);
}

function sortRows(name, rows) {
    const state = tableSort[name];
    const field = state && (SORT_FIELDS[name] || [])[state.index];
    if (!field) return rows;

    const direction = state.direction === 'descending' ? -1 : 1;
    return [...rows].sort((a, b) => direction * compareSortValues(sortValue(a, field), sortValue(b, field)));
}

function sortValue(row, field) {
    if (field === 'type') return SEVERITY_ORDER[row.type] ?? 3;
    const value = row[field];
    return value === undefined || value === null ? '' : value;
}

function compareSortValues(a, b) {
    const numberA = typeof a === 'number' ? a : Number(a);
    const numberB = typeof b === 'number' ? b : Number(b);
    if (a !== '' && b !== '' && Number.isFinite(numberA) && Number.isFinite(numberB)) {
        return numberA - numberB;
    }
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

function initTableSorting() {
    Object.entries(TABLE_CONTAINERS).forEach(([name, selector]) => {
        const container = document.querySelector(selector);
        const headerRow = container?.querySelector('thead tr');
        if (!headerRow) return;

        Array.from(headerRow.children).forEach((th, index) => {
            if (!(SORT_FIELDS[name] || [])[index]) {
                th.removeAttribute('aria-sort');
                return;
            }
            // The chevron is drawn off aria-sort; the hit target is the inner
            // span so column-resize.js keeps its absolutely-positioned grip
            const target = th.querySelector('.th-inner') || th;
            if (!th.hasAttribute('aria-sort')) th.setAttribute('aria-sort', 'none');
            target.setAttribute('role', 'button');
            if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '0');

            const activate = event => {
                if (event.target.closest && event.target.closest('.column-resize-grip')) return;
                toggleSort(name, index);
            };
            target.addEventListener('click', activate);
            target.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    activate(event);
                }
            });
        });
    });
}

function toggleSort(name, index) {
    const current = tableSort[name];
    tableSort[name] = {
        index,
        direction: current && current.index === index && current.direction === 'ascending'
            ? 'descending' : 'ascending'
    };
    syncSortIndicators(name);

    // The Issues table is a stream of group headers, rows and evidence
    // blocks: re-sorting that array in place would scramble it, so the whole
    // view is rebuilt from the issues instead.
    if (name === 'issues') {
        renderIssueList();
        return;
    }

    const scroller = virtualScrollers[name];
    if (scroller) {
        scroller.setData(sortRows(name, scroller.data));
    }
}

function syncSortIndicators(name) {
    const container = document.querySelector(TABLE_CONTAINERS[name]);
    const headerRow = container?.querySelector('thead tr');
    if (!headerRow) return;
    const state = tableSort[name];
    Array.from(headerRow.children).forEach((th, index) => {
        if (!(SORT_FIELDS[name] || [])[index]) return;
        th.setAttribute('aria-sort', state && state.index === index ? state.direction : 'none');
    });
}

function emptyStateFor(name, container) {
    if (name === 'issues') {
        const existing = document.getElementById('issuesEmptyState');
        if (existing) return existing;
    }

    let el = container.querySelector(':scope > .table-empty');
    if (el) return el;

    el = document.createElement('div');
    el.className = 'table-empty empty-state';
    el.style.display = 'none';
    el.innerHTML = `<div class="empty-state-content">${iconMarkup('table', { size: 32 })}<h3></h3><p></p></div>`;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-secondary';
    button.textContent = 'Clear filters';
    button.hidden = true;
    button.addEventListener('click', () => clearAllFilters());
    el.querySelector('.empty-state-content').appendChild(button);

    container.appendChild(el);
    return el;
}

function updateTableState(name, count) {
    const container = document.querySelector(TABLE_CONTAINERS[name]);
    if (!container) return;

    const table = container.querySelector('table');
    const empty = emptyStateFor(name, container);
    if (!empty) return;

    const filtered = activeFilterList().length > 0
        || (name === 'issues' && crawlState.filters.issueFilter !== 'all')
        || (name === 'internalLinks' && (crawlState.filters.linksFilter.internalStatusCode !== 'all' || crawlState.filters.linksFilter.internalSearch))
        || (name === 'externalLinks' && (crawlState.filters.linksFilter.externalStatusCode !== 'all' || crawlState.filters.linksFilter.externalSearch));

    if (count > 0) {
        empty.style.display = 'none';
        if (table) table.style.display = 'table';
        return;
    }

    // #issuesEmptyState ships its own copy and icon; only the generated ones
    // get their text rewritten
    if (empty.classList.contains('table-empty')) {
        const heading = empty.querySelector('h3');
        const body = empty.querySelector('p');
        const button = empty.querySelector('button');
        // icons.js marks each icon with .icon-<name>, so the glyph only gets
        // swapped when the reason the table is empty changed
        const glyph = empty.querySelector('svg.icon');
        const wanted = filtered ? 'filter' : 'table';
        if (glyph && !glyph.classList.contains(`icon-${wanted}`)) {
            const replacement = iconNode(wanted, { size: 32 });
            if (replacement) glyph.replaceWith(replacement);
        }
        if (heading) heading.textContent = filtered ? 'Nothing matches these filters' : 'No rows yet';
        if (body) {
            body.textContent = filtered
                ? 'Remove a filter to see more of this crawl.'
                : 'Run or open a crawl to fill this table.';
        }
        if (button) button.hidden = !filtered;
    }

    empty.style.display = 'block';
    if (table) table.style.display = 'none';
}

function updateAllTableStates() {
    Object.keys(TABLE_CONTAINERS).forEach(name => {
        const scroller = virtualScrollers[name];
        if (scroller) updateTableState(name, scroller.data.length);
    });
}

// ---- Loading skeletons ----------------------------------------------------
//
// Skeleton rows go into <tbody> above the scroller's bottom spacer, so the
// scroller's own next render removes them along with the other data rows.

function showTableSkeleton(name, rowCount = 10) {
    const container = document.querySelector(TABLE_CONTAINERS[name]);
    const tbody = container?.querySelector('tbody');
    if (!tbody) return;

    clearTableSkeleton(name);

    const columns = container.querySelectorAll('thead th').length || 1;
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < rowCount; i++) {
        const row = document.createElement('tr');
        row.className = 'skeleton-row';
        row.setAttribute('aria-hidden', 'true');
        for (let column = 0; column < columns; column++) {
            const cell = document.createElement('td');
            const bar = document.createElement('span');
            bar.className = 'skeleton';
            cell.appendChild(bar);
            row.appendChild(cell);
        }
        fragment.appendChild(row);
    }

    const scroller = virtualScrollers[name];
    if (scroller && scroller.bottomSpacer && scroller.bottomSpacer.parentNode === tbody) {
        tbody.insertBefore(fragment, scroller.bottomSpacer);
    } else {
        tbody.appendChild(fragment);
    }

    const table = container.querySelector('table');
    if (table) table.style.display = 'table';
    const empty = emptyStateFor(name, container);
    if (empty) empty.style.display = 'none';
}

function clearTableSkeleton(name) {
    const container = document.querySelector(TABLE_CONTAINERS[name]);
    container?.querySelectorAll('tr.skeleton-row').forEach(row => row.remove());
}

function setTablesLoading(isLoading) {
    Object.keys(TABLE_CONTAINERS).forEach(name => {
        if (isLoading) showTableSkeleton(name);
        else clearTableSkeleton(name);
    });
}

// ===========================================================================
// Icons
// ===========================================================================
//
// icons.js (loaded before this file) owns the drawings. These wrappers keep
// every call site safe if it is ever missing, and keep colour where it
// belongs: on the element around the icon, never on the icon.

function iconMarkup(name, options) {
    return typeof icon === 'function' ? icon(name, options) : '';
}

function iconNode(name, options) {
    return typeof iconElement === 'function' ? iconElement(name, options) : null;
}

// Draw the <i data-icon="…"> placeholders inside markup injected after load
function paintIcons(root) {
    if (root && typeof renderIcons === 'function') renderIcons(root);
}

// Put an icon in `element`, falling back to `text` when icons.js is absent
function appendIcon(element, name, options, text) {
    const node = iconNode(name, options);
    if (node) element.appendChild(node);
    else if (text) element.textContent = text;
    return node;
}

// An icon plus the word, in the details popup. The tone class colours both.
function yesNo(value) {
    return value
        ? `<span class="text-ok">${iconMarkup('check-circle', { size: 14 })} Yes</span>`
        : `<span class="text-muted">${iconMarkup('x-circle', { size: 14 })} No</span>`;
}

// ===========================================================================
// Export menu
// ===========================================================================
//
// The exports API (src/api/exports) streams a dataset straight to the browser,
// so every download here is a plain navigation on an <a download>: a 500k-row
// export must not be buffered in the tab as a blob first.
//
// The catalogue at /api/export/datasets says which datasets and formats exist,
// so this menu is built from the server's answer rather than a list baked in
// here. The filters on screen ride along in the query string, using the names
// the reporting API already uses (severity, internal, url_prefix, ...).

const EXPORT_FORMAT_KEY = 'toadstone.exportFormats';

const EXPORT_FALLBACK_CATALOGUE = {
    formats: ['csv', 'json', 'ndjson', 'xlsx'],
    bundle_formats: ['xlsx', 'zip'],
    report_formats: ['html', 'md'],
    datasets: []
};

// Which dataset a tab is looking at
const TAB_DATASETS = {
    overview: 'pages',
    internal: 'pages',
    external: 'pages',
    'status-codes': 'pages',
    links: 'links',
    issues: 'issues',
    pagespeed: 'pages',
    visualization: 'graph-edges',
    sitemap: 'sitemap-coverage',
    compare: 'issues',
    rules: 'issues'
};

let exportCatalogue = null;
let exportCataloguePromise = null;

function currentTabName() {
    const active = document.querySelector('.tab-btn.active');
    const match = active?.getAttribute('onclick')?.match(/switchTab\('([^']+)'\)/);
    return match ? match[1] : (active?.dataset.pluginId || 'overview');
}

async function loadExportCatalogue() {
    if (exportCatalogue) return exportCatalogue;
    if (!exportCataloguePromise) {
        exportCataloguePromise = (async () => {
            try {
                const response = await fetch('/api/export/datasets');
                const data = await response.json();
                exportCatalogue = data && data.success ? data : EXPORT_FALLBACK_CATALOGUE;
            } catch (error) {
                console.warn('Export catalogue could not be read:', error);
                exportCatalogue = EXPORT_FALLBACK_CATALOGUE;
            }
            return exportCatalogue;
        })();
    }
    return exportCataloguePromise;
}

function datasetForCurrentTab() {
    const wanted = TAB_DATASETS[currentTabName()] || 'pages';
    const known = exportCatalogue?.datasets || [];
    if (!known.length) return wanted;
    return known.some(entry => entry.dataset === wanted) ? wanted : 'pages';
}

function datasetAbout(dataset) {
    return (exportCatalogue?.datasets || []).find(entry => entry.dataset === dataset)?.about || '';
}

// Remembered per viewer, per menu row
function readExportFormats() {
    try {
        const stored = JSON.parse(localStorage.getItem(EXPORT_FORMAT_KEY) || '{}');
        return stored && typeof stored === 'object' ? stored : {};
    } catch (error) {
        return {};
    }
}

function rememberExportFormat(kind, format) {
    const formats = readExportFormats();
    formats[kind] = format;
    try {
        localStorage.setItem(EXPORT_FORMAT_KEY, JSON.stringify(formats));
    } catch (error) {
        console.warn('Export format preference could not be stored:', error);
    }
}

function preferredExportFormat(kind, available) {
    const stored = readExportFormats()[kind];
    return available.includes(stored) ? stored : available[0];
}

// The filters on screen, in the names the exports API understands. The rail
// maps straight through: response-code facets are status_class, content-type
// facets are content_type, Internal/External is internal, Issues severity is
// severity. A filter only goes on the URL when the dataset's rows can answer
// it — the API skips one whose column its rows do not have, and a filter that
// was quietly skipped would export more than the screen shows.

// param -> the column a row needs for the API to apply it
const EXPORT_FILTER_COLUMNS = {
    internal: 'is_internal',
    severity: 'type',
    status_class: 'status_code',
    content_type: 'content_type'
};

function datasetColumns(dataset) {
    return (exportCatalogue?.datasets || []).find(entry => entry.dataset === dataset)?.columns || null;
}

function datasetSupports(dataset, param) {
    const columns = datasetColumns(dataset);
    if (!columns) return true;   // no catalogue in hand: let the server decide
    return columns.includes(EXPORT_FILTER_COLUMNS[param] || param);
}

// { params: URLSearchParams, applied: [names], unapplied: [labels] }
function exportFilterPlan(dataset) {
    const params = new URLSearchParams();
    const unapplied = [];
    const filters = activeFilterList();
    const tab = currentTabName();

    const add = (param, value, labels) => {
        if (datasetSupports(dataset, param)) params.set(param, value);
        else unapplied.push(...labels);
    };

    const internalOnly = filters.includes('internal') && !filters.includes('external');
    const externalOnly = filters.includes('external') && !filters.includes('internal');
    const scope = internalOnly ? 'true'
        : externalOnly ? 'false'
        : tab === 'internal' ? 'true'
        : tab === 'external' ? 'false'
        : null;
    if (scope !== null) {
        add('internal', scope, [scope === 'true' ? 'Internal' : 'External']);
    }

    const statusFacets = filters.filter(name => FILTER_GROUPS.status.includes(name));
    if (statusFacets.length) {
        add('status_class', statusFacets.join(','), statusFacets.map(name => FILTER_LABELS[name]));
    }

    const contentFacets = filters.filter(name => FILTER_GROUPS.content.includes(name));
    if (contentFacets.length) {
        add('content_type', contentFacets.join(','), contentFacets.map(name => FILTER_LABELS[name]));
    }

    const severity = crawlState.filters.issueFilter;
    if (severity && severity !== 'all') {
        add('severity', severity, [`severity: ${severity}`]);
    }

    // The Links tab's search is a substring match over source, target and
    // anchor text; url_prefix is a prefix on one column, so it is not the
    // same filter and is not passed off as one.
    const links = crawlState.filters.linksFilter;
    if (tab === 'links') {
        if (links.internalStatusCode !== 'all') unapplied.push(`link status ${links.internalStatusCode}`);
        if (links.internalSearch) unapplied.push(`search "${links.internalSearch}"`);
        if (links.externalStatusCode !== 'all') unapplied.push(`external link status ${links.externalStatusCode}`);
        if (links.externalSearch) unapplied.push(`external search "${links.externalSearch}"`);
    }

    return { params, applied: Array.from(params.keys()), unapplied };
}

function exportFilterParams(dataset) {
    return exportFilterPlan(dataset).params;
}

function exportUrlFor(kind, format) {
    const crawlId = window.Router?.currentCrawlId();
    if (!crawlId) return null;

    if (kind === 'all') {
        return `/api/crawls/${crawlId}/export/all?format=${encodeURIComponent(format)}`;
    }
    if (kind === 'report') {
        const params = new URLSearchParams({ format });
        const baseline = document.getElementById('compareAgainst')?.value;
        if (currentTabName() === 'compare' && baseline) params.set('baseline', baseline);
        return `/api/crawls/${crawlId}/report?${params}`;
    }
    const dataset = datasetForCurrentTab();
    const params = exportFilterParams(dataset);
    params.set('format', format);
    return `/api/crawls/${crawlId}/export/${dataset}?${params}`;
}

function exportMenuElement() {
    let menu = document.getElementById('exportMenu');
    if (menu) return menu;

    menu = document.createElement('div');
    menu.id = 'exportMenu';
    menu.className = 'export-menu menu-panel';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);

    menu.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.stopPropagation();
            closeExportMenu(true);
            return;
        }
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const items = Array.from(menu.querySelectorAll('[role="menuitem"]:not([disabled])'));
        if (!items.length) return;
        event.preventDefault();
        const step = event.key === 'ArrowDown' ? 1 : -1;
        const index = items.indexOf(document.activeElement);
        items[(index + step + items.length) % items.length].focus();
    });

    return menu;
}

function exportMenuRow(kind, label, hint, formats) {
    const row = document.createElement('div');
    row.className = 'export-menu-row';

    const text = document.createElement('div');
    text.className = 'export-menu-text';
    const name = document.createElement('span');
    name.className = 'export-menu-label';
    name.textContent = label;
    text.appendChild(name);
    if (hint) {
        const note = document.createElement('span');
        note.className = 'export-menu-hint';
        note.textContent = hint;
        text.appendChild(note);
    }
    row.appendChild(text);

    const group = document.createElement('div');
    group.className = 'export-menu-formats';
    const preferred = preferredExportFormat(kind, formats);

    formats.forEach(format => {
        const link = document.createElement('a');
        link.className = 'export-format btn btn-ghost btn-sm';
        link.setAttribute('role', 'menuitem');
        link.dataset.kind = kind;
        link.dataset.format = format;
        link.textContent = format;
        link.download = '';

        const href = exportUrlFor(kind, format);
        if (href) {
            link.href = href;
        } else {
            link.setAttribute('aria-disabled', 'true');
        }
        if (format === preferred) link.setAttribute('aria-current', 'true');

        link.addEventListener('click', event => {
            if (!href) {
                event.preventDefault();
                showNotification('Open a crawl first — there is nothing to export yet', 'warning');
                return;
            }
            rememberExportFormat(kind, format);
            closeExportMenu(false);
        });
        group.appendChild(link);
    });

    row.appendChild(group);
    return row;
}

function buildExportMenu(menu) {
    const catalogue = exportCatalogue || EXPORT_FALLBACK_CATALOGUE;
    const crawlId = window.Router?.currentCrawlId();
    menu.textContent = '';

    const heading = document.createElement('div');
    heading.className = 'export-menu-heading';
    heading.innerHTML = `${iconMarkup('download', { size: 14 })} Export`;
    menu.appendChild(heading);

    if (!crawlId) {
        const note = document.createElement('p');
        note.className = 'export-menu-note';
        note.textContent = 'Open or run a crawl to export it.';
        menu.appendChild(note);
        return;
    }

    const dataset = datasetForCurrentTab();
    const rows = visibleRowCount();
    const viewHint = [dataset, rows !== null ? `${rows} rows on screen` : ''].filter(Boolean).join(' · ');

    menu.appendChild(exportMenuRow('view', 'This view', viewHint, catalogue.formats || EXPORT_FALLBACK_CATALOGUE.formats));

    const about = datasetAbout(dataset);
    if (about) {
        const note = document.createElement('p');
        note.className = 'export-menu-note';
        note.textContent = about;
        menu.appendChild(note);
    }

    const plan = exportFilterPlan(dataset);
    if (plan.applied.length || plan.unapplied.length) {
        const note = document.createElement('p');
        note.className = 'export-menu-note';
        note.textContent = [
            plan.applied.length ? `The download is filtered by ${plan.applied.join(', ')}.` : '',
            plan.unapplied.length
                ? `This dataset cannot be filtered by ${plan.unapplied.join(', ')}, so the file includes those rows.`
                : ''
        ].filter(Boolean).join(' ');
        menu.appendChild(note);
    }

    menu.appendChild(exportMenuRow('all', 'Everything', 'every dataset in one file',
        catalogue.bundle_formats || EXPORT_FALLBACK_CATALOGUE.bundle_formats));
    menu.appendChild(exportMenuRow('report', 'Summary report',
        currentTabName() === 'compare' ? 'against the crawl being compared' : 'a self-contained summary',
        catalogue.report_formats || EXPORT_FALLBACK_CATALOGUE.report_formats));

    // The Settings tab's 22 export fields still drive this one
    const custom = document.createElement('button');
    custom.type = 'button';
    custom.className = 'export-menu-custom btn btn-ghost btn-sm';
    custom.setAttribute('role', 'menuitem');
    custom.innerHTML = `${iconMarkup('sliders', { size: 14 })} Fields chosen in Settings`;
    custom.addEventListener('click', () => {
        closeExportMenu(false);
        exportWithSettings();
    });
    menu.appendChild(custom);
}

// A panel with no background is unusable, so if the stylesheet has no
// .export-menu rules yet this drops a minimal sheet in as the FIRST child of
// <head> — first, so styles.css still wins the moment it defines them.
function ensureExportMenuStyles(menu) {
    const background = getComputedStyle(menu).backgroundColor;
    const bare = !background || background === 'transparent' || background === 'rgba(0, 0, 0, 0)';
    if (!bare || document.getElementById('export-menu-fallback')) return;

    const style = document.createElement('style');
    style.id = 'export-menu-fallback';
    style.textContent = `
        .export-menu { min-width:320px; max-width:420px; display:flex; flex-direction:column;
            gap:var(--sp-3,6px); padding:var(--sp-5,12px); background:var(--bg-overlay,#fff);
            border:1px solid var(--border,#d4d4d4); border-radius:var(--r-lg,8px);
            box-shadow:var(--shadow-lg,0 18px 44px -10px rgba(0,0,0,.3));
            color:var(--text-primary,#111); z-index:var(--z-modal,100); }
        .export-menu-heading { display:flex; align-items:center; gap:var(--sp-3,6px);
            font-size:var(--fs-xs,11px); text-transform:uppercase; letter-spacing:.06em;
            color:var(--text-tertiary,#777); }
        .export-menu-row { display:flex; align-items:center; justify-content:space-between;
            gap:var(--sp-5,12px); }
        .export-menu-text { display:flex; flex-direction:column; min-width:0; }
        .export-menu-hint, .export-menu-note { font-size:var(--fs-xs,11px);
            color:var(--text-tertiary,#777); }
        .export-menu-note { margin:0; }
        .export-menu-formats { display:flex; gap:var(--sp-2,4px); }
        .export-menu-custom { align-self:flex-start; display:flex; align-items:center;
            gap:var(--sp-3,6px); }`;
    document.head.insertBefore(style, document.head.firstChild);
}

function positionExportMenu(menu, trigger) {
    const rect = trigger?.getBoundingClientRect();
    if (!rect) return;
    menu.style.position = 'fixed';
    menu.style.top = `${Math.round(rect.bottom + 6)}px`;
    // Keep the panel on screen when the trigger sits near the right edge
    const width = menu.offsetWidth || 320;
    menu.style.left = `${Math.round(Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)))}px`;
}

function exportMenuTrigger() {
    const active = document.activeElement;
    if (active && active.getAttribute && /exportData\(\)/.test(active.getAttribute('onclick') || '')) {
        return active;
    }
    return document.getElementById('exportBtn');
}

function closeExportMenu(returnFocus) {
    const menu = document.getElementById('exportMenu');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    const trigger = document.getElementById('exportBtn');
    trigger?.setAttribute('aria-expanded', 'false');
    if (returnFocus) trigger?.focus();
}

// The Export button in the sub bar and the mega menu's "Export data" both
// call this (their inline onclick is part of the contract).
async function exportData() {
    const menu = exportMenuElement();
    if (!menu.hidden) {
        closeExportMenu(true);
        return;
    }

    const trigger = exportMenuTrigger();
    await loadExportCatalogue();
    buildExportMenu(menu);
    menu.hidden = false;
    ensureExportMenuStyles(menu);
    positionExportMenu(menu, trigger);
    document.getElementById('exportBtn')?.setAttribute('aria-expanded', 'true');
    menu.querySelector('[role="menuitem"]')?.focus();
}

function setupExportMenu() {
    document.addEventListener('click', event => {
        const menu = document.getElementById('exportMenu');
        if (!menu || menu.hidden) return;
        if (event.target.closest('#exportMenu')) return;
        if (event.target.closest('[onclick*="exportData()"]')) return;
        closeExportMenu(false);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeExportMenu(true);
    });
    window.addEventListener('popstate', () => closeExportMenu(false));
    window.addEventListener('resize', () => closeExportMenu(false));
}

// How many rows the tab on screen is showing, or null when it is not a table
function visibleRowCount() {
    const byTab = {
        overview: 'overview',
        internal: 'internal',
        external: 'external',
        issues: 'issues',
        links: 'internalLinks'
    };
    const scroller = virtualScrollers[byTab[currentTabName()]];
    return scroller ? scroller.data.length : null;
}
