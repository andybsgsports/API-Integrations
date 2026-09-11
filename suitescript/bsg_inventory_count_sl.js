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
 * URL actions (the page calls these itself):
 *   GET  ?action=search&q=<words>&loc=<id>&page=<n>&instock=T|F
 *                        -> one page of the item list (all items at the
 *                           location when q is blank) with on-hand
 *   GET  ?action=orders&item=<id>&loc=<id>   -> open sales-order lines for the item
 *   POST ?action=onhand   { ids:[], loc }                -> fresh on-hand per item
 *   POST ?action=submit   { loc, account, memo,
 *                           lines:[{ item, count, shelf, offshelf, offreason, offorders }] }
 *                                                       -> creates the adjustment
 *
 * @NApiVersion 2.1
 * @NScriptType Suitelet
 */
define(['N/search', 'N/record', 'N/runtime', 'N/url', 'N/cache', 'N/log'], function (search, record, runtime, url, cache, log) {

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
            loadDead();
            var isPost = request.method === 'POST';
            var body = isPost ? parseBody(request) : {};
            if (action === 'search') {
                out = searchItems(request.parameters.q, posInt(request.parameters.loc),
                    parseInt(request.parameters.page, 10) || 0, request.parameters.instock !== 'F');
            } else if (action === 'onhand') {
                out = isPost ? refreshOnHand(body) : { ok: false, error: 'POST required.' };
            } else if (action === 'orders') {
                out = openOrders(posInt(request.parameters.item), posInt(request.parameters.loc));
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
        var m = /:\s*([a-z0-9_]+)\.?\s*$/i.exec(msg);
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
    function openOrders(itemId, locId) {
        if (!itemId) { return { ok: false, error: 'Item required.' }; }
        if (!multiLocation()) { locId = null; }
        var out = [];
        function buildSpec() {
            var filters = [
                ['type', 'anyof', ['SalesOrd']], 'and',
                ['mainline', 'is', 'F'], 'and',
                ['item', 'anyof', [String(itemId)]], 'and',
                ['status', 'anyof', OPEN_SO_STATUSES]
            ];
            if (locId && !isDead('transaction', 'filter', 'location')) {
                filters.push('and');
                filters.push(['location', 'anyof', [String(locId)]]);
            }
            var cols = [search.createColumn({ name: 'trandate', sort: search.Sort.ASC }), 'tranid', 'quantity'];
            ['entity', 'statusref', 'quantityshiprecv', 'quantitycommitted'].forEach(function (c) {
                if (!isDead('transaction', 'column', c)) { cols.push(c); }
            });
            return { type: search.Type.TRANSACTION, filters: filters, columns: cols };
        }
        function tval(r, name) {
            if (isDead('transaction', 'column', name)) { return ''; }
            try { return r.getValue(name) || ''; } catch (e) { return ''; }
        }
        function ttxt(r, name) {
            if (isDead('transaction', 'column', name)) { return ''; }
            try { return r.getText(name) || ''; } catch (e) { return ''; }
        }
        withSearch('transaction', buildSpec, function (srch) {
            srch.run().getRange({ start: 0, end: 50 }).forEach(function (r) {
                var qty = Math.abs(parseFloat(tval(r, 'quantity')) || 0);
                var shipped = Math.abs(parseFloat(tval(r, 'quantityshiprecv')) || 0);
                var committed = Math.abs(parseFloat(tval(r, 'quantitycommitted')) || 0);
                var recUrl = '';
                try { recUrl = url.resolveRecord({ recordType: 'salesorder', recordId: r.id, isEditMode: false }); } catch (e) { /* cosmetic */ }
                out.push({
                    id: String(r.id),
                    tranid: String(tval(r, 'tranid')),
                    customer: ttxt(r, 'entity') || tval(r, 'entity'),
                    date: String(tval(r, 'trandate')),
                    qty: qty,
                    shipped: shipped,
                    remaining: round4(Math.max(0, qty - shipped)),
                    committed: committed,
                    status: ttxt(r, 'statusref') || '',
                    url: recUrl
                });
            });
        });
        return { ok: true, orders: out };
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

    var OFF_REASONS = ['Decorator', 'Customer pickup', 'Other'];
    function cleanRef(v) {
        return String(v == null ? '' : v).replace(/[^A-Za-z0-9#_\-. ]/g, '').trim().slice(0, 30);
    }

    // A line is { item, count } at minimum. When the page also sends the split
    // (shelf + offshelf, with a reason and the sales orders that explain it),
    // the count is recomputed from the split and the split goes on the line memo.
    function normalizeLines(raw) {
        var seen = {}, out = [];
        (Array.isArray(raw) ? raw : []).forEach(function (l) {
            if (!l) { return; }
            var id = posInt(l.item);
            if (!id) { return; }
            var count = parseFloat(l.count);
            var shelf = parseFloat(l.shelf), off = parseFloat(l.offshelf);
            var split = isFinite(shelf) && shelf >= 0 && isFinite(off) && off >= 0;
            if (split) { count = round4(shelf + off); }
            if (!isFinite(count) || count < 0) { return; }
            var line = { item: String(id), count: count };
            if (split && off > 0) {
                line.shelf = round4(shelf);
                line.offshelf = round4(off);
                line.offreason = OFF_REASONS.indexOf(l.offreason) !== -1 ? l.offreason : 'Off shelf';
                line.offorders = (Array.isArray(l.offorders) ? l.offorders : []).map(cleanRef).filter(Boolean).slice(0, 20);
            }
            if (seen[id]) { // same item twice: last wins
                var keep = seen[id];
                Object.keys(keep).forEach(function (k) { if (k !== 'item') { delete keep[k]; } });
                Object.keys(line).forEach(function (k) { keep[k] = line[k]; });
                return;
            }
            seen[id] = line;
            out.push(line);
        });
        return out;
    }

    function lineMemo(l) {
        if (l.offshelf > 0) {
            var so = l.offorders && l.offorders.length ? ': ' + l.offorders.join(', ') : '';
            return 'Counted ' + l.count + ' = ' + l.shelf + ' on shelf + ' + l.offshelf + ' off shelf (' + l.offreason + so + '); on hand ' + l.onhand;
        }
        return 'Counted ' + l.count + ' (on hand ' + l.onhand + ')';
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
            candidates.push({ item: l.item, name: it.name, count: l.count, onhand: it.onhand, shelf: l.shelf, offshelf: l.offshelf || 0, offreason: l.offreason, offorders: l.offorders });
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
            offReasons: OFF_REASONS,
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
.ic-head{position:sticky;top:0;z-index:5;background:var(--red);color:#fff;padding:10px 12px;box-shadow:0 1px 4px rgba(0,0,0,.25)}
.ic-head{padding-top:calc(10px + env(safe-area-inset-top,0px))}
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
.ic-line-top{display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.ic-lbl{font-size:12px;color:var(--muted);min-width:58px;text-align:right}
.ic-qty.is-sm{width:72px;min-height:40px;font-size:17px}
.ic-sel{min-height:40px;border:2px solid var(--line);border-radius:10px;padding:0 8px;background:#fff;font-size:14px;max-width:160px}
.ic-total{font-weight:800;font-size:16px;min-width:40px;text-align:center}
.ic-commit-warn{font-size:12px;color:var(--warn);background:#fff3df;border-radius:6px;padding:4px 8px;margin-top:6px}
.ic-link{background:none;border:0;color:var(--focus);font-weight:700;padding:4px 0;font-size:13px;text-decoration:underline;cursor:pointer}
.ic-commit-link{background:none;border:0;padding:0 2px;font:inherit;font-weight:700;color:var(--focus);text-decoration:underline;cursor:pointer;min-height:28px}
.ic-orders{margin-top:6px;border-top:1px dashed var(--line);padding-top:6px;font-size:13px}
.ic-order{display:flex;align-items:flex-start;gap:8px;padding:4px 0}
.ic-order input{width:22px;height:22px;margin:1px 0 0;flex:0 0 auto}
.ic-order a{color:var(--focus);font-weight:700}
.ic-order small{color:var(--muted)}
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
.ic-footer{text-align:center;color:var(--muted);font-size:12px;padding:20px 0}
.ic-footer{padding-bottom:calc(20px + env(safe-area-inset-bottom,0px))}
.ic-listhead{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 0 8px 2px;flex-wrap:wrap}
.ic-count{font-size:13px;color:var(--muted);min-height:20px}
.ic-seg{display:inline-flex;border:2px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}
.ic-segbtn{border:0;background:transparent;padding:8px 12px;min-height:40px;font-weight:700;color:var(--muted)}
.ic-segbtn.is-on{background:var(--red);color:#fff}
@media (max-width:480px){.ic-row{flex-wrap:wrap}.ic-ctl{width:100%;justify-content:flex-end;align-items:center;flex-direction:row}.ic-ctl .ic-qty{flex:1 1 auto}.ic-line-ctl{width:100%;flex-direction:column;align-items:stretch}.ic-line-top{justify-content:flex-start}.ic-lbl{min-width:72px;text-align:left}.ic-sel{flex:1 1 auto;max-width:none}.ic-user{display:none}.ic-loc select{max-width:100%;flex:1 1 auto}.ic-loc{flex:1 1 100%}}
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
    // count = shelf + off shelf, always. Older saved lines only carry count.
    function syncCount(line) {
        if (line.shelf == null) { line.shelf = Number(line.count) || 0; }
        if (line.offshelf == null) { line.offshelf = 0; }
        line.count = round4((Number(line.shelf) || 0) + (Number(line.offshelf) || 0));
        return line.count;
    }
    function belowCommitted(line) {
        var c = Number(line.committed) || 0;
        return !line.blocked && c > 0 && (Number(line.count) || 0) < c;
    }

    function lineFor(item) {
        return state.sheet[item.id] || null;
    }
    function setCount(item, count) {
        var line = state.sheet[item.id];
        if (!line) {
            line = { id: item.id, name: item.name, display: item.display, desc: item.desc, upc: item.upc, vendor: item.vendor, onhand: item.onhand, available: item.available, committed: item.committed, onorder: item.onorder, blocked: item.blocked || '', shelf: 0, offshelf: 0, offreason: '', offorders: [] };
            state.sheet[item.id] = line;
            state.order.push(item.id);
        }
        // The Qty box on a result row is what was found on the shelf; anything
        // off shelf (decorator, pickup) is added on the sheet and kept here.
        line.shelf = count;
        syncCount(line);
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
        var lines = 0, pos = 0, neg = 0, blocked = 0, below = 0;
        state.order.forEach(function (id) {
            var l = state.sheet[id];
            if (!l) { return; }
            syncCount(l);
            lines++;
            if (l.blocked) { blocked++; return; }
            if (belowCommitted(l)) { below++; }
            var d = (Number(l.count) || 0) - (Number(l.onhand) || 0);
            if (d > 0) { pos += d; } else { neg += -d; }
        });
        return { lines: lines, pos: pos, neg: neg, blocked: blocked, below: below };
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

    // The open sales orders for one item, loaded on first open. With opts
    // (isTicked / onTick) each order gets a checkbox that moves its unshipped
    // units into Off shelf -- that is the Sheet; the search page shows the
    // same list read-only, since the line may not be on the sheet yet.
    function ordersPanel(itemId, opts) {
        var box = el('div', { class: 'ic-orders', hidden: true });
        function paint() {
            box.innerHTML = '';
            var cached = ordersCache[itemId];
            if (!cached) {
                box.appendChild(el('div', { class: 'ic-progress', text: 'Loading open orders…' }));
                apiGet('orders', { item: itemId, loc: state.loc || '' }).then(function (res) {
                    ordersCache[itemId] = (!res || !res.ok) ? { error: (res && res.error) || 'Could not load orders.' } : { orders: res.orders || [] };
                    if (!box.hidden) { paint(); }
                });
                return;
            }
            if (cached.error) { box.appendChild(el('div', { class: 'ic-error', text: cached.error })); return; }
            if (!cached.orders.length) {
                box.appendChild(el('div', { text: 'No open sales orders for this item' + (state.loc ? ' at ' + locName(state.loc) : '') + '.' }));
                return;
            }
            box.appendChild(el('div', { class: 'ic-meta', text: opts
                ? 'Tick the orders whose units are off the shelf (at the decorator, staged for pickup). Their unshipped quantity is added to Off shelf.'
                : 'Open sales orders still owing this item. Add the item, then mark off-shelf units on the Sheet tab.' }));
            cached.orders.forEach(function (o) {
                var num = String(o.tranid || o.id);
                var ref = /^so/i.test(num) ? num : 'SO ' + num;
                var kids = [];
                if (opts) {
                    var cb = el('input', { type: 'checkbox', 'aria-label': 'Off shelf: ' + ref });
                    cb.checked = !!opts.isTicked(ref);
                    cb.addEventListener('change', function () { opts.onTick(o, ref, cb.checked); });
                    kids.push(cb);
                }
                kids.push(el('span', {}, [
                    o.url ? el('a', { href: o.url, target: '_blank', rel: 'noopener', text: ref }) : el('b', { text: ref }),
                    o.customer ? ' · ' + o.customer : '',
                    el('br'),
                    el('small', { text: fmt(o.remaining) + ' unshipped of ' + fmt(o.qty) + (o.status ? ' · ' + o.status : '') + (o.date ? ' · ' + o.date : '') })
                ]));
                box.appendChild(el(opts ? 'label' : 'div', { class: 'ic-order' }, kids));
            });
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

    // "Committed: 10" with the number clickable when there is something to see.
    function committedEl(committed, panel) {
        if (!(Number(committed) > 0)) { return el('b', { text: fmt(committed) }); }
        return el('button', { class: 'ic-commit-link', type: 'button', text: fmt(committed), 'aria-label': 'Show open orders (' + fmt(committed) + ' committed)', onclick: function () { panel.toggle(); } });
    }

    function resultRow(item) {
        var line = lineFor(item);
        var panel = ordersPanel(item.id, null);
        var row = el('div', { class: 'ic-row' + (line ? ' is-onsheet' : '') + (item.blocked ? ' is-blocked' : ''), 'data-row-for': item.id });
        var meta = [];
        if (item.vendor) { meta.push(item.vendor); }
        if (item.upc) { meta.push('UPC ' + item.upc); }
        var info = el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name', text: item.name }),
            (item.display || item.desc) ? el('div', { class: 'ic-desc', text: item.display && item.desc && item.display !== item.desc ? item.display + ' - ' + item.desc : (item.display || item.desc) }) : null,
            el('div', { class: 'ic-meta' }, [meta.length ? meta.join(' · ') + ' · ' : '', 'On hand: ', el('b', { text: fmt(item.onhand) }), ' · Avail: ', el('b', { text: fmt(item.available) }), ' · Committed: ', committedEl(item.committed, panel), ' · On order: ', el('b', { text: fmt(item.onorder) })]),
            item.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + item.blocked + ') - adjust manually' }) : null,
            line ? el('div', { class: 'ic-onsheet', 'data-onsheet-for': item.id, text: 'On sheet: ' + fmt(line.count) }) : null,
            panel.box
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
        main.appendChild(el('p', { class: 'ic-hint', text: 'Every item at this location is listed below; In stock keeps those with quantity on hand, available, or on order. Search to jump to one, key the counted quantity and press Add (Enter jumps to the next item). Counts wait on the Sheet tab until you submit.' }));
        main.appendChild(el('div', { id: 'icResults' }));
        renderResults();
        if (!state.loaded && !state.searching && !state.searchError) { runSearch(true); }
    }

    // -------------------------------------------------------------- sheet --

    function sheetRow(line) {
        syncCount(line);
        var row = el('div', { class: 'ic-row' + (line.error ? ' has-error' : '') });
        var delta = el('div', { class: 'ic-delta' });
        var total = el('span', { class: 'ic-total' });
        var warn = el('div', { class: 'ic-commit-warn' });
        function paint() {
            syncCount(line);
            total.textContent = fmt(line.count);
            if (line.blocked) { delta.className = 'ic-delta is-zero'; delta.textContent = 'n/a'; }
            else {
                var d = round4((Number(line.count) || 0) - (Number(line.onhand) || 0));
                delta.className = 'ic-delta ' + (d > 0 ? 'is-pos' : d < 0 ? 'is-neg' : 'is-zero');
                delta.textContent = signed(d);
            }
            if (belowCommitted(line)) {
                warn.textContent = fmt(line.committed) + ' committed to open sales orders but only ' + fmt(line.count) + ' counted. Check Open orders: units at the decorator or waiting for pickup belong in Off shelf.';
                warn.hidden = false;
            } else { warn.hidden = true; }
            saveSheet(); paintTotals();
        }

        // Shelf count with -/+ steppers.
        var shelf = el('input', { class: 'ic-qty is-sm', type: 'text', inputmode: 'decimal', autocomplete: 'off', value: fmt(line.shelf), 'aria-label': 'Shelf count for ' + line.name });
        function applyShelf(v) {
            var c = parseCount(v);
            if (c === null) { return; }
            line.shelf = c; line.error = ''; row.classList.remove('has-error'); paint();
        }
        shelf.addEventListener('input', function () { applyShelf(shelf.value); });
        shelf.addEventListener('blur', function () { shelf.value = fmt(line.shelf); });
        var minus = el('button', { class: 'ic-step', type: 'button', text: '−', 'aria-label': 'Minus one', onclick: function () {
            var c = Math.max(0, (Number(line.shelf) || 0) - 1); shelf.value = fmt(c); applyShelf(c);
        } });
        var plus = el('button', { class: 'ic-step', type: 'button', text: '+', 'aria-label': 'Plus one', onclick: function () {
            var c = (Number(line.shelf) || 0) + 1; shelf.value = fmt(c); applyShelf(c);
        } });

        // Off-shelf quantity + where it is.
        var off = el('input', { class: 'ic-qty is-sm', type: 'text', inputmode: 'decimal', autocomplete: 'off', value: line.offshelf ? fmt(line.offshelf) : '', placeholder: '0', 'aria-label': 'Off-shelf quantity for ' + line.name });
        off.addEventListener('input', function () {
            var c = off.value.trim() === '' ? 0 : parseCount(off.value);
            if (c === null) { return; }
            line.offshelf = c; paint();
        });
        off.addEventListener('blur', function () { off.value = line.offshelf ? fmt(line.offshelf) : ''; });
        var reason = el('select', { class: 'ic-sel', 'aria-label': 'Where the off-shelf units are', onchange: function (ev) { line.offreason = ev.target.value; saveSheet(); } });
        reason.appendChild(el('option', { value: '', text: 'Where?' }));
        (BOOT.offReasons || ['Decorator', 'Customer pickup', 'Other']).forEach(function (r) {
            var o = el('option', { value: r, text: r });
            if (r === line.offreason) { o.selected = true; }
            reason.appendChild(o);
        });

        var remove = el('button', { class: 'ic-x', type: 'button', text: '×', 'aria-label': 'Remove ' + line.name, onclick: function () {
            removeLine(line.id); render();
        } });

        // Open sales orders for this item. Ticking one adds its unshipped
        // quantity to Off shelf and records the SO number for the memo.
        var panel = ordersPanel(line.id, {
            isTicked: function (ref) { return (line.offorders || []).indexOf(ref) !== -1; },
            onTick: function (o, ref, checked) {
                line.offorders = (line.offorders || []).filter(function (x) { return x !== ref; });
                var current = Number(line.offshelf) || 0;
                if (checked) { line.offorders.push(ref); line.offshelf = round4(current + o.remaining); }
                else { line.offshelf = round4(Math.max(0, current - o.remaining)); }
                off.value = line.offshelf ? fmt(line.offshelf) : '';
                if (checked && !line.offreason) { line.offreason = (BOOT.offReasons || ['Decorator'])[0]; reason.value = line.offreason; }
                paint();
            }
        });
        var ordersBox = panel.box;
        var ordersBtn = el('button', { class: 'ic-link', type: 'button', text: 'Open orders' + (Number(line.committed) > 0 ? ' (' + fmt(line.committed) + ' committed)' : ''), onclick: function () { panel.toggle(); } });

        row.appendChild(el('div', { class: 'ic-info' }, [
            el('div', { class: 'ic-name', text: line.name }),
            (line.display || line.desc) ? el('div', { class: 'ic-desc', text: line.display || line.desc }) : null,
            el('div', { class: 'ic-meta' }, ['On hand: ', el('b', { text: fmt(line.onhand) }), ' · Avail: ', el('b', { text: fmt(line.available) }), ' · Committed: ', committedEl(line.committed, panel), ' · On order: ', el('b', { text: fmt(line.onorder) }), line.vendor ? ' · ' + line.vendor : '']),
            line.blocked ? el('div', { class: 'ic-flag', text: 'Needs inventory detail (' + line.blocked + ') - will be skipped' }) : null,
            line.error ? el('div', { class: 'ic-error', text: line.error }) : null,
            warn,
            line.blocked ? null : ordersBtn,
            ordersBox
        ]));
        row.appendChild(el('div', { class: 'ic-line-ctl' }, [
            el('div', { class: 'ic-line-top' }, [el('span', { class: 'ic-lbl', text: 'Shelf' }), minus, shelf, plus]),
            el('div', { class: 'ic-line-top' }, [el('span', { class: 'ic-lbl', text: 'Off shelf' }), off, reason]),
            el('div', { class: 'ic-line-top' }, [el('span', { class: 'ic-lbl', text: 'Count' }), total, delta, remove])
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
        if (state.submitError) { form.appendChild(el('div', { class: 'ic-error', text: state.submitError })); }
        if (state.submitting) {
            form.appendChild(el('div', { class: 'ic-progress', text: state.progress || 'Submitting…' }));
        } else if (state.confirm) {
            var live = t.lines - t.blocked;
            form.appendChild(el('div', { class: 'ic-confirm' }, [
                el('p', {}, [el('b', { text: 'Create an Inventory Adjustment for ' + live + ' line' + (live === 1 ? '' : 's') + (state.loc ? ' at ' + locName(state.loc) : '') + '?' })]),
                el('p', { text: 'Each item’s on-hand becomes the count you entered (shelf plus off shelf: ' + '+' + fmt(t.pos) + ' / −' + fmt(t.neg) + ' units). Items whose count already matches are left out.' + (t.blocked ? ' ' + t.blocked + ' flagged line' + (t.blocked === 1 ? ' is' : 's are') + ' skipped.' : '') + (t.below ? ' ' + t.below + ' line' + (t.below === 1 ? ' is' : 's are') + ' below the quantity committed to open sales orders.' : '') }),
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
                    var l = state.sheet[id]; syncCount(l);
                    return { item: id, count: l.count, shelf: l.shelf, offshelf: l.offshelf, offreason: l.offreason || '', offorders: l.offorders || [] };
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
        // Attach before rendering the view: the list renderer finds its container
        // by id, which only works once the section is in the document.
        root.appendChild(main);
        if (state.view === 'done') { renderDoneView(main); }
        else if (state.view === 'sheet') { renderSheetView(main); }
        else { renderSearchView(main); }
        main.appendChild(el('div', { class: 'ic-footer', text: 'Counts are saved in this browser until you submit. Submitting creates an Inventory Adjustment in NetSuite as you.' }));
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
