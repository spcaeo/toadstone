/* Connecting a Google account, from the user's side.
 *
 * The whole panel is a state machine with five states and exactly one
 * visible at a time, because the worst version of this screen is the one
 * that shows setup instructions and a Connect button together and leaves
 * the reader to work out which half is theirs.
 *
 * Nothing here ever sees a token. The server returns whether an account is
 * connected and which one; the credential never reaches the browser.
 */
(function () {
  'use strict';

  var panel;
  var refreshing = false;

  function $(id) { return document.getElementById(id); }

  function setState(name) {
    if (!panel) return;
    /* The note sits outside the five states, so a message left over from one
     * would otherwise still be on screen under the next - "pre-selected your
     * property" under "something went wrong". Changing state clears it;
     * staying in the same state does not, so a note set by Save survives. */
    if (panel.getAttribute('data-state') !== name) note('');
    panel.setAttribute('data-state', name);
  }

  function note(message, tone) {
    var el = $('gcNote');
    if (!el) return;
    if (!message) { el.hidden = true; return; }
    el.textContent = message;
    el.setAttribute('data-tone', tone || 'ok');
    el.hidden = false;
  }

  function get(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); });
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  function fillSetup(setup) {
    if (!setup) return;
    var why = $('gcSetupWhy');
    if (why) why.textContent = setup.why + ' Takes ' + setup.takes + '.';
    var list = $('gcSetupSteps');
    if (list) {
      list.innerHTML = '';
      (setup.steps || []).forEach(function (step) {
        var li = document.createElement('li');
        li.textContent = step;
        list.appendChild(li);
      });
    }
    var field = $('gcRedirect');
    if (field) field.value = setup.redirect_uri || '';
    var link = $('gcConsoleLink');
    if (link) link.href = setup.console_url || '#';
  }

  function fillScopes(scopes) {
    var list = $('gcScopes');
    if (!list) return;
    list.innerHTML = '';
    (scopes || []).forEach(function (scope) {
      var li = document.createElement('li');
      var name = document.createElement('b');
      name.textContent = scope.name;
      var why = document.createElement('span');
      why.textContent = scope.why;
      li.appendChild(name);
      li.appendChild(why);
      list.appendChild(li);
    });
  }

  /* A <select> of properties, with the one we think they mean already
   * chosen. Picking from a list beats typing `sc-domain:example.com` and
   * wondering why nothing comes back. */
  function fillSelect(select, rows, selected, noneLabel) {
    if (!select) return;
    select.innerHTML = '';
    if (noneLabel) {
      var none = document.createElement('option');
      none.value = '';
      none.textContent = noneLabel;
      select.appendChild(none);
    }
    (rows || []).forEach(function (row) {
      var option = document.createElement('option');
      option.value = row.id;
      option.textContent = row.account
        ? row.label + ' — ' + row.account
        : row.label + (row.kind ? '  (' + row.kind + ')' : '');
      if (row.can_read === false) {
        option.textContent += '  — no read access';
        option.disabled = true;
      }
      select.appendChild(option);
    });
    if (selected) select.value = selected;
  }

  function loadProperties() {
    return get('/api/google/properties').then(function (data) {
      if (!data.success) { note(data.error, 'bad'); return; }
      fillSelect($('gcSite'), data.search_console,
                 data.selected_search_console || data.suggested_search_console, '');
      fillSelect($('gcGa'), data.analytics,
                 data.selected_analytics || data.suggested_analytics, 'None');

      var help = $('gcSiteHelp');
      if (help && !data.search_console.length) {
        help.textContent = 'This account has no Search Console properties. Add and '
          + 'verify your site at search.google.com/search-console first.';
      }
      var gaHelp = $('gcGaHelp');
      if (gaHelp && data.analytics_error) {
        gaHelp.textContent = 'Analytics is unavailable for this account: '
          + data.analytics_error + ' Search Console still works.';
      }
      /* Suggesting the property that matches the site being crawled turns a
       * decision into a confirmation. */
      if (!data.selected_search_console && data.suggested_search_console) {
        note('Pre-selected the property matching the site you are crawling. '
             + 'Change it if that is not the one you want.', 'ok');
      }
    });
  }

  function refresh() {
    if (refreshing) return Promise.resolve();
    refreshing = true;
    return get('/api/google/status').then(function (data) {
      var g = (data && data.google) || {};
      if (!g.installation_ready) {
        fillSetup(g.setup);
        setState('setup');
        return;
      }
      if (!g.can_connect) {
        var err = $('gcError');
        if (err) err.textContent = g.reason || 'Credentials cannot be stored here.';
        setState('error');
        return;
      }
      if (!g.connected) {
        fillScopes(g.scopes_explained);
        setState('disconnected');
        return;
      }
      var account = $('gcAccount');
      if (account) {
        account.textContent = g.account_email
          ? g.account_email + ' — read-only access'
          : 'Account connected, read-only access';
      }
      setState('connected');
      return loadProperties();
    }).catch(function (e) {
      var err = $('gcError');
      if (err) err.textContent = 'Could not reach Toadstone (' + e.message + ').';
      setState('error');
    }).then(function () { refreshing = false; });
  }

  function connect() {
    /* A popup, not a redirect: the person keeps their crawl, their settings
     * and their place on the page while they are away at Google. */
    var popup = window.open('/api/google/connect', 'toadstone-google',
                            'width=520,height=680');
    if (!popup) {
      note('Your browser blocked the sign-in window. Allow pop-ups for this '
           + 'page and try again.', 'bad');
      return;
    }
    note('Finish signing in to Google in the window that opened.', 'ok');
  }

  function wire() {
    panel = $('gcPanel');
    if (!panel || panel.dataset.wired) return;
    panel.dataset.wired = '1';

    var connectBtn = $('gcConnect');
    if (connectBtn) connectBtn.addEventListener('click', connect);

    var retry = $('gcRetry');
    if (retry) retry.addEventListener('click', function () { note(''); refresh(); });

    var recheck = $('gcRecheck');
    if (recheck) recheck.addEventListener('click', function () { note(''); refresh(); });

    var copy = $('gcCopyRedirect');
    if (copy) {
      copy.addEventListener('click', function () {
        var field = $('gcRedirect');
        if (!field) return;
        var done = function () {
          copy.textContent = 'Copied';
          setTimeout(function () { copy.textContent = 'Copy'; }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(field.value).then(done, function () {
            field.select(); done();
          });
        } else { field.select(); done(); }
      });
    }

    var save = $('gcSaveProperty');
    if (save) {
      save.addEventListener('click', function () {
        save.disabled = true;
        post('/api/google/property', {
          search_console_property: ($('gcSite') || {}).value || '',
          analytics_property_id: ($('gcGa') || {}).value || ''
        }).then(function (data) {
          note(data.success ? 'Saved. Your next crawl will include Google data.'
                            : (data.error || 'Could not save that.'),
               data.success ? 'ok' : 'bad');
        }).catch(function () {
          note('Could not save that. Check Toadstone is still running.', 'bad');
        }).then(function () { save.disabled = false; });
      });
    }

    var disconnect = $('gcDisconnect');
    if (disconnect) {
      disconnect.addEventListener('click', function () {
        /* Confirmed in place rather than with a dialog: the button becomes
         * the confirmation, and anything else undoes it. */
        if (disconnect.dataset.confirming !== '1') {
          disconnect.dataset.confirming = '1';
          disconnect.textContent = 'Really disconnect?';
          setTimeout(function () {
            if (disconnect.dataset.confirming === '1') {
              disconnect.dataset.confirming = '';
              disconnect.textContent = 'Disconnect';
            }
          }, 4000);
          return;
        }
        disconnect.dataset.confirming = '';
        disconnect.textContent = 'Disconnect';
        disconnect.disabled = true;
        post('/api/google/disconnect').then(function () {
          return refresh();
        }).then(function () {
          note('Disconnected. Google has been asked to forget this app too.', 'ok');
          disconnect.disabled = false;
        });
      });
    }

    /* The callback page tells us the moment it finishes, so the panel
     * updates without anyone refreshing anything. */
    window.addEventListener('message', function (event) {
      /* Only our own callback page may drive this. Without the origin check
       * any page that opened or framed this one could announce a connection
       * that never happened. */
      if (event.origin !== window.location.origin) return;
      var payload = event && event.data;
      if (!payload || payload.source !== 'toadstone-google') return;
      /* After the refresh, not before: refresh changes state, and changing
        * state clears the note. */
      refresh().then(function () {
        note(payload.ok ? 'Connected.' : 'Not connected.', payload.ok ? 'ok' : 'bad');
      });
    });

    refresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }

  window.googleConnectRefresh = refresh;
})();
