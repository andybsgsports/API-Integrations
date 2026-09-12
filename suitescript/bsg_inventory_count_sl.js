/**
 * BSG Inventory Count - phone / tablet / desktop physical-count Suitelet.
 *
 * One page, login required. It opens on the full list of items in stock at
 * the chosen location (NetSuite's current inventory snapshot, the same rows
 * as the Physical Inventory Worksheet), pageable and narrowable by search
 * (style #, name, description, UPC, vendor code -- any words, in any order).
 * Staff key the counted quantity next to each item and build a "count sheet"
 * that lives in the browser until they press Submit. Submit posts the sheet back to this same Suitelet, which
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
 * Only the roles in CONFIG.SUBMIT_ROLE_IDS (Administrator by default) can post
 * the adjustment: everyone else counts, ticks orders and exports, and the
 * Refresh / Clear sheet / Submit buttons are not shown to them. The submit
 * endpoint enforces this too -- a hidden button is not a permission.
 *
 * URL actions (the page calls these itself):
 *   GET  ?action=search&q=<words>&loc=<id>&page=<n>&instock=T|F
 *                        -> one page of the item list (all items at the
 *                           location when q is blank) with on-hand
 *   GET  ?action=orders&loc=<id>[&item=<id>]  -> unshipped sales-order lines (and
 *                           picked/packed fulfillments) at the location, for one
 *                           item or all
 *   POST ?action=onhand   { ids:[], loc }                -> fresh on-hand per item
 *   POST ?action=submit   { loc, account, memo,
 *                           lines:[{ item, count, orders:[{ ref, qty }] }] }
 *   POST ?action=export   form fields what=items|orders|sheet, format=csv|xlsx|pdf,
 *                         loc, instock, q, payload (JSON)  -> file download
 *                                                       -> creates the adjustment
 *
 * @NApiVersion 2.1
 * @NScriptType Suitelet
 */
