/**
 * BSG Inventory Count - phone / tablet / desktop physical-count Suitelet.
 *
 * One page, login required. Staff search items (style #, name, description,
 * UPC, vendor code -- any words, in any order), key the counted quantity next
 * to each hit, and build a "count sheet" that lives in the browser until they
 * press Submit. Submit posts the sheet back to this same Suitelet, which
 * re-reads the CURRENT on-hand for every item at the chosen location and
 * creates one Inventory Adjustment whose lines bring each item's on-hand to
 * the counted quantity (Adjust Qty. By = counted - on hand). Items whose count
 * already matches on-hand are left off the adjustment; if nothing differs, no
 * adjustment is created at all.
 *
 * Serialized / lot-numbered / bin-tracked items need Inventory Detail, which
 * this tool does not collect -- they are flagged in search results and refused
 * at submit so the rest of the sheet still posts.
 *
 * Deploy: docs/INVENTORY_COUNT.md. The deployment runs as the logged-in user,
 * so their role needs Inventory Adjustment (Create) plus Items / Locations /
 * Accounts (View). The adjustment is stamped with that user as creator, which
 * is the audit trail you want on a physical count.
 *
 * URL actions (the page calls these itself):
 *   GET  ?action=search&q=<words>&loc=<id>&offset=<n>   -> item hits + on-hand
 *   POST ?action=onhand   { ids:[], loc }                -> fresh on-hand per item
 *   POST ?action=submit   { loc, account, memo, lines:[{ item, count }] }
 *                                                       -> creates the adjustment
 *
 * @NApiVersion 2.1
 * @NScriptType Suitelet
 */
