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
 *                           lines:[{ item, count, by, orders:[{ ref, qty }] }] }
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
        // Shared count. Every device's lines are kept in this custom record so
        // one administrator can review and post everyone's count at once
        // (docs/INVENTORY_COUNT.md, "Shared count" -- a one-time record type to
        // create). Set SHARED_RECORD to null for device-only sheets.
        SHARED_RECORD: 'customrecord_bsg_count_line',
        SHARED_FIELDS: {
            item: 'custrecord_bcl_item',            // List/Record: Item
            location: 'custrecord_bcl_location',    // List/Record: Location
            shelf: 'custrecord_bcl_shelf',          // Decimal: units physically counted
            orders: 'custrecord_bcl_orders',        // Long Text: ticked open orders, JSON
            onhand: 'custrecord_bcl_onhand',        // Decimal: on hand shown when counted
            counter: 'custrecord_bcl_counter',      // List/Record: Employee
            device: 'custrecord_bcl_device',        // Free-Form Text: the browser's id
            label: 'custrecord_bcl_device_label',   // Free-Form Text: "Warehouse tablet 1"
            posted: 'custrecord_bcl_posted',        // Check Box
            adjustment: 'custrecord_bcl_adjustment' // List/Record: Transaction
        },
        // Lines per Inventory Adjustment when posting the shared count: each line
        // is also marked posted in the same request, so this stays well inside
        // the request's governance.
        SHARED_BATCH: 100,
        // Lines a device may push in one sync request.
        SYNC_MAX_LINES: 50,
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
            } else if (action === 'sync') {
                out = isPost ? syncLines(body) : { ok: false, error: 'POST required.' };
            } else if (action === 'mine') {
                out = mineLines(request.parameters);
            } else if (action === 'session') {
                out = sessionLines(request.parameters);
            } else if (action === 'mark') {
                out = isPost ? markPosted(body) : { ok: false, error: 'POST required.' };
            } else if (action === 'discard') {
                out = isPost ? discardShared(body) : { ok: false, error: 'POST required.' };
            } else if (action === 'precheck') {
                out = isPost ? precheckAdjusted(body) : { ok: false, error: 'POST required.' };
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

    // "In stock" = quantity on hand, positive or negative: something is (or is
    // supposed to be) on a shelf. On order is not in stock -- nothing has been
    // received, so there is nothing to count. Available never exceeds on hand,
    // so its clause only matters where the account rejects the on-hand field.
    // Values are strings on purpose -- a bare numeric 0 is dropped as "no value"
    // by the filter parser, which silently turns the condition into "any".
    function stockFilter(locId) {
        var oh = qtyField(locId, 'onhand'), av = qtyField(locId, 'available');
        var parts = [];
        if (!isDead('item', 'filter', oh)) { parts.push([oh, 'greaterthan', '0']); parts.push([oh, 'lessthan', '0']); }
        if (!isDead('item', 'filter', av)) { parts.push([av, 'greaterthan', '0']); }
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
    // rows with quantity on hand (see stockFilter).
    // Matrix children in the order people say them. Alphabetically the sizes of
    // 1379806-Black run 2X-Large, 3X-Large, Large, Medium, Small, Small-Tall,
    // X-Large, X-Small; a counter walking a rack wants X-Small, Small,
    // Small-Tall, Medium, Large, X-Large, 2X-Large, 3X-Large. The list is
    // already alphabetical, so a parent's children sit together; only those
    // runs are reordered, by color and then size. Self-contained on purpose:
    // the same source is sent to the browser (see renderPage).
    function naturalItemOrder(items) {
        var SIZES = [
            ['youth x-small', 10], ['yxs', 10], ['youth small', 11], ['ys', 11], ['youth medium', 12], ['ym', 12], ['youth large', 13], ['yl', 13], ['youth x-large', 14], ['yxl', 14],
            ['xxs', 18], ['2xs', 18], ['2x-small', 18], ['xs', 20], ['x-small', 20], ['xs/s', 25], ['s', 30], ['small', 30], ['s/m', 35], ['sm', 35], ['small/medium', 35],
            ['m', 40], ['medium', 40], ['m/l', 45], ['medium/large', 45], ['l', 50], ['large', 50], ['l/xl', 55], ['lxl', 55], ['large/x-large', 55],
            ['xl', 60], ['x-large', 60], ['xl/2xl', 65], ['xxl', 70], ['2xl', 70], ['2x-large', 70], ['xxxl', 80], ['3xl', 80], ['3x-large', 80],
            ['4xl', 90], ['4x-large', 90], ['5xl', 100], ['5x-large', 100], ['6xl', 110], ['6x-large', 110],
            ['osfa', 200], ['osfm', 200], ['os', 200], ['o/s', 200], ['one size', 200], ['one size fits all', 200], ['one size fits most', 200]
        ];
        var MODS = { tall: 1, long: 1, 'x-tall': 2, short: -1, regular: 0, reg: 0 };
        function sizeRank(s) {
            var t = String(s || '').trim().toLowerCase(), mod = 0;
            if (!t) { return null; }
            var m = /^(.+?)[-\s\/]+(x-tall|tall|long|short|regular|reg)$/.exec(t);
            if (m) { mod = MODS[m[2]]; t = m[1]; }
            for (var i = 0; i < SIZES.length; i++) { if (SIZES[i][0] === t) { return SIZES[i][1] + mod; } }
            var n = /^(\d+(?:\.\d+)?)(?:\s*[xX\/]\s*(\d+(?:\.\d+)?))?$/.exec(t);   // shoes 9.5, waist 32x30, youth 7/8
            if (n) { return 1000 + parseFloat(n[1]) + (n[2] ? parseFloat(n[2]) / 1000 : 0) + mod; }
            return null;
        }
        // "1379806 : 1379806-Black-2X-Large" -> parent 1379806, color Black, rank 70
        function parts(name) {
            var full = String(name || ''), i = full.indexOf(' : ');
            if (i < 0) { return { parent: '', color: '', rank: null }; }
            var parent = full.slice(0, i), child = full.slice(i + 3), variant = child;
            if (child.toLowerCase().indexOf(parent.toLowerCase() + '-') === 0) { variant = child.slice(parent.length + 1); }
            var segs = variant.split('-'), best = null;
            // the longest tail that reads as a size is the size; what is left is the color
            for (var k = 1; k <= Math.min(3, segs.length); k++) {
                var r = sizeRank(segs.slice(segs.length - k).join('-'));
                if (r != null) { best = { rank: r, color: segs.slice(0, segs.length - k).join('-') }; }
            }
            return best ? { parent: parent, color: best.color, rank: best.rank } : { parent: parent, color: variant, rank: null };
        }
        var out = items.slice(), i = 0;
        while (i < out.length) {
            var p = parts(out[i].name), j = i + 1;
            if (!p.parent) { i++; continue; }
            while (j < out.length && parts(out[j].name).parent === p.parent) { j++; }
            if (j - i > 1) {
                var run = out.slice(i, j).map(function (it) { return { it: it, p: parts(it.name) }; });
                run.sort(function (a, b) {
                    var ca = a.p.color.toLowerCase(), cb = b.p.color.toLowerCase();
                    if (ca !== cb) { return ca < cb ? -1 : 1; }
                    var ra = a.p.rank == null ? 500 : a.p.rank, rb = b.p.rank == null ? 500 : b.p.rank;
                    if (ra !== rb) { return ra - rb; }
                    var na = String(a.it.name).toLowerCase(), nb = String(b.it.name).toLowerCase();
                    return na < nb ? -1 : na > nb ? 1 : 0;
                });
                for (var k2 = 0; k2 < run.length; k2++) { out[i + k2] = run[k2].it; }
            }
            i = j;
        }
        return out;
    }

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
    // A person's name (or a display time) as the page reports it: control
    // characters out, whitespace collapsed, capped so a memo cannot be flooded.
    function cleanName(v, max) {
        return String(v == null ? '' : v).replace(/[\x00-\x1f\x7f]/g, '').replace(/\s+/g, ' ').trim().slice(0, max || 60);
    }

    // A line is { item, count } plus, optionally, who keyed it (by) and the open
    // orders the counter ticked to account for units that are pulled, packed, at
    // the decorator or waiting for pickup: orders:[{ ref, qty }]. Both go on the
    // line memo, so a doubtful count can be traced back to a person.
    function normalizeLines(raw) {
        var seen = {}, out = [];
        (Array.isArray(raw) ? raw : []).forEach(function (l) {
            if (!l) { return; }
            var id = posInt(l.item);
            var count = parseFloat(l.count);
            if (!id || !isFinite(count) || count < 0) { return; }
            var orders = cleanOrders(l.orders);
            // seen: the shared count lines behind this total as the administrator
            // saw them (id, shelf, ticked orders); they are marked posted afterwards,
            // and a line that has changed since makes the submit stale.
            var seenLines = (Array.isArray(l.seen) ? l.seen : []).slice(0, 50).map(function (x) {
                return { id: posInt(x && x.id), shelf: round4(parseFloat(x && x.shelf) || 0), orders: cleanOrders(x && x.orders) };
            }).filter(function (x) { return !!x.id; });
            var ids = seenLines.length ? seenLines.map(function (x) { return String(x.id); }) : idList(l.ids, 50);
            var line = { item: String(id), count: round4(count), orders: orders, by: cleanName(l.by, 160), ids: ids, seen: seenLines };
            if (seen[id]) { seen[id].count = line.count; seen[id].orders = orders; seen[id].by = line.by; seen[id].ids = ids; seen[id].seen = seenLines; return; } // same item twice: last wins
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
        return memo + '; on hand ' + l.onhand + (l.by ? '; counted by ' + l.by : '');
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

        // Shared count: every open line for these items must be in this submit,
        // holding the value the administrator saw. A line that is missing, or
        // has changed since (a counter re-keyed it), makes the total stale --
        // reload rather than post it.
        var shared = lines.some(function (l) { return l.ids.length > 0; });
        if (shared) {
            requireShared();
            var carried = {};
            lines.forEach(function (l) {
                l.ids.forEach(function (id) { carried[id] = true; });
                l.seen.forEach(function (x) { carried[String(x.id)] = x; });
            });
            var SF = CONFIG.SHARED_FIELDS, stale = {};
            sharedRows(sharedLocFilter([[SF.posted, 'is', 'F'], 'and', [SF.item, 'anyof', lines.map(function (l) { return l.item; })]], locId))
                .forEach(function (r) {
                    var sv = carried[r.id];
                    var changed = sv && typeof sv === 'object' && (round4(r.shelf) !== sv.shelf || JSON.stringify(r.orders) !== JSON.stringify(sv.orders));
                    if (!sv || changed) { stale[r.item] = r.name || ('item ' + r.item); }
                });
            var staleItems = Object.keys(stale);
            if (staleItems.length) {
                return { ok: false, stale: staleItems.map(function (i) { return { item: i, name: stale[i] }; }),
                    error: 'New counts arrived for ' + staleItems.length + ' item' + (staleItems.length === 1 ? '' : 's') + ' after the sheet was loaded ('
                        + staleItems.slice(0, 3).map(function (i) { return stale[i]; }).join(', ') + (staleItems.length > 3 ? ', …' : '') + '). The sheet has been reloaded -- check them and submit again.' };
            }
        }

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
            candidates.push({ item: l.item, name: it.name, count: l.count, onhand: it.onhand, orders: l.orders, by: l.by, ids: l.ids });
        });
        // Shared lines behind the counts that went through (posted or already
        // matching) are marked posted so they leave everyone's open sheet;
        // blocked ones stay open, with the reason on the page.
        function finish(out) {
            if (shared) {
                var toMark = [];
                out.applied.concat(out.skipped).forEach(function (l) { toMark = toMark.concat(l.ids || []); });
                var m = markLines(toMark, out.adjustment ? posInt(out.adjustment.id) : null);
                out.marked = m.marked.length;
                out.unmarked = m.unmarked;
            }
            return out;
        }
        if (!candidates.length) {
            return finish({ ok: true, adjustment: null, applied: [], skipped: skipped, blocked: blocked, message: 'Nothing on this sheet could be adjusted.' });
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
            return finish({ ok: true, adjustment: null, applied: [], skipped: skipped, blocked: blocked, message: 'Every count already matches on-hand -- no adjustment needed.' });
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
        return finish({
            ok: true,
            adjustment: { id: String(id), tranid: String(tranid), url: recUrl },
            applied: applied, skipped: skipped, blocked: blocked
        });
    }

    // ---------------------------------------------------------------- shared --
    // The shared count. Every device's lines are kept in a custom record so
    // one administrator can review and post everyone's count at once. A line
    // is one (item, counter, device) at a location and stays open until it is
    // posted; two counters on the same item are two lines, which the
    // administrator's sheet adds up. Without the record type the page runs as
    // it always did: one sheet per device, submitted from that device.

    function sharedOn() { return !!CONFIG.SHARED_RECORD; }

    // Can the record be searched with every field we need? Anything missing
    // comes back by name so the setup notice can say exactly what to add.
    function sharedStatus() {
        if (!sharedOn()) { return { ready: false, off: true }; }
        var F = CONFIG.SHARED_FIELDS;
        var cols = Object.keys(F).map(function (k) { return F[k]; });
        try {
            search.create({ type: CONFIG.SHARED_RECORD, filters: [[F.posted, 'is', 'F']], columns: cols }).run().getRange({ start: 0, end: 1 });
            return { ready: true };
        } catch (e) {
            var rf = rejectedField(e);
            return { ready: false, missing: rf ? rf.name : '', error: userErr(e) };
        }
    }
    function requireShared() {
        var st = sharedStatus();
        if (!st.ready) { throw new Error(st.off ? 'The shared count is switched off.' : 'The shared count is not set up: ' + (st.error || 'record type missing')); }
    }

    function sharedUser() {
        try { return posInt(runtime.getCurrentUser().id); } catch (e) { return null; }
    }
    // A browser's id for its own lines: letters and digits, long enough not to
    // collide with another tablet's.
    function cleanDevice(v) {
        var s = String(v == null ? '' : v).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);
        return s.length >= 8 ? s : '';
    }
    function cleanOrders(raw) {
        var out = [];
        (Array.isArray(raw) ? raw : []).slice(0, 30).forEach(function (o) {
            var ref = cleanRef(o && o.ref), qty = parseFloat(o && o.qty);
            if (ref && isFinite(qty) && qty > 0) { out.push({ ref: ref, qty: round4(qty) }); }
        });
        return out;
    }
    function parseOrdersJson(s) {
        try { return cleanOrders(JSON.parse(String(s || '[]'))); } catch (e) { return []; }
    }
    function sharedLocFilter(filters, locId) {
        if (locId) { filters.push('and'); filters.push([CONFIG.SHARED_FIELDS.location, 'anyof', [String(locId)]]); }
        return filters;
    }
    function remainingUsage() {
        try { return runtime.getCurrentScript().getRemainingUsage(); } catch (e) { return 1000; }
    }
    // Who this device is and where it is counting, or why the request cannot go on.
    function deviceGuard(body) {
        var uid = sharedUser();
        if (!uid) { return { error: 'No signed-in user.' }; }
        var device = cleanDevice(body.device);
        if (!device) { return { error: 'This device has no id yet; reload the page.' }; }
        var locId = multiLocation() ? posInt(body.loc) : null;
        if (multiLocation() && !locId) { return { error: 'Pick a location first.' }; }
        return { uid: uid, device: device, locId: locId };
    }

    // Count lines matching the filters, as plain objects. Paged in 1,000s up to
    // 5,000 -- more than a full physical count of BSG's catalog.
    function sharedRows(filters) {
        var F = CONFIG.SHARED_FIELDS;
        var cols = [F.item, F.shelf, F.orders, F.onhand, F.counter, F.device, F.label, F.posted, F.adjustment, 'lastmodified'];
        var rs = search.create({ type: CONFIG.SHARED_RECORD, filters: filters, columns: cols }).run();
        var rows = [], start = 0, MAX = 5000;
        function num(v) { return v === '' || v == null ? null : parseFloat(v); }
        while (start < MAX) {
            var chunk = rs.getRange({ start: start, end: Math.min(start + 1000, MAX) });
            chunk.forEach(function (r) {
                var posted = r.getValue(F.posted);
                rows.push({
                    id: String(r.id),
                    item: String(r.getValue(F.item) || ''),
                    name: String(r.getText(F.item) || ''),
                    shelf: num(r.getValue(F.shelf)) || 0,
                    orders: parseOrdersJson(r.getValue(F.orders)),
                    onhand: num(r.getValue(F.onhand)),
                    counter: String(r.getValue(F.counter) || ''),
                    counterName: String(r.getText(F.counter) || ''),
                    device: String(r.getValue(F.device) || ''),
                    label: String(r.getValue(F.label) || ''),
                    posted: posted === true || posted === 'T',
                    adjustment: String(r.getValue(F.adjustment) || ''),
                    at: String(r.getValue('lastmodified') || '')
                });
            });
            if (chunk.length < 1000) { break; }
            start += 1000;
        }
        return rows;
    }

    // A device pushes the lines it changed and the items it removed. One
    // search finds what it already has open, then each line is updated or
    // created; the device's label rides along so the administrator can tell
    // "Warehouse tablet 1" from "Retail iPad".
    function syncLines(body) {
        requireShared();
        var g = deviceGuard(body);
        if (g.error) { return { ok: false, error: g.error }; }
        var F = CONFIG.SHARED_FIELDS, label = cleanName(body.label, 40);
        var ups = (Array.isArray(body.upserts) ? body.upserts : []).slice(0, CONFIG.SYNC_MAX_LINES);
        var dels = idList(body.deletes, CONFIG.SYNC_MAX_LINES);
        var items = {};
        ups.forEach(function (u) { var id = posInt(u && u.item); if (id) { items[id] = true; } });
        dels.forEach(function (d) { items[d] = true; });
        var itemIds = Object.keys(items);
        if (!itemIds.length) { return { ok: true, synced: 0, deleted: 0, errors: [] }; }
        var existing = {};
        sharedRows(sharedLocFilter([[F.counter, 'anyof', [String(g.uid)]], 'and', [F.device, 'is', g.device], 'and', [F.posted, 'is', 'F'], 'and', [F.item, 'anyof', itemIds]], g.locId))
            .forEach(function (r) { existing[r.item] = r.id; });
        var synced = 0, deleted = 0, errors = [];
        ups.forEach(function (u) {
            var id = posInt(u && u.item);
            if (!id) { return; }
            var shelf = parseFloat(u.shelf);
            if (!isFinite(shelf) || shelf < 0) { errors.push({ item: String(id), error: 'Bad count.' }); return; }
            var vals = {};
            vals[F.shelf] = round4(shelf);
            vals[F.orders] = JSON.stringify(cleanOrders(u.orders));
            vals[F.label] = label;
            var oh = parseFloat(u.onhand);
            if (isFinite(oh)) { vals[F.onhand] = oh; }
            try {
                if (existing[id]) {
                    record.submitFields({ type: CONFIG.SHARED_RECORD, id: existing[id], values: vals });
                } else {
                    var rec = record.create({ type: CONFIG.SHARED_RECORD });
                    // If the record type kept its Name field, give it one rather
                    // than fail the save; without a Name field this is a no-op.
                    trySet(rec, 'name', 'Item ' + id + ' · ' + g.device.slice(0, 8));
                    rec.setValue({ fieldId: F.item, value: id });
                    if (g.locId) { rec.setValue({ fieldId: F.location, value: g.locId }); }
                    rec.setValue({ fieldId: F.counter, value: g.uid });
                    rec.setValue({ fieldId: F.device, value: g.device });
                    rec.setValue({ fieldId: F.posted, value: false });
                    Object.keys(vals).forEach(function (k) { rec.setValue({ fieldId: k, value: vals[k] }); });
                    existing[id] = String(rec.save());
                }
                synced++;
            } catch (e) { errors.push({ item: String(id), error: userErr(e) }); }
        });
        dels.forEach(function (d) {
            if (!existing[d]) { return; }
            try { record.delete({ type: CONFIG.SHARED_RECORD, id: existing[d] }); deleted++; }
            catch (e) { errors.push({ item: String(d), error: userErr(e) }); }
        });
        return { ok: true, synced: synced, deleted: deleted, errors: errors };
    }

    // This device's own open lines, so a reloaded or wiped browser gets them back.
    function mineLines(params) {
        requireShared();
        var g = deviceGuard(params);
        if (g.error) { return { ok: false, error: g.error }; }
        var F = CONFIG.SHARED_FIELDS;
        var rows = sharedRows(sharedLocFilter([[F.counter, 'anyof', [String(g.uid)]], 'and', [F.device, 'is', g.device], 'and', [F.posted, 'is', 'F']], g.locId));
        return { ok: true, lines: rows.map(function (r) { return { id: r.id, item: r.item, name: r.name, shelf: r.shelf, orders: r.orders, onhand: r.onhand, at: r.at }; }) };
    }

    // Every open line at the location, for the administrator's merged sheet.
    function sessionLines(params) {
        requireShared();
        if (!canSubmit()) { return { ok: false, error: 'Your role cannot review the shared count.' }; }
        var locId = multiLocation() ? posInt(params.loc) : null;
        if (multiLocation() && !locId) { return { ok: false, error: 'Pick a location first.' }; }
        var F = CONFIG.SHARED_FIELDS;
        var rows = sharedRows(sharedLocFilter([[F.posted, 'is', 'F']], locId));
        return { ok: true, lines: rows, truncated: rows.length >= 5000 };
    }

    // Marks lines posted, with the adjustment that took them. Stops short of the
    // governance limit and hands back what is left for the page to retry.
    function markLines(ids, adjustmentId) {
        var F = CONFIG.SHARED_FIELDS, marked = [], unmarked = [];
        var vals = {};
        vals[F.posted] = true;
        if (adjustmentId) { vals[F.adjustment] = adjustmentId; }
        ids.forEach(function (id) {
            if (unmarked.length || remainingUsage() < 40) { unmarked.push(id); return; }
            try { record.submitFields({ type: CONFIG.SHARED_RECORD, id: id, values: vals }); marked.push(id); }
            catch (e) { unmarked.push(id); log.error({ title: 'invcount: could not mark count line ' + id + ' posted', details: safeErr(e) }); }
        });
        return { marked: marked, unmarked: unmarked };
    }
    function markPosted(body) {
        requireShared();
        if (!canSubmit()) { return { ok: false, error: 'Your role cannot post the shared count.' }; }
        var r = markLines(idList(body.ids, 200), posInt(body.adjustment) || null);
        return { ok: true, marked: r.marked.length, unmarked: r.unmarked };
    }

    // Removes open lines: this user's own (scope "mine", every device of theirs)
    // or, for an administrator, everyone's at the location. 100 per call; the
    // page keeps calling while "remaining" is above zero.
    function discardShared(body) {
        requireShared();
        var F = CONFIG.SHARED_FIELDS;
        var locId = multiLocation() ? posInt(body.loc) : null;
        if (multiLocation() && !locId) { return { ok: false, error: 'Pick a location first.' }; }
        var all = body.scope === 'all';
        if (all && !canSubmit()) { return { ok: false, error: 'Your role cannot discard other people\'s counts.' }; }
        var filters = [[F.posted, 'is', 'F']];
        if (!all) {
            var uid = sharedUser();
            if (!uid) { return { ok: false, error: 'No signed-in user.' }; }
            filters.push('and');
            filters.push([F.counter, 'anyof', [String(uid)]]);
        }
        var rows = sharedRows(sharedLocFilter(filters, locId));
        var deleted = 0;
        rows.slice(0, 100).forEach(function (r) {
            if (remainingUsage() < 40) { return; }
            try { record.delete({ type: CONFIG.SHARED_RECORD, id: r.id }); deleted++; } catch (e) { /* stays in remaining */ }
        });
        return { ok: true, deleted: deleted, remaining: Math.max(0, rows.length - deleted) };
    }

    // Items a count already adjusted today, so a second submit is a decision
    // rather than an accident. Best effort: if the search is refused the page
    // just does not get the warning.
    function precheckAdjusted(body) {
        var ids = idList(body.items, 500);
        var locId = multiLocation() ? posInt(body.loc) : null;
        if (!ids.length) { return { ok: true, adjusted: [] }; }
        var out = [];
        try {
            var filters = [['type', 'anyof', ['InvAdjst']], 'and', ['mainline', 'is', 'F'], 'and', ['trandate', 'on', 'today'], 'and', ['item', 'anyof', ids], 'and', ['memo', 'startswith', 'Counted']];
            if (locId) { filters.push('and'); filters.push(['location', 'anyof', [String(locId)]]); }
            search.create({ type: search.Type.TRANSACTION, filters: filters, columns: ['tranid', 'item', 'quantity', 'memo', 'internalid'] }).run().each(function (r) {
                var tid = r.getValue('internalid'), link = '';
                try { link = tid ? url.resolveRecord({ recordType: 'inventoryadjustment', recordId: tid, isEditMode: false }) : ''; } catch (e) { /* cosmetic */ }
                out.push({ item: String(r.getValue('item')), tranid: String(r.getValue('tranid') || ''), qty: parseFloat(r.getValue('quantity')) || 0, memo: String(r.getValue('memo') || ''), url: link });
                return out.length < 500;
            });
        } catch (e) {
            log.audit({ title: 'invcount: already-adjusted check skipped', details: userErr(e) });
            return { ok: true, adjusted: [], skipped: userErr(e) };
        }
        return { ok: true, adjusted: out };
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
        rows = naturalItemOrder(rows);
        return {
            title: 'Inventory count list' + (where ? ' - ' + where : ''),
            subtitle: (inStock && stockApplied ? 'Items in stock (quantity on hand)' : 'All items') + (words.length ? ' matching "' + words.join(' ') + '"' : '') + ' · ' + rows.length + ' items · blank Count column to fill in',
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
        { key: 'count', label: 'Count', num: true }, { key: 'delta', label: 'Adjust by', num: true }, { key: 'orders', label: 'On open orders' },
        { key: 'by', label: 'Counted by' }, { key: 'when', label: 'When' }
    ];

    function exportSheet(where, payload) {
        var rows = (Array.isArray(payload.lines) ? payload.lines : []).slice(0, CONFIG.EXPORT_MAX_ROWS).map(function (l) {
            var onhand = parseFloat(l.onhand) || 0, count = parseFloat(l.count) || 0;
            return {
                name: String(l.name || ''), display: String(l.display || l.desc || ''), vendor: String(l.vendor || ''), onhand: onhand, count: count,
                delta: round4(count - onhand),
                orders: (Array.isArray(l.orders) ? l.orders : []).map(function (o) { return cleanRef(o && o.ref) + (o && o.qty ? ' (' + o.qty + ')' : ''); }).filter(Boolean).join(', '),
                by: cleanName(l.by), when: cleanName(l.when)
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
        try { boot.user = runtime.getCurrentUser().name || ''; boot.userId = String(runtime.getCurrentUser().id || ''); } catch (e) { /* ignore */ }
        // Shared count: ready, not set up (with what is missing), or switched off.
        try { boot.shared = sharedStatus(); } catch (e3) { boot.shared = { ready: false, error: userErr(e3) }; }
        boot.sharedBatch = CONFIG.SHARED_BATCH;
        boot.sharedSetup = { record: CONFIG.SHARED_RECORD, fields: CONFIG.SHARED_FIELDS };
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
            '<script>var naturalItemOrder = ' + naturalItemOrder.toString() + ';</script>' +
            '<script>' + CLIENT_JS + '</script>' +
            '</body></html>'
        );
    }

    // ------------------------------------------------------------------- css --

    var PAGE_CSS = String.raw`
/* Modernist: Archivo throughout, BSG red as the one accent, flat surfaces,
   zero corner radius, 2px rules for structure and 1px between rows. Counts
   line up in real columns -- a count sheet is a table, so it reads as one. */
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&display=swap');
:root{
  --red:#ec3013;--red-600:#dd2b0f;--red-700:#ae1800;--red-tint:#ffe0d9;--red-100:#fff2ef;
  --ink:#201e1d;--ink-2:rgba(32,30,29,.75);--muted:rgba(32,30,29,.55);--faint:rgba(32,30,29,.4);
  --line:rgba(32,30,29,.4);--line-2:rgba(32,30,29,.17);--wash:rgba(32,30,29,.05);
  --bg:#f3f2f2;--surface:#eae9e9;
  --ok:#1a6b3f;--ok-bg:#e2eee8;--ok-line:#8ab8a0;
  --warn:#7c4a00;--warn-bg:#f6ecd8;--warn-line:#c9a35c;
  --bad:#ae1800;--bad-bg:#ffe0d9;
  --font:"Archivo","Helvetica Neue",Helvetica,Arial,system-ui,sans-serif;
  --wide:1320px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--font);-webkit-text-size-adjust:100%;-webkit-font-smoothing:antialiased}
button,input,select{font:inherit;color:inherit}
button{cursor:pointer}
b,strong{font-weight:600}
:focus{outline:none}
:focus-visible{outline:2px solid var(--red);outline-offset:2px}
input:focus-visible,select:focus-visible,textarea:focus-visible{outline-offset:0}
::selection{background:rgba(236,48,19,.28)}
.ic-loading{padding:64px 20px;text-align:center;color:var(--muted)}

/* ------------------------------------------------------------- masthead -- */
/* Flat, not a red bar: red is spent on the active tab and the one primary
   action, which is what makes it mean something. */
.ic-head{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:2px solid var(--line)}
.ic-head-row{display:flex;align-items:center;gap:16px;flex-wrap:wrap;max-width:var(--wide);margin:0 auto;padding-left:24px;padding-right:24px;padding-bottom:12px;padding-top:12px;padding-top:calc(12px + env(safe-area-inset-top,0px))}
.ic-title{font-weight:800;font-size:19px;letter-spacing:-.015em;flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ic-user{font-size:13px;color:var(--muted);white-space:nowrap}
.ic-loc{display:flex;align-items:center;gap:8px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.ic-loc select{background-color:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:0;padding:6px 30px 6px 10px;max-width:60vw;min-height:36px;font-size:14px;letter-spacing:normal;text-transform:none;-webkit-appearance:none;appearance:none;background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath fill='%23201e1d' d='M1 1l5 5 5-5'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center}
.ic-loc select:hover{border-color:var(--ink)}

/* Tabs are one segmented control -- a single hard-edged block, no floating. */
.ic-tabs{max-width:var(--wide);margin:0 auto;padding:18px 24px 0}
.ic-tabseg{display:inline-flex;max-width:100%;border:1px solid var(--line)}
.ic-tab{display:inline-flex;align-items:center;gap:8px;background:none;border:0;border-radius:0;color:var(--ink);font-family:var(--font);font-weight:600;font-size:16px;padding:12px 24px;min-height:50px;white-space:nowrap}
.ic-tab+.ic-tab{border-left:1px solid var(--line)}
.ic-tab:hover{background:var(--wash)}
.ic-tab.is-on{background:var(--red);color:#fff}
.ic-tab.is-on:hover{background:var(--red-600)}
.ic-badge{display:inline-flex;align-items:center;justify-content:center;min-width:22px;padding:2px 8px;background:var(--surface);color:var(--ink);font-size:11px;font-weight:700;letter-spacing:.02em;font-variant-numeric:tabular-nums;line-height:1.5}
.ic-tab.is-on .ic-badge{background:#fff;color:var(--red-700)}

.ic-main{padding:24px;max-width:var(--wide);margin:0 auto}

/* --------------------------------------------------------------- notices -- */
.ic-warn{background:var(--warn-bg);border-left:4px solid var(--warn-line);color:var(--warn);padding:11px 14px;margin-bottom:14px;font-size:13.5px;line-height:1.5}
.ic-error{background:var(--bad-bg);border-left:4px solid var(--red);color:var(--bad);padding:11px 14px;margin:12px 0;font-size:13.5px;word-break:break-word}
.ic-empty{padding:48px 16px;text-align:center;color:var(--muted);font-size:14px}
.ic-progress{padding:18px;text-align:center;color:var(--muted);font-size:14px}

/* ---------------------------------------------------------------- search -- */
.ic-search{position:relative;margin-bottom:4px}
.ic-search input{width:100%;padding:11px 46px 11px 12px;border:1px solid var(--line);border-radius:0;background:var(--surface);font-size:15px;min-height:46px;caret-color:var(--red)}
.ic-search input::placeholder{color:var(--muted)}
.ic-search input:focus{border-color:var(--red)}
.ic-search .ic-clear{position:absolute;right:4px;top:50%;transform:translateY(-50%);background:transparent;border:0;border-radius:0;font-size:20px;color:var(--muted);width:38px;height:38px;line-height:1}
.ic-search .ic-clear:hover{color:var(--red);background:var(--wash)}
.ic-hint{font-size:13px;color:var(--muted);margin:12px 0 16px;line-height:1.55;max-width:96ch}

.ic-listhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 14px;flex-wrap:wrap}
.ic-count{font-size:13px;color:var(--muted);min-height:20px;font-variant-numeric:tabular-nums}
.ic-seg{display:inline-flex;border:1px solid var(--line)}
.ic-segbtn{border:0;border-radius:0;background:transparent;padding:7px 16px;min-height:38px;font-weight:600;font-size:13px;color:var(--ink)}
.ic-segbtn+.ic-segbtn{border-left:1px solid var(--line)}
.ic-segbtn:hover{background:var(--wash)}
.ic-segbtn.is-on{background:var(--red);color:#fff}
.ic-segbtn.is-on:hover{background:var(--red-600)}

/* ----------------------------------------------------------------- table -- */
/* The header strip and every row share one grid template, so the numbers sit
   in true columns without a <table> swallowing the expandable orders panel. */
.ic-table{margin:0}
.ic-thead,.ic-row{display:grid;align-items:center;column-gap:16px}
.ic-table.is-items .ic-thead,.ic-table.is-items .ic-row{grid-template-columns:minmax(200px,3fr) minmax(110px,1.2fr) 74px 74px 92px 84px 296px}
.ic-table.is-sheet .ic-thead,.ic-table.is-sheet .ic-row{grid-template-columns:minmax(200px,3fr) 74px 74px 92px 84px 156px 74px 44px}
.ic-thead{padding:0 8px 8px;border-bottom:2px solid var(--line);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:400}
.ic-row{padding:11px 8px;border-bottom:1px solid var(--line);row-gap:6px}
.ic-row:hover{background:var(--wash)}
.ic-row.is-onsheet{background:var(--ok-bg);box-shadow:inset 3px 0 0 var(--ok)}
.ic-row.is-onsheet:hover{background:#d9e8e0}
.ic-row.is-blocked{opacity:.62}
.ic-row.has-error{background:var(--bad-bg);box-shadow:inset 3px 0 0 var(--red)}
.ic-num{text-align:right;font-variant-numeric:tabular-nums;font-size:14px;min-width:0}
.ic-th-num{text-align:right}
.ic-vendor{font-size:13px;color:var(--muted);min-width:0;word-break:break-word}
.ic-rowpanel{grid-column:1/-1;min-width:0}
.ic-rowpanel:empty{display:none}

.ic-info{min-width:0}
.ic-name{font-weight:600;font-size:14px;letter-spacing:-.005em;word-break:break-word}
.ic-name a{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
.ic-name a:hover{color:var(--red);border-bottom-color:currentColor}
.ic-desc{font-size:13px;color:var(--muted);word-break:break-word;margin-top:2px}
.ic-meta{font-size:12.5px;color:var(--muted);margin-top:4px;word-break:break-word;font-variant-numeric:tabular-nums}
.ic-meta b{color:var(--ink);font-weight:600}
.ic-flag{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:var(--warn);background:var(--warn-bg);padding:2px 8px;margin-top:6px}
.ic-onsheet{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:var(--ok);background:var(--ok-bg);border:1px solid var(--ok-line);padding:2px 8px;margin-top:6px}

/* --------------------------------------------------------------- controls -- */
.ic-ctl{display:flex;align-items:center;gap:8px;justify-content:flex-end;flex-wrap:wrap}
.ic-ctl .ic-btn{min-width:88px}
.ic-ok{display:inline-flex;align-items:center;gap:7px;padding:0 11px;min-height:44px;border:1px solid var(--line);background:transparent;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);cursor:pointer;user-select:none;white-space:nowrap}
.ic-ok:hover{background:var(--wash);color:var(--ink)}
.ic-ok input{width:17px;height:17px;margin:0;accent-color:var(--ok);cursor:pointer}
.ic-ok.is-on,.ic-ok:has(input:checked){border-color:var(--ok);background:var(--ok-bg);color:var(--ok)}
.ic-ok.is-on:hover,.ic-ok:has(input:checked):hover{background:#d9e8e0}
.ic-qty{width:74px;min-height:44px;padding:8px;border:1px solid var(--line);border-radius:0;font-size:17px;font-weight:600;text-align:center;background:var(--surface);font-variant-numeric:tabular-nums;caret-color:var(--red)}
.ic-qty::placeholder{font-weight:400;font-size:14px;color:var(--muted)}
.ic-qty:focus{border-color:var(--red)}
.ic-qty.is-bad{border-color:var(--red);background:var(--bad-bg)}
.ic-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:44px;padding:0 18px;border-radius:0;border:1px solid transparent;background:var(--red);color:#fff;font-weight:800;font-size:14px;line-height:1.2;white-space:nowrap}
.ic-btn:hover{background:var(--red-600)}
.ic-btn:active{background:var(--red-700)}
.ic-btn:disabled{opacity:.45;cursor:default;background:var(--red)}
.ic-btn.is-ghost{background:transparent;color:var(--ink);border-color:var(--line)}
.ic-btn.is-ghost:hover{background:var(--wash)}
.ic-btn.is-ok{background:var(--ok);border-color:var(--ok)}
.ic-btn.is-ok:hover{background:#155634}
.ic-btn.is-sm{min-height:36px;padding:0 13px;font-size:12.5px}
.ic-step{width:34px;height:42px;border-radius:0;border:1px solid var(--line);background:transparent;font-size:18px;font-weight:500;line-height:1;color:var(--ink)}
.ic-step:hover{background:var(--wash)}
.ic-x{width:36px;height:36px;border-radius:0;border:0;background:transparent;color:var(--red);font-size:18px;line-height:1}
.ic-x:hover{background:var(--red-tint)}
.ic-more{margin:20px 0;text-align:center}
.ic-link{background:none;border:0;border-radius:0;color:var(--red-700);font-weight:600;padding:4px 0;font-size:12px;text-decoration:underline;text-underline-offset:3px}
.ic-link:hover{color:var(--red)}
.ic-commit-link{background:none;border:0;padding:0;font:inherit;font-weight:700;color:var(--red-700);text-decoration:underline;text-underline-offset:3px;font-variant-numeric:tabular-nums}
.ic-commit-link:hover{color:var(--red)}

/* ----------------------------------------------------------------- sheet -- */
.ic-sum{display:flex;gap:32px;flex-wrap:wrap;padding:0 4px 16px;border-bottom:2px solid var(--line);margin-bottom:20px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.ic-sum div{flex:0 0 auto;min-width:76px}
.ic-sum div:last-child{margin-left:auto;text-align:right}
.ic-sum b{font-size:24px;display:block;color:var(--ink);font-weight:800;letter-spacing:-.02em;margin-bottom:1px;text-transform:none;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.ic-sum b#icTotNeg{color:var(--red-700)}
.ic-deltacell{text-align:right;min-width:0}
.ic-xcell{text-align:right;min-width:0}
.ic-delta{display:inline-block;font-weight:700;min-width:52px;text-align:center;padding:4px 8px;font-size:13px;font-variant-numeric:tabular-nums}
.ic-delta.is-pos{background:var(--ok-bg);color:var(--ok)}
.ic-delta.is-neg{background:var(--red-tint);color:var(--red-700)}
.ic-delta.is-zero{background:var(--surface);color:var(--muted)}
.ic-line-ctl{display:flex;align-items:center;gap:6px}
.ic-line-top{display:flex;align-items:center;gap:6px}
.ic-commit-warn{font-size:12.5px;color:var(--warn);background:var(--warn-bg);border-left:4px solid var(--warn-line);padding:8px 12px;margin-top:8px;line-height:1.45}
.ic-incl{font-size:12.5px;color:var(--ok);background:var(--ok-bg);border-left:4px solid var(--ok);padding:8px 12px;margin-top:8px}
.ic-form{background:var(--surface);border:1px solid var(--line);padding:18px;margin-top:22px;display:flex;flex-direction:column;gap:16px}
/* Sentence case, not uppercase: these labels carry a parenthetical
   ("Memo (goes on the Inventory Adjustment)") that shouts in all caps. */
.ic-form label{font-size:12px;color:var(--muted);display:block;margin-bottom:5px}
.ic-form input,.ic-form select{width:100%;min-height:42px;padding:9px 11px;border:1px solid var(--line);border-radius:0;background:var(--bg);font-size:14px}
.ic-form input:focus,.ic-form select:focus{border-color:var(--red)}
.ic-actions{display:flex;gap:12px;flex-wrap:wrap}
.ic-actions .ic-btn{flex:1 1 auto;min-height:48px;font-size:15px}
/* A toolbar of small buttons (Expand all / Collapse all / Reload) keeps its
   own size inside an .ic-actions row rather than stretching like a CTA. */
.ic-actions .ic-btn.is-sm{flex:0 0 auto;min-height:36px;font-size:12.5px}
.ic-confirm{background:var(--bg);border-left:4px solid var(--red);padding:16px}
.ic-confirm p{margin:0 0 10px;font-size:14px;line-height:1.55;color:var(--ink-2)}
.ic-confirm p b{color:var(--ink);font-weight:800}
.ic-done{background:var(--surface);border:1px solid var(--line);padding:22px;margin-bottom:16px}
.ic-done h2{margin:0 0 10px;font-size:22px;font-weight:800;letter-spacing:-.015em;color:var(--ink)}
.ic-done a{color:var(--red-700);font-weight:600;font-size:16px}
.ic-done ul{margin:10px 0 0 18px;padding:0;font-size:13.5px;color:var(--ink-2);line-height:1.7}
.ic-export{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:var(--muted);margin:20px 0 0;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.ic-export .ic-btn{min-height:34px;padding:0 14px;font-size:12.5px;text-transform:none;letter-spacing:normal}

/* ----------------------------------------------------------- open orders -- */
.ic-list{display:block}
.ic-repgrp{border-bottom:2px solid var(--line)}
.ic-rephead{display:flex;align-items:center;gap:6px 12px;flex-wrap:wrap;cursor:pointer;user-select:none;padding:13px 6px}
.ic-rephead:hover{background:var(--wash)}
.ic-repname{font-weight:800;font-size:16px;letter-spacing:-.01em}
.ic-repsum{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap;font-variant-numeric:tabular-nums}
.ic-repsum.is-ticked{color:var(--ok);font-weight:600}
.ic-repbody{padding-left:24px;border-top:2px solid var(--line)}
.ic-ordgrp{border-bottom:1px solid var(--line)}
.ic-ordgrp:last-child{border-bottom:0}
.ic-ordhead{display:flex;flex-wrap:wrap;gap:2px 10px;align-items:baseline;cursor:pointer;user-select:none;padding:11px 6px}
.ic-ordhead:hover{background:var(--wash)}
.ic-caret{display:inline-block;width:14px;color:var(--muted);font-size:10px}
.ic-ordhead a{color:var(--ink);font-weight:700;font-size:14.5px;text-decoration:underline;text-underline-offset:3px}
.ic-ordhead a:hover{color:var(--red)}
.ic-ordhead small{color:var(--muted);font-size:12px}
.ic-ordsum{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap;font-variant-numeric:tabular-nums}
.ic-ordsum.is-ticked{color:var(--ok);font-weight:600}
.ic-ordline{display:flex;align-items:flex-start;gap:12px;padding:9px 6px 9px 10px;border-top:1px solid var(--line-2);cursor:pointer;min-width:0}
.ic-ordline:hover{background:var(--wash)}
.ic-ordline input{width:18px;height:18px;margin:1px 0 0;flex:0 0 auto;accent-color:var(--red);cursor:pointer}
.ic-ordline .ic-name{font-size:13.5px}
.ic-ordline .ic-meta{margin-top:2px}
.ic-ordline.is-off{opacity:.45;cursor:default}
.ic-ordall-row{background:var(--surface);border-top:1px solid var(--line)}
.ic-ordall-row:hover{background:#e1e0e0}
.ic-ordall-row .ic-name{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.ic-orders{margin-top:8px;border-top:2px solid var(--line);padding-top:6px;font-size:13px}
.ic-order{display:flex;align-items:flex-start;gap:11px;padding:7px 0;cursor:pointer;border-bottom:1px solid var(--line-2);min-width:0}
.ic-order:last-child{border-bottom:0}
.ic-order input{width:18px;height:18px;margin:1px 0 0;flex:0 0 auto;accent-color:var(--red);cursor:pointer}
.ic-order a{color:var(--red-700);font-weight:700;text-decoration:underline;text-underline-offset:3px}
.ic-order small{color:var(--muted);font-variant-numeric:tabular-nums}
.ic-order.is-off{opacity:.45;cursor:default}

.ic-sync{font-size:12px;color:var(--muted);white-space:nowrap}
.ic-sync.is-busy{color:var(--ink)}
.ic-sync.is-bad{color:var(--red-700);font-weight:600}
.ic-devlabel{display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap;margin:0 0 18px}
.ic-devlabel label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.ic-devlabel input{min-height:38px;padding:7px 10px;border:1px solid var(--line);border-radius:0;background:var(--surface);font-size:14px;width:240px;max-width:100%}
.ic-devlabel .ic-meta{margin:0;flex:1 1 260px}
.ic-subs{margin-top:6px;display:flex;flex-direction:column;gap:3px}
.ic-sub{display:flex;align-items:baseline;gap:8px;font-size:12.5px;color:var(--ink-2);cursor:pointer;font-variant-numeric:tabular-nums}
.ic-sub input{width:15px;height:15px;margin:0;flex:0 0 auto;accent-color:var(--red);position:relative;top:2px;cursor:pointer}
.ic-sub-dot{display:inline-block;width:15px;text-align:center;color:var(--faint);flex:0 0 auto}
.ic-sub.is-off{opacity:.45;text-decoration:line-through}
.ic-sub b{color:var(--ink)}
.ic-drift{color:var(--warn)}
.ic-flag.is-review{color:var(--red-700);background:var(--red-100)}
.ic-row.is-review{box-shadow:inset 3px 0 0 var(--red)}
.ic-qty.is-override{border-color:var(--red);background:var(--red-100)}
.ic-chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.ic-chip{border:1px solid var(--line);padding:6px 10px;font-size:12.5px;color:var(--ink-2);display:inline-flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.ic-chip b{color:var(--ink)}
.ic-chip .ic-meta{margin:0}
.ic-setup{background:var(--warn-bg);border-left:4px solid var(--warn-line);padding:14px 16px;margin:0 0 18px;font-size:13.5px;line-height:1.55;color:var(--ink)}
.ic-setup p{margin:6px 0 8px;color:var(--warn)}
.ic-setup ul{margin:0 0 0 18px;padding:0}
.ic-setup li{margin:2px 0}
.ic-setup code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;background:rgba(32,30,29,.07);padding:1px 5px}
.ic-footer{text-align:center;color:var(--muted);font-size:12px;padding:32px 16px 20px;line-height:1.6;padding-bottom:calc(20px + env(safe-area-inset-bottom,0px))}

/* Under a tablet the columns fold: the item keeps a full row, the four
   quantities pair off two by two, and the controls span the width. */
@media (max-width:980px){
  .ic-table .ic-thead{display:none}
  .ic-table.is-items .ic-row,.ic-table.is-sheet .ic-row{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:10px;column-gap:14px;padding:14px 8px}
  .ic-info,.ic-vendor,.ic-ctl,.ic-rowpanel{grid-column:1/-1}
  .ic-ctl{justify-content:flex-start}
  .ic-ctl .ic-qty{flex:0 1 104px;width:104px}
  .ic-num,.ic-vendor,.ic-deltacell{display:flex;align-items:baseline;justify-content:space-between;gap:8px;text-align:left;border-bottom:1px solid var(--line-2);padding-bottom:3px}
  .ic-num{font-size:14px;font-weight:600}
  .ic-num::before,.ic-vendor::before,.ic-deltacell::before{color:var(--muted);font-size:10px;font-weight:400;letter-spacing:.08em;text-transform:uppercase;flex:0 0 auto}
  .ic-num::before{content:attr(data-label)}
  .ic-vendor::before{content:'Vendor'}
  .ic-vendor{font-size:13px;text-align:right}
  .ic-deltacell::before{content:'Adjust by'}
  .ic-xcell{text-align:right}
  .ic-line-ctl{grid-column:1/-1;justify-content:flex-start}
}
@media (max-width:640px){
  .ic-main{padding:16px}
  .ic-head-row{gap:10px;padding-left:16px;padding-right:16px;padding-bottom:10px;padding-top:10px;padding-top:calc(10px + env(safe-area-inset-top,0px))}
  .ic-tabs{padding:14px 16px 0}
  .ic-tabseg{width:100%}
  .ic-tab{flex:1 1 0;justify-content:center;font-size:14px;padding:12px 8px;min-height:48px}
  .ic-user{display:none}
  .ic-loc{flex:1 1 100%;font-size:10px}
  .ic-loc select{max-width:100%;flex:1 1 auto}
  .ic-sum{gap:20px}
  .ic-sum div{min-width:66px}
  .ic-sum b{font-size:20px}
  .ic-sum div:last-child{margin-left:0;text-align:left;flex:1 1 100%}
  .ic-repsum,.ic-ordsum{margin-left:0;flex:1 1 100%;white-space:normal}
  .ic-repbody{padding-left:10px}
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
        instock: true,             // list only items with quantity on hand
        sheet: {}, order: [],      // itemId -> line; ids in the order added
        memo: '',
        submitting: false, progress: '', submitError: '',
        confirm: false,
        done: null,
        device: '', deviceLabel: '', // this browser's id and the name shown to the administrator
        session: null              // administrator, shared count: everyone's open lines
    };
    var searchTimer = null, reqSeq = 0;

    // ------------------------------------------------------------ storage --

    function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
    function lsDel(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }

    function loadPrefs() { try { return JSON.parse(lsGet(PREF_KEY) || '{}') || {}; } catch (e) { return {}; } }
    function savePrefs() { lsSet(PREF_KEY, JSON.stringify({ loc: state.loc, account: state.account, instock: state.instock, device: state.device, deviceLabel: state.deviceLabel })); }
    function newDeviceId() {
        var s = 'd', chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
        for (var i = 0; i < 20; i++) { s += chars.charAt(Math.floor(Math.random() * chars.length)); }
        return s;
    }

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
    // Who keyed this line, and when: the NetSuite user signed in on this device.
    // It rides on the sheet, the exports and the adjustment line memo, so a
    // doubtful count can be traced back to the person who took it.
    function stamp(line) { line.by = BOOT.user || ''; line.at = Date.now(); markDirty(line.id); return line; }
    function fmtWhen(ms) {
        if (!ms) { return ''; }
        try { return new Date(ms).toLocaleString([], { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' }); }
        catch (e) { return new Date(ms).toLocaleString(); }
    }
    // A "last modified" from the server: NetSuite hands back the user's own
    // display format, which is shown as is; an ISO string is made readable.
    function fmtAt(v) {
        var t = String(v || '');
        if (!/^\d{4}-\d{2}-\d{2}T/.test(t)) { return t; }
        var d = new Date(t);
        return isNaN(d.getTime()) ? t : fmtWhen(d.getTime());
    }
    // The item name opens its record in a new tab. The generic item page takes
    // any item type, so no record-type lookup is needed.
    function itemLink(id, name) {
        if (!id) { return el('span', { text: name }); }
        return el('a', { href: '/app/common/item/item.nl?id=' + encodeURIComponent(id), target: '_blank', rel: 'noopener', title: 'Open the item record in a new tab', text: name });
    }
    // ------------------------------------------------------------- shared --
    // Every line this device keys is pushed to NetSuite (action=sync) so the
    // administrator's sheet shows everyone's count. The local sheet stays the
    // working copy: changes are batched, retried on failure, and reconciled
    // with the server on load and every minute. Once a line has been saved,
    // NetSuite owns it -- if it is gone from the server (posted, or discarded
    // by the administrator) it leaves this sheet too.
    var SHARED = !!(BOOT.shared && BOOT.shared.ready);
    var sync = { dirty: {}, removed: {}, timer: null, inflight: false, status: SHARED ? 'idle' : 'off', error: '', backoff: 0, lastOk: 0, pulling: false };
    function markDirty(id) { if (!SHARED) { return; } sync.dirty[id] = Date.now(); delete sync.removed[id]; scheduleSync(); }
    function markRemoved(id) { if (!SHARED) { return; } sync.removed[id] = true; delete sync.dirty[id]; scheduleSync(); }
    function pendingCount() { return Object.keys(sync.dirty).length + Object.keys(sync.removed).length; }
    function scheduleSync(ms) {
        if (!SHARED) { return; }
        clearTimeout(sync.timer);
        sync.timer = setTimeout(pushSync, ms == null ? 1200 : ms);
        if (pendingCount() && sync.status !== 'offline') { sync.status = 'pending'; }
        paintSync();
    }
    function pushSync() {
        if (!SHARED || sync.inflight) { return; }
        if (BOOT.multiLoc && !state.loc) { return; }
        var ids = Object.keys(sync.dirty).slice(0, 50), dels = Object.keys(sync.removed).slice(0, 50);
        if (!ids.length && !dels.length) { sync.status = 'saved'; paintSync(); return; }
        var sentAt = {}, upserts = [];
        ids.forEach(function (id) {
            sentAt[id] = sync.dirty[id];
            var l = state.sheet[id];
            if (l) { upserts.push({ item: id, shelf: shelfOf(l), orders: l.orders || [], onhand: l.onhand }); }
        });
        sync.inflight = true; sync.status = 'saving'; paintSync();
        apiPost('sync', { loc: state.loc || '', device: state.device, label: state.deviceLabel || '', upserts: upserts, deletes: dels }).then(function (res) {
            sync.inflight = false;
            if (!res || !res.ok) {
                sync.status = 'offline'; sync.error = (res && res.error) || 'Could not reach NetSuite.';
                sync.backoff = Math.min(60000, sync.backoff ? sync.backoff * 2 : 5000);
                scheduleSync(sync.backoff);
                return;
            }
            sync.backoff = 0; sync.error = ''; sync.lastOk = Date.now();
            var failed = {};
            (res.errors || []).forEach(function (e) { failed[e.item] = e.error; });
            ids.forEach(function (id) {
                if (sync.dirty[id] !== sentAt[id]) { return; }      // changed again while in flight: goes next time
                delete sync.dirty[id];
                var l = state.sheet[id];
                if (l) { if (failed[id]) { l.error = 'Not saved to NetSuite: ' + failed[id]; } else { l.synced = true; } }
            });
            dels.forEach(function (id) { delete sync.removed[id]; });
            saveSheet();
            if (pendingCount()) { scheduleSync(50); } else { sync.status = 'saved'; paintSync(); }
        });
    }
    function syncText() {
        if (!SHARED) { return ''; }
        var n = pendingCount();
        if (sync.status === 'saving') { return 'Saving to NetSuite…'; }
        if (sync.status === 'offline') { return 'Not saved (' + n + ' line' + (n === 1 ? '' : 's') + ') — retrying'; }
        if (sync.status === 'pending') { return n + ' line' + (n === 1 ? '' : 's') + ' to save'; }
        if (sync.lastOk) { return 'Saved to NetSuite · ' + fmtWhen(sync.lastOk); }
        return 'Shared count';
    }
    function paintSync() {
        var e = document.getElementById('icSync');
        if (!e) { return; }
        e.textContent = syncText();
        e.className = 'ic-sync' + (sync.status === 'offline' ? ' is-bad' : (sync.status === 'saving' || sync.status === 'pending') ? ' is-busy' : '');
        e.title = sync.error || '';
    }
    // What the server holds for this device against what the browser holds.
    // Lines never saved are pushed; lines saved before and now gone from the
    // server were posted or discarded, so they leave here too; lines on both
    // sides are pushed only when they differ (the browser is the working copy).
    function reconcile() {
        if (!SHARED || sync.pulling) { return; }
        if (BOOT.multiLoc && !state.loc) { return; }
        sync.pulling = true;
        apiGet('mine', { loc: state.loc || '', device: state.device }).then(function (res) {
            sync.pulling = false;
            if (!res || !res.ok) { sync.status = 'offline'; sync.error = (res && res.error) || 'Could not reach NetSuite.'; paintSync(); scheduleSync(15000); return; }
            var server = {}, changed = false;
            (res.lines || []).forEach(function (s) { server[s.item] = s; });
            state.order.slice().forEach(function (id) {
                var l = state.sheet[id], s = server[id];
                if (!l) { return; }
                if (!s) {
                    if (l.synced && !sync.dirty[id]) { removeLocal(id); changed = true; }
                    else { sync.dirty[id] = sync.dirty[id] || Date.now(); }
                    return;
                }
                var same = round4(s.shelf) === round4(shelfOf(l)) && JSON.stringify((s.orders || []).map(function (o) { return [o.ref, o.qty]; })) === JSON.stringify((l.orders || []).map(function (o) { return [o.ref, o.qty]; }));
                if (!same) { sync.dirty[id] = sync.dirty[id] || Date.now(); }
                else { l.synced = true; }
            });
            (res.lines || []).forEach(function (s) {
                if (state.sheet[s.item] || sync.removed[s.item]) { return; }
                var line = ensureLine({ id: s.item, name: s.name || ('item ' + s.item), onhand: s.onhand });
                line.orders = (s.orders || []).slice();
                line.count = round4((Number(s.shelf) || 0) + ordersTotal(line));
                line.by = BOOT.user || ''; line.at = Date.now(); line.synced = true;
                changed = true;
            });
            saveSheet(); updateBadge();
            if (pendingCount()) { scheduleSync(100); } else { sync.status = sync.lastOk ? 'saved' : 'idle'; if (!sync.lastOk) { sync.lastOk = Date.now(); } paintSync(); }
            if (changed && state.view !== 'done') { render(); }
        });
    }
    // Who is holding this device, as the administrator will see it.
    function deviceLabelField() {
        if (!SHARED) { return null; }
        return el('div', { class: 'ic-devlabel' }, [
            el('label', { for: 'icDevLabel', text: 'This device' }),
            el('input', { id: 'icDevLabel', type: 'text', maxlength: '40', placeholder: 'e.g. Warehouse tablet 1', value: state.deviceLabel || '', autocomplete: 'off',
                onchange: function (ev) { state.deviceLabel = String(ev.target.value || '').trim().slice(0, 40); savePrefs(); state.order.forEach(function (id) { markDirty(id); }); } }),
            el('span', { class: 'ic-meta', text: 'Shown with your name on the administrator\'s sheet, so a count can be traced to a place as well as a person.' })
        ]);
    }
    // Shown to administrators until the custom record exists.
    function setupNotice() {
        var sh = BOOT.shared || {}, su = BOOT.sharedSetup || {}, F = su.fields || {};
        var spec = { item: 'List/Record → Item (mandatory)', location: 'List/Record → Location', shelf: 'Decimal Number', orders: 'Long Text', onhand: 'Decimal Number',
            counter: 'List/Record → Employee', device: 'Free-Form Text', label: 'Free-Form Text', posted: 'Check Box', adjustment: 'List/Record → Transaction' };
        var list = el('ul', {}, [el('li', {}, ['Record type, ID ', el('code', { text: su.record || '' }), ' — Access Type: No Permission Required'])]);
        Object.keys(spec).forEach(function (k) {
            if (!F[k]) { return; }
            list.appendChild(el('li', {}, [el('code', { text: F[k] }), ' — ' + spec[k] + (sh.missing && sh.missing === F[k] ? '  ← missing' : '')]));
        });
        return el('div', { class: 'ic-setup' }, [
            el('b', { text: 'Shared count is not set up yet' }),
            el('p', { text: 'Until the custom record below exists, each device keeps its own sheet and only this one can be submitted from here. Create it once (Customization › Lists, Records & Fields › Record Types › New, then its Fields subtab), then reload this page. NetSuite said: ' + (sh.error || 'record type missing') + '.' }),
            list
        ]);
    }

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
            markDirty(id);
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
        stamp(line);
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
        stamp(line);
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

    function removeLocal(id) {
        delete state.sheet[id];
        state.order = state.order.filter(function (x) { return x !== id; });
        saveSheet();
        updateBadge();
    }
    function removeLine(id) {
        removeLocal(id);
        markRemoved(id);
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
    function sheetSize() {
        if (SHARED && BOOT.canSubmit && state.session && state.session.items) { return state.session.items.length; }
        return state.order.length;
    }
    function updateBadge() {
        var b = document.getElementById('icSheetBadge');
        if (b) { b.textContent = String(sheetSize()); }
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
        var info = el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name' }, [itemLink(item.id, item.name)]),
            (item.display || item.desc) ? el('div', { class: 'ic-desc', text: item.display && item.desc && item.display !== item.desc ? item.display + ' - ' + item.desc : (item.display || item.desc) }) : null,
            item.upc ? el('div', { class: 'ic-meta', text: 'UPC ' + item.upc }) : null,
            item.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + item.blocked + ') - adjust manually' }) : null,
            line ? el('div', { class: 'ic-onsheet', 'data-onsheet-for': item.id, text: onSheetText(line) }) : null
        ]);
        row.appendChild(info);
        row.appendChild(el('div', { class: 'ic-vendor', text: item.vendor || '' }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'On hand', 'data-cell': 'onhand', text: fmt(item.onhand) }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'Avail', 'data-cell': 'available', text: fmt(item.available) }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'Committed', 'data-cell': 'committed' }, [committedEl(item.committed, panel)]));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'On order', 'data-cell': 'onorder', text: fmt(item.onorder) }));
        var ctl = el('div', { class: 'ic-ctl' });
        row.appendChild(ctl);
        row.appendChild(el('div', { class: 'ic-rowpanel' }, [panel.box]));
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
            ctl.appendChild(input); ctl.appendChild(btn); ctl.appendChild(okWrap);
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
            else if (info) { info.appendChild(el('div', { class: 'ic-onsheet', 'data-onsheet-for': itemId, text: onSheetText(line) })); }
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
        naturalItemOrder(state.results).forEach(function (it) { list.appendChild(resultRow(it)); });
        var table = el('div', { class: 'ic-table is-items' });
        if (state.results.length) {
            table.appendChild(el('div', { class: 'ic-thead' }, [
                el('div', { text: 'Item' }),
                el('div', { text: 'Vendor' }),
                el('div', { class: 'ic-th-num', text: 'On hand' }),
                el('div', { class: 'ic-th-num', text: 'Avail' }),
                el('div', { class: 'ic-th-num', text: 'Committed' }),
                el('div', { class: 'ic-th-num', text: 'On order' }),
                el('div', { class: 'ic-th-num', text: 'Count' })
            ]));
        }
        table.appendChild(list);
        box.appendChild(table);
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
        main.appendChild(el('p', { class: 'ic-hint', text: 'Every item at this location is listed below; In stock keeps those with quantity on hand; what is only on order has not been received and is not listed. Search to jump to one, key what is on the shelf and press Add (Enter jumps to the next item), or tick Correct when the shelf holds the expected quantity. Units pulled for open orders are added on the Open orders tab. Counts wait on the Sheet tab until you submit.' }));
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
        var who = el('div', { class: 'ic-meta ic-who' });
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
            who.textContent = line.by ? 'Counted by ' + line.by + (line.at ? ' · ' + fmtWhen(line.at) : '') : '';
            who.hidden = !line.by;
            paintWarn();
            saveSheet(); paintTotals();
        }
        function apply(v) {
            var c = parseCount(v);
            if (c === null) { return; }
            line.count = c; line.error = ''; row.classList.remove('has-error'); stamp(line); paint();
        }
        input.addEventListener('input', function () {
            var c = parseCount(input.value);
            if (c === null) { return; }
            line.count = c; line.error = ''; row.classList.remove('has-error'); stamp(line);
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
            el('div', { class: 'ic-name' }, [itemLink(line.id, line.name)]),
            (line.display || line.desc) ? el('div', { class: 'ic-desc', text: line.display || line.desc }) : null,
            line.vendor ? el('div', { class: 'ic-meta', text: line.vendor }) : null,
            who,
            line.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + line.blocked + ') - will be skipped' }) : null,
            line.blocked ? null : ordersBtn
        ]));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'On hand', text: fmt(line.onhand) }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'Avail', text: fmt(line.available) }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'Committed' }, [committedEl(line.committed, panel)]));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'On order', text: fmt(line.onorder) }));
        row.appendChild(el('div', { class: 'ic-line-ctl' }, [minus, input, plus]));
        row.appendChild(el('div', { class: 'ic-deltacell' }, [delta]));
        row.appendChild(el('div', { class: 'ic-xcell' }, [remove]));
        row.appendChild(el('div', { class: 'ic-rowpanel' }, [
            line.error ? el('div', { class: 'ic-error', text: line.error }) : null,
            incl,
            warn,
            panel.box
        ]));
        paint();
        return row;
    }

    // ------------------------------------------------------- shared sheet --
    // The administrator's sheet in a shared count: everyone's open lines,
    // merged per item. Two counters on one item are two sub-lines that add up
    // -- a warehouse count and a retail-floor count of the same SKU -- and
    // either can be left out, or the total set by hand, before posting.

    function loadSession(after) {
        var prev = state.session || {};
        state.session = { loc: state.loc, loading: true, error: '', lines: [], items: [], byItem: {}, fresh: {}, freshDone: false, loadedAt: 0, truncated: false,
            excluded: prev.excluded || {}, overrides: prev.overrides || {}, filter: prev.filter || 'all', precheck: null };
        var s = state.session;
        apiGet('session', { loc: state.loc || '' }).then(function (res) {
            if (state.session !== s) { return; }   // superseded (location change, discard)
            s.loading = false;
            if (!res || !res.ok) { s.error = (res && res.error) || 'Could not load the shared count.'; render(); return; }
            s.lines = res.lines || []; s.truncated = !!res.truncated; s.loadedAt = Date.now();
            mergeSession(s);
            updateBadge();
            if (state.view === 'sheet') { render(); loadFresh(s, after); }
            else if (after) { after(); }
        });
    }
    function mergeSession(s) {
        var byItem = {}, items = [];
        s.lines.forEach(function (l) {
            var m = byItem[l.item];
            if (!m) { m = byItem[l.item] = { id: l.item, name: l.name || ('item ' + l.item), subs: [] }; items.push(m); }
            m.subs.push(l);
        });
        items.sort(function (a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : 0; });
        s.items = naturalItemOrder(items); s.byItem = byItem;
    }
    // Fresh on-hand for every merged item, 200 at a time; the deltas the
    // administrator sees are against NetSuite right now, not the counters' screens.
    function loadFresh(s, after) {
        if (s.freshLoading || s.freshDone) { if (after) { after(); } return; }
        var ids = s.items.map(function (m) { return m.id; }), i = 0;
        s.freshLoading = true;
        function next() {
            if (state.session !== s) { return; }
            if (i >= ids.length) { s.freshLoading = false; s.freshDone = true; render(); if (after) { after(); } return; }
            var batch = ids.slice(i, i + 200); i += 200;
            apiPost('onhand', { ids: batch, loc: state.loc || '' }).then(function (res) {
                if (res && res.ok && res.items) { Object.keys(res.items).forEach(function (k) { s.fresh[k] = res.items[k]; }); }
                batch.forEach(function (id) { if (!s.fresh[id]) { s.fresh[id] = { missing: true }; } });
                next();
            });
        }
        next();
    }
    // The merged count for an item: the shelf units of every counter not left
    // out, plus each ticked open order once (two people ticking the same order
    // are agreeing, not doubling it), unless the administrator set a total.
    function mergedFor(m) {
        var s = state.session, shelf = 0, orders = {}, refs = [], names = [], ids = [], live = 0, latest = 0;
        m.subs.forEach(function (l) {
            ids.push(l.id);
            if (s.excluded[l.id]) { return; }
            live++;
            shelf += Number(l.shelf) || 0;
            (l.orders || []).forEach(function (o) { if (!orders[o.ref]) { orders[o.ref] = Number(o.qty) || 0; refs.push(o.ref); } });
            if (l.counterName && names.indexOf(l.counterName) === -1) { names.push(l.counterName); }
            if (l.at > latest) { latest = l.at; }
        });
        var onOrders = 0;
        refs.forEach(function (r) { onOrders += orders[r]; });
        var computed = round4(shelf + onOrders);
        var ov = s.overrides[m.id];
        var total = ov != null ? round4(ov) : computed;
        var f = s.fresh[m.id] || {};
        var onhand = f.onhand == null ? null : Number(f.onhand);
        var drift = m.subs.some(function (l) { return !s.excluded[l.id] && l.onhand != null && onhand != null && round4(l.onhand) !== round4(onhand); });
        var usable = !f.missing && !f.blocked && live > 0;
        return { computed: computed, total: total, overridden: ov != null, orders: refs.map(function (r) { return { ref: r, qty: orders[r] }; }), onOrders: onOrders,
            names: names, ids: ids, seen: m.subs.map(function (l) { return { id: l.id, shelf: l.shelf, orders: l.orders || [] }; }),
            live: live, latest: latest, fresh: f, onhand: onhand, drift: drift, usable: usable,
            delta: onhand == null || !usable ? null : round4(total - onhand),
            review: m.subs.length > 1 || drift || !!f.missing || !!f.blocked };
    }
    function sharedTotals(s) {
        var t = { items: 0, pos: 0, neg: 0, changed: 0, review: 0, blocked: 0, counters: {}, lines: 0 };
        s.items.forEach(function (m) {
            var g = mergedFor(m);
            t.items++; t.lines += m.subs.length;
            if (g.review) { t.review++; }
            if (!g.usable) { t.blocked++; return; }
            if (g.delta == null) { return; }
            if (g.delta !== 0) { t.changed++; }
            if (g.delta > 0) { t.pos += g.delta; } else { t.neg += -g.delta; }
        });
        s.lines.forEach(function (l) {
            var k = (l.counterName || 'Unknown') + '|' + (l.label || '');
            var c = t.counters[k] || (t.counters[k] = { name: l.counterName || 'Unknown', label: l.label || '', lines: 0, at: '' });
            c.lines++;
            if (l.at > c.at) { c.at = l.at; }
        });
        return t;
    }

    function sharedRow(m) {
        var s = state.session, g = mergedFor(m), f = g.fresh;
        var row = el('div', { class: 'ic-row' + (g.review ? ' is-review' : '') + (f.missing ? ' has-error' : ''), 'data-row-for': m.id });
        var subs = el('div', { class: 'ic-subs' });
        m.subs.forEach(function (l) {
            var off = !!s.excluded[l.id];
            var cb = el('input', { type: 'checkbox', 'aria-label': 'Include the count from ' + (l.counterName || 'this counter') });
            cb.checked = !off;
            cb.addEventListener('change', function () { if (cb.checked) { delete s.excluded[l.id]; } else { s.excluded[l.id] = true; } render(); });
            var extra = (l.orders || []).length ? ' + ' + (l.orders || []).map(function (o) { return fmt(o.qty) + ' on ' + o.ref; }).join(', ') : '';
            var stale = l.onhand != null && g.onhand != null && round4(l.onhand) !== round4(g.onhand);
            subs.appendChild(el('label', { class: 'ic-sub' + (off ? ' is-off' : ''), 'data-sub-for': l.id }, [
                m.subs.length > 1 ? cb : el('span', { class: 'ic-sub-dot', text: '·' }),
                el('span', {}, [
                    el('b', { text: fmt(l.shelf) + extra }),
                    ' · ' + (l.counterName || 'unknown') + (l.label ? ' · ' + l.label : '') + (l.at ? ' · ' + fmtAt(l.at) : ''),
                    stale ? el('span', { class: 'ic-drift', text: ' · on hand was ' + fmt(l.onhand) + ' when counted' }) : null
                ])
            ]));
        });
        var input = el('input', { class: 'ic-qty' + (g.overridden ? ' is-override' : ''), type: 'text', inputmode: 'decimal', autocomplete: 'off', value: fmt(g.total), 'aria-label': 'Counted quantity for ' + m.name });
        input.addEventListener('change', function () {
            var c = parseCount(input.value);
            if (c === null) { input.value = fmt(g.total); return; }
            if (c === g.computed) { delete s.overrides[m.id]; } else { s.overrides[m.id] = c; }
            render();
        });
        var reset = el('button', { class: 'ic-x', type: 'button', text: '↺', title: 'Back to the counted total (' + fmt(g.computed) + ')', 'aria-label': 'Use the counted total', onclick: function () { delete s.overrides[m.id]; render(); } });
        if (!g.overridden) { reset.style.visibility = 'hidden'; }
        var delta = el('div', { class: 'ic-delta ' + (g.delta == null ? 'is-zero' : g.delta > 0 ? 'is-pos' : g.delta < 0 ? 'is-neg' : 'is-zero'), text: g.delta == null ? (f.missing || f.blocked ? 'n/a' : '…') : signed(g.delta) });
        row.appendChild(el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name' }, [itemLink(m.id, f.name || m.name)]),
            (f.display || f.desc) ? el('div', { class: 'ic-desc', text: f.display || f.desc }) : null,
            m.subs.length > 1 ? el('div', { class: 'ic-flag is-review', text: m.subs.length + ' counters' + (g.live < m.subs.length ? ' · ' + (m.subs.length - g.live) + ' left out' : ' · added together') }) : null,
            g.drift ? el('div', { class: 'ic-flag', text: 'On hand changed since it was counted' }) : null,
            f.missing ? el('div', { class: 'ic-flag', text: 'Not found at this location - will be skipped' }) : null,
            f.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + f.blocked + ') - will be skipped' }) : null,
            g.overridden ? el('div', { class: 'ic-onsheet', text: 'Total set by hand · counted ' + fmt(g.computed) }) : null,
            subs
        ]));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'On hand', text: g.onhand == null ? '…' : fmt(g.onhand) }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'Avail', text: f.available == null ? '…' : fmt(f.available) }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'Committed', text: f.committed == null ? '…' : fmt(f.committed) }));
        row.appendChild(el('div', { class: 'ic-num', 'data-label': 'On order', text: f.onorder == null ? '…' : fmt(f.onorder) }));
        row.appendChild(el('div', { class: 'ic-line-ctl' }, [input]));
        row.appendChild(el('div', { class: 'ic-deltacell' }, [delta]));
        row.appendChild(el('div', { class: 'ic-xcell' }, [reset]));
        row.appendChild(el('div', { class: 'ic-rowpanel' }, [
            g.orders.length ? el('div', { class: 'ic-incl', text: 'Includes ' + fmt(g.onOrders) + ' on open orders: ' + g.orders.map(function (o) { return o.ref; }).join(', ') }) : null
        ]));
        return row;
    }

    function sharedExportLines() {
        var s = state.session;
        return s.items.map(function (m) {
            var g = mergedFor(m), f = g.fresh;
            return { name: f.name || m.name, display: f.display || f.desc || '', vendor: f.vendor || '', onhand: g.onhand, count: g.total,
                orders: g.orders, by: g.names.join(', '), when: fmtAt(g.latest) };
        });
    }

    function renderSharedSheetView(main) {
        if (BOOT.multiLoc && !state.loc) {
            main.appendChild(el('div', { class: 'ic-empty', text: 'Pick a location at the top to load the shared count.' }));
            return;
        }
        var s = state.session;
        if (!s || s.loc !== state.loc) { loadSession(); s = state.session; }
        main.appendChild(deviceLabelField());
        if (s.loading) { main.appendChild(el('div', { class: 'ic-progress', text: 'Loading everyone\'s counts…' })); return; }
        if (s.error) {
            main.appendChild(el('div', { class: 'ic-error', text: s.error }));
            main.appendChild(el('div', { class: 'ic-actions' }, [el('button', { class: 'ic-btn is-ghost is-sm', type: 'button', text: 'Try again', onclick: function () { loadSession(); render(); } })]));
            return;
        }
        if (!s.freshDone) { loadFresh(s); }
        var t = sharedTotals(s);
        var counterKeys = Object.keys(t.counters).sort();
        main.appendChild(el('div', { class: 'ic-sum' }, [
            el('div', {}, [el('b', { id: 'icTotLines', text: String(t.items) }), 'items']),
            el('div', {}, [el('b', { text: String(counterKeys.length) }), 'counters']),
            el('div', {}, [el('b', { id: 'icTotPos', text: '+' + fmt(t.pos) }), 'units up']),
            el('div', {}, [el('b', { id: 'icTotNeg', text: '−' + fmt(t.neg) }), 'units down']),
            el('div', {}, [el('b', { text: state.loc ? locName(state.loc) : (BOOT.multiLoc ? 'No location' : 'All locations') }), 'location'])
        ]));
        if (counterKeys.length) {
            main.appendChild(el('div', { class: 'ic-chips' }, counterKeys.map(function (k) {
                var c = t.counters[k];
                return el('span', { class: 'ic-chip' }, [el('b', { text: c.name }), c.label ? el('span', { text: c.label }) : null, el('span', { class: 'ic-meta', text: c.lines + ' line' + (c.lines === 1 ? '' : 's') + (c.at ? ' · ' + fmtAt(c.at) : '') })]);
            })));
        }
        if (s.truncated) { main.appendChild(el('div', { class: 'ic-warn', text: 'The shared count has more than 5,000 lines; only the first 5,000 are shown. Post these, then reload for the rest.' })); }
        if (state.submitError) { main.appendChild(el('div', { class: 'ic-error', text: state.submitError })); }
        if (!s.items.length) {
            main.appendChild(el('div', { class: 'ic-empty', text: 'Nobody has counted anything yet at this location. Counts appear here as each device saves them.' }));
            main.appendChild(el('div', { class: 'ic-actions' }, [
                el('button', { class: 'ic-btn', type: 'button', text: 'Go to search', onclick: function () { state.view = 'search'; render(); } }),
                el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Reload', onclick: function () { loadSession(); render(); } })
            ]));
            return;
        }
        main.appendChild(el('div', { class: 'ic-listhead' }, [
            el('div', { class: 'ic-count', text: t.lines + ' line' + (t.lines === 1 ? '' : 's') + ' from ' + counterKeys.length + ' counter' + (counterKeys.length === 1 ? '' : 's') + ' · ' + t.review + ' to review · ' + t.changed + ' change' + (t.changed === 1 ? '' : 's') + (s.loadedAt ? ' · loaded ' + fmtWhen(s.loadedAt) : '') }),
            el('div', { class: 'ic-actions' }, [
                el('div', { class: 'ic-seg', role: 'group', 'aria-label': 'Which items to list' }, [
                    el('button', { class: 'ic-segbtn' + (s.filter === 'all' ? ' is-on' : ''), type: 'button', text: 'All', onclick: function () { s.filter = 'all'; render(); } }),
                    el('button', { class: 'ic-segbtn' + (s.filter === 'review' ? ' is-on' : ''), type: 'button', text: 'Needs review (' + t.review + ')', onclick: function () { s.filter = 'review'; render(); } })
                ]),
                el('button', { class: 'ic-btn is-ghost is-sm', type: 'button', text: 'Reload', onclick: function () { state.submitError = ''; loadSession(); render(); } })
            ])
        ]));
        var list = el('div', { class: 'ic-list' });
        var shown = 0;
        s.items.forEach(function (m) {
            if (s.filter === 'review' && !mergedFor(m).review) { return; }
            shown++;
            list.appendChild(sharedRow(m));
        });
        main.appendChild(el('div', { class: 'ic-table is-sheet' }, [
            el('div', { class: 'ic-thead' }, [
                el('div', { text: 'Item' }), el('div', { class: 'ic-th-num', text: 'On hand' }), el('div', { class: 'ic-th-num', text: 'Avail' }),
                el('div', { class: 'ic-th-num', text: 'Committed' }), el('div', { class: 'ic-th-num', text: 'On order' }), el('div', { text: 'Counted' }),
                el('div', { class: 'ic-th-num', text: 'Adjust' }), el('div', {})
            ]),
            list
        ]));
        if (!shown) { main.appendChild(el('div', { class: 'ic-empty', text: 'Nothing needs review.' })); }

        var form = el('div', { class: 'ic-form' });
        form.appendChild(el('div', {}, [
            el('label', { for: 'icMemo', text: 'Memo (goes on the Inventory Adjustment)' }),
            el('input', { id: 'icMemo', type: 'text', maxlength: '200', placeholder: 'e.g. Q3 cycle count', value: state.memo,
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
        form.appendChild(exportBar('sheet', sharedExportLines));
        if (state.submitting) {
            form.appendChild(el('div', { class: 'ic-progress', text: state.progress || 'Submitting…' }));
        } else if (state.confirm) {
            var live = t.items - t.blocked;
            var nothing = t.changed === 0;
            var pre = s.precheck;
            form.appendChild(el('div', { class: 'ic-confirm' }, [
                el('p', {}, [el('b', { text: nothing
                    ? 'Nothing to adjust — every counted item already matches.'
                    : 'Create an Inventory Adjustment for ' + t.changed + ' item' + (t.changed === 1 ? '' : 's') + (state.loc ? ' at ' + locName(state.loc) : '') + '?' })]),
                el('p', { text: nothing
                    ? 'All ' + live + ' item' + (live === 1 ? '' : 's') + ' equal the on-hand NetSuite already holds, so submitting records the count, clears everyone\'s sheets, and creates no Inventory Adjustment.'
                    : 'Those ' + t.changed + ' item' + (t.changed === 1 ? '' : 's') + ' move to the merged count (+' + fmt(t.pos) + ' / −' + fmt(t.neg) + ' units). The other ' + (live - t.changed) + ' already match and are left off. Every counter\'s lines are then cleared from their sheets.' }),
                t.review ? el('p', { text: t.review + ' item' + (t.review === 1 ? ' is' : 's are') + ' flagged for review (two counters, or on hand changed). Counts from two people are added together unless one is unticked.' }) : null,
                t.blocked ? el('p', { text: t.blocked + ' item' + (t.blocked === 1 ? ' is' : 's are') + ' skipped and stay on the sheet.' }) : null,
                pre === null ? el('p', { class: 'ic-meta', text: 'Checking today\'s adjustments…' }) : (pre && pre.length ? el('div', { class: 'ic-commit-warn' }, [
                    el('b', { text: pre.length + ' item' + (pre.length === 1 ? ' was' : 's were') + ' already adjusted by a count today: ' }),
                    pre.slice(0, 8).map(function (a) { var m = s.byItem[a.item]; return (m ? m.name : 'item ' + a.item) + ' (' + signed(a.qty) + (a.tranid ? ', #' + a.tranid : '') + ')'; }).join('; ') + (pre.length > 8 ? '; …' : ''),
                    ' Submitting adjusts them again against the current on-hand.'
                ]) : null),
                el('div', { class: 'ic-actions' }, [
                    el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Cancel', onclick: function () { state.confirm = false; render(); } }),
                    el('button', { class: 'ic-btn is-ok', type: 'button', text: nothing ? 'Yes, record the count' : 'Yes, submit count', onclick: submitShared })
                ])
            ]));
        } else {
            form.appendChild(el('div', { class: 'ic-actions' }, [
                el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Refresh on-hand', onclick: function () { s.fresh = {}; s.freshDone = false; s.freshLoading = false; render(); } }),
                el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Discard all counts…', onclick: discardAll }),
                el('button', { class: 'ic-btn', type: 'button', text: 'Submit count…', onclick: function () {
                    state.submitError = '';
                    if (BOOT.multiLoc && !state.loc) { state.submitError = 'Pick a location at the top first.'; render(); return; }
                    if (!BOOT.accountLocked && !state.account) { state.submitError = 'Pick the adjustment account first.'; render(); return; }
                    if (!s.freshDone) { state.submitError = 'Still loading on-hand — one moment.'; render(); return; }
                    if (t.items - t.blocked <= 0) { state.submitError = 'No countable items on the shared sheet.'; render(); return; }
                    state.confirm = true; s.precheck = null; render();
                    apiPost('precheck', { loc: state.loc || '', items: s.items.map(function (m) { return m.id; }) }).then(function (r) {
                        if (state.session !== s) { return; }
                        s.precheck = (r && r.ok && r.adjusted) || [];
                        if (state.confirm) { render(); }
                    });
                } })
            ]));
        }
        main.appendChild(form);
    }

    function submitShared() {
        var s = state.session, lines = [];
        s.items.forEach(function (m) {
            var g = mergedFor(m);
            if (!g.usable) { return; }
            lines.push({ item: m.id, count: g.total, orders: g.orders, by: g.names.join(', '), ids: g.ids, seen: g.seen });
        });
        var size = BOOT.sharedBatch || 100, batches = [];
        for (var i = 0; i < lines.length; i += size) { batches.push(lines.slice(i, i + size)); }
        var done = { adjustments: [], applied: 0, skipped: 0, blocked: [], unmarked: [], location: state.loc ? locName(state.loc) : '', shared: true };
        state.confirm = false; state.submitting = true; state.submitError = '';
        var n = 0;
        function retryMark(unmarked, adj, cb) {
            if (!unmarked || !unmarked.length) { cb(); return; }
            apiPost('mark', { ids: unmarked, adjustment: adj ? adj.id : '' }).then(function (r) {
                var left = (r && r.ok) ? (r.unmarked || []) : unmarked;
                if (left.length && left.length < unmarked.length) { retryMark(left, adj, cb); return; }
                done.unmarked = done.unmarked.concat(left);
                cb();
            });
        }
        function finish() {
            state.submitting = false; state.progress = '';
            state.session = null;    // whatever is left -- blocked, or arrived meanwhile -- reloads on the next visit
            state.done = done; state.view = 'done';
            render();
            reconcile();             // this device's own posted lines leave its local sheet
        }
        function next() {
            if (n >= batches.length) { finish(); return; }
            var batch = batches[n++];
            state.progress = batches.length > 1 ? 'Submitting batch ' + n + ' of ' + batches.length + '…' : 'Creating the Inventory Adjustment…';
            render();
            apiPost('submit', { loc: state.loc || '', account: state.account || '', memo: state.memo || '', lines: batch }).then(function (res) {
                if (!res || !res.ok) {
                    state.submitting = false; state.progress = '';
                    state.submitError = ((res && res.error) || 'Submit failed.') + (n > 1 && !(res && res.stale) ? ' (Earlier batches posted; what is left is still on the shared sheet.)' : '');
                    if (done.adjustments.length) { state.done = done; }
                    // A stale item was decided on old numbers: forget that decision, keep the others.
                    (res && res.stale || []).forEach(function (st) {
                        delete s.overrides[st.item];
                        var m = s.byItem[st.item];
                        if (m) { m.subs.forEach(function (l) { delete s.excluded[l.id]; }); }
                    });
                    loadSession();
                    render();
                    return;
                }
                if (res.adjustment) { done.adjustments.push(res.adjustment); }
                done.applied += (res.applied || []).length;
                done.skipped += (res.skipped || []).length;
                (res.blocked || []).forEach(function (b) { done.blocked.push({ name: b.name || ('item ' + b.item), reason: b.reason || '' }); });
                retryMark(res.unmarked, res.adjustment, next);
            });
        }
        next();
    }

    // Everyone's open lines at this location, gone. The counters' devices notice
    // within a minute and empty their sheets too.
    function discardAll() {
        var s = state.session || { lines: [] };
        var who = {};
        s.lines.forEach(function (l) { who[l.counterName || '?'] = true; });
        var n = s.lines.length, w = Object.keys(who).length;
        if (!window.confirm('Discard all ' + n + ' count line' + (n === 1 ? '' : 's') + ' from ' + w + ' counter' + (w === 1 ? '' : 's') + (state.loc ? ' at ' + locName(state.loc) : '') + '? Their sheets empty out too. Nothing is posted to NetSuite.')) { return; }
        state.submitting = true; state.progress = 'Discarding…'; state.submitError = ''; render();
        function step() {
            apiPost('discard', { loc: state.loc || '', scope: 'all' }).then(function (r) {
                if (!r || !r.ok) { state.submitting = false; state.progress = ''; state.submitError = (r && r.error) || 'Discard failed.'; state.session = null; render(); return; }
                if (r.remaining > 0 && r.deleted > 0) { step(); return; }
                state.submitting = false; state.progress = ''; state.session = null;
                state.order.slice().forEach(removeLocal);
                sync.dirty = {}; sync.removed = {};
                render();
            });
        }
        step();
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
        if (!SHARED && BOOT.canSubmit && BOOT.shared && !BOOT.shared.off) { main.appendChild(setupNotice()); }
        if (SHARED) { main.appendChild(deviceLabelField()); }
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
        main.appendChild(el('div', { class: 'ic-table is-sheet' }, [
            el('div', { class: 'ic-thead' }, [
                el('div', { text: 'Item' }),
                el('div', { class: 'ic-th-num', text: 'On hand' }),
                el('div', { class: 'ic-th-num', text: 'Avail' }),
                el('div', { class: 'ic-th-num', text: 'Committed' }),
                el('div', { class: 'ic-th-num', text: 'On order' }),
                el('div', { text: 'Counted' }),
                el('div', { class: 'ic-th-num', text: 'Adjust' }),
                el('div', {})
            ]),
            list
        ]));

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
            form.appendChild(el('div', { class: 'ic-warn', text: SHARED
                ? 'Your counts save to NetSuite as you go (see the status at the top). The administrator reviews everyone\'s lines together and posts the adjustment; these leave your sheet once that happens.'
                : 'Your role cannot post inventory adjustments. Export the sheet above and hand it to an administrator, who can key or import the counts.' }));
            if (SHARED) {
                form.appendChild(el('div', { class: 'ic-actions' }, [
                    el('button', { class: 'ic-btn is-ghost is-sm', type: 'button', text: 'Clear my lines on this device…', onclick: function () {
                        if (window.confirm('Remove all ' + state.order.length + ' lines you counted on this device? They are removed from the shared count too. Nothing is posted to NetSuite.')) {
                            state.order.slice().forEach(removeLine); state.memo = ''; saveSheet(); render();
                        }
                    } })
                ]));
            }
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
                    return { item: id, count: l.count, orders: l.orders || [], by: l.by || '' };
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

    function exportBar(what, linesFn) {
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
                    lines: linesFn ? linesFn() : state.order.map(function (id) { return state.sheet[id]; }).filter(Boolean).map(function (l) {
                        return { name: l.name, display: l.display || l.desc || '', vendor: l.vendor || '', onhand: l.onhand, count: l.count, orders: l.orders || [], by: l.by || '', when: fmtWhen(l.at) };
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
        if (d.unmarked && d.unmarked.length) {
            box.appendChild(el('div', { class: 'ic-warn', text: d.unmarked.length + ' count line' + (d.unmarked.length === 1 ? '' : 's') + ' posted but could not be marked as posted in NetSuite, so ' + (d.unmarked.length === 1 ? 'it' : 'they') + ' will show on the shared sheet again. Check the adjustment above before submitting them a second time.' }));
        }
        main.appendChild(box);
        main.appendChild(el('div', { class: 'ic-actions' }, [
            el('button', { class: 'ic-btn', type: 'button', text: 'Count more items', onclick: function () { state.done = null; state.q = ''; state.loaded = false; state.results = []; state.view = 'search'; render(); } }),
            d.shared ? el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Back to the shared sheet', onclick: function () { state.done = null; state.view = 'sheet'; render(); } })
                : (state.order.length ? el('button', { class: 'ic-btn is-ghost', type: 'button', text: 'Back to sheet (' + state.order.length + ')', onclick: function () { state.done = null; state.view = 'sheet'; render(); } }) : null)
        ]));
    }

    // ------------------------------------------------------------- render --

    function renderHeader(root) {
        var head = el('div', { class: 'ic-head' });
        var row = el('div', { class: 'ic-head-row' }, [
            el('div', { class: 'ic-title', text: BOOT.title || 'Inventory Count' }),
            SHARED ? el('span', { id: 'icSync', class: 'ic-sync', text: syncText() }) : null,
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
                state.allOrders = null; ordersCache = {}; state.session = null;
                sync.dirty = {}; sync.removed = {};
                render(); // the search view reloads the list for the new location
                reconcile();
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
            el('div', { class: 'ic-tabseg', role: 'tablist' }, [
                el('button', { class: 'ic-tab' + (state.view === 'search' ? ' is-on' : ''), type: 'button', text: 'Count', onclick: function () { state.view = 'search'; state.done = null; render(); } }),
                el('button', { class: 'ic-tab' + (state.view === 'orders' ? ' is-on' : ''), type: 'button', text: 'Open orders', onclick: function () { state.view = 'orders'; state.done = null; render(); } }),
                el('button', { class: 'ic-tab' + (state.view === 'sheet' ? ' is-on' : ''), type: 'button', onclick: function () { state.view = 'sheet'; state.done = null; state.submitError = ''; state.confirm = false; render(); } }, [
                    'Sheet', el('span', { id: 'icSheetBadge', class: 'ic-badge', text: String(sheetSize()) })
                ])
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
        else if (state.view === 'sheet') { if (SHARED && BOOT.canSubmit) { renderSharedSheetView(main); } else { renderSheetView(main); } }
        else if (state.view === 'orders') { renderOrdersView(main); }
        else { renderSearchView(main); }
        main.appendChild(el('div', { class: 'ic-footer', text: SHARED
            ? (BOOT.canSubmit
                ? 'Counts save to NetSuite as they are keyed, from every device. Submitting posts everyone\'s merged sheet as an Inventory Adjustment, as you.'
                : 'Counts save to NetSuite as you key them. An administrator reviews everyone\'s lines together and posts the adjustment.')
            : (BOOT.canSubmit
                ? 'Counts are saved in this browser until you submit. Submitting creates an Inventory Adjustment in NetSuite as you.'
                : 'Counts are saved in this browser. Your role cannot post adjustments, so export the sheet when you are done.') }));
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
        state.device = prefs.device || newDeviceId();
        state.deviceLabel = prefs.deviceLabel || '';
        loadSheet();
        savePrefs();
        render();
        if (SHARED) {
            reconcile();
            if (BOOT.canSubmit && !(BOOT.multiLoc && !state.loc)) { loadSession(); }
            // Pick up posts and discards while the page sits open, and never
            // let a tab close with counts that have not reached NetSuite.
            setInterval(function () { if (!document.hidden && !sync.inflight) { reconcile(); } }, 60000);
            document.addEventListener('visibilitychange', function () { if (!document.hidden) { reconcile(); } });
            window.addEventListener('beforeunload', function (ev) { if (pendingCount()) { ev.preventDefault(); ev.returnValue = ''; } });
        }
    }

    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); }
    else { init(); }
})();
`;

    return { onRequest: onRequest };
});