define(['N/search', 'N/record', 'N/runtime', 'N/url', 'N/cache', 'N/file', 'N/render', 'N/log'], function (search, record, runtime, url, cache, file, render, log) {

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
        // Internal ids of the roles allowed to post the count as an Inventory
        // Adjustment. 3 is NetSuite's Administrator. Every other role still
        // counts, ticks open orders and exports, but sees no Refresh / Clear
        // sheet / Submit buttons and is refused by the submit endpoint. Add an
        // id here (Setup > Users/Roles > Manage Roles, the id= in its URL) to
        // let another role submit; set to null to let any role submit.
        SUBMIT_ROLE_IDS: [3],
        // Item types that can be counted. Matrix PARENTS are always excluded
        // (they hold no stock); their color/size children are what get counted.
        ITEM_TYPES: ['InvtPart', 'Assembly'],
        // Rows per page of the item list (Load more pages on).
        SEARCH_PAGE_SIZE: 100,
        SEARCH_MAX_WORDS: 6,
        // The page opens on the full list of items at the chosen location -- the
        // "current inventory snapshot". false = every item, zeros included, like
        // BSG's "Custom Current Inventory Snapshot 2" report (Show Zeros on);
        // true = only items whose on-hand is not zero. The page has an
        // In stock / All items toggle either way, remembered per browser.
        IN_STOCK_DEFAULT: false,
        // Lines per Inventory Adjustment. Bigger sheets are submitted by the page
        // as several adjustments, one after another.
        MAX_LINES_PER_ADJUSTMENT: 200,
        MEMO_PREFIX: 'Physical count',
        // Export caps. CSV / Excel page through the whole list; the PDF renderer
        // is slow on big tables, so it stops at PDF_MAX_ROWS and says so.
        EXPORT_MAX_ROWS: 20000,
        PDF_MAX_ROWS: 2000
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

        if (action === 'export') {
            try {
                handleExport(request, response);
            } catch (eExp) {
                log.error({ title: 'invcount export error', details: safeErr(eExp) });
                try { response.addHeader({ name: 'Content-Type', value: 'text/html; charset=utf-8' }); } catch (eh2) { /* headers may be sent */ }
                response.write('<!DOCTYPE html><html><body style="font-family:sans-serif;padding:24px"><h2>Export failed</h2><p>' + escapeHtml(userErr(eExp)) + '</p><p><a href="javascript:history.back()">Back</a></p></body></html>');
            }
            return;
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
            loadDead();
            var isPost = request.method === 'POST';
            var body = isPost ? parseBody(request) : {};
            if (action === 'search') {
                out = searchItems(request.parameters.q, posInt(request.parameters.loc),
                    parseInt(request.parameters.page, 10) || 0, request.parameters.instock !== 'F');
            } else if (action === 'onhand') {
                out = isPost ? refreshOnHand(body) : { ok: false, error: 'POST required.' };
            } else if (action === 'orders') {
                out = openOrders(posInt(request.parameters.item) || null, posInt(request.parameters.loc));
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

    // May this user post the adjustment? Matched on the role's internal id, with
    // the script id ('administrator') accepted too so the stock Administrator
    // role passes even in an account where its id differs.
    function canSubmit() {
        var allowed = CONFIG.SUBMIT_ROLE_IDS;
        if (!allowed) { return true; }   // null = any role
        var user;
        try { user = runtime.getCurrentUser(); } catch (e) { return false; }
        var roleId = null, roleScriptId = '';
        try { roleId = posInt(user.role); } catch (e1) { /* ignore */ }
        try { roleScriptId = String(user.roleId || '').toLowerCase(); } catch (e2) { /* ignore */ }
        if (roleScriptId === 'administrator') { return true; }
        for (var i = 0; i < allowed.length; i++) {
            if (roleId && posInt(allowed[i]) === roleId) { return true; }
            if (roleScriptId && String(allowed[i]).toLowerCase() === roleScriptId) { return true; }
        }
        return false;
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
    // Not in the list: purchasedescription -- NetSuite rejects it as search
    // criteria ("An nlobjSearchFilter contains invalid search criteria").
    var WORD_FIELDS = ['itemid', 'displayname', 'salesdescription', 'vendorname', 'upccode'];
    function wordFilter(w) {
        var out = [];
        WORD_FIELDS.forEach(function (f) {
            if (isDead('item', 'filter', f)) { return; }
            if (out.length) { out.push('or'); }
            out.push([f, f === 'upccode' ? 'is' : 'contains', w]);
        });
        return out;
    }

    // Item fields NetSuite only knows when a feature is on. Asking for one in an
    // account without the feature rejects the WHOLE search ("An nlobjSearchColumn
    // contains an invalid column, or is not in proper syntax: isserialitem"), so
    // each is included only when its feature is in effect.
    var FLAG_COLS = [
        { name: 'isserialitem', feature: 'SERIALIZEDINVENTORY', flag: 'serialized' },
        { name: 'islotitem', feature: 'LOTNUMBEREDINVENTORY', flag: 'lot-numbered' },
        { name: 'usebins', feature: 'BINMANAGEMENT', flag: 'bin-tracked' }
    ];
    function flagCols() {
        return FLAG_COLS.filter(function (c) { return !isDead('item', 'column', c.name) && feature(c.feature); });
    }

    // Fields this account rejected during this request, columns and filters kept
    // apart (the same name can be fine as one and not the other). Anything not in
    // REQUIRED is nice-to-have: withSearch drops it and retries.
    var dead = {};   // 'item:column' -> { fieldname: true }, 'transaction:filter' -> ...
    function deadSet(type, kind) {
        var k = type + ':' + kind;
        return dead[k] || (dead[k] = {});
    }
    function isDead(type, kind, name) { return !!deadSet(type, kind)[name]; }
    var REQUIRED = {
        'item:column': { itemid: 1, type: 1, quantityonhand: 1, locationquantityonhand: 1 },
        'item:filter': { internalid: 1, type: 1, isinactive: 1, matrix: 1, inventorylocation: 1 },
        'transaction:column': { tranid: 1, quantity: 1 },
        'transaction:filter': { type: 1, mainline: 1, item: 1, status: 1 }
    };
    var OPTIONAL_COLS = ['displayname', 'salesdescription', 'upccode', 'vendorname', 'vendor', 'parent'];

    function qtyField(locId, which) {
        return (locId ? 'locationquantity' : 'quantity') + which;
    }

    // "In stock" = anything a counter should see: on hand (positive or negative),
    // available to sell, or on order and not yet received. Values are strings on
    // purpose -- a bare numeric 0 is dropped as "no value" by the filter parser,
    // which silently turns the condition into "any", zeros and all.
    function stockFilter(locId) {
        var oh = qtyField(locId, 'onhand'), av = qtyField(locId, 'available'), oo = qtyField(locId, 'onorder');
        var parts = [];
        if (!isDead('item', 'filter', oh)) { parts.push([oh, 'greaterthan', '0']); parts.push([oh, 'lessthan', '0']); }
        if (!isDead('item', 'filter', av)) { parts.push([av, 'greaterthan', '0']); }
        if (!isDead('item', 'filter', oo)) { parts.push([oo, 'greaterthan', '0']); }
        var expr = [];
        parts.forEach(function (p, i) { if (i) { expr.push('or'); } expr.push(p); });
        return expr.length ? expr : null;
    }

    // Both wordings seen in production, the field name last, after the final colon:
    //   "An nlobjSearchColumn contains an invalid column, or is not in proper syntax: isserialitem."
    //   "An nlobjSearchFilter contains invalid search criteria: purchasedescription."
    function rejectedField(e) {
        var msg = userErr(e);
        var kind = /nlobjSearchColumn/i.test(msg) ? 'column' : (/nlobjSearchFilter/i.test(msg) ? 'filter' : null);
        var m = /:\s*([a-z0-9_.]*[a-z0-9_])\.?\s*$/i.exec(msg);
        return kind && m ? { kind: kind, name: m[1].toLowerCase() } : null;
    }

    // Rejected fields are remembered across requests (a day) so a page load does
    // not pay for a failing search before the one that works.
    var DEAD_CACHE_KEY = 'dead_fields_v2';
    function deadCache() {
        try { return cache.getCache({ name: 'bsg_invcount', scope: cache.Scope.PROTECTED }); } catch (e) { return null; }
    }
    function loadDead() {
        var c = deadCache();
        if (!c) { return; }
        try {
            var raw = c.get({ key: DEAD_CACHE_KEY });
            var parsed = raw ? JSON.parse(raw) : null;
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) { dead = parsed; }
        } catch (e) { /* miss or garbage: start clean */ }
    }
    function saveDead() {
        var c = deadCache();
        if (!c) { return; }
        try { c.put({ key: DEAD_CACHE_KEY, value: JSON.stringify(dead), ttl: 86400 }); } catch (e) { /* best effort */ }
    }

    // Runs a search built by buildSpec(); if NetSuite rejects an optional column
    // or filter field, marks it dead (per search type) and rebuilds -- so an
    // account that lacks a field degrades to a slightly thinner row instead of
    // a broken page. type is the dead-field namespace ('item', 'transaction').
    function withSearch(type, buildSpec, run) {
        for (var attempt = 0; attempt < 8; attempt++) {
            try {
                return run(search.create(buildSpec()));
            } catch (e) {
                var bad = rejectedField(e);
                if (!bad || isDead(type, bad.kind, bad.name) || (REQUIRED[type + ':' + bad.kind] || {})[bad.name]) { throw e; }
                deadSet(type, bad.kind)[bad.name] = true;
                saveDead();
                log.audit({ title: 'invcount: this account rejects ' + type + ' ' + bad.kind + ' ' + bad.name + '; retrying without it', details: userErr(e) });
            }
        }
        throw new Error('Search keeps failing after dropping unsupported fields.');
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
        var cols = [search.createColumn({ name: 'itemid', sort: search.Sort.ASC }), 'type', qtyField(locId, 'onhand')];
        [qtyField(locId, 'available'), qtyField(locId, 'committed'), qtyField(locId, 'onorder')].concat(OPTIONAL_COLS).forEach(function (c) {
            if (!isDead('item', 'column', c)) { cols.push(c); }
        });
        flagCols().forEach(function (c) { cols.push(c.name); });
        return cols;
    }

    function itemSpec(filters, locId) {
        return { type: search.Type.ITEM, filters: filters, columns: itemColumns(locId) };
    }

    // Column value / text that tolerates a column this account does not have.
    function val(r, name) {
        if (isDead('item', 'column', name)) { return ''; }
        try { return r.getValue(name) || ''; } catch (e) { return ''; }
    }
    function txt(r, name) {
        if (isDead('item', 'column', name)) { return ''; }
        try { return r.getText(name) || ''; } catch (e) { return ''; }
    }
    function num(r, name) {
        var n = parseFloat(val(r, name));
        return isFinite(n) ? n : 0;
    }

    function rowToItem(r, locId) {
        var qty = parseFloat(r.getValue(qtyField(locId, 'onhand')));
        var flags = [];
        flagCols().forEach(function (c) { if (isTrue(r.getValue(c.name))) { flags.push(c.flag); } });
        return {
            id: String(r.id),
            name: r.getValue('itemid') || '',
            display: val(r, 'displayname'),
            desc: val(r, 'salesdescription'),
            upc: val(r, 'upccode'),
            vendor: txt(r, 'vendor') || val(r, 'vendorname'),
            parent: txt(r, 'parent'),
            onhand: isFinite(qty) ? qty : 0,
            available: num(r, qtyField(locId, 'available')),
            committed: num(r, qtyField(locId, 'committed')),
            onorder: num(r, qtyField(locId, 'onorder')),
            blocked: flags.length ? flags.join(', ') : ''
        };
    }

    // One search serves both modes. With no words it is the full list of items at
    // the location (the current inventory snapshot, sorted like the Physical
    // Inventory Worksheet); with words it narrows that list. inStock keeps only
    // rows with quantity on hand, available, or on order (see stockFilter).
    function searchItems(q, locId, page, inStock) {
        var words = tokenize(q);
        if (!multiLocation()) { locId = null; }
        var stockApplied = false;
        function buildSpec() {
            var filters = baseFilters(locId);
            stockApplied = false;
            if (inStock) {
                var sf = stockFilter(locId);
                if (sf) { filters.push('and'); filters.push(sf); stockApplied = true; }
            }
            words.forEach(function (w) {
                var wf = wordFilter(w);
                if (wf.length) { filters.push('and'); filters.push(wf); }
            });
            return itemSpec(filters, locId);
        }
        var paged = withSearch('item', buildSpec, function (srch) { return srch.runPaged({ pageSize: CONFIG.SEARCH_PAGE_SIZE }); });
        var pageCount = paged.pageRanges.length;
        var idx = Math.max(0, page || 0);
        var items = [];
        if (idx < pageCount) {
            paged.fetch({ index: idx }).data.forEach(function (r) { items.push(rowToItem(r, locId)); });
        }
        return { ok: true, items: items, total: paged.count, page: idx, more: idx + 1 < pageCount, stockApplied: !inStock || stockApplied };
    }

    // Fresh on-hand for a set of items at a location, keyed by item id. Carries
    // the blocked flag too, so submit refuses serial/lot/bin items server side
    // no matter what the page sent.
    function lookupOnHand(ids, locId) {
        var map = {};
        if (!ids.length) { return map; }
        if (!multiLocation()) { locId = null; }
        function buildSpec() {
            var filters = baseFilters(locId);
            filters.push('and');
            filters.push(['internalid', 'anyof', ids]);
            return itemSpec(filters, locId);
        }
        withSearch('item', buildSpec, function (srch) {
            srch.run().each(function (r) {
                var it = rowToItem(r, locId);
                map[it.id] = it;
                return true;
            });
        });
        return map;
    }

    // Sales orders that still owe the customer some of this item: the stock
    // counters may find pulled and staged, out at the decorator, or waiting for
    // pickup rather than on the shelf. Pending Fulfillment, Partially Fulfilled,
    // and Pending Billing/Partially Fulfilled are the statuses with unshipped
    // lines; anything shipped is off the books already and must not be counted.
    var OPEN_SO_STATUSES = ['SalesOrd:B', 'SalesOrd:D', 'SalesOrd:E'];
    // Item fulfillments that exist but have not shipped (Pick, Pack, Ship):
    // the units are pulled and boxed, still on hand in NetSuite, and no
    // longer "unshipped" on the sales order line -- so they need listing too.
    var UNSHIPPED_IF_STATUSES = ['ItemShip:A', 'ItemShip:B'];

    // BSG's order numbers already read "JH-SO625"; a bare number gets "SO ".
    function orderRef(tranid, id) {
        var n = String(tranid || id || '').trim();
        return /^\d+$/.test(n) ? 'SO ' + n : n;
    }

    function tval(r, name) {
        if (isDead('transaction', 'column', name)) { return ''; }
        try { return r.getValue(name) || ''; } catch (e) { return ''; }
    }
    function ttxt(r, name) {
        if (isDead('transaction', 'column', name)) { return ''; }
        try { return r.getText(name) || ''; } catch (e) { return ''; }
    }
    function recUrl(type, id) {
        try { return url.resolveRecord({ recordType: type, recordId: id, isEditMode: false }); } catch (e) { return ''; }
    }

    // Sales-order lines whose units have been RECEIVED (committed from stock)
    // and not shipped. Those units are what the counters may find pulled and
    // staged, out at the decorator, or waiting for pickup rather than on the
    // shelf. Committed quantity is the one reliable signal: picking or packing
    // does not clear it (an order shows Committed 10 next to Pulled 10),
    // shipping does, and a line that was zeroed or returned to the vendor has
    // none even when a pick record survives. So a line is listed only when it
    // still has committed units on an order that is itself open (Pending
    // Fulfillment / Partially Fulfilled / Pending Billing-Partially Fulfilled;
    // never Billed, Closed or Cancelled). Picked / packed fulfillments only
    // annotate their order line -- "10 to count (10 layaway)" -- and a
    // fulfillment with no committed line behind it is a leftover pick record
    // with nothing to count. itemId null = every item at the location.
    function openOrders(itemId, locId) {
        if (!multiLocation()) { locId = null; }
        var out = [];
        var MAX = 5000;
        var truncated = false;
        function fetchAll(srch) {
            var rs = srch.run(), rows = [], start = 0, page = 1000;
            while (start < MAX) {
                var chunk = rs.getRange({ start: start, end: Math.min(start + page, MAX) });
                rows = rows.concat(chunk);
                if (chunk.length < page) { break; }
                start += page;
            }
            if (rows.length >= MAX) { truncated = true; }
            return rows;
        }

        // Decoration, setup and other service / charge lines (PP3C, STTEMBLOGO)
        // are never counted: only the item types the count list itself shows.
        function itemTypeFilter(filters) {
            if (!isDead('transaction', 'filter', 'item.type')) {
                filters.push('and');
                filters.push(['item.type', 'anyof', CONFIG.ITEM_TYPES]);
            }
        }
        function soSpec() {
            var filters = [
                ['type', 'anyof', ['SalesOrd']], 'and',
                ['mainline', 'is', 'F'], 'and',
                ['status', 'anyof', OPEN_SO_STATUSES]
            ];
            itemTypeFilter(filters);
            if (itemId) { filters.push('and'); filters.push(['item', 'anyof', [String(itemId)]]); }
            if (locId && !isDead('transaction', 'filter', 'location')) { filters.push('and'); filters.push(['location', 'anyof', [String(locId)]]); }
            var cols = [search.createColumn({ name: 'trandate', sort: search.Sort.ASC }), 'tranid', 'quantity', 'item'];
            ['entity', 'statusref', 'quantityshiprecv', 'quantitycommitted', 'salesrep'].forEach(function (c) {
                if (!isDead('transaction', 'column', c)) { cols.push(c); }
            });
            return { type: search.Type.TRANSACTION, filters: filters, columns: cols };
        }
        withSearch('transaction', soSpec, function (srch) {
            fetchAll(srch).forEach(function (r) {
                var qty = Math.abs(parseFloat(tval(r, 'quantity')) || 0);
                var shipped = Math.abs(parseFloat(tval(r, 'quantityshiprecv')) || 0);
                var remaining = round4(Math.max(0, qty - shipped));
                if (remaining <= 0) { return; } // this line is fully shipped even if the order is still open
                var committed = Math.min(remaining, Math.abs(parseFloat(tval(r, 'quantitycommitted')) || 0));
                if (committed <= 0) { return; } // nothing received for this line yet: not in the building
                out.push({
                    kind: 'order',
                    id: String(r.id),
                    ref: orderRef(tval(r, 'tranid'), r.id),
                    customer: ttxt(r, 'entity') || '',
                    rep: ttxt(r, 'salesrep') || '',
                    date: String(tval(r, 'trandate')),
                    item: String(tval(r, 'item')),
                    itemName: ttxt(r, 'item') || '',
                    qty: qty,
                    shipped: round4(shipped),
                    remaining: remaining,
                    committed: round4(committed),
                    backordered: round4(Math.max(0, remaining - committed)),
                    status: ttxt(r, 'statusref') || '',
                    url: recUrl('salesorder', r.id)
                });
            });
        });

        // Picked / packed fulfillments (BSG's labels: Pulled / Layaway) annotate
        // the order lines above; they never add rows of their own. Best effort:
        // an account without Pick, Pack, Ship simply has none (or rejects the
        // status values -> logged). A fulfillment line comes back twice from a
        // transaction search -- the item line and its cost-of-goods posting line
        // -- so the COGS rows are filtered out and, in case that filter is ever
        // rejected, the rows are de-duplicated by record + item.
        var pulled = [];
        if (!out.length) { return { ok: true, orders: out, truncated: truncated }; }
        try {
            function ifSpec() {
                var filters = [
                    ['type', 'anyof', ['ItemShip']], 'and',
                    ['mainline', 'is', 'F'], 'and',
                    ['status', 'anyof', UNSHIPPED_IF_STATUSES]
                ];
                if (!isDead('transaction', 'filter', 'cogs')) { filters.push('and'); filters.push(['cogs', 'is', 'F']); }
                itemTypeFilter(filters);
                if (itemId) { filters.push('and'); filters.push(['item', 'anyof', [String(itemId)]]); }
                if (locId && !isDead('transaction', 'filter', 'location')) { filters.push('and'); filters.push(['location', 'anyof', [String(locId)]]); }
                var cols = [search.createColumn({ name: 'trandate', sort: search.Sort.ASC }), 'tranid', 'quantity', 'item'];
                ['entity', 'statusref', 'createdfrom'].forEach(function (c) {
                    if (!isDead('transaction', 'column', c)) { cols.push(c); }
                });
                return { type: search.Type.TRANSACTION, filters: filters, columns: cols };
            }
            withSearch('transaction', ifSpec, function (srch) {
                var seen = {};
                fetchAll(srch).forEach(function (r) {
                    var qty = Math.abs(parseFloat(tval(r, 'quantity')) || 0);
                    if (qty <= 0) { return; }
                    var key = String(r.id) + ':' + String(tval(r, 'item'));
                    if (seen[key]) { return; }
                    seen[key] = true;
                    var from = ttxt(r, 'createdfrom');           // "Sales Order #JH-SO625"
                    var m = /#\s*(\S+)/.exec(from);
                    var ref = m ? orderRef(m[1], '') : ('IF ' + String(tval(r, 'tranid') || r.id));
                    pulled.push({ ref: ref, item: String(tval(r, 'item')), qty: qty, pulledStatus: ttxt(r, 'statusref') || 'Pulled' });
                });
            });
        } catch (e) {
            log.audit({ title: 'invcount: picked/packed fulfillment search skipped', details: userErr(e) });
        }
        // If the item-type join filter was rejected, do the same check through
        // the item search the count list uses (one query per 1,000 items).
        if (isDead('transaction', 'filter', 'item.type')) {
            var ids = {}, list = [];
            out.forEach(function (e) { if (e.item && !ids[e.item]) { ids[e.item] = true; list.push(e.item); } });
            var countable = {};
            for (var i = 0; i < list.length; i += 1000) {
                var chunk = list.slice(i, i + 1000);
                withSearch('item', function () {
                    return { type: search.Type.ITEM, filters: [['type', 'anyof', CONFIG.ITEM_TYPES], 'and', ['internalid', 'anyof', chunk]], columns: ['internalid'] };
                }, function (srch) {
                    srch.run().each(function (r) { countable[String(r.id)] = true; return true; });
                });
            }
            out = out.filter(function (e) { return countable[e.item]; });
        }

        // A pulled / packed fulfillment's units are still inside its order
        // line's committed quantity, so it only annotates that line.
        pulled.forEach(function (f) {
            for (var i = 0; i < out.length; i++) {
                if (out[i].ref === f.ref && out[i].item === f.item) {
                    out[i].pulled = round4((out[i].pulled || 0) + f.qty);
                    out[i].pulledStatus = f.pulledStatus;
                    break;
                }
            }
        });
        return { ok: true, orders: out, truncated: truncated };
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

    function cleanRef(v) {
        return String(v == null ? '' : v).replace(/[^A-Za-z0-9#_\-. ]/g, '').trim().slice(0, 30);
    }

    // A line is { item, count } plus, optionally, the open orders the counter
    // ticked to account for units that are pulled, packed, at the decorator or
    // waiting for pickup: orders:[{ ref, qty }]. They go on the line memo.
    function normalizeLines(raw) {
        var seen = {}, out = [];
        (Array.isArray(raw) ? raw : []).forEach(function (l) {
            if (!l) { return; }
            var id = posInt(l.item);
            var count = parseFloat(l.count);
            if (!id || !isFinite(count) || count < 0) { return; }
            var orders = [];
            (Array.isArray(l.orders) ? l.orders : []).slice(0, 30).forEach(function (o) {
                var ref = cleanRef(o && o.ref);
                var qty = parseFloat(o && o.qty);
                if (ref && isFinite(qty) && qty > 0) { orders.push({ ref: ref, qty: round4(qty) }); }
            });
            var line = { item: String(id), count: round4(count), orders: orders };
            if (seen[id]) { seen[id].count = line.count; seen[id].orders = orders; return; } // same item twice: last wins
            seen[id] = line;
            out.push(line);
        });
        return out;
    }

    function lineMemo(l) {
        var memo = 'Counted ' + l.count;
        if (l.orders && l.orders.length) {
            var total = 0;
            l.orders.forEach(function (o) { total += o.qty; });
            memo += ' (incl. ' + round4(total) + ' on open orders: ' + l.orders.map(function (o) { return o.ref; }).join(', ') + ')';
        }
        return memo + '; on hand ' + l.onhand;
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
        if (!canSubmit()) {
            return { ok: false, error: 'Your role cannot post inventory adjustments. Export the sheet and hand it to an administrator.' };
        }
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
            candidates.push({ item: l.item, name: it.name, count: l.count, onhand: it.onhand, orders: l.orders });
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
            trySetLine(rec, 'memo', lineMemo(l).slice(0, 999));
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

    // ---------------------------------------------------------------- export --
    // One dataset shape { title, subtitle, columns:[{ key, label, num }], rows:[{}] }
    // rendered three ways. Items and orders are fetched here so the export covers
    // the whole list, not the page on screen; the sheet lives in the browser, so
    // the page posts its lines.

    function handleExport(request, response) {
        loadDead();
        var P = request.parameters || {};
        var what = String(P.what || 'items');
        var format = String(P.format || 'csv').toLowerCase();
        var locId = multiLocation() ? posInt(P.loc) : null;
        var payload = {};
        try { payload = P.payload ? JSON.parse(P.payload) : {}; } catch (e) { payload = {}; }
        var where = locName(locId);
        var stamp = new Date().toISOString().slice(0, 10);
        var ds;
        if (what === 'orders') { ds = exportOrders(locId, where, payload); }
        else if (what === 'sheet') { ds = exportSheet(where, payload); }
        else { ds = exportItems(String(P.q || ''), locId, P.instock !== 'F', where); }
        var base = 'BSG-count-' + what + (where ? '-' + where.replace(/[^A-Za-z0-9]+/g, '_') : '') + '-' + stamp;
        if (format === 'pdf') {
            if (ds.rows.length > CONFIG.PDF_MAX_ROWS) {
                throw new Error('The PDF export stops at ' + CONFIG.PDF_MAX_ROWS + ' rows and this is ' + ds.rows.length + '. Narrow the list (In stock, or a search) or use CSV / Excel.');
            }
            var pdf = render.xmlToPdf({ xmlString: pdfXml(ds) });
            pdf.name = base + '.pdf';
            response.writeFile({ file: pdf, isInline: false });
        } else if (format === 'xlsx') {
            response.writeFile({ file: xlsxFile(ds, base + '.xlsx'), isInline: false });
        } else {
            var csv = file.create({ name: base + '.csv', fileType: file.Type.CSV, contents: '\ufeff' + csvText(ds) });
            response.writeFile({ file: csv, isInline: false });
        }
    }

    function locName(locId) {
        if (!locId) { return ''; }
        try { return String(search.lookupFields({ type: search.Type.LOCATION, id: locId, columns: ['name'] }).name || ''); } catch (e) { return ''; }
    }

    var ITEM_COLS = [
        { key: 'name', label: 'Item' }, { key: 'display', label: 'Description' }, { key: 'vendor', label: 'Pref. vendor' },
        { key: 'upc', label: 'UPC' }, { key: 'onhand', label: 'On hand', num: true }, { key: 'available', label: 'Available', num: true },
        { key: 'committed', label: 'Committed', num: true }, { key: 'onorder', label: 'On order', num: true }, { key: 'count', label: 'Count' }
    ];

    function exportItems(q, locId, inStock, where) {
        var words = tokenize(q);
        var stockApplied = false;
        function buildSpec() {
            var filters = baseFilters(locId);
            stockApplied = false;
            if (inStock) {
                var sf = stockFilter(locId);
                if (sf) { filters.push('and'); filters.push(sf); stockApplied = true; }
            }
            words.forEach(function (w) {
                var wf = wordFilter(w);
                if (wf.length) { filters.push('and'); filters.push(wf); }
            });
            return itemSpec(filters, locId);
        }
        var rows = [];
        withSearch('item', buildSpec, function (srch) {
            var paged = srch.runPaged({ pageSize: 1000 });
            for (var i = 0; i < paged.pageRanges.length && rows.length < CONFIG.EXPORT_MAX_ROWS; i++) {
                paged.fetch({ index: i }).data.forEach(function (r) {
                    var it = rowToItem(r, locId);
                    it.display = it.display || it.desc;
                    it.count = '';
                    rows.push(it);
                });
            }
        });
        return {
            title: 'Inventory count list' + (where ? ' - ' + where : ''),
            subtitle: (inStock && stockApplied ? 'Items in stock (on hand, available or on order)' : 'All items') + (words.length ? ' matching "' + words.join(' ') + '"' : '') + ' · ' + rows.length + ' items · blank Count column to fill in',
            columns: ITEM_COLS, rows: rows
        };
    }

    var ORDER_COLS = [
        { key: 'rep', label: 'Sales rep' },
        { key: 'ref', label: 'Order' }, { key: 'customer', label: 'Customer' }, { key: 'date', label: 'Date' }, { key: 'status', label: 'Status' },
        { key: 'itemName', label: 'Item' }, { key: 'qty', label: 'Ordered', num: true }, { key: 'shipped', label: 'Shipped', num: true },
        { key: 'committed', label: 'To count', num: true }, { key: 'pulled', label: 'Pulled', num: true }, { key: 'ticked', label: 'Ticked' }
    ];

    function exportOrders(locId, where, payload) {
        var ticked = {};
        (Array.isArray(payload.ticked) ? payload.ticked : []).forEach(function (t) { ticked[String(t)] = true; });
        var rows = openOrders(null, locId).orders.map(function (o) {
            return {
                rep: o.rep || NO_REP, ref: o.ref, customer: o.customer, date: o.date, status: o.status,
                itemName: o.itemName, qty: o.qty, shipped: o.shipped, committed: o.committed, pulled: o.pulled || 0,
                ticked: ticked[o.ref + '|' + o.item] ? 'Yes' : ''
            };
        });
        // Same order as the page: sales rep, then oldest order first.
        rows.sort(function (x, y) {
            if (x.rep !== y.rep) { return repRank(x.rep) - repRank(y.rep) || (x.rep < y.rep ? -1 : 1); }
            return dateKey(x.date) - dateKey(y.date) || (x.ref < y.ref ? -1 : x.ref > y.ref ? 1 : 0);
        });
        return {
            title: 'Open orders to count' + (where ? ' - ' + where : ''),
            subtitle: 'Sales-order lines received but not shipped · ' + rows.length + ' lines',
            columns: ORDER_COLS, rows: rows
        };
    }

    var SHEET_COLS = [
        { key: 'name', label: 'Item' }, { key: 'display', label: 'Description' }, { key: 'vendor', label: 'Pref. vendor' }, { key: 'onhand', label: 'On hand', num: true },
        { key: 'count', label: 'Count', num: true }, { key: 'delta', label: 'Adjust by', num: true }, { key: 'orders', label: 'On open orders' }
    ];

    function exportSheet(where, payload) {
        var rows = (Array.isArray(payload.lines) ? payload.lines : []).slice(0, CONFIG.EXPORT_MAX_ROWS).map(function (l) {
            var onhand = parseFloat(l.onhand) || 0, count = parseFloat(l.count) || 0;
            return {
                name: String(l.name || ''), display: String(l.display || l.desc || ''), vendor: String(l.vendor || ''), onhand: onhand, count: count,
                delta: round4(count - onhand),
                orders: (Array.isArray(l.orders) ? l.orders : []).map(function (o) { return cleanRef(o && o.ref) + (o && o.qty ? ' (' + o.qty + ')' : ''); }).filter(Boolean).join(', ')
            };
        });
        return {
            title: 'Count sheet' + (where ? ' - ' + where : ''),
            subtitle: rows.length + ' lines · not yet submitted' + (payload.memo ? ' · ' + String(payload.memo).slice(0, 200) : ''),
            columns: SHEET_COLS, rows: rows
        };
    }

    // "No sales rep" sorts last; everyone else alphabetically.
    var NO_REP = 'No sales rep';
    function repRank(name) { return name === NO_REP ? 1 : 0; }
    // NetSuite hands dates back as the user's display format (M/D/YYYY here).
    // Anything unparseable sorts first so it is never hidden at the bottom.
    function dateKey(v) {
        var m = /^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/.exec(String(v || '').trim());
        if (!m) { return 0; }
        var y = parseInt(m[3], 10);
        if (y < 100) { y += 2000; }
        return y * 10000 + parseInt(m[1], 10) * 100 + parseInt(m[2], 10);
    }

    function cell(v) { return v == null ? '' : String(v); }

    function csvText(ds) {
        function q(v) {
            var t = cell(v);
            return /[",\r\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
        }
        var lines = [ds.columns.map(function (c) { return q(c.label); }).join(',')];
        ds.rows.forEach(function (r) { lines.push(ds.columns.map(function (c) { return q(r[c.key]); }).join(',')); });
        return lines.join('\r\n') + '\r\n';
    }

    function xmlEsc(v) {
        return cell(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
            .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '');
    }

    // A minimal .xlsx: the OOXML parts zipped with N/compress (loaded on demand so
    // an account without it still serves CSV and PDF). Inline strings, so no
    // shared-string table; numbers are real numbers.
    function xlsxFile(ds, name) {
        var compress;
        try { compress = require('N/compress'); } catch (e) { throw new Error('Excel export is not available in this account (N/compress). Use CSV, which opens in Excel.'); }
        function colRef(i) {
            var s2 = '';
            i += 1;
            while (i > 0) { var m = (i - 1) % 26; s2 = String.fromCharCode(65 + m) + s2; i = Math.floor((i - 1) / 26); }
            return s2;
        }
        var xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'];
        var head = ['<row r="1">'];
        ds.columns.forEach(function (c, i) { head.push('<c r="' + colRef(i) + '1" t="inlineStr"><is><t>' + xmlEsc(c.label) + '</t></is></c>'); });
        head.push('</row>');
        xml.push(head.join(''));
        ds.rows.forEach(function (r, ri) {
            var rn = ri + 2, row = ['<row r="' + rn + '">'];
            ds.columns.forEach(function (c, i) {
                var v = r[c.key];
                if (c.num && v !== '' && v != null && isFinite(parseFloat(v))) {
                    row.push('<c r="' + colRef(i) + rn + '"><v>' + parseFloat(v) + '</v></c>');
                } else if (v !== '' && v != null) {
                    row.push('<c r="' + colRef(i) + rn + '" t="inlineStr"><is><t xml:space="preserve">' + xmlEsc(v) + '</t></is></c>');
                }
            });
            row.push('</row>');
            xml.push(row.join(''));
        });
        xml.push('</sheetData></worksheet>');
        var parts = [
            { dir: '', name: '[Content_Types].xml', body: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>' },
            { dir: '_rels', name: '.rels', body: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>' },
            { dir: 'xl', name: 'workbook.xml', body: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Count" sheetId="1" r:id="rId1"/></sheets></workbook>' },
            { dir: 'xl/_rels', name: 'workbook.xml.rels', body: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>' },
            { dir: 'xl/worksheets', name: 'sheet1.xml', body: xml.join('') }
        ];
        var archiver = compress.createArchiver();
        parts.forEach(function (pt) {
            var f = file.create({ name: pt.name, fileType: file.Type.XMLDOC, contents: pt.body });
            var opts = { file: f };
            if (pt.dir) { opts.directory = pt.dir; }
            archiver.add(opts);
        });
        return archiver.archive({ name: name, type: compress.Type.ZIP });
    }

    // BFO template for N/render: landscape, header row repeated on every page.
    function pdfXml(ds) {
        var widths = ds.columns.map(function (c) { return c.num ? 7 : (c.key === 'display' || c.key === 'customer' || c.key === 'itemName' ? 22 : 12); });
        var total = widths.reduce(function (a, b) { return a + b; }, 0);
        var head = ds.columns.map(function (c, i) {
            return '<th width="' + Math.round(widths[i] / total * 100) + '%"' + (c.num ? ' align="right"' : '') + '>' + xmlEsc(c.label) + '</th>';
        }).join('');
        var body = ds.rows.map(function (r) {
            return '<tr>' + ds.columns.map(function (c) {
                return '<td' + (c.num ? ' align="right"' : '') + '>' + xmlEsc(r[c.key]) + '</td>';
            }).join('') + '</tr>';
        }).join('');
        return '<?xml version="1.0"?><!DOCTYPE pdf PUBLIC "-//big.faceless.org//report" "report-1.1.dtd">' +
            '<pdf><head><style type="text/css">body{font-family:Helvetica,sans-serif;font-size:8pt}h1{font-size:14pt;margin:0 0 2pt 0}p.sub{color:#555;margin:0 0 8pt 0}table{width:100%;border-collapse:collapse}th{background:#b3252a;color:#fff;font-weight:bold;padding:3pt 4pt;text-align:left;font-size:8pt}td{padding:2pt 4pt;border-bottom:0.5pt solid #ccc;vertical-align:top}</style>' +
            '<macrolist><macro id="nlfooter"><p align="right" style="font-size:7pt;color:#777">' + xmlEsc(ds.title) + ' · page <pagenumber/> of <totalpages/></p></macro></macrolist></head>' +
            '<body size="Letter-Landscape" footer="nlfooter" footer-height="14pt" margin-top="24pt" margin-bottom="28pt" margin-left="24pt" margin-right="24pt">' +
            '<h1>' + xmlEsc(ds.title) + '</h1><p class="sub">' + xmlEsc(ds.subtitle) + ' · ' + xmlEsc(new Date().toISOString().slice(0, 10)) + '</p>' +
            '<table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></body></pdf>';
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
            inStockDefault: CONFIG.IN_STOCK_DEFAULT !== false,
            maxLines: CONFIG.MAX_LINES_PER_ADJUSTMENT,
            canSubmit: canSubmit(),
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
/* Palette: BSG red as the accent, a cool neutral scale for everything else.
   Red is reserved for the masthead, the active tab and the one primary action
   on a screen -- spreading it over every control is what made this look like a
   toy rather than a tool. */
:root{
  --red:#a4161a;--red-deep:#7f1113;--red-tint:#fbeced;
  --ink:#16181d;--ink-2:#3d424d;--muted:#6b7280;--faint:#9aa0aa;
  --line:#e3e5ea;--line-2:#eff0f3;--bg:#f5f6f8;--card:#fff;
  --ok:#16794c;--ok-bg:#e7f5ee;--warn:#9a5b00;--warn-bg:#fdf4e3;--bad:#b3261e;--bad-bg:#fdecea;
  --focus:#2563eb;
  --shadow:0 1px 2px rgba(16,24,40,.05);
  --shadow-lift:0 4px 12px rgba(16,24,40,.09);
  --r:10px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-text-size-adjust:100%;-webkit-font-smoothing:antialiased}
button,input,select{font:inherit;color:inherit}
button{cursor:pointer}
b,strong{font-weight:600}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.ic-loading{padding:56px 20px;text-align:center;color:var(--muted)}

/* ------------------------------------------------------------- masthead -- */
.ic-head{position:sticky;top:0;z-index:20;background:var(--red);color:#fff;box-shadow:var(--shadow-lift)}
.ic-head-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;max-width:1120px;margin:0 auto;padding:13px 20px;padding-top:calc(13px + env(safe-area-inset-top,0px))}
.ic-title{font-weight:700;font-size:17px;letter-spacing:-.01em;flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ic-user{font-size:13px;color:rgba(255,255,255,.78);white-space:nowrap}
.ic-loc{display:flex;align-items:center;gap:8px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:rgba(255,255,255,.7)}
.ic-loc select{background:rgba(255,255,255,.12);color:#fff;border:1px solid rgba(255,255,255,.35);border-radius:8px;padding:7px 10px;max-width:60vw;min-height:38px;font-size:14px;letter-spacing:normal;text-transform:none;-webkit-appearance:none;appearance:none;background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath fill='%23fff' d='M1 1l5 5 5-5'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;padding-right:28px}
.ic-loc select:hover{background-color:rgba(255,255,255,.2)}
.ic-loc select option{color:var(--ink);background:#fff}

/* Tabs sit on their own white rail under the masthead: an underlined strip
   reads as navigation, where three big red blocks read as three buttons. */
.ic-tabs{display:flex;gap:0;background:var(--card);border-bottom:1px solid var(--line);padding:0 20px;max-width:1120px;margin:0 auto}
.ic-tab{flex:1 1 0;max-width:190px;background:none;border:0;border-bottom:2.5px solid transparent;border-radius:0;color:var(--muted);font-weight:600;font-size:14.5px;padding:13px 14px 11px;min-height:46px;white-space:nowrap;transition:color .12s,border-color .12s}
.ic-tab:hover{color:var(--ink)}
.ic-tab.is-on{color:var(--red);border-bottom-color:var(--red)}
.ic-badge{display:inline-block;min-width:20px;padding:1px 7px;border-radius:10px;background:var(--line);color:var(--ink-2);font-size:11.5px;font-weight:700;margin-left:7px;vertical-align:1px;font-variant-numeric:tabular-nums}
.ic-tab.is-on .ic-badge{background:var(--red);color:#fff}

.ic-main{padding:20px;max-width:1120px;margin:0 auto}

/* --------------------------------------------------------------- notices -- */
.ic-warn{background:var(--warn-bg);border:1px solid #f0dcb4;color:var(--warn);padding:11px 14px;border-radius:var(--r);margin-bottom:12px;font-size:13.5px;line-height:1.5}
.ic-error{background:var(--bad-bg);border:1px solid #f3c6c1;color:var(--bad);padding:11px 14px;border-radius:var(--r);margin:12px 0;font-size:13.5px;word-break:break-word}
.ic-empty{padding:44px 16px;text-align:center;color:var(--muted);font-size:14px}
.ic-progress{padding:18px;text-align:center;color:var(--muted);font-size:14px}

/* ---------------------------------------------------------------- search -- */
.ic-search{position:relative;margin-bottom:12px}
.ic-search input{width:100%;padding:12px 44px 12px 14px;border:1px solid var(--line);border-radius:var(--r);background:var(--card);font-size:15.5px;min-height:48px;box-shadow:var(--shadow)}
.ic-search input::placeholder{color:var(--faint)}
.ic-search input:focus{outline:none;border-color:var(--focus);box-shadow:0 0 0 3px rgba(37,99,235,.14)}
.ic-search .ic-clear{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:transparent;border:0;font-size:20px;color:var(--faint);width:36px;height:36px;border-radius:18px;line-height:1}
.ic-search .ic-clear:hover{color:var(--ink);background:var(--line-2)}
.ic-hint{font-size:13px;color:var(--muted);margin:0 0 14px;line-height:1.55;max-width:78ch}

.ic-listhead{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 10px;flex-wrap:wrap}
.ic-count{font-size:13px;color:var(--muted);min-height:20px;font-variant-numeric:tabular-nums}
.ic-seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--card);box-shadow:var(--shadow)}
.ic-segbtn{border:0;background:transparent;padding:7px 14px;min-height:38px;font-weight:600;font-size:13.5px;color:var(--muted)}
.ic-segbtn:hover{background:var(--line-2);color:var(--ink)}
.ic-segbtn.is-on{background:var(--red);color:#fff}
.ic-segbtn.is-on:hover{background:var(--red-deep)}

/* ------------------------------------------------------------------ rows -- */
.ic-list{display:flex;flex-direction:column;gap:8px}
.ic-row{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;display:flex;gap:14px;align-items:center;box-shadow:var(--shadow);transition:border-color .12s,box-shadow .12s}
.ic-row:hover{border-color:#d2d5dc}
.ic-row.is-onsheet{border-color:#a8d7bf;background:var(--ok-bg)}
.ic-row.is-blocked{opacity:.68}
.ic-row.has-error{border-color:#f0b9b9;background:#fffafa}
.ic-info{flex:1 1 auto;min-width:0}
.ic-name{font-weight:600;font-size:14.5px;letter-spacing:-.005em;word-break:break-word}
.ic-desc{font-size:13px;color:var(--ink-2);word-break:break-word;margin-top:1px}
.ic-meta{font-size:12.5px;color:var(--muted);margin-top:4px;word-break:break-word;font-variant-numeric:tabular-nums}
.ic-meta b{color:var(--ink);font-weight:600}
.ic-flag{display:inline-block;font-size:11.5px;font-weight:600;color:var(--warn);background:var(--warn-bg);border:1px solid #f0dcb4;border-radius:5px;padding:1px 7px;margin-top:6px}
.ic-onsheet{display:inline-block;font-size:11.5px;font-weight:600;color:var(--ok);background:var(--ok-bg);border:1px solid #bfe3d0;border-radius:5px;padding:1px 7px;margin-top:6px}

/* --------------------------------------------------------------- controls -- */
.ic-ctl{display:flex;align-items:center;gap:8px;flex:0 0 auto}
.ic-ok{display:inline-flex;align-items:center;gap:6px;padding:0 10px 0 8px;min-height:44px;border:1px solid var(--line);border-radius:8px;background:var(--card);font-size:12.5px;font-weight:600;color:var(--muted);cursor:pointer;user-select:none;white-space:nowrap}
.ic-ok:hover{background:var(--line-2);color:var(--ink-2)}
.ic-ok input{width:17px;height:17px;margin:0;accent-color:var(--ok);cursor:pointer}
.ic-ok.is-on,.ic-ok:has(input:checked){border-color:#a8d7bf;background:var(--ok-bg);color:var(--ok)}
.ic-ok.is-on:hover,.ic-ok:has(input:checked):hover{background:#dcefe4}
.ic-qty{width:80px;min-height:44px;padding:8px;border:1px solid var(--line);border-radius:8px;font-size:18px;font-weight:600;text-align:center;background:var(--card);font-variant-numeric:tabular-nums}
.ic-qty::placeholder{font-weight:400;font-size:15px;color:var(--faint)}
.ic-qty:focus{outline:none;border-color:var(--focus);box-shadow:0 0 0 3px rgba(37,99,235,.14)}
.ic-btn{min-height:44px;padding:0 16px;border-radius:8px;border:1px solid transparent;background:var(--red);color:#fff;font-weight:600;font-size:14.5px;white-space:nowrap;transition:background .12s,box-shadow .12s}
.ic-btn:hover{background:var(--red-deep)}
.ic-btn:disabled{opacity:.45;cursor:default;background:var(--red)}
.ic-btn.is-ghost{background:var(--card);color:var(--ink);border-color:var(--line);box-shadow:var(--shadow)}
.ic-btn.is-ghost:hover{background:var(--line-2);border-color:#d2d5dc}
.ic-btn.is-ok{background:var(--ok)}
.ic-btn.is-ok:hover{background:#12613d}
.ic-btn.is-sm{min-height:36px;padding:0 12px;font-size:13.5px}
.ic-step{width:38px;height:44px;border-radius:8px;border:1px solid var(--line);background:var(--card);font-size:19px;font-weight:500;line-height:1;color:var(--ink-2)}
.ic-step:hover{background:var(--line-2);color:var(--ink)}
.ic-x{width:34px;height:34px;border-radius:17px;border:0;background:transparent;color:var(--faint);font-size:19px;line-height:1}
.ic-x:hover{color:var(--bad);background:var(--bad-bg)}
.ic-more{margin:16px 0;text-align:center}
.ic-link{background:none;border:0;color:var(--focus);font-weight:600;padding:5px 0;font-size:13px;cursor:pointer}
.ic-link:hover{text-decoration:underline}
.ic-commit-link{background:none;border:0;padding:0 1px;font:inherit;font-weight:700;color:var(--focus);text-decoration:underline;text-underline-offset:2px;cursor:pointer;min-height:26px;font-variant-numeric:tabular-nums}
.ic-commit-link:hover{color:#1d4ed8}

/* ----------------------------------------------------------------- sheet -- */
.ic-sum{display:flex;gap:8px;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;margin-bottom:12px;font-size:12.5px;color:var(--muted);box-shadow:var(--shadow)}
.ic-sum div{flex:1 1 auto;min-width:110px;text-transform:uppercase;letter-spacing:.05em;font-size:11px}
.ic-sum b{font-size:21px;display:block;color:var(--ink);font-weight:650;letter-spacing:-.015em;margin-bottom:2px;text-transform:none;font-variant-numeric:tabular-nums}
.ic-delta{font-weight:700;min-width:54px;text-align:center;padding:6px 8px;border-radius:7px;font-size:14px;font-variant-numeric:tabular-nums}
.ic-delta.is-pos{background:var(--ok-bg);color:var(--ok)}
.ic-delta.is-neg{background:var(--bad-bg);color:var(--bad)}
.ic-delta.is-zero{background:var(--line-2);color:var(--faint)}
.ic-line-ctl{display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex:0 0 auto}
.ic-line-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.ic-commit-warn{font-size:12.5px;color:var(--warn);background:var(--warn-bg);border:1px solid #f0dcb4;border-radius:7px;padding:6px 10px;margin-top:8px;line-height:1.45}
.ic-incl{font-size:12.5px;color:var(--ok);background:var(--ok-bg);border:1px solid #bfe3d0;border-radius:7px;padding:6px 10px;margin-top:8px}
.ic-form{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px;margin-top:14px;display:flex;flex-direction:column;gap:14px;box-shadow:var(--shadow)}
/* Sentence case, not uppercase: these labels carry a parenthetical
   ("Memo (goes on the Inventory Adjustment)") that shouts in all caps. */
.ic-form label{font-size:12.5px;color:var(--muted);display:block;margin-bottom:6px;font-weight:600}
.ic-form input,.ic-form select{width:100%;min-height:44px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);font-size:14.5px}
.ic-form input:focus,.ic-form select:focus{outline:none;border-color:var(--focus);box-shadow:0 0 0 3px rgba(37,99,235,.14)}
.ic-actions{display:flex;gap:10px;flex-wrap:wrap}
.ic-actions .ic-btn{flex:1 1 auto;min-height:48px;font-size:15px}
/* A toolbar of small buttons (Expand all / Collapse all / Reload) keeps its
   own size inside an .ic-actions row rather than stretching like a CTA. */
.ic-actions .ic-btn.is-sm{flex:0 0 auto;min-height:36px;font-size:13.5px}
.ic-confirm{background:var(--warn-bg);border:1px solid #eed9ab;border-radius:var(--r);padding:16px;margin-top:12px}
.ic-confirm p{margin:0 0 10px;font-size:14px;line-height:1.55;color:var(--ink-2)}
.ic-confirm p b{color:var(--ink)}
.ic-done{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:20px;margin-bottom:14px;box-shadow:var(--shadow)}
.ic-done h2{margin:0 0 10px;font-size:19px;color:var(--ok);letter-spacing:-.01em}
.ic-done a{color:var(--focus);font-weight:600;font-size:16px}
.ic-done ul{margin:10px 0 0 18px;padding:0;font-size:13.5px;color:var(--ink-2);line-height:1.7}
.ic-export{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:var(--muted);margin:14px 0 0;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.ic-export .ic-btn{min-height:34px;padding:0 13px;font-size:13px;text-transform:none;letter-spacing:normal}

/* ----------------------------------------------------------- open orders -- */
.ic-repgrp{border:1px solid var(--line);border-radius:var(--r);background:var(--card);padding:6px;box-shadow:var(--shadow)}
.ic-rephead{display:flex;align-items:center;gap:6px 12px;flex-wrap:wrap;cursor:pointer;user-select:none;padding:10px 12px;border-radius:8px}
.ic-rephead:hover{background:var(--line-2)}
.ic-repname{font-weight:650;font-size:15px;color:var(--ink);letter-spacing:-.01em}
.ic-repsum{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap;font-variant-numeric:tabular-nums}
.ic-repsum.is-ticked{color:var(--ok);font-weight:600}
.ic-repbody{display:flex;flex-direction:column;gap:8px;margin:2px 6px 6px}
.ic-ordgrp{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:4px 10px 8px}
.ic-ordhead{display:flex;flex-wrap:wrap;gap:3px 10px;align-items:baseline;cursor:pointer;user-select:none;padding:8px 4px;border-radius:6px}
.ic-ordhead:hover{background:#ebedf1}
.ic-caret{display:inline-block;width:16px;color:var(--faint);font-size:11px}
.ic-ordhead a{color:var(--ink);font-weight:650;font-size:14.5px;text-decoration:none;border-bottom:1px solid var(--line)}
.ic-ordhead a:hover{color:var(--focus);border-bottom-color:currentColor}
.ic-ordhead small{color:var(--muted);font-size:12px}
.ic-ordsum{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap;font-variant-numeric:tabular-nums}
.ic-ordsum.is-ticked{color:var(--ok);font-weight:600}
.ic-ordline{display:flex;align-items:flex-start;gap:11px;padding:9px 4px;border-top:1px solid var(--line-2);cursor:pointer}
.ic-ordline:hover{background:#ebedf1;border-radius:6px}
.ic-ordline input{width:19px;height:19px;margin:2px 0 0;flex:0 0 auto;accent-color:var(--red);cursor:pointer}
.ic-ordline .ic-name{font-size:14px}
.ic-ordline .ic-meta{margin-top:2px}
.ic-ordline.is-off{opacity:.5;cursor:default}
.ic-ordall-row{background:var(--card);border:1px solid var(--line);border-radius:7px;padding:9px 10px;margin-top:6px;border-top:1px solid var(--line)}
.ic-ordall-row:hover{background:var(--card)}
.ic-ordall-row .ic-name{font-size:13.5px;color:var(--ink-2)}
.ic-orders{margin-top:8px;border-top:1px solid var(--line-2);padding-top:6px;font-size:13px}
.ic-order{display:flex;align-items:flex-start;gap:10px;padding:6px 0;cursor:pointer}
.ic-order input{width:19px;height:19px;margin:1px 0 0;flex:0 0 auto;accent-color:var(--red);cursor:pointer}
.ic-order a{color:var(--focus);font-weight:600}
.ic-order small{color:var(--muted);font-variant-numeric:tabular-nums}
.ic-order.is-off{opacity:.5;cursor:default}

.ic-footer{text-align:center;color:var(--faint);font-size:12px;padding:28px 16px 20px;line-height:1.6;padding-bottom:calc(20px + env(safe-area-inset-bottom,0px))}

@media (max-width:640px){
  .ic-main{padding:14px}
  .ic-head-row{padding:11px 14px;padding-top:calc(11px + env(safe-area-inset-top,0px));gap:10px}
  .ic-tabs{padding:0 8px}
  .ic-tab{max-width:none;font-size:13.5px;padding:12px 6px 10px}
  .ic-user{display:none}
  .ic-loc{flex:1 1 100%;font-size:11px}
  .ic-loc select{max-width:100%;flex:1 1 auto}
  .ic-row{flex-wrap:wrap;gap:10px}
  .ic-ctl,.ic-line-ctl{width:100%;justify-content:flex-end;align-items:center;flex-direction:row}
  .ic-ctl .ic-qty{flex:1 1 auto}
  .ic-sum div{min-width:72px}
  .ic-sum b{font-size:18px}
  .ic-repsum,.ic-ordsum{margin-left:0;flex:1 1 100%;white-space:normal}
}
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
        view: 'search',            // search | orders | sheet | done
        allOrders: null,           // Open orders tab: { entries, loc } | { error }
        ordersFilter: '',
        ordersOpen: {},            // Open orders tab: order refs expanded by the user
        repsClosed: {},            // Open orders tab: sales reps collapsed by the user
        loc: '', account: '',
        q: '', results: [], more: false, page: 0, total: 0, loaded: false, searching: false, searchError: '', stockApplied: true,
        instock: true,             // list only items with qty on hand, available or on order
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
    function savePrefs() { lsSet(PREF_KEY, JSON.stringify({ loc: state.loc, account: state.account, instock: state.instock })); }

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

    function round4(n) { return Math.round((Number(n) || 0) * 10000) / 10000; }
    function ordersTotal(line) {
        var t = 0;
        (line.orders || []).forEach(function (o) { t += Number(o.qty) || 0; });
        return round4(t);
    }
    function belowCommitted(line) {
        var c = Number(line.committed) || 0;
        return !line.blocked && c > 0 && (Number(line.count) || 0) < c;
    }

    function lineFor(item) {
        return state.sheet[item.id] || null;
    }
    // A sheet line for the item, created from whatever we know about it. Lines
    // created from the Open orders tab know only id and name; hydrateLine fills
    // in on-hand etc. in the background.
    function ensureLine(item) {
        var line = state.sheet[item.id];
        if (!line) {
            line = { id: item.id, name: item.name || ('item ' + item.id), display: item.display || '', desc: item.desc || '', upc: item.upc || '', vendor: item.vendor || '',
                onhand: item.onhand, available: item.available, committed: item.committed, onorder: item.onorder, blocked: item.blocked || '', count: 0, orders: [] };
            state.sheet[item.id] = line;
            state.order.push(item.id);
            if (item.onhand == null) { hydrateLine(item.id); }
        }
        return line;
    }
    var hydrating = {};
    function hydrateLine(id) {
        if (hydrating[id]) { return; }
        hydrating[id] = true;
        apiPost('onhand', { ids: [id], loc: state.loc || '' }).then(function (res) {
            hydrating[id] = false;
            var line = state.sheet[id], fresh = res && res.ok && res.items && res.items[id];
            if (!line || !fresh) { return; }
            line.name = fresh.name || line.name; line.display = fresh.display; line.desc = fresh.desc; line.upc = fresh.upc; line.vendor = fresh.vendor;
            line.onhand = fresh.onhand; line.available = fresh.available; line.committed = fresh.committed; line.onorder = fresh.onorder; line.blocked = fresh.blocked || '';
            saveSheet();
            if (state.view === 'sheet') { render(); }
        });
    }
    // The Qty box holds what is ON THE SHELF. Units committed to open orders
    // that have been pulled are not on the shelf; they come back in by ticking
    // those orders, which add on top. So the shelf NetSuite expects is
    // Available (on hand - committed), and the total it expects is on hand.
    function shelfOf(line) {
        return round4((Number(line.count) || 0) - ordersTotal(line));
    }
    // "Correct" on a result row: the counter looked, and the shelf holds what
    // NetSuite expects -- Available. Committed units are accounted for on the
    // Open orders tab, not here, so ticking this alone leaves the line short by
    // the committed quantity until those orders are ticked too (the sheet says
    // so). With nothing committed, Available is on hand and the delta is zero.
    function confirmExpected(item) {
        return setCount(item, Number(item.available) || 0);
    }
    // Drives the Correct box: the shelf matches Available.
    function matchesExpected(line) {
        if (!line || line.blocked || line.available == null) { return false; }
        return shelfOf(line) === round4(Number(line.available) || 0);
    }
    // Drives "· no change" and the confirm panel: the total matches on hand, so
    // this line produces no adjustment at all.
    function noChange(line) {
        return !!line && !line.blocked && round4(Number(line.count) || 0) === round4(Number(line.onhand) || 0);
    }

    // The Qty box on a result row is what was found on the shelf; units ticked
    // on open orders sit on top of it.
    function setCount(item, count) {
        var line = ensureLine(item);
        line.count = round4(count + ordersTotal(line));
        line.viaTick = false;
        line.error = '';
        line.addedAt = Date.now();
        saveSheet();
        updateBadge();
        return line;
    }
    // Tick / untick an open order (or picked/packed fulfillment) for an item:
    // its committed units go into that item's count and the order is recorded.
    function tickOrder(item, entry, checked) {
        var existed = !!state.sheet[item.id];
        var line = ensureLine(item);
        if (!existed) { line.viaTick = true; } // never keyed: only here because of a tick
        var qty = round4(entry.committed);
        line.orders = (line.orders || []).filter(function (o) { return o.ref !== entry.ref; });
        if (checked) {
            line.orders.push({ ref: entry.ref, qty: qty });
            line.count = round4((Number(line.count) || 0) + qty);
        } else {
            line.count = round4(Math.max(0, (Number(line.count) || 0) - qty));
            if (line.viaTick && !line.orders.length && line.count === 0) {
                removeLine(item.id);
                return null;
            }
        }
        line.error = '';
        saveSheet();
        updateBadge();
        return line;
    }
    function isTicked(itemId, ref) {
        var line = state.sheet[itemId];
        return !!line && (line.orders || []).some(function (o) { return o.ref === ref; });
    }
    function inclText(line) {
        var t = ordersTotal(line);
        return t ? 'Includes ' + fmt(t) + ' on open orders: ' + line.orders.map(function (o) { return o.ref; }).join(', ') : '';
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
        var lines = 0, pos = 0, neg = 0, blocked = 0, below = 0, changed = 0;
        state.order.forEach(function (id) {
            var l = state.sheet[id];
            if (!l) { return; }
            lines++;
            if (l.blocked) { blocked++; return; }
            if (belowCommitted(l)) { below++; }
            if (!noChange(l)) { changed++; }
            var d = (Number(l.count) || 0) - (Number(l.onhand) || 0);
            if (d > 0) { pos += d; } else { neg += -d; }
        });
        return { lines: lines, pos: pos, neg: neg, blocked: blocked, below: below, changed: changed };
    }
    function updateBadge() {
        var b = document.getElementById('icSheetBadge');
        if (b) { b.textContent = String(state.order.length); }
    }

    // ------------------------------------------------------------- search --

    // Loads one page of the item list: the full in-stock list when the search box
    // is empty, the matching subset when it is not. reset = start from page 0
    // (existing rows stay on screen until the new page lands, so typing does
    // not flicker); otherwise appends the next page.
    function runSearch(reset) {
        var q = state.q.trim();
        if (BOOT.multiLoc && !state.loc) {
            state.results = []; state.total = 0; state.more = false; state.loaded = false; state.searching = false; state.searchError = '';
            renderResults();
            return;
        }
        state.searching = true; state.searchError = '';
        renderResults();
        var mySeq = ++searchSeq;
        var page = reset ? 0 : state.page + 1;
        apiGet('search', { q: q, loc: state.loc || '', page: page, instock: state.instock ? 'T' : 'F' }).then(function (res) {
            if (mySeq !== searchSeq) { return; } // a newer request superseded this one
            state.searching = false;
            if (!res || !res.ok) {
                state.searchError = (res && res.error) || 'Could not load items.';
                if (reset) { state.results = []; state.total = 0; state.more = false; }
            } else {
                state.results = reset ? res.items : state.results.concat(res.items);
                state.total = typeof res.total === 'number' ? res.total : state.results.length;
                state.page = res.page || 0;
                state.more = !!res.more;
                state.stockApplied = res.stockApplied !== false;
                state.loaded = true;
            }
            renderResults();
            if (reset && q && res && res.ok) { autoFocusSingle(q); }
        });
    }
    function setInStock(v) {
        if (state.instock === v) { return; }
        state.instock = v; savePrefs();
        runSearch(true);
    }
    function countLabel(q) {
        var n = state.total, shown = state.results.length;
        var where = state.loc ? ' at ' + locName(state.loc) : '';
        if (q) {
            return (n > shown ? shown + ' of ' + n : String(n)) + ' match' + (n === 1 ? '' : 'es') + ' for "' + q + '"' + (state.instock ? ' in stock' : '');
        }
        return (n > shown ? shown + ' of ' + n : String(n)) + (state.instock ? ' items in stock' : ' items') + where;
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

    var ordersCache = {};   // itemId -> { orders: [...] } | { error } for this page load

    // The open orders for one item, loaded on first open. Each gets a checkbox:
    // ticking it puts the order's committed units into the item's count.
    function ordersPanel(item, onChange) {
        var box = el('div', { class: 'ic-orders', hidden: true });
        function paint() {
            box.innerHTML = '';
            var cached = ordersCache[item.id];
            if (!cached) {
                box.appendChild(el('div', { class: 'ic-progress', text: 'Loading open orders…' }));
                apiGet('orders', { item: item.id, loc: state.loc || '' }).then(function (res) {
                    ordersCache[item.id] = (!res || !res.ok) ? { error: (res && res.error) || 'Could not load orders.' } : { orders: res.orders || [] };
                    if (!box.hidden) { paint(); }
                });
                return;
            }
            if (cached.error) { box.appendChild(el('div', { class: 'ic-error', text: cached.error })); return; }
            if (!cached.orders.length) {
                box.appendChild(el('div', { text: 'No received-but-unshipped sales orders for this item' + (state.loc ? ' at ' + locName(state.loc) : '') + '. Anything marked shipped is already off the books.' }));
                return;
            }
            box.appendChild(el('div', { class: 'ic-meta', text: 'Received or pulled for these orders, not marked shipped. Tick an order once you have found its units (staged, at the decorator, waiting for pickup): they are added to the count.' }));
            cached.orders.forEach(function (o) { box.appendChild(orderRow(item, o, onChange)); });
        }
        return {
            box: box,
            toggle: function () {
                if (!box.hidden) { box.hidden = true; return; }
                box.hidden = false;
                paint();
            }
        };
    }

    // "0 shipped of 10 · 10 to count (10 layaway) · Pending Fulfillment"
    function orderDetail(o) {
        return fmt(o.shipped) + ' shipped of ' + fmt(o.qty) + ' · ' + fmt(o.committed) + ' to count'
            + (o.pulled ? ' (' + fmt(o.pulled) + ' ' + String(o.pulledStatus || 'pulled').toLowerCase() + ')' : '')
            + (o.backordered ? ' · ' + fmt(o.backordered) + ' more not received yet' : '')
            + (o.status ? ' · ' + o.status : '');
    }

    // One order line with its checkbox, shared by the item panels and the
    // Open orders tab.
    function orderRow(item, o, onChange) {
        var off = !(o.committed > 0);
        var cb = el('input', { type: 'checkbox', 'aria-label': 'Counted: ' + o.ref + ' ' + (o.itemName || item.name || '') });
        cb.checked = isTicked(item.id, o.ref);
        cb.disabled = off;
        cb.addEventListener('change', function () {
            tickOrder(item, o, cb.checked);
            if (onChange) { onChange(); }
        });
        var detail = orderDetail(o);
        return el('label', { class: 'ic-order' + (off ? ' is-off' : '') }, [
            cb,
            el('span', {}, [
                o.url ? el('a', { href: o.url, target: '_blank', rel: 'noopener', text: o.ref }) : el('b', { text: o.ref }),
                o.customer ? ' · ' + o.customer : '',
                el('br'),
                el('small', { text: detail + (o.date ? ' · ' + o.date : '') })
            ])
        ]);
    }

    // "Committed: 10" with the number clickable when there is something to see.
    function committedEl(committed, panel) {
        if (!(Number(committed) > 0)) { return el('b', { text: fmt(committed == null ? 0 : committed) }); }
        return el('button', { class: 'ic-commit-link', type: 'button', text: fmt(committed), 'aria-label': 'Show open orders (' + fmt(committed) + ' committed)', onclick: function () { panel.toggle(); } });
    }

    function onSheetText(line) {
        var t = ordersTotal(line);
        return 'On sheet: ' + fmt(line.count)
            + (t ? ' (incl. ' + fmt(t) + ' on open orders)' : '')
            + (noChange(line) ? ' · no change' : '');
    }

    function resultRow(item) {
        var line = lineFor(item);
        var panel = ordersPanel(item, function () { refreshResultRow(item.id); });
        var row = el('div', { class: 'ic-row' + (line ? ' is-onsheet' : '') + (item.blocked ? ' is-blocked' : ''), 'data-row-for': item.id });
        var meta = [];
        if (item.vendor) { meta.push(item.vendor); }
        if (item.upc) { meta.push('UPC ' + item.upc); }
        var info = el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name', text: item.name }),
            (item.display || item.desc) ? el('div', { class: 'ic-desc', text: item.display && item.desc && item.display !== item.desc ? item.display + ' - ' + item.desc : (item.display || item.desc) }) : null,
            el('div', { class: 'ic-meta' }, [meta.length ? meta.join(' · ') + ' · ' : '', 'On hand: ', el('b', { text: fmt(item.onhand) }), ' · Avail: ', el('b', { text: fmt(item.available) }), ' · Committed: ', committedEl(item.committed, panel), ' · On order: ', el('b', { text: fmt(item.onorder) })]),
            item.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + item.blocked + ') - adjust manually' }) : null,
            line ? el('div', { class: 'ic-onsheet', 'data-onsheet-for': item.id, text: onSheetText(line) }) : null,
            panel.box
        ]);
        row.appendChild(info);
        if (!item.blocked) {
            var input = el('input', {
                class: 'ic-qty', type: 'text', inputmode: 'decimal', enterkeyhint: 'next', autocomplete: 'off', placeholder: 'Qty',
                'data-qty-for': item.id, value: line ? fmt(line.count) : '', 'aria-label': 'Counted quantity for ' + item.name
            });
            var btn = el('button', { class: 'ic-btn', type: 'button', text: line ? 'Update' : 'Add' });
            // Right of Add: "counted it, the book is right". Ticking it puts the
            // item on the sheet at its current on-hand (zero delta); unticking
            // takes it back off.
            var ok = el('input', { type: 'checkbox', 'data-ok-for': item.id,
                'aria-label': 'Counted ' + (item.name || 'this item') + ' and the shelf holds the expected ' + fmt(item.available) });
            ok.checked = matchesExpected(line);
            ok.addEventListener('change', function () {
                if (ok.checked) {
                    confirmExpected(item);
                    input.value = fmt(item.available);
                } else {
                    removeLine(item.id);
                    input.value = '';
                }
                refreshResultRow(item.id);
            });
            var okWrap = el('label', { class: 'ic-ok' + (ok.checked ? ' is-on' : ''), title: 'Counted, and the shelf holds the expected ' + fmt(item.available) + (Number(item.committed) > 0 ? ' (the ' + fmt(item.committed) + ' committed are accounted for on Open orders)' : '') }, [
                ok, el('span', { text: 'Correct' })
            ]);
            function commit(advance) {
                var c = parseCount(input.value);
                if (c === null) { input.focus(); input.classList.add('is-bad'); return; }
                setCount(item, c);
                refreshResultRow(item.id);
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
            row.appendChild(el('div', { class: 'ic-ctl' }, [input, btn, okWrap]));
        }
        return row;
    }

    // Repaint the on-sheet tag / button of one result row after its line changed.
    function refreshResultRow(itemId) {
        var row = document.querySelector('[data-row-for="' + itemId + '"]');
        if (!row) { return; }
        var line = state.sheet[itemId];
        var info = row.querySelector('.ic-info');
        var tag = info && info.querySelector('[data-onsheet-for]');
        var btn = row.querySelector('.ic-ctl .ic-btn');
        var ok = row.querySelector('[data-ok-for]');
        if (ok) {
            ok.checked = matchesExpected(line);
            // :has() is not everywhere yet, so the green state is a class too.
            if (ok.parentNode) { ok.parentNode.className = 'ic-ok' + (ok.checked ? ' is-on' : ''); }
        }
        if (line) {
            row.classList.add('is-onsheet');
            if (btn) { btn.textContent = 'Update'; }
            if (tag) { tag.textContent = onSheetText(line); }
            else if (info) { info.insertBefore(el('div', { class: 'ic-onsheet', 'data-onsheet-for': itemId, text: onSheetText(line) }), info.querySelector('.ic-orders')); }
        } else {
            row.classList.remove('is-onsheet');
            if (btn) { btn.textContent = 'Add'; }
            if (tag) { tag.parentNode.removeChild(tag); }
        }
    }

    function renderResults() {
        var box = document.getElementById('icResults');
        if (!box) { return; }
        box.innerHTML = '';
        if (state.searchError) { box.appendChild(el('div', { class: 'ic-error', text: state.searchError })); }
        if (BOOT.multiLoc && !state.loc) {
            box.appendChild(el('div', { class: 'ic-empty', text: 'Pick a location at the top to load its inventory.' }));
            return;
        }
        var q = state.q.trim();
        if (state.instock && state.loaded && !state.stockApplied) {
            box.appendChild(el('div', { class: 'ic-warn', text: 'This account does not support the In stock filter, so every item is listed.' }));
        }
        box.appendChild(el('div', { class: 'ic-listhead' }, [
            el('div', { class: 'ic-count', text: state.loaded ? countLabel(q) : '' }),
            el('div', { class: 'ic-seg', role: 'group', 'aria-label': 'Which items to list' }, [
                el('button', { class: 'ic-segbtn' + (state.instock ? ' is-on' : ''), type: 'button', text: 'In stock', onclick: function () { setInStock(true); } }),
                el('button', { class: 'ic-segbtn' + (!state.instock ? ' is-on' : ''), type: 'button', text: 'All items', onclick: function () { setInStock(false); } })
            ])
        ]));
        var list = el('div', { class: 'ic-list' });
        state.results.forEach(function (it) { list.appendChild(resultRow(it)); });
        box.appendChild(list);
        if (state.searching) {
            box.appendChild(el('div', { class: 'ic-progress', text: state.results.length ? 'Loading…' : 'Loading inventory…' }));
        } else if (!state.results.length) {
            var msg;
            if (q) { msg = 'No ' + (state.instock ? 'in-stock ' : '') + 'items match "' + q + '".' + (state.instock ? ' Try All items.' : ''); }
            else { msg = state.instock ? 'Nothing in stock at this location. Try All items.' : 'No inventory items at this location.'; }
            box.appendChild(el('div', { class: 'ic-empty', text: msg }));
        } else if (state.more) {
            var left = Math.max(0, state.total - state.results.length);
            box.appendChild(el('div', { class: 'ic-more' }, [
                el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Load more' + (left ? ' (' + left + ' left)' : ''), onclick: function () { runSearch(false); } })
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
        main.appendChild(el('p', { class: 'ic-hint', text: 'Every item at this location is listed below; In stock keeps those with quantity on hand, available, or on order. Search to jump to one, key what is on the shelf and press Add (Enter jumps to the next item), or tick Correct when the shelf holds the expected quantity. Units pulled for open orders are added on the Open orders tab. Counts wait on the Sheet tab until you submit.' }));
        main.appendChild(el('div', { id: 'icResults' }));
        renderResults();
        if (!(BOOT.multiLoc && !state.loc)) { main.appendChild(exportBar('items')); }
        if (!state.loaded && !state.searching && !state.searchError) { runSearch(true); }
    }

    // -------------------------------------------------------------- sheet --

    function sheetRow(line) {
        var row = el('div', { class: 'ic-row' + (line.error ? ' has-error' : '') });
        var delta = el('div', { class: 'ic-delta' });
        var warn = el('div', { class: 'ic-commit-warn' });
        var incl = el('div', { class: 'ic-incl' });
        var input = el('input', { class: 'ic-qty', type: 'text', inputmode: 'decimal', autocomplete: 'off', value: fmt(line.count), 'aria-label': 'Counted quantity for ' + line.name });
        function paintDelta() {
            if (line.blocked) { delta.className = 'ic-delta is-zero'; delta.textContent = 'n/a'; return; }
            var d = round4((Number(line.count) || 0) - (Number(line.onhand) || 0));
            delta.className = 'ic-delta ' + (d > 0 ? 'is-pos' : d < 0 ? 'is-neg' : 'is-zero');
            delta.textContent = signed(d);
        }
        function paintWarn() {
            if (belowCommitted(line)) {
                warn.textContent = fmt(line.committed) + ' units are committed to open sales orders and are not counted here yet. Tick those orders (below, or on the Open orders tab) to bring them in, or ship what has already left. Otherwise this line adjusts down by ' + fmt(round4((Number(line.onhand) || 0) - (Number(line.count) || 0))) + '.';
                warn.hidden = false;
            } else { warn.hidden = true; }
        }
        function paint() {
            input.value = fmt(line.count);
            paintDelta();
            var t = inclText(line);
            incl.textContent = t; incl.hidden = !t;
            paintWarn();
            saveSheet(); paintTotals();
        }
        function apply(v) {
            var c = parseCount(v);
            if (c === null) { return; }
            line.count = c; line.error = ''; row.classList.remove('has-error'); paint();
        }
        input.addEventListener('input', function () {
            var c = parseCount(input.value);
            if (c === null) { return; }
            line.count = c; line.error = ''; row.classList.remove('has-error');
            // no input.value rewrite while typing (keeps the caret); update the rest
            paintDelta(); paintWarn();
            saveSheet(); paintTotals();
        });
        input.addEventListener('blur', function () { paint(); });
        var minus = el('button', { class: 'ic-step', type: 'button', text: '−', 'aria-label': 'Minus one', onclick: function () { apply(Math.max(0, (Number(line.count) || 0) - 1)); } });
        var plus = el('button', { class: 'ic-step', type: 'button', text: '+', 'aria-label': 'Plus one', onclick: function () { apply((Number(line.count) || 0) + 1); } });
        var remove = el('button', { class: 'ic-x', type: 'button', text: '×', 'aria-label': 'Remove ' + line.name, onclick: function () {
            removeLine(line.id); render();
        } });

        var panel = ordersPanel(line, paint);
        var ordersBtn = el('button', { class: 'ic-link', type: 'button', text: 'Open orders' + (Number(line.committed) > 0 ? ' (' + fmt(line.committed) + ' committed)' : ''), onclick: function () { panel.toggle(); } });

        row.appendChild(el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name', text: line.name }),
            (line.display || line.desc) ? el('div', { class: 'ic-desc', text: line.display || line.desc }) : null,
            el('div', { class: 'ic-meta' }, ['On hand: ', el('b', { text: fmt(line.onhand) }), ' · Avail: ', el('b', { text: fmt(line.available) }), ' · Committed: ', committedEl(line.committed, panel), ' · On order: ', el('b', { text: fmt(line.onorder) }), line.vendor ? ' · ' + line.vendor : '']),
            line.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + line.blocked + ') - will be skipped' }) : null,
            line.error ? el('div', { class: 'ic-error', text: line.error }) : null,
            incl,
            warn,
            line.blocked ? null : ordersBtn,
            panel.box
        ]));
        row.appendChild(el('div', { class: 'ic-line-ctl' }, [
            el('div', { class: 'ic-line-top' }, [minus, input, plus, delta, remove])
        ]));
        paint();
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
        form.appendChild(exportBar('sheet'));
        if (state.submitError) { form.appendChild(el('div', { class: 'ic-error', text: state.submitError })); }
        if (state.submitting) {
            form.appendChild(el('div', { class: 'ic-progress', text: state.progress || 'Submitting…' }));
        } else if (state.confirm) {
            var live = t.lines - t.blocked;
            // Every line matching on-hand is the good case, not a no-op to
            // apologise for: say so plainly instead of asking them to confirm
            // an adjustment that will not exist.
            var nothingToAdjust = t.changed === 0;
            form.appendChild(el('div', { class: 'ic-confirm' }, [
                el('p', {}, [el('b', { text: nothingToAdjust
                    ? 'Nothing to adjust — every counted line already matches.'
                    : 'Create an Inventory Adjustment for ' + t.changed + ' line' + (t.changed === 1 ? '' : 's') + (state.loc ? ' at ' + locName(state.loc) : '') + '?' })]),
                el('p', { text: nothingToAdjust
                    ? 'All ' + live + ' line' + (live === 1 ? '' : 's') + ' on this sheet equal the on-hand NetSuite already holds, so submitting records the count and creates no Inventory Adjustment. Nothing about your inventory changes.'
                    : 'Those ' + t.changed + ' item' + (t.changed === 1 ? '' : 's') + ' move to the count you entered (' + '+' + fmt(t.pos) + ' / −' + fmt(t.neg) + ' units). The other ' + (live - t.changed) + ' already match and are left off the adjustment entirely.' }
                    ),
                (t.blocked || t.below) ? el('p', { text: (t.blocked ? t.blocked + ' flagged line' + (t.blocked === 1 ? ' is' : 's are') + ' skipped. ' : '') + (t.below ? t.below + ' line' + (t.below === 1 ? ' is' : 's are') + ' below the quantity committed to open sales orders — check the Open orders tab before posting.' : '') }) : null,
                el('div', { class: 'ic-actions' }, [
                    el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Cancel', onclick: function () { state.confirm = false; render(); } }),
                    el('button', { class: 'ic-btn is-ok', type: 'button', text: nothingToAdjust ? 'Yes, record the count' : 'Yes, submit count', onclick: submitSheet })
                ])
            ]));
        } else if (!BOOT.canSubmit) {
            form.appendChild(el('div', { class: 'ic-warn', text: 'Your role cannot post inventory adjustments. Export the sheet above and hand it to an administrator, who can key or import the counts.' }));
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
                if (fresh) { line.onhand = fresh.onhand; line.available = fresh.available; line.committed = fresh.committed; line.onorder = fresh.onorder; line.blocked = fresh.blocked || ''; line.error = ''; }
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
                lines: batch.map(function (id) {
                    var l = state.sheet[id];
                    return { item: id, count: l.count, orders: l.orders || [] };
                })
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

    // Downloads go through a hidden form POST to the same Suitelet (action=export)
    // in a new tab, so the browser handles the file and the page keeps its state.
    var NO_REP = 'No sales rep';
    function repRank(name) { return name === NO_REP ? 1 : 0; }
    function dateKey(v) {
        var m = /^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/.exec(String(v || '').trim());
        if (!m) { return 0; }
        var y = parseInt(m[3], 10);
        if (y < 100) { y += 2000; }
        return y * 10000 + parseInt(m[1], 10) * 100 + parseInt(m[2], 10);
    }

    function exportBar(what) {
        function go(format) {
            var fields = { action: 'export', what: what, format: format, loc: state.loc || '', instock: state.instock ? 'T' : 'F', q: state.q || '' };
            if (what === 'orders') {
                var ticked = [];
                state.order.forEach(function (id) {
                    var l = state.sheet[id];
                    (l && l.orders || []).forEach(function (o) { ticked.push(o.ref + '|' + id); });
                });
                fields.payload = JSON.stringify({ ticked: ticked });
            } else if (what === 'sheet') {
                fields.payload = JSON.stringify({
                    memo: state.memo || '',
                    lines: state.order.map(function (id) { return state.sheet[id]; }).filter(Boolean).map(function (l) {
                        return { name: l.name, display: l.display || l.desc || '', vendor: l.vendor || '', onhand: l.onhand, count: l.count, orders: l.orders || [] };
                    })
                });
            }
            var form = el('form', { method: 'post', action: API, target: '_blank', style: 'display:none' });
            Object.keys(fields).forEach(function (k) {
                form.appendChild(el('input', { type: 'hidden', name: k, value: fields[k] }));
            });
            document.body.appendChild(form);
            form.submit();
            setTimeout(function () { if (form.parentNode) { form.parentNode.removeChild(form); } }, 1000);
        }
        var label = what === 'orders' ? 'Export open orders:' : what === 'sheet' ? 'Export sheet:' : 'Export this list:';
        return el('div', { class: 'ic-export', 'data-export': what }, [
            el('span', { text: label }),
            el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'CSV', onclick: function () { go('csv'); } }),
            el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Excel', onclick: function () { go('xlsx'); } }),
            el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'PDF', onclick: function () { go('pdf'); } })
        ]);
    }

    // Open orders tab: every unshipped sales-order line (and picked/packed
    // fulfillment) at the location, grouped by order -- the printout replaced.
    function loadAllOrders() {
        state.allOrders = { loading: true };
        var loc = state.loc || '';
        apiGet('orders', { loc: loc }).then(function (res) {
            state.allOrders = (!res || !res.ok) ? { error: (res && res.error) || 'Could not load open orders.' } : { entries: res.orders || [], truncated: !!res.truncated, loc: loc };
            if (state.view === 'orders') { render(); }
        });
    }
    function renderOrdersView(main) {
        if (BOOT.multiLoc && !state.loc) {
            main.appendChild(el('div', { class: 'ic-empty', text: 'Pick a location at the top to load its open orders.' }));
            return;
        }
        var a = state.allOrders;
        if (!a || (a.loc !== undefined && a.loc !== (state.loc || ''))) { loadAllOrders(); a = state.allOrders; }
        main.appendChild(el('p', { class: 'ic-hint', text: 'Sales orders at this location whose items have been received or pulled and packed but not marked shipped. Tick a line once you have found its units (staged, at the decorator, waiting for pickup): they are added to that item’s count on the Sheet. Orders whose items have not been received, and anything marked shipped, are not listed.' }));
        var bar = el('div', { class: 'ic-search' }, [
            el('input', { id: 'icOrdFilter', type: 'text', placeholder: 'Filter by order #, customer or item…', value: state.ordersFilter, autocomplete: 'off',
                oninput: function (ev) { state.ordersFilter = ev.target.value; paintGroups(); } }),
            el('button', { class: 'ic-clear', type: 'button', text: '×', 'aria-label': 'Clear filter', style: state.ordersFilter ? '' : 'display:none', onclick: function () {
                state.ordersFilter = ''; var f = document.getElementById('icOrdFilter'); if (f) { f.value = ''; } paintGroups();
            } })
        ]);
        main.appendChild(bar);
        var head = el('div', { class: 'ic-listhead' }, [
            el('div', { class: 'ic-count', id: 'icOrdCount' }),
            el('div', { class: 'ic-actions' }, [
                el('button', { class: 'ic-btn is-ghost is-sm', type: 'button', text: 'Expand all', onclick: function () { setAllOpen(true); } }),
                el('button', { class: 'ic-btn is-ghost is-sm', type: 'button', text: 'Collapse all', onclick: function () { setAllOpen(false); } }),
                el('button', { class: 'ic-btn is-ghost is-sm', type: 'button', text: 'Reload', onclick: function () { state.allOrders = null; render(); } })
            ])
        ]);
        function setAllOpen(open) {
            state.ordersOpen = {};
            state.repsClosed = {};
            if (open && a.entries) { a.entries.forEach(function (o) { state.ordersOpen[o.ref] = true; }); }
            paintGroups();
        }
        main.appendChild(head);
        var box = el('div', { id: 'icOrdGroups', class: 'ic-list' });
        main.appendChild(box);
        main.appendChild(exportBar('orders'));
        function paintGroups() {
            box.innerHTML = '';
            var cnt = document.getElementById('icOrdCount');
            var clr = bar.querySelector('.ic-clear');
            if (clr) { clr.style.display = state.ordersFilter ? '' : 'none'; }
            if (a.loading) { box.appendChild(el('div', { class: 'ic-progress', text: 'Loading open orders…' })); if (cnt) { cnt.textContent = ''; } return; }
            if (a.error) { box.appendChild(el('div', { class: 'ic-error', text: a.error })); if (cnt) { cnt.textContent = ''; } return; }
            var q = state.ordersFilter.trim().toLowerCase();
            var groups = {}, order = [];
            a.entries.forEach(function (o) {
                var hay = (o.ref + ' ' + o.customer + ' ' + (o.rep || '') + ' ' + o.itemName).toLowerCase();
                if (q && hay.indexOf(q) === -1) { return; }
                if (!groups[o.ref]) { groups[o.ref] = { ref: o.ref, customer: o.customer, rep: o.rep || NO_REP, date: o.date, status: o.status, url: o.url, lines: [] }; order.push(o.ref); }
                groups[o.ref].lines.push(o);
            });
            // Sales rep first, their orders oldest first.
            var reps = {}, repNames = [];
            order.forEach(function (ref) {
                var r = groups[ref].rep;
                if (!reps[r]) { reps[r] = []; repNames.push(r); }
                reps[r].push(ref);
            });
            repNames.sort(function (x, y) { return repRank(x) - repRank(y) || (x < y ? -1 : x > y ? 1 : 0); });
            repNames.forEach(function (r) {
                reps[r].sort(function (x, y) {
                    return dateKey(groups[x].date) - dateKey(groups[y].date) || (x < y ? -1 : x > y ? 1 : 0);
                });
            });
            var lineCount = 0, ticked = 0;
            order.forEach(function (ref) { groups[ref].lines.forEach(function (o) { lineCount++; if (isTicked(o.item, o.ref)) { ticked++; } }); });
            if (cnt) {
                cnt.textContent = repNames.length + ' sales rep' + (repNames.length === 1 ? '' : 's') + ' · ' + order.length + ' open order' + (order.length === 1 ? '' : 's') + ' · ' + lineCount + ' line' + (lineCount === 1 ? '' : 's') + ' to count · ' + ticked + ' ticked' + (a.truncated ? ' · list capped' : '');
            }
            if (!order.length) {
                box.appendChild(el('div', { class: 'ic-empty', text: q ? 'Nothing matches "' + state.ordersFilter.trim() + '".' : 'No received-but-unshipped sales order lines at this location.' }));
                return;
            }

            function orderGroupEl(ref) {
                var g = groups[ref];
                var grp = el('div', { class: 'ic-ordgrp' });
                // Collapsed unless the user opened it; a filter opens what it matched.
                var open = !!q || !!state.ordersOpen[ref];
                var gTicked = 0, gToCount = 0;
                g.lines.forEach(function (o) { gToCount += Number(o.committed) || 0; if (isTicked(o.item, o.ref)) { gTicked++; } });
                var headEl = el('div', { class: 'ic-ordhead', role: 'button', 'aria-expanded': open ? 'true' : 'false', onclick: function (ev) {
                    if (ev.target && ev.target.tagName === 'A') { return; } // the order link itself
                    state.ordersOpen[ref] = !state.ordersOpen[ref];
                    paintGroups();
                } }, [
                    el('span', { class: 'ic-caret', text: open ? '▾' : '▸' }),
                    g.url ? el('a', { href: g.url, target: '_blank', rel: 'noopener', text: g.ref }) : el('b', { text: g.ref }),
                    g.customer ? el('span', { text: g.customer }) : null,
                    el('small', { text: (g.date ? g.date + ' · ' : '') + (g.status || '') }),
                    el('span', { class: 'ic-ordsum' + (gTicked === g.lines.length ? ' is-ticked' : ''), text: g.lines.length + ' line' + (g.lines.length === 1 ? '' : 's') + ' · ' + fmt(gToCount) + ' to count · ' + gTicked + ' ticked' })
                ]);
                grp.appendChild(headEl);
                if (!open) { return grp; }
                // "Select all" first: large team orders have dozens of lines.
                var tickable = g.lines.filter(function (o) { return o.committed > 0; });
                if (tickable.length > 1) {
                    var all = el('input', { type: 'checkbox', class: 'ic-ordall', 'aria-label': 'Select all lines on ' + g.ref });
                    all.checked = gTicked === tickable.length;
                    all.indeterminate = gTicked > 0 && gTicked < tickable.length;
                    all.addEventListener('change', function () {
                        var want = all.checked;
                        tickable.forEach(function (o) {
                            if (isTicked(o.item, o.ref) !== want) { tickOrder({ id: o.item, name: o.itemName }, o, want); }
                        });
                        paintGroups();
                    });
                    grp.appendChild(el('label', { class: 'ic-ordline ic-ordall-row' }, [
                        all,
                        el('span', { class: 'ic-info' }, [
                            el('div', { class: 'ic-name', text: 'Select all' }),
                            el('div', { class: 'ic-meta', text: tickable.length + ' lines · ' + fmt(gToCount) + ' to count' })
                        ])
                    ]));
                }
                g.lines.forEach(function (o) {
                    var item = { id: o.item, name: o.itemName };
                    var off = !(o.committed > 0);
                    var cb = el('input', { type: 'checkbox', 'aria-label': 'Counted: ' + o.ref + ' ' + o.itemName });
                    cb.checked = isTicked(o.item, o.ref);
                    cb.disabled = off;
                    cb.addEventListener('change', function () {
                        tickOrder(item, o, cb.checked);
                        paintGroups();
                    });
                    var detail = orderDetail(o);
                    var line = state.sheet[o.item];
                    grp.appendChild(el('label', { class: 'ic-ordline' + (off ? ' is-off' : '') }, [
                        cb,
                        el('span', { class: 'ic-info' }, [
                            el('div', { class: 'ic-name', text: o.itemName || ('item ' + o.item) }),
                            el('div', { class: 'ic-meta', text: detail + (line ? ' · on sheet: ' + fmt(line.count) : '') })
                        ])
                    ]));
                });
                return grp;
            }

            repNames.forEach(function (repName) {
                var refs = reps[repName];
                var repOpen = !state.repsClosed[repName];
                var rTicked = 0, rLines = 0, rToCount = 0;
                refs.forEach(function (ref) {
                    groups[ref].lines.forEach(function (o) {
                        rLines++; rToCount += Number(o.committed) || 0;
                        if (isTicked(o.item, o.ref)) { rTicked++; }
                    });
                });
                var sec = el('div', { class: 'ic-repgrp', 'data-rep': repName });
                sec.appendChild(el('div', { class: 'ic-rephead', role: 'button', 'aria-expanded': repOpen ? 'true' : 'false', onclick: function () {
                    if (state.repsClosed[repName]) { delete state.repsClosed[repName]; } else { state.repsClosed[repName] = true; }
                    paintGroups();
                } }, [
                    el('span', { class: 'ic-caret', text: repOpen ? '▾' : '▸' }),
                    el('span', { class: 'ic-repname', text: repName }),
                    el('span', { class: 'ic-repsum' + (rLines && rTicked === rLines ? ' is-ticked' : ''), text: refs.length + ' order' + (refs.length === 1 ? '' : 's') + ' · ' + rLines + ' line' + (rLines === 1 ? '' : 's') + ' · ' + fmt(rToCount) + ' to count · ' + rTicked + ' ticked' })
                ]));
                if (repOpen) {
                    var body = el('div', { class: 'ic-repbody' });
                    refs.forEach(function (ref) { body.appendChild(orderGroupEl(ref)); });
                    sec.appendChild(body);
                }
                box.appendChild(sec);
            });
        }
        paintGroups();
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
            el('button', { class: 'ic-btn', type: 'button', text: 'Count more items', onclick: function () { state.done = null; state.q = ''; state.loaded = false; state.results = []; state.view = 'search'; render(); } }),
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
                state.results = []; state.total = 0; state.page = 0; state.loaded = false; state.searchError = '';
                state.allOrders = null; ordersCache = {};
                render(); // the search view reloads the list for the new location
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
            el('button', { class: 'ic-tab' + (state.view === 'search' ? ' is-on' : ''), type: 'button', text: 'Count', onclick: function () { state.view = 'search'; state.done = null; render(); } }),
            el('button', { class: 'ic-tab' + (state.view === 'orders' ? ' is-on' : ''), type: 'button', text: 'Open orders', onclick: function () { state.view = 'orders'; state.done = null; render(); } }),
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
        // Attach before rendering the view: the list renderer finds its container
        // by id, which only works once the section is in the document.
        root.appendChild(main);
        if (state.view === 'done') { renderDoneView(main); }
        else if (state.view === 'sheet') { renderSheetView(main); }
        else if (state.view === 'orders') { renderOrdersView(main); }
        else { renderSearchView(main); }
        main.appendChild(el('div', { class: 'ic-footer', text: BOOT.canSubmit
            ? 'Counts are saved in this browser until you submit. Submitting creates an Inventory Adjustment in NetSuite as you.'
            : 'Counts are saved in this browser. Your role cannot post adjustments, so export the sheet when you are done.' }));
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
        state.instock = typeof prefs.instock === 'boolean' ? prefs.instock : BOOT.inStockDefault !== false;
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
