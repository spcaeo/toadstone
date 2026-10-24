# Writing a plugin

> For the complete documentation index, see [llms.txt](https://spcaeo.github.io/toadstone/llms.txt). Every page here is also served as Markdown at the same address with `.md` instead of `.html`.

Every tab after **Rules** in the strip is a plugin: `E-E-A-T` and `About` are
both files in `web/static/plugins/`, loaded at startup, with no build step and
no framework. A plugin gets a tab, a container to render into, and the crawl
data as it arrives.

`web/static/plugins/about.js` is the shortest complete example (~180 lines) and
`e-e-a-t.js` is the serious one — it scores every crawled page and redraws as a
live crawl streams in.

---


Drop your custom plugin files in `/web/static/plugins/`! Each `.js` file will automatically create a new tab in Toadstone.


## 🔌 Quick Start

1. Create a new `.js` file in `web/static/plugins/` (e.g. `my-plugin.js`)
2. Register it with `ToadstonePlugin.register({...})`
3. **Add the filename to `manualPlugins` in `web/static/js/plugin-loader.js`.**
   There is no directory listing to discover it from, so a plugin that is not
   in that array is a file nobody loads — and it fails by doing nothing, which
   is the worst way to fail.
4. Reload the app; the tab appears at the end of the strip.

## 📝 Example Plugin Structure

```javascript
ToadstonePlugin.register({
  // Required: Unique ID (used for tab identification)
  id: 'my-plugin',

  // Required: Display name
  name: 'My Plugin',

  // Required: Tab configuration
  tab: {
    label: 'My Tab',
    iconName: 'zap',   // a name from the app's icon set; window.ToadstoneIcons.names
    icon: '🔥',        // or an emoji, if you would rather
    position: 'end',
  },

  // Called when your tab is activated
  onTabActivate(container, data) {
    // data contains: { urls, links, issues, stats }
    container.innerHTML = `
      <div class="plugin-content" style="padding: 20px; overflow-y: auto; max-height: calc(100vh - 280px);">
        <h2>My Custom Analysis</h2>
        <p>Found ${data.urls.length} URLs!</p>
      </div>
    `;
  },

  // Optional: Called during live crawls when data updates
  onDataUpdate(data) {
    if (this.isActive) {
      // Update your UI
    }
  }
});
```

## 🎯 Available Data

Your plugin receives the same data as built-in tabs:

- **`urls`** - Array of all crawled URLs with full metadata
- **`links`** - All discovered links (internal/external)
- **`issues`** - Detected SEO issues
- **`stats`** - Crawl statistics (discovered, crawled, depth, speed)

## 📚 Full API Reference

### Plugin Configuration

```javascript
{
  id: string,              // Unique identifier
  name: string,            // Display name
  version: string,         // Optional version
  author: string,          // Optional author
  description: string,     // Optional description

  tab: {
    label: string,         // Tab button text
    icon: string,          // Optional emoji/icon
    position: number       // Optional position (default: append to end)
  }
}
```

### Lifecycle Hooks

- `onLoad()` - Called when plugin loads
- `onTabActivate(container, data)` - Called when tab becomes active
- `onTabDeactivate()` - Called when user switches away
- `onDataUpdate(data)` - Called during live crawls
- `onCrawlComplete(data)` - Called when crawl finishes

### Utilities

Access built-in utilities via `this.utils`:

```javascript
this.utils.showNotification(message, type) // 'success', 'error', 'info'
this.utils.formatUrl(url)
this.utils.escapeHtml(text)
```

### 🎨 Styling

Use these CSS classes to match Toadstone's design:

- `.plugin-content` - Main container
- `.plugin-header` - Header section
- `.data-table` - Tables (auto-styled)
- `.stat-card` - Statistic cards
- `.score-good` / `.score-needs-improvement` / `.score-poor` - Score indicators

**Important:** Always add these styles to your main plugin container for proper scrolling:

```javascript
container.innerHTML = `
  <div class="plugin-content" style="padding: 20px; overflow-y: auto; max-height: calc(100vh - 280px);">
    <!-- Your content here -->
  </div>
`;
```

The `max-height: calc(100vh - 280px)` ensures your content scrolls properly within the tab pane.

### Example Plugins

Check out these example plugins to get started:

- `about.js` — the shortest complete plugin: one tab, some prose, and one live
  figure fetched from `/api/checks`. Start here.
- `e-e-a-t.js` — the serious one: scores every crawled page across four pillars,
  redraws as a live crawl streams in (coalesced to one render a second so the
  tab does not stutter), and shows the evidence behind every number.
- `_example-plugin.js` — a bare template. The leading underscore keeps the
  loader from picking it up.

## Rendering icons inside your panel

Icon placeholders in HTML you insert are inert until they are swapped for SVG:

```javascript
container.innerHTML = '<i data-icon="zap" data-icon-size="13"></i> Ready';
if (typeof window.renderIcons === 'function') window.renderIcons(container);
```

The global is `renderIcons`. Calling a plausible-looking `window.Icons.render`
instead throws nothing and renders nothing, which cost an hour the first time.

---

Last updated: 2026-10-20. Source: [docs/plugins.md on GitHub](https://github.com/spcaeo/toadstone/blob/main/docs/plugins.md).