define(['N/search', 'N/record', 'N/runtime', 'N/url', 'N/log'], function (search, record, runtime, url, log) {

    var CONFIG = {
        TITLE: 'BSG Inventory Count',
        // Internal id of the account the Inventory Adjustment posts against (its
        // header "Account" field). BSG posts counts to 5005 INVENTORY ADJUSTMENT
        // (Cost of Goods Sold), internal id 222 -- confirmed by Andy 2026-09-11.
        // Set to null and the page instead offers a dropdown of active Expense /
        // COGS / Other Expense accounts, pre-selecting one whose name mentions
        // "adjust", and remembers the pick per browser.
        ADJUSTMENT_ACCOUNT_ID: 222,
        // OneWorld: force the adjustment's subsidiary. Leave null to use the
        // chosen location's subsidiary, falling back to the logged-in user's.
        SUBSIDIARY_ID: null,
        // Item types that can be counted. Matrix PARENTS are always excluded
        // (they hold no stock); their color/size children are what get counted.
        ITEM_TYPES: ['InvtPart', 'Assembly'],
        SEARCH_PAGE_SIZE: 50,
        SEARCH_MAX_WORDS: 6,
        // Lines per Inventory Adjustment. Bigger sheets are submitted by the page
        // as several adjustments, one after another.
        MAX_LINES_PER_ADJUSTMENT: 200,
        MEMO_PREFIX: 'Physical count'
    };

    // ---------------------------------------------------------------- entry --

    // Extract a loggable string from ANY thrown value. Some NetSuite errors are
    // raw Java adapter objects where merely reading .message or .stack re-throws.
    function safeErr(e) {
        var out = 'Unexpected error';
        try { if (e && e.stack) { return String(e.stack); } } catch (ignore) { /* java adapter */ }
        try { if (e && e.message) { return String(e.message); } } catch (ignore2) { /* java adapter */ }
        try { out = String(e); } catch (ignore3) { /* even toString can throw */ }
        return out;
    }

    // The message NetSuite would show a user ("Please enter value(s) for: Account")
    // without the stack. This is an internal, logged-in tool: the real reason is
    // the useful one, so it goes back to the page as-is.
    function userErr(e) {
        var msg = '';
        try { if (e && e.message) { msg = String(e.message); } } catch (ignore) { /* java adapter */ }
        if (!msg) { try { msg = String(e); } catch (ignore2) { msg = 'Unexpected error'; } }
        return msg.slice(0, 500);
    }

    function onRequest(context) {
        var request = context.request;
        var response = context.response;

        var action = null;
        try {
            action = getAction(request);
        } catch (eAct) {
            action = (request.parameters && request.parameters.action) || null;
        }

        if (action) {
            // Whatever happens, an API call returns JSON -- never a NetSuite HTML
            // error page the client can't parse.
            try {
                handleApi(action, request, response);
            } catch (e) {
                log.error({ title: 'invcount onRequest fatal: ' + action, details: safeErr(e) });
                try { response.addHeader({ name: 'Content-Type', value: 'application/json' }); } catch (eh) { /* headers may be sent */ }
                response.write(JSON.stringify({ ok: false, error: userErr(e) }));
            }
            return;
        }
        renderPage(response);
    }

    function getAction(request) {
        return (request.parameters && request.parameters.action) || null;
    }

    function parseBody(request) {
        try {
            return request.body ? JSON.parse(request.body) : {};
        } catch (e) {
            return {};
        }
    }

    function handleApi(action, request, response) {
        response.addHeader({ name: 'Content-Type', value: 'application/json' });
        response.addHeader({ name: 'Cache-Control', value: 'no-store, no-cache, must-revalidate, max-age=0' });
        var out;
        try {
            var isPost = request.method === 'POST';
            var body = isPost ? parseBody(request) : {};
            if (action === 'search') {
                out = searchItems(request.parameters.q, posInt(request.parameters.loc), parseInt(request.parameters.offset, 10) || 0);
            } else if (action === 'onhand') {
                out = isPost ? refreshOnHand(body) : { ok: false, error: 'POST required.' };
            } else if (action === 'submit') {
                out = isPost ? submitCount(body) : { ok: false, error: 'POST required.' };
            } else {
                out = { ok: false, error: 'Unknown action.' };
            }
        } catch (e) {
            log.error({ title: 'invcount API error: ' + action, details: safeErr(e) });
            out = { ok: false, error: userErr(e) };
        }
        var text;
        try {
            text = JSON.stringify(out);
        } catch (e2) {
            text = JSON.stringify({ ok: false, error: 'Response could not be serialized.' });
        }
        response.write(text);
    }

    // -------------------------------------------------------------- helpers --

    function posInt(v) {
        var n = parseInt(v, 10);
        return isFinite(n) && n > 0 ? n : null;
    }

    function isTrue(v) {
        return v === true || v === 'T' || v === 'true';
    }

    function round4(n) {
        return Math.round(n * 10000) / 10000;
    }

    function feature(id) {
        try { return !!runtime.isFeatureInEffect({ feature: id }); } catch (e) { return false; }
    }

    // Multi-Location Inventory decides whether on-hand is per location (and the
    // adjustment line needs a location) or account-wide.
    var multiLocCache = null;
    function multiLocation() {
        if (multiLocCache === null) { multiLocCache = feature('MULTILOCINVT'); }
        return multiLocCache;
    }

    function trySet(rec, fieldId, value) {
        try {
            rec.setValue({ fieldId: fieldId, value: value });
            return true;
        } catch (e) {
            log.debug({ title: 'invcount: could not set ' + fieldId, details: userErr(e) });
            return false;
        }
    }

    function trySetLine(rec, fieldId, value) {
        try {
            rec.setCurrentSublistValue({ sublistId: 'inventory', fieldId: fieldId, value: value });
            return true;
        } catch (e) {
            log.debug({ title: 'invcount: could not set line ' + fieldId, details: userErr(e) });
            return false;
        }
    }

    // ---------------------------------------------------------------- search --

    function tokenize(q) {
        return String(q || '').split(/\s+/)
            .map(function (w) { return w.trim(); })
            .filter(function (w) { return w.length > 0; })
            .slice(0, CONFIG.SEARCH_MAX_WORDS)
            .map(function (w) { return w.slice(0, 60); });
    }

    // Every word must hit somewhere, and each word may hit a different field, so
    // "royale 5" finds "Royale NFHS V25 Soccer Ball - Size 5" (name + description)
    // and "0125666912 white" narrows a style to its white children.
    function wordFilter(w) {
        return [
            ['itemid', 'contains', w], 'or',
            ['displayname', 'contains', w], 'or',
            ['salesdescription', 'contains', w], 'or',
            ['purchasedescription', 'contains', w], 'or',
            ['vendorname', 'contains', w], 'or',
            ['upccode', 'is', w]
        ];
    }

    function baseFilters(locId) {
        var f = [
            ['type', 'anyof', CONFIG.ITEM_TYPES], 'and',
            ['isinactive', 'is', 'F'], 'and',
            ['matrix', 'is', 'F']
        ];
        if (locId) {
            f.push('and');
            f.push(['inventorylocation', 'anyof', [String(locId)]]);
        }
        return f;
    }

    function itemColumns(locId) {
        return [
            search.createColumn({ name: 'itemid', sort: search.Sort.ASC }),
            'displayname', 'salesdescription', 'upccode', 'vendorname', 'vendor', 'type',
            'isserialitem', 'islotitem', 'usebins', 'parent',
            locId ? 'locationquantityonhand' : 'quantityonhand'
        ];
    }

    function rowToItem(r, locId) {
        var qty = parseFloat(r.getValue(locId ? 'locationquantityonhand' : 'quantityonhand'));
        var flags = [];
        if (isTrue(r.getValue('isserialitem'))) { flags.push('serialized'); }
        if (isTrue(r.getValue('islotitem'))) { flags.push('lot-numbered'); }
        if (isTrue(r.getValue('usebins'))) { flags.push('bin-tracked'); }
        return {
            id: String(r.id),
            name: r.getValue('itemid') || '',
            display: r.getValue('displayname') || '',
            desc: r.getValue('salesdescription') || '',
            upc: r.getValue('upccode') || '',
            vendor: r.getText('vendor') || r.getValue('vendorname') || '',
            parent: r.getText('parent') || '',
            onhand: isFinite(qty) ? qty : 0,
            blocked: flags.length ? flags.join(', ') : ''
        };
    }

    function searchItems(q, locId, offset) {
        var words = tokenize(q);
        if (!words.length) { return { ok: true, items: [], more: false, offset: 0 }; }
        if (!multiLocation()) { locId = null; }
        var filters = baseFilters(locId);
        words.forEach(function (w) {
            filters.push('and');
            filters.push(wordFilter(w));
        });
        var start = Math.max(0, offset || 0);
        var size = CONFIG.SEARCH_PAGE_SIZE;
        var rows = search.create({ type: search.Type.ITEM, filters: filters, columns: itemColumns(locId) })
            .run().getRange({ start: start, end: start + size + 1 });
        var more = rows.length > size;
        var items = rows.slice(0, size).map(function (r) { return rowToItem(r, locId); });
        return { ok: true, items: items, more: more, offset: start + items.length };
    }

    // Fresh on-hand for a set of items at a location, keyed by item id. Carries
    // the blocked flag too, so submit refuses serial/lot/bin items server side
    // no matter what the page sent.
    function lookupOnHand(ids, locId) {
        var map = {};
        if (!ids.length) { return map; }
        if (!multiLocation()) { locId = null; }
        var filters = baseFilters(locId);
        filters.push('and');
        filters.push(['internalid', 'anyof', ids]);
        search.create({ type: search.Type.ITEM, filters: filters, columns: itemColumns(locId) })
            .run().each(function (r) {
                var it = rowToItem(r, locId);
                map[it.id] = it;
                return true;
            });
        return map;
    }

    function idList(raw, max) {
        var seen = {}, out = [];
        (Array.isArray(raw) ? raw : []).forEach(function (v) {
            var id = posInt(v);
            if (id && !seen[id]) { seen[id] = true; out.push(String(id)); }
        });
        return out.slice(0, max);
    }

    function refreshOnHand(body) {
        var ids = idList(body.ids, 500);
        var locId = multiLocation() ? posInt(body.loc) : null;
        return { ok: true, items: lookupOnHand(ids, locId) };
    }

    // ---------------------------------------------------------------- submit --

    function normalizeLines(raw) {
        var seen = {}, out = [];
        (Array.isArray(raw) ? raw : []).forEach(function (l) {
            if (!l) { return; }
            var id = posInt(l.item);
            var count = parseFloat(l.count);
            if (!id || !isFinite(count) || count < 0) { return; }
            if (seen[id]) { seen[id].count = count; return; } // same item twice: last wins
            seen[id] = { item: String(id), count: count };
            out.push(seen[id]);
        });
        return out;
    }

    function defaultMemo() {
        var d = new Date();
        var who = '';
        try { who = runtime.getCurrentUser().name || ''; } catch (e) { /* ignore */ }
        return CONFIG.MEMO_PREFIX + ' ' + d.toISOString().slice(0, 10) + (who ? ' - ' + who : '');
    }

    function locationSubsidiary(locId) {
        if (!locId) { return null; }
        try {
            var f = search.lookupFields({ type: search.Type.LOCATION, id: locId, columns: ['subsidiary'] });
            var v = f && f.subsidiary;
            if (Array.isArray(v)) { v = v.length ? v[0].value : null; }
            return posInt(v);
        } catch (e) {
            return null; // not OneWorld, or no permission on the location record
        }
    }

    function currentUserSubsidiary() {
        try { return posInt(runtime.getCurrentUser().subsidiary); } catch (e) { return null; }
    }

    function submitCount(body) {
        var lines = normalizeLines(body.lines);
        if (!lines.length) { return { ok: false, error: 'Nothing to submit -- enter at least one count.' }; }
        if (lines.length > CONFIG.MAX_LINES_PER_ADJUSTMENT) {
            return { ok: false, error: 'Too many lines in one request (max ' + CONFIG.MAX_LINES_PER_ADJUSTMENT + ').' };
        }
        var locId = multiLocation() ? posInt(body.loc) : null;
        if (multiLocation() && !locId) { return { ok: false, error: 'Pick a location first.' }; }
        var accountId = posInt(CONFIG.ADJUSTMENT_ACCOUNT_ID) || posInt(body.account);
        if (!accountId) { return { ok: false, error: 'Pick an adjustment account first.' }; }
        var memo = String(body.memo || '').trim().slice(0, 999) || defaultMemo();

        // Fresh on-hand, right now, server side -- the page's numbers may be hours old.
        var current = lookupOnHand(lines.map(function (l) { return l.item; }), locId);
        var candidates = [], skipped = [], blocked = [];
        lines.forEach(function (l) {
            var it = current[l.item];
            if (!it) {
                blocked.push({ item: l.item, count: l.count, reason: 'Not found: inactive, not an inventory item, or not stocked at this location.' });
                return;
            }
            if (it.blocked) {
                blocked.push({ item: l.item, name: it.name, count: l.count, reason: 'Needs inventory detail (' + it.blocked + ') -- adjust this one manually.' });
                return;
            }
            candidates.push({ item: l.item, name: it.name, count: l.count, onhand: it.onhand });
        });
        if (!candidates.length) {
            return { ok: true, adjustment: null, applied: [], skipped: skipped, blocked: blocked, message: 'Nothing on this sheet could be adjusted.' };
        }

        var rec = record.create({ type: record.Type.INVENTORY_ADJUSTMENT, isDynamic: true });
        var subId = posInt(CONFIG.SUBSIDIARY_ID) || locationSubsidiary(locId) || currentUserSubsidiary();
        if (subId) { trySet(rec, 'subsidiary', subId); }
        rec.setValue({ fieldId: 'account', value: accountId });
        if (locId) { trySet(rec, 'adjlocation', locId); }
        rec.setValue({ fieldId: 'trandate', value: new Date() });
        rec.setValue({ fieldId: 'memo', value: memo });

        var applied = [];
        candidates.forEach(function (l) {
            rec.selectNewLine({ sublistId: 'inventory' });
            rec.setCurrentSublistValue({ sublistId: 'inventory', fieldId: 'item', value: l.item });
            if (locId) { rec.setCurrentSublistValue({ sublistId: 'inventory', fieldId: 'location', value: locId }); }
            // Prefer the on-hand NetSuite itself just sourced onto the line over the
            // search value: it is the number the adjustment is applied against.
            var sourced = parseFloat(rec.getCurrentSublistValue({ sublistId: 'inventory', fieldId: 'quantityonhand' }));
            if (isFinite(sourced)) { l.onhand = sourced; }
            l.delta = round4(l.count - l.onhand);
            if (l.delta === 0) {
                rec.cancelLine({ sublistId: 'inventory' });
                skipped.push(l);
                return;
            }
            rec.setCurrentSublistValue({ sublistId: 'inventory', fieldId: 'adjustqtyby', value: l.delta });
            trySetLine(rec, 'memo', 'Counted ' + l.count + ' (on hand ' + l.onhand + ')');
            rec.commitLine({ sublistId: 'inventory' });
            applied.push(l);
        });

        if (!applied.length) {
            return { ok: true, adjustment: null, applied: [], skipped: skipped, blocked: blocked, message: 'Every count already matches on-hand -- no adjustment needed.' };
        }

        var id = rec.save({ enableSourcing: true, ignoreMandatoryFields: false });
        var tranid = '';
        try {
            tranid = search.lookupFields({ type: search.Type.INVENTORY_ADJUSTMENT, id: id, columns: ['tranid'] }).tranid || '';
        } catch (e) { /* cosmetic */ }
        var recUrl = '';
        try {
            recUrl = url.resolveRecord({ recordType: 'inventoryadjustment', recordId: id, isEditMode: false });
        } catch (e2) { /* cosmetic */ }
        log.audit({
            title: 'invcount: adjustment ' + id + (tranid ? ' (#' + tranid + ')' : ''),
            details: applied.length + ' lines, ' + skipped.length + ' unchanged, ' + blocked.length + ' blocked; location ' + (locId || 'n/a') + '; account ' + accountId
        });
        return {
            ok: true,
            adjustment: { id: String(id), tranid: String(tranid), url: recUrl },
            applied: applied, skipped: skipped, blocked: blocked
        };
    }

    // ------------------------------------------------------------------ page --

    function listLocations() {
        var out = [];
        search.create({
            type: search.Type.LOCATION,
            filters: [['isinactive', 'is', 'F']],
            columns: [search.createColumn({ name: 'name', sort: search.Sort.ASC })]
        }).run().each(function (r) {
            out.push({ id: String(r.id), name: r.getValue('name') });
            return true;
        });
        return out;
    }

    function listAccounts() {
        var out = [];
        search.create({
            type: search.Type.ACCOUNT,
            filters: [['isinactive', 'is', 'F'], 'and', ['type', 'anyof', ['Expense', 'COGS', 'OthExpense']]],
            columns: [search.createColumn({ name: 'number', sort: search.Sort.ASC }), 'name']
        }).run().each(function (r) {
            var number = r.getValue('number') || '';
            var name = r.getValue('name') || '';
            out.push({
                id: String(r.id),
                label: (number ? number + ' ' : '') + name,
                suggested: /adjust|shrink|count/i.test(name)
            });
            return true;
        });
        return out;
    }

    function bootData() {
        var boot = {
            title: CONFIG.TITLE,
            multiLoc: multiLocation(),
            locations: [],
            accounts: [],
            accountLocked: !!posInt(CONFIG.ADJUSTMENT_ACCOUNT_ID),
            pageSize: CONFIG.SEARCH_PAGE_SIZE,
            maxLines: CONFIG.MAX_LINES_PER_ADJUSTMENT,
            user: '',
            warnings: []
        };
        try { boot.user = runtime.getCurrentUser().name || ''; } catch (e) { /* ignore */ }
        if (boot.multiLoc) {
            try { boot.locations = listLocations(); } catch (e1) { boot.warnings.push('Could not list locations: ' + userErr(e1)); }
        }
        if (!boot.accountLocked) {
            try { boot.accounts = listAccounts(); } catch (e2) { boot.warnings.push('Could not list accounts: ' + userErr(e2)); }
        }
        return boot;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // JSON that is safe inside a <script> block (a "</script>" in a location
    // name must not end the block early).
    function jsonForHtml(obj) {
        return JSON.stringify(obj).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
    }

    function renderPage(response) {
        var boot = bootData();
        response.addHeader({ name: 'Cache-Control', value: 'no-store, no-cache, must-revalidate, max-age=0' });
        response.write(
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">' +
            '<meta name="apple-mobile-web-app-capable" content="yes">' +
            '<meta name="mobile-web-app-capable" content="yes">' +
            '<meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate">' +
            '<title>' + escapeHtml(CONFIG.TITLE) + '</title>' +
            '<style>' + PAGE_CSS + '</style></head>' +
            '<body><div id="app"><div class="ic-loading">Loading&hellip;</div></div>' +
            '<script>window.INVCOUNT_BOOT=' + jsonForHtml(boot) + ';</script>' +
            '<script>' + CLIENT_JS + '</script>' +
            '</body></html>'
        );
    }

    // ------------------------------------------------------------------- css --

    var PAGE_CSS = String.raw`
:root{--red:#b3252a;--red-dark:#8f1c20;--ink:#1c1c1e;--muted:#6b6b70;--line:#dedee3;--bg:#f4f4f6;--card:#fff;--ok:#1f7a3a;--warn:#b26a00;--bad:#b3252a;--neg-bg:#fdecec;--pos-bg:#e8f6ec;--focus:#2b6cb0}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);font:16px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%}
button,input,select{font:inherit;color:inherit}
button{cursor:pointer}
.ic-loading{padding:40px;text-align:center;color:var(--muted)}
.ic-head{position:sticky;top:0;z-index:5;background:var(--red);color:#fff;padding:10px 12px calc(env(safe-area-inset-top,0px) + 0px);box-shadow:0 1px 4px rgba(0,0,0,.25)}
.ic-head-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ic-title{font-weight:800;font-size:18px;letter-spacing:.2px;flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ic-user{font-size:12px;opacity:.85;white-space:nowrap}
.ic-loc{display:flex;align-items:center;gap:6px;font-size:13px}
.ic-loc select{background:#fff;color:var(--ink);border:0;border-radius:8px;padding:8px 10px;max-width:60vw;min-height:40px}
.ic-tabs{display:flex;gap:6px;margin-top:8px}
.ic-tab{flex:1 1 0;background:rgba(255,255,255,.14);color:#fff;border:1px solid rgba(255,255,255,.35);border-radius:10px;padding:10px 8px;font-weight:700;min-height:44px}
.ic-tab.is-on{background:#fff;color:var(--red);border-color:#fff}
.ic-badge{display:inline-block;min-width:22px;padding:1px 7px;border-radius:11px;background:var(--red-dark);color:#fff;font-size:12px;margin-left:6px}
.ic-tab.is-on .ic-badge{background:var(--red)}
.ic-main{padding:12px;max-width:900px;margin:0 auto}
.ic-warn{background:#fff6e5;border:1px solid #f1d59a;color:#6b4a00;padding:10px 12px;border-radius:10px;margin-bottom:10px;font-size:14px}
.ic-error{background:var(--neg-bg);border:1px solid #f0b9b9;color:var(--bad);padding:10px 12px;border-radius:10px;margin:10px 0;font-size:14px;word-break:break-word}
.ic-search{position:relative;margin-bottom:8px}
.ic-search input{width:100%;padding:14px 44px 14px 14px;border:2px solid var(--line);border-radius:12px;background:#fff;font-size:17px;min-height:52px}
.ic-search input:focus{outline:none;border-color:var(--focus)}
.ic-search .ic-clear{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:transparent;border:0;font-size:22px;color:var(--muted);width:40px;height:40px;border-radius:20px}
.ic-hint{font-size:13px;color:var(--muted);margin:0 0 10px 2px}
.ic-list{display:flex;flex-direction:column;gap:8px}
.ic-row{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;display:flex;gap:10px;align-items:center}
.ic-row.is-onsheet{border-color:#9fd3ae;background:#f3fbf5}
.ic-row.is-blocked{opacity:.75}
.ic-row.has-error{border-color:#f0b9b9;background:#fff7f7}
.ic-info{flex:1 1 auto;min-width:0}
.ic-name{font-weight:700;word-break:break-word}
.ic-desc{font-size:13px;color:#3a3a3f;word-break:break-word}
.ic-meta{font-size:12px;color:var(--muted);margin-top:2px;word-break:break-word}
.ic-meta b{color:var(--ink)}
.ic-flag{display:inline-block;font-size:11px;font-weight:700;color:var(--warn);background:#fff3df;border-radius:6px;padding:1px 6px;margin-top:4px}
.ic-onsheet{display:inline-block;font-size:11px;font-weight:700;color:var(--ok);background:#e0f3e6;border-radius:6px;padding:1px 6px;margin-top:4px}
.ic-ctl{display:flex;align-items:center;gap:6px;flex:0 0 auto}
.ic-qty{width:84px;min-height:46px;padding:8px;border:2px solid var(--line);border-radius:10px;font-size:20px;font-weight:700;text-align:center;background:#fff}
.ic-qty:focus{outline:none;border-color:var(--focus)}
.ic-btn{min-height:46px;padding:0 14px;border-radius:10px;border:0;background:var(--red);color:#fff;font-weight:700;white-space:nowrap}
.ic-btn:disabled{opacity:.5;cursor:default}
.ic-btn.is-ghost{background:#fff;color:var(--ink);border:2px solid var(--line)}
.ic-btn.is-ok{background:var(--ok)}
.ic-btn.is-sm{min-height:38px;padding:0 10px;font-size:14px}
.ic-step{width:40px;height:46px;border-radius:10px;border:2px solid var(--line);background:#fff;font-size:22px;font-weight:700;line-height:1;color:var(--ink)}
.ic-x{width:36px;height:36px;border-radius:18px;border:0;background:transparent;color:var(--muted);font-size:22px;line-height:1}
.ic-x:hover{color:var(--bad);background:#fdecec}
.ic-more{margin:12px 0;text-align:center}
.ic-empty{padding:30px 10px;text-align:center;color:var(--muted)}
.ic-sum{display:flex;gap:10px;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:10px;font-size:14px}
.ic-sum div{flex:1 1 auto}
.ic-sum b{font-size:18px;display:block}
.ic-delta{font-weight:800;min-width:56px;text-align:center;padding:6px 6px;border-radius:8px;font-size:15px}
.ic-delta.is-pos{background:var(--pos-bg);color:var(--ok)}
.ic-delta.is-neg{background:var(--neg-bg);color:var(--bad)}
.ic-delta.is-zero{background:#eeeef1;color:var(--muted)}
.ic-line-ctl{display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex:0 0 auto}
.ic-line-top{display:flex;align-items:center;gap:6px}
.ic-form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:12px;display:flex;flex-direction:column;gap:10px}
.ic-form label{font-size:13px;color:var(--muted);display:block;margin-bottom:4px}
.ic-form input,.ic-form select{width:100%;min-height:46px;padding:10px 12px;border:2px solid var(--line);border-radius:10px;background:#fff}
.ic-form input:focus,.ic-form select:focus{outline:none;border-color:var(--focus)}
.ic-actions{display:flex;gap:8px;flex-wrap:wrap}
.ic-actions .ic-btn{flex:1 1 auto;min-height:52px;font-size:17px}
.ic-confirm{background:#fff9e8;border:2px solid #f1d59a;border-radius:12px;padding:12px;margin-top:10px}
.ic-confirm p{margin:0 0 10px}
.ic-done{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:12px}
.ic-done h2{margin:0 0 8px;font-size:20px;color:var(--ok)}
.ic-done a{color:var(--focus);font-weight:700;font-size:17px}
.ic-done ul{margin:6px 0 0 18px;padding:0;font-size:14px}
.ic-progress{padding:14px;text-align:center;color:var(--muted)}
.ic-footer{text-align:center;color:var(--muted);font-size:12px;padding:20px 0 calc(20px + env(safe-area-inset-bottom,0px))}
@media (max-width:480px){.ic-row{flex-wrap:wrap}.ic-ctl,.ic-line-ctl{width:100%;justify-content:flex-end;align-items:center;flex-direction:row}.ic-qty{flex:1 1 auto}.ic-user{display:none}.ic-loc select{max-width:100%;flex:1 1 auto}.ic-loc{flex:1 1 100%}}
`;

    // ---------------------------------------------------------------- client --
    // Plain browser JavaScript inlined into the page (ES5 so older tablets work).
    // It talks back to this same Suitelet URL for search / on-hand / submit and
    // keeps the in-progress count sheet in localStorage until it is submitted.
    // NOTE: no backticks or "${" inside -- this is a raw template literal.

    var CLIENT_JS = String.raw`
(function () {
    'use strict';

    var BOOT = window.INVCOUNT_BOOT || {};
    // Always call the SAME url the page was served from (script + deploy ids
    // included), so it works on whichever domain NetSuite served it on.
    var API = (function () {
        var base = location.origin + location.pathname + location.search;
        return base.indexOf('?') === -1 ? base + '?_=1' : base;
    })();
    var SHEET_KEY = 'bsg_invcount_sheet_v1:';
    var PREF_KEY = 'bsg_invcount_prefs_v1';

    var state = {
        view: 'search',            // search | sheet | done
        loc: '', account: '',
        q: '', results: [], more: false, offset: 0, searching: false, searchError: '',
        sheet: {}, order: [],      // itemId -> line; ids in the order added
        memo: '',
        submitting: false, progress: '', submitError: '',
        confirm: false,
        done: null
    };
    var searchTimer = null, reqSeq = 0;

    // ------------------------------------------------------------ storage --

    function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
    function lsDel(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }

    function loadPrefs() { try { return JSON.parse(lsGet(PREF_KEY) || '{}') || {}; } catch (e) { return {}; } }
    function savePrefs() { lsSet(PREF_KEY, JSON.stringify({ loc: state.loc, account: state.account })); }

    function sheetKey() { return SHEET_KEY + (state.loc || 'all'); }
    function loadSheet() {
        var saved = null;
        try { saved = JSON.parse(lsGet(sheetKey()) || 'null'); } catch (e) { saved = null; }
        state.sheet = {}; state.order = []; state.memo = '';
        if (saved && saved.sheet && saved.order) {
            state.sheet = saved.sheet; state.order = saved.order.filter(function (id) { return !!state.sheet[id]; });
            state.memo = saved.memo || '';
        }
    }
    function saveSheet() {
        if (!state.order.length && !state.memo) { lsDel(sheetKey()); return; }
        lsSet(sheetKey(), JSON.stringify({ sheet: state.sheet, order: state.order, memo: state.memo, savedAt: Date.now() }));
    }

    // ---------------------------------------------------------------- api --

    function parseJson(t, action) {
        try { return JSON.parse(t); } catch (e) {
            var snippet = String(t || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
            return { ok: false, error: 'Server returned a non-JSON response for "' + action + '". ' + (snippet || '(empty response)') };
        }
    }
    function apiGet(action, params) {
        var qs = Object.keys(params || {}).map(function (k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
        }).join('&');
        return fetch(API + '&action=' + action + (qs ? '&' + qs : '') + '&_ts=' + (++reqSeq), { credentials: 'same-origin', cache: 'no-store' })
            .then(function (r) { return r.text(); }).then(function (t) { return parseJson(t, action); });
    }
    function apiPost(action, body) {
        return fetch(API + '&action=' + action + '&_ts=' + (++reqSeq), {
            method: 'POST', credentials: 'same-origin', cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(function (r) { return r.text(); }).then(function (t) { return parseJson(t, action); });
    }

    // ---------------------------------------------------------------- dom --

    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                var v = attrs[k];
                if (v == null || v === false) { return; }
                if (k === 'class') { e.className = v; }
                else if (k === 'text') { e.textContent = v; }
                else if (k.indexOf('on') === 0) { e.addEventListener(k.slice(2), v); }
                else { e.setAttribute(k, v === true ? '' : v); }
            });
        }
        (children || []).forEach(function (c) {
            if (c == null || c === false) { return; }
            e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
        });
        return e;
    }
    function fmt(n) {
        n = Number(n);
        if (!isFinite(n)) { return '0'; }
        return (Math.round(n * 10000) / 10000).toString();
    }
    function signed(n) { return (n > 0 ? '+' : '') + fmt(n); }
    function locName(id) {
        var hit = (BOOT.locations || []).filter(function (l) { return l.id === id; })[0];
        return hit ? hit.name : '';
    }

    // -------------------------------------------------------------- sheet --

    function lineFor(item) {
        return state.sheet[item.id] || null;
    }
    function setCount(item, count) {
        var line = state.sheet[item.id];
        if (!line) {
            line = { id: item.id, name: item.name, display: item.display, desc: item.desc, upc: item.upc, vendor: item.vendor, onhand: item.onhand, blocked: item.blocked || '' };
            state.sheet[item.id] = line;
            state.order.push(item.id);
        }
        line.count = count;
        line.error = '';
        line.addedAt = Date.now();
        saveSheet();
        updateBadge();
    }
    function removeLine(id) {
        delete state.sheet[id];
        state.order = state.order.filter(function (x) { return x !== id; });
        saveSheet();
        updateBadge();
    }
    function parseCount(v) {
        var s = String(v == null ? '' : v).trim();
        if (!s) { return null; }
        var n = Number(s);
        if (!isFinite(n) || n < 0) { return null; }
        return Math.round(n * 10000) / 10000;
    }
    function totals() {
        var lines = 0, pos = 0, neg = 0, blocked = 0;
        state.order.forEach(function (id) {
            var l = state.sheet[id];
            if (!l) { return; }
            lines++;
            if (l.blocked) { blocked++; return; }
            var d = (Number(l.count) || 0) - (Number(l.onhand) || 0);
            if (d > 0) { pos += d; } else { neg += -d; }
        });
        return { lines: lines, pos: pos, neg: neg, blocked: blocked };
    }
    function updateBadge() {
        var b = document.getElementById('icSheetBadge');
        if (b) { b.textContent = String(state.order.length); }
    }

    // ------------------------------------------------------------- search --

    function runSearch(reset) {
        var q = state.q.trim();
        if (!q) {
            state.results = []; state.more = false; state.offset = 0; state.searching = false; state.searchError = '';
            renderResults();
            return;
        }
        if (reset) { state.offset = 0; }
        state.searching = true; state.searchError = '';
        renderResults();
        var mySeq = ++searchSeq;
        apiGet('search', { q: q, loc: state.loc || '', offset: reset ? 0 : state.offset }).then(function (res) {
            if (mySeq !== searchSeq) { return; } // a newer search superseded this one
            state.searching = false;
            if (!res || !res.ok) {
                state.searchError = (res && res.error) || 'Search failed.';
                if (reset) { state.results = []; }
            } else {
                state.results = reset ? res.items : state.results.concat(res.items);
                state.more = !!res.more;
                state.offset = res.offset || state.results.length;
            }
            renderResults();
            if (reset && res && res.ok) { autoFocusSingle(q); }
        });
    }
    var searchSeq = 0;

    // A barcode scanner types the UPC and presses Enter: when the query lands on
    // exactly one countable item, jump straight to its count box.
    function autoFocusSingle(q) {
        var live = state.results.filter(function (it) { return !it.blocked; });
        if (live.length !== 1) { return; }
        var only = live[0];
        var exact = only.upc && only.upc === q;
        if (!exact && state.results.length !== 1) { return; }
        var input = document.querySelector('[data-qty-for="' + only.id + '"]');
        if (input) { input.focus(); input.select(); }
    }

    function onSearchInput(ev) {
        state.q = ev.target.value;
        var clear = document.getElementById('icClear');
        if (clear) { clear.style.display = state.q ? '' : 'none'; }
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () { runSearch(true); }, 280);
    }
    function onSearchKey(ev) {
        if (ev.key !== 'Enter') { return; }
        ev.preventDefault();
        clearTimeout(searchTimer);
        runSearch(true);
    }

    function resultRow(item) {
        var line = lineFor(item);
        var row = el('div', { class: 'ic-row' + (line ? ' is-onsheet' : '') + (item.blocked ? ' is-blocked' : ''), 'data-row-for': item.id });
        var meta = [];
        if (item.vendor) { meta.push(item.vendor); }
        if (item.upc) { meta.push('UPC ' + item.upc); }
        var info = el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name', text: item.name }),
            (item.display || item.desc) ? el('div', { class: 'ic-desc', text: item.display && item.desc && item.display !== item.desc ? item.display + ' - ' + item.desc : (item.display || item.desc) }) : null,
            el('div', { class: 'ic-meta' }, [meta.length ? meta.join(' · ') + ' · ' : '', 'On hand: ', el('b', { text: fmt(item.onhand) })]),
            item.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + item.blocked + ') - adjust manually' }) : null,
            line ? el('div', { class: 'ic-onsheet', 'data-onsheet-for': item.id, text: 'On sheet: ' + fmt(line.count) }) : null
        ]);
        row.appendChild(info);
        if (!item.blocked) {
            var input = el('input', {
                class: 'ic-qty', type: 'text', inputmode: 'decimal', enterkeyhint: 'next', autocomplete: 'off', placeholder: 'Qty',
                'data-qty-for': item.id, value: line ? fmt(line.count) : '', 'aria-label': 'Counted quantity for ' + item.name
            });
            var btn = el('button', { class: 'ic-btn', type: 'button', text: line ? 'Update' : 'Add' });
            function commit(advance) {
                var c = parseCount(input.value);
                if (c === null) { input.focus(); input.classList.add('is-bad'); return; }
                setCount(item, c);
                row.classList.add('is-onsheet');
                btn.textContent = 'Update';
                var tag = info.querySelector('[data-onsheet-for]');
                if (tag) { tag.textContent = 'On sheet: ' + fmt(c); }
                else { info.appendChild(el('div', { class: 'ic-onsheet', 'data-onsheet-for': item.id, text: 'On sheet: ' + fmt(c) })); }
                if (advance) {
                    var all = Array.prototype.slice.call(document.querySelectorAll('[data-qty-for]'));
                    var idx = all.indexOf(input);
                    var next = all[idx + 1];
                    if (next) { next.focus(); next.select(); }
                    else { var s = document.getElementById('icSearch'); if (s) { s.focus(); s.select(); } }
                }
            }
            input.addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter') { ev.preventDefault(); commit(true); }
            });
            btn.addEventListener('click', function () { commit(false); });
            row.appendChild(el('div', { class: 'ic-ctl' }, [input, btn]));
        }
        return row;
    }

    function renderResults() {
        var box = document.getElementById('icResults');
        if (!box) { return; }
        box.innerHTML = '';
        if (state.searchError) { box.appendChild(el('div', { class: 'ic-error', text: state.searchError })); }
        if (!state.q.trim()) {
            box.appendChild(el('div', { class: 'ic-empty', text: state.order.length
                ? 'Search for the next item to count. Your sheet has ' + state.order.length + ' line' + (state.order.length === 1 ? '' : 's') + '.'
                : 'Search by style #, name, description, UPC or vendor code. Any words, any order.' }));
            return;
        }
        var list = el('div', { class: 'ic-list' });
        state.results.forEach(function (it) { list.appendChild(resultRow(it)); });
        box.appendChild(list);
        if (state.searching) {
            box.appendChild(el('div', { class: 'ic-progress', text: state.results.length ? 'Loading more…' : 'Searching…' }));
        } else if (!state.results.length) {
            box.appendChild(el('div', { class: 'ic-empty', text: 'No inventory items match "' + state.q.trim() + '".' }));
        } else if (state.more) {
            box.appendChild(el('div', { class: 'ic-more' }, [
                el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Load more', onclick: function () { runSearch(false); } })
            ]));
        }
    }

    function renderSearchView(main) {
        var wrap = el('div', { class: 'ic-search' }, [
            el('input', {
                id: 'icSearch', type: 'text', placeholder: 'Style #, description, UPC…', value: state.q,
                autocomplete: 'off', autocorrect: 'off', autocapitalize: 'off', spellcheck: 'false', enterkeyhint: 'search',
                oninput: onSearchInput, onkeydown: onSearchKey
            }),
            el('button', { id: 'icClear', class: 'ic-clear', type: 'button', text: '×', 'aria-label': 'Clear search', style: state.q ? '' : 'display:none', onclick: function () {
                state.q = ''; var s = document.getElementById('icSearch'); if (s) { s.value = ''; s.focus(); }
                var c = document.getElementById('icClear'); if (c) { c.style.display = 'none'; }
                runSearch(true);
            } })
        ]);
        main.appendChild(wrap);
        main.appendChild(el('p', { class: 'ic-hint', text: 'Key the counted quantity next to an item and press Add (Enter jumps to the next item). Counts wait on the Sheet tab until you submit.' }));
        main.appendChild(el('div', { id: 'icResults' }));
        renderResults();
        if (!state.results.length && state.q.trim()) { runSearch(true); }
    }

    // -------------------------------------------------------------- sheet --

    function sheetRow(line) {
        var row = el('div', { class: 'ic-row' + (line.error ? ' has-error' : '') });
        var delta = el('div', { class: 'ic-delta' });
        function paintDelta() {
            if (line.blocked) { delta.className = 'ic-delta is-zero'; delta.textContent = 'n/a'; return; }
            var d = (Number(line.count) || 0) - (Number(line.onhand) || 0);
            d = Math.round(d * 10000) / 10000;
            delta.className = 'ic-delta ' + (d > 0 ? 'is-pos' : d < 0 ? 'is-neg' : 'is-zero');
            delta.textContent = signed(d);
        }
        paintDelta();
        var input = el('input', {
            class: 'ic-qty', type: 'text', inputmode: 'decimal', autocomplete: 'off', value: fmt(line.count),
            'aria-label': 'Counted quantity for ' + line.name
        });
        function apply(v) {
            var c = parseCount(v);
            if (c === null) { return; }
            line.count = c; line.error = '';
            row.classList.remove('has-error');
            saveSheet(); paintDelta(); paintTotals();
        }
        input.addEventListener('input', function () { apply(input.value); });
        input.addEventListener('blur', function () { input.value = fmt(line.count); });
        var minus = el('button', { class: 'ic-step', type: 'button', text: '−', 'aria-label': 'Minus one', onclick: function () {
            var c = Math.max(0, (Number(line.count) || 0) - 1); input.value = fmt(c); apply(c);
        } });
        var plus = el('button', { class: 'ic-step', type: 'button', text: '+', 'aria-label': 'Plus one', onclick: function () {
            var c = (Number(line.count) || 0) + 1; input.value = fmt(c); apply(c);
        } });
        var remove = el('button', { class: 'ic-x', type: 'button', text: '×', 'aria-label': 'Remove ' + line.name, onclick: function () {
            removeLine(line.id); render();
        } });
        row.appendChild(el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name', text: line.name }),
            (line.display || line.desc) ? el('div', { class: 'ic-desc', text: line.display || line.desc }) : null,
            el('div', { class: 'ic-meta' }, ['On hand: ', el('b', { text: fmt(line.onhand) }), line.vendor ? ' · ' + line.vendor : '']),
            line.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + line.blocked + ') - will be skipped' }) : null,
            line.error ? el('div', { class: 'ic-error', text: line.error }) : null
        ]));
        row.appendChild(el('div', { class: 'ic-line-ctl' }, [
            el('div', { class: 'ic-line-top' }, [minus, input, plus, delta, remove])
        ]));
        return row;
    }

    function paintTotals() {
        var t = totals();
        var e;
        if ((e = document.getElementById('icTotLines'))) { e.textContent = String(t.lines); }
        if ((e = document.getElementById('icTotPos'))) { e.textContent = '+' + fmt(t.pos); }
        if ((e = document.getElementById('icTotNeg'))) { e.textContent = '−' + fmt(t.neg); }
    }

    function renderSheetView(main) {
        var t = totals();
        main.appendChild(el('div', { class: 'ic-sum' }, [
            el('div', {}, [el('b', { id: 'icTotLines', text: String(t.lines) }), 'lines']),
            el('div', {}, [el('b', { id: 'icTotPos', text: '+' + fmt(t.pos) }), 'units up']),
            el('div', {}, [el('b', { id: 'icTotNeg', text: '−' + fmt(t.neg) }), 'units down']),
            el('div', {}, [el('b', { text: state.loc ? locName(state.loc) : (BOOT.multiLoc ? 'No location' : 'All locations') }), 'location'])
        ]));
        if (!state.order.length) {
            main.appendChild(el('div', { class: 'ic-empty', text: 'The sheet is empty. Search for items and add counts.' }));
            main.appendChild(el('div', { class: 'ic-actions' }, [
                el('button', { class: 'ic-btn', type: 'button', text: 'Go to search', onclick: function () { state.view = 'search'; render(); } })
            ]));
            return;
        }
        var list = el('div', { class: 'ic-list' });
        // Newest first: the line you just added is the one you want to see.
        state.order.slice().reverse().forEach(function (id) {
            var line = state.sheet[id];
            if (line) { list.appendChild(sheetRow(line)); }
        });
        main.appendChild(list);

        var form = el('div', { class: 'ic-form' });
        form.appendChild(el('div', {}, [
            el('label', { for: 'icMemo', text: 'Memo (goes on the Inventory Adjustment)' }),
            el('input', { id: 'icMemo', type: 'text', maxlength: '200', placeholder: 'e.g. Q3 cycle count - aisle 4', value: state.memo,
                oninput: function (ev) { state.memo = ev.target.value; saveSheet(); } })
        ]));
        if (!BOOT.accountLocked) {
            var sel = el('select', { id: 'icAccount', onchange: function (ev) { state.account = ev.target.value; savePrefs(); } });
            sel.appendChild(el('option', { value: '', text: '— pick the adjustment account —' }));
            (BOOT.accounts || []).forEach(function (a) {
                var o = el('option', { value: a.id, text: a.label });
                if (a.id === state.account) { o.selected = true; }
                sel.appendChild(o);
            });
            form.appendChild(el('div', {}, [el('label', { for: 'icAccount', text: 'Adjustment account' }), sel]));
        }
        if (state.submitError) { form.appendChild(el('div', { class: 'ic-error', text: state.submitError })); }
        if (state.submitting) {
            form.appendChild(el('div', { class: 'ic-progress', text: state.progress || 'Submitting…' }));
        } else if (state.confirm) {
            var live = t.lines - t.blocked;
            form.appendChild(el('div', { class: 'ic-confirm' }, [
                el('p', {}, [el('b', { text: 'Create an Inventory Adjustment for ' + live + ' line' + (live === 1 ? '' : 's') + (state.loc ? ' at ' + locName(state.loc) : '') + '?' })]),
                el('p', { text: 'Each item’s on-hand becomes the count you entered (' + '+' + fmt(t.pos) + ' / −' + fmt(t.neg) + ' units). Items whose count already matches are left out.' + (t.blocked ? ' ' + t.blocked + ' flagged line' + (t.blocked === 1 ? ' is' : 's are') + ' skipped.' : '') }),
                el('div', { class: 'ic-actions' }, [
                    el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Cancel', onclick: function () { state.confirm = false; render(); } }),
                    el('button', { class: 'ic-btn is-ok', type: 'button', text: 'Yes, submit count', onclick: submitSheet })
                ])
            ]));
        } else {
            form.appendChild(el('div', { class: 'ic-actions' }, [
                el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Refresh on-hand', onclick: refreshOnHand }),
                el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Clear sheet', onclick: function () {
                    if (window.confirm('Remove all ' + state.order.length + ' lines from the sheet? Nothing is posted to NetSuite.')) {
                        state.order.slice().forEach(removeLine); state.memo = ''; saveSheet(); render();
                    }
                } }),
                el('button', { class: 'ic-btn', type: 'button', text: 'Submit count…', onclick: function () {
                    state.submitError = '';
                    if (BOOT.multiLoc && !state.loc) { state.submitError = 'Pick a location at the top first.'; render(); return; }
                    if (!BOOT.accountLocked && !state.account) { state.submitError = 'Pick the adjustment account first.'; render(); return; }
                    if (t.lines - t.blocked <= 0) { state.submitError = 'No countable lines on the sheet.'; render(); return; }
                    state.confirm = true; render();
                } })
            ]));
        }
        main.appendChild(form);
    }

    function refreshOnHand() {
        var ids = state.order.slice();
        if (!ids.length) { return; }
        state.submitting = true; state.progress = 'Refreshing on-hand…'; state.submitError = '';
        render();
        apiPost('onhand', { ids: ids, loc: state.loc || '' }).then(function (res) {
            state.submitting = false; state.progress = '';
            if (!res || !res.ok) { state.submitError = (res && res.error) || 'Refresh failed.'; render(); return; }
            ids.forEach(function (id) {
                var line = state.sheet[id], fresh = res.items && res.items[id];
                if (!line) { return; }
                if (fresh) { line.onhand = fresh.onhand; line.blocked = fresh.blocked || ''; line.error = ''; }
                else { line.error = 'Not found at this location any more.'; }
            });
            saveSheet(); render();
        });
    }

    // Submit in batches of BOOT.maxLines, one adjustment each, in order. Lines
    // that posted (or already matched) leave the sheet; blocked ones stay on it
    // with the reason so nothing silently disappears.
    function submitSheet() {
        var ids = state.order.filter(function (id) { return state.sheet[id] && !state.sheet[id].blocked; });
        var batches = [];
        for (var i = 0; i < ids.length; i += (BOOT.maxLines || 200)) { batches.push(ids.slice(i, i + (BOOT.maxLines || 200))); }
        var done = { adjustments: [], applied: 0, skipped: 0, blocked: [], location: state.loc ? locName(state.loc) : '' };
        state.confirm = false; state.submitting = true; state.submitError = '';
        var n = 0;
        function next() {
            if (n >= batches.length) {
                state.submitting = false; state.progress = '';
                state.done = done; state.view = 'done';
                render();
                return;
            }
            var batch = batches[n++];
            state.progress = batches.length > 1 ? 'Submitting batch ' + n + ' of ' + batches.length + '…' : 'Creating the Inventory Adjustment…';
            render();
            apiPost('submit', {
                loc: state.loc || '', account: state.account || '', memo: state.memo || '',
                lines: batch.map(function (id) { return { item: id, count: state.sheet[id].count }; })
            }).then(function (res) {
                if (!res || !res.ok) {
                    state.submitting = false; state.progress = '';
                    state.submitError = ((res && res.error) || 'Submit failed.') + (n > 1 ? ' (Earlier batches posted; the remaining lines are still on the sheet.)' : '');
                    if (done.adjustments.length) { state.done = done; }
                    render();
                    return;
                }
                if (res.adjustment) { done.adjustments.push(res.adjustment); }
                done.applied += (res.applied || []).length;
                done.skipped += (res.skipped || []).length;
                (res.applied || []).concat(res.skipped || []).forEach(function (l) { removeLine(String(l.item)); });
                (res.blocked || []).forEach(function (b) {
                    var line = state.sheet[String(b.item)];
                    if (line) { line.error = b.reason || 'Blocked.'; }
                    done.blocked.push({ name: b.name || (line && line.name) || ('item ' + b.item), reason: b.reason || '' });
                });
                saveSheet();
                next();
            });
        }
        next();
    }

    function renderDoneView(main) {
        var d = state.done || { adjustments: [], applied: 0, skipped: 0, blocked: [] };
        var box = el('div', { class: 'ic-done' });
        if (d.adjustments.length) {
            box.appendChild(el('h2', { text: d.adjustments.length === 1 ? 'Inventory Adjustment created' : d.adjustments.length + ' Inventory Adjustments created' }));
            d.adjustments.forEach(function (a) {
                var label = 'Inventory Adjustment ' + (a.tranid ? '#' + a.tranid : '(id ' + a.id + ')');
                box.appendChild(el('div', {}, [a.url ? el('a', { href: a.url, target: '_blank', rel: 'noopener', text: label }) : el('b', { text: label })]));
            });
        } else {
            box.appendChild(el('h2', { text: 'No adjustment needed' }));
        }
        box.appendChild(el('ul', {}, [
            el('li', { text: d.applied + ' line' + (d.applied === 1 ? '' : 's') + ' adjusted' + (d.location ? ' at ' + d.location : '') }),
            el('li', { text: d.skipped + ' already matched on-hand (left off the adjustment)' }),
            d.blocked.length ? el('li', { text: d.blocked.length + ' could not be adjusted and stayed on the sheet:' }) : null
        ]));
        if (d.blocked.length) {
            box.appendChild(el('ul', {}, d.blocked.map(function (b) { return el('li', { text: b.name + ' - ' + b.reason }); })));
        }
        main.appendChild(box);
        main.appendChild(el('div', { class: 'ic-actions' }, [
            el('button', { class: 'ic-btn', type: 'button', text: 'Count more items', onclick: function () { state.done = null; state.q = ''; state.results = []; state.view = 'search'; render(); } }),
            state.order.length ? el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Back to sheet (' + state.order.length + ')', onclick: function () { state.done = null; state.view = 'sheet'; render(); } }) : null
        ]));
    }

    // ------------------------------------------------------------- render --

    function renderHeader(root) {
        var head = el('div', { class: 'ic-head' });
        var row = el('div', { class: 'ic-head-row' }, [
            el('div', { class: 'ic-title', text: BOOT.title || 'Inventory Count' }),
            BOOT.user ? el('div', { class: 'ic-user', text: BOOT.user }) : null
        ]);
        if (BOOT.multiLoc) {
            var sel = el('select', { 'aria-label': 'Location', onchange: function (ev) {
                if (state.order.length && ev.target.value !== state.loc) {
                    // Sheets are per location; switching keeps this one saved under its own location.
                    saveSheet();
                }
                state.loc = ev.target.value; savePrefs(); loadSheet();
                state.results = []; state.offset = 0;
                render();
                if (state.q.trim()) { runSearch(true); }
            } });
            sel.appendChild(el('option', { value: '', text: (BOOT.locations || []).length ? '— pick location —' : 'No locations found' }));
            (BOOT.locations || []).forEach(function (l) {
                var o = el('option', { value: l.id, text: l.name });
                if (l.id === state.loc) { o.selected = true; }
                sel.appendChild(o);
            });
            row.appendChild(el('div', { class: 'ic-loc' }, [el('span', { text: 'Location' }), sel]));
        }
        head.appendChild(row);
        head.appendChild(el('div', { class: 'ic-tabs' }, [
            el('button', { class: 'ic-tab' + (state.view === 'search' ? ' is-on' : ''), type: 'button', text: 'Search & count', onclick: function () { state.view = 'search'; state.done = null; render(); } }),
            el('button', { class: 'ic-tab' + (state.view === 'sheet' ? ' is-on' : ''), type: 'button', onclick: function () { state.view = 'sheet'; state.done = null; state.submitError = ''; state.confirm = false; render(); } }, [
                'Sheet', el('span', { id: 'icSheetBadge', class: 'ic-badge', text: String(state.order.length) })
            ])
        ]));
        root.appendChild(head);
    }

    function render() {
        var root = document.getElementById('app');
        if (!root) { return; }
        var active = document.activeElement;
        var keepSearchFocus = active && active.id === 'icSearch';
        root.innerHTML = '';
        renderHeader(root);
        var main = el('div', { class: 'ic-main' });
        (BOOT.warnings || []).forEach(function (w) { main.appendChild(el('div', { class: 'ic-warn', text: w })); });
        if (BOOT.multiLoc && !state.loc) {
            main.appendChild(el('div', { class: 'ic-warn', text: 'Pick the location you are counting at the top. On-hand quantities and the adjustment are per location.' }));
        }
        if (state.view === 'done') { renderDoneView(main); }
        else if (state.view === 'sheet') { renderSheetView(main); }
        else { renderSearchView(main); }
        main.appendChild(el('div', { class: 'ic-footer', text: 'Counts are saved in this browser until you submit. Submitting creates an Inventory Adjustment in NetSuite as you.' }));
        root.appendChild(main);
        if (state.view === 'search' && (keepSearchFocus || !state.q)) {
            var s = document.getElementById('icSearch');
            if (s && !('ontouchstart' in window && !keepSearchFocus)) { s.focus(); }
        }
    }

    // --------------------------------------------------------------- boot --

    function init() {
        var prefs = loadPrefs();
        var locs = BOOT.locations || [];
        if (BOOT.multiLoc) {
            var known = locs.filter(function (l) { return l.id === prefs.loc; })[0];
            state.loc = known ? known.id : (locs.length === 1 ? locs[0].id : '');
        }
        if (!BOOT.accountLocked) {
            var accts = BOOT.accounts || [];
            var pick = accts.filter(function (a) { return a.id === prefs.account; })[0] || accts.filter(function (a) { return a.suggested; })[0];
            state.account = pick ? pick.id : '';
        }
        loadSheet();
        savePrefs();
        render();
    }

    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); }
    else { init(); }
})();
`;

    return { onRequest: onRequest };
});
