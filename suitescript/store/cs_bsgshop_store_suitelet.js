/**
 * BSG Ordering Site - public storefront Suitelet.
 *
 * Deployed "Available Without Login" so customers with no NetSuite account can
 * browse the catalog and submit an order or a quote. It is the ONLY entry point
 * (RESTlets can't be anonymous), so it serves both the HTML store page (GET with
 * no action) and a small JSON API (GET/POST with an `action`) that the inlined
 * client script calls.
 *
 * Catalog is driven entirely by native item "Web Store" fields -- an item shows
 * up when its native "Display in Web Store" (isonline) box is checked. See
 * docs/ORDERING_SITE.md.
 *
 * IMPORTANT (setup): the deployment must "Execute as" a role that can read Items
 * and Customers and create Sales Orders / Estimates, and CONFIG.SUBMIT_AUTHOR_-
 * EMPLOYEE_ID should be a real employee, or confirmation emails won't send.
 *
 * @NApiVersion 2.1
 * @NScriptType Suitelet
 */
define([
    'N/search',
    'N/record',
    'N/file',
    'N/url',
    'N/email',
    'N/runtime',
    'N/cache',
    'N/log',
    './lib/bsgshop.constants.js',
    './lib/bsgshop.logo.js',
    './lib/bsgshop.image_map.js',
    './lib/bsgshop.sample_art.js'
], function (search, record, file, url, email, runtime, cache, log, C, LOGO, IMGMAP, ART) {

    var CLIENT_SCRIPT_PATH = '/SuiteScripts/bsg-ordering-site/cs_bsgshop_store_client.js';

    // ---------------------------------------------------------------- entry --

    // Extract a loggable string from ANY thrown value. Some NetSuite errors are raw
    // Java adapter objects (ScriptNullObjectAdapter) where merely reading .message
    // or .stack RE-THROWS -- which detonates the catch block itself and lets the
    // original error escape as an HTML Notice page. Every catch below must go
    // through this, never touch e.message/e.stack directly.
    function safeErr(e) {
        var out = 'Unexpected error';
        try { if (e && e.stack) { return String(e.stack); } } catch (ignore) { /* java adapter */ }
        try { if (e && e.message) { return String(e.message); } } catch (ignore2) { /* java adapter */ }
        try { out = String(e); } catch (ignore3) { /* even toString can throw */ }
        return out;
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

        // Sample-art images stream as binary, so they bypass the JSON wrapper below.
        if (action === C.ACTION.ART) {
            serveArt(request, response);
            return;
        }

        if (action) {
            // Absolute last line of defense: whatever happens, an API call returns
            // JSON, never a NetSuite HTML error page (which the client can't parse).
            try {
                handleApi(action, request, response);
            } catch (e) {
                log.error({ title: 'bsgshop onRequest fatal: ' + action, details: safeErr(e) });
                try { response.addHeader({ name: 'Content-Type', value: 'application/json' }); } catch (eh) { /* headers may be sent */ }
                response.write(JSON.stringify({ ok: false, error: 'The store service hit an unexpected error. Please try again.' }));
            }
            return;
        }
        renderStorePage(response);
    }

    // ---------------------------------------------------------- sample art --
    // BSG's printed sample-art books, one image per coded design. Shoppers browse
    // them to tell their rep "build me something like FB7"; the rep recreates the
    // style with the school's own identity (they are references, not print files).

    // Whitelist of servable names, built once per execution from the manifest. A
    // public endpoint must never turn a caller-supplied string into a File Cabinet
    // path -- only names this store actually published can be requested.
    var artAllowed = null;
    function artIsAllowed(name) {
        if (!artAllowed) {
            artAllowed = {};
            (ART.GROUPS || []).forEach(function (g) {
                (g.designs || []).forEach(function (d) { artAllowed[d.f] = true; });
            });
        }
        return !!artAllowed[name];
    }

    // The storefront is Available Without Login, so images are streamed through it
    // rather than linked at their File Cabinet URL (which would additionally require
    // the folder itself to be public).
    function serveArt(request, response) {
        try {
            var name = String((request.parameters && request.parameters.f) || '');
            if (!artIsAllowed(name)) {
                response.write('');
                return;
            }
            var f = file.load({ id: ART.FOLDER + name });
            // Design art is immutable once published -- let the browser keep it.
            response.addHeader({ name: 'Cache-Control', value: 'public, max-age=604800' });
            response.writeFile({ file: f, isInline: true });
        } catch (e) {
            log.error({ title: 'bsgshop art', details: safeErr(e) });
            try { response.write(''); } catch (e2) { /* headers already sent */ }
        }
    }

    function sampleArtBoot() {
        if (!C.CONFIG.SAMPLE_ART_ENABLED) { return []; }
        return (ART.GROUPS || []).map(function (g) {
            return {
                sport: g.sport,
                designs: (g.designs || []).map(function (d) {
                    return { code: d.c, file: d.f };
                })
            };
        });
    }

    function getAction(request) {
        if (request.method === 'POST') {
            var body = parseBody(request);
            return body && body.action ? body.action : (request.parameters.action || null);
        }
        return request.parameters.action || null;
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
        // Never let a browser/proxy cache an API response -- a cached error looks
        // exactly like "the fix didn't work" after a redeploy.
        response.addHeader({ name: 'Cache-Control', value: 'no-store, no-cache, must-revalidate, max-age=0' });
        var body;
        try {
            var out;
            if (action === C.ACTION.PRODUCTS) {
                out = cachedJson(catalogCacheKey(request.parameters), C.CONFIG.CACHE_CATALOG_TTL,
                    function () { return listProducts(request.parameters); });
            } else if (action === C.ACTION.PRODUCT) {
                out = cachedJson('prod:v1:' + String(request.parameters.id || '').slice(0, 200), C.CONFIG.CACHE_DETAIL_TTL,
                    function () { return getProduct(request.parameters.id); });
            } else if (action === C.ACTION.FILTERS) {
                out = cachedJson(filtersCacheKey(request.parameters), C.CONFIG.CACHE_FILTERS_TTL,
                    function () { return listFilters(request.parameters); });
            } else if (action === C.ACTION.SCHOOLS) {
                out = searchSchools(request.parameters.q);
            } else if (action === C.ACTION.SUBMIT) {
                out = submitOrder(parseBody(request));
            } else if (action === C.ACTION.TRACK) {
                out = trackEvent(parseBody(request));
            } else {
                out = { ok: false, error: 'Unknown action.' };
            }
            // Serialize INSIDE the try: if JSON.stringify itself throws, we still
            // return JSON (an error object) rather than letting the platform emit
            // an HTML error page the client can't parse.
            body = JSON.stringify(out);
        } catch (e) {
            log.error({ title: 'bsgshop API error: ' + action, details: safeErr(e) });
            // Return a GENERIC message to the anonymous client; the real error
            // (including its stack) stays in the server log above. Never expose
            // internal paths / field ids / stack traces to a public caller.
            body = JSON.stringify({ ok: false, error: 'The store service hit an unexpected error. Please try again.' });
        }
        response.write(body);
    }

    // ------------------------------------------------------------- catalog --

    // The rule for what appears on the storefront, per CONFIG.CATALOG_MODE. Default
    // ('inventory') = active and in stock; images never gate listing in this mode.
    //
    // Matrix handling: children (one row per size/color) are ALWAYS excluded so the
    // catalog shows ONE card per product; the parent lists instead. Parents carry no
    // stock themselves (inventory lives on the children), so in inventory mode the
    // qty filter is (qty > 0 OR is-matrix-parent) -- the detail view then shows
    // per-size/color availability.
    // Every place "in stock" is checked at the search-filter level: native Quantity
    // Available OR any per-vendor Qty Available field (see QTY_FIELDS in
    // lib/bsgshop.constants.js) OR is a matrix parent. Drop-ship/vendor-synced
    // items often track real availability only in their vendor-specific qty field
    // -- without this OR, they'd look permanently out of stock and, worse, be
    // excluded from the catalog outright by a native-only filter.
    function qtyAvailabilityFilter(includeVendorQty) {
        var parts = [[C.ITEM_FIELD.QTY_AVAILABLE, 'greaterthan', 0]];
        if (includeVendorQty) {
            C.QTY_FIELDS.forEach(function (key) {
                parts.push('OR', [C.ITEM_FIELD[key], 'greaterthan', 0]);
            });
        }
        return parts;
    }

    function catalogBaseFilters(includeVendorQty) {
        var f = [['isinactive', 'is', 'F'], 'AND', [C.ITEM_FIELD.MATRIX_CHILD, 'is', 'F']];
        if (C.CONFIG.CATALOG_MODE === 'web_store_flag') {
            f.push('AND', [C.ITEM_FIELD.IS_ONLINE, 'is', 'T']);
        } else {
            f.push('AND', qtyAvailabilityFilter(includeVendorQty).concat(['OR', [C.ITEM_FIELD.MATRIX, 'is', 'T']]));
            if (C.CONFIG.CATALOG_MODE === 'inventory_image') {
                f.push('AND', [C.ITEM_FIELD.DISPLAY_IMAGE, 'isnotempty']);
            }
        }
        return f;
    }

    // Whether a single item (already looked up) is allowed to be viewed. Detail is a
    // bit more lenient than the list, so an item can still be opened even if it just
    // went to 0 on hand (the badge then shows "Out of Stock").
    function productQualifies(f) {
        if (C.CONFIG.CATALOG_MODE === 'web_store_flag') {
            return f[C.ITEM_FIELD.IS_ONLINE] === true;
        }
        if (C.CONFIG.CATALOG_MODE === 'inventory_image') {
            return !!firstId(f[C.ITEM_FIELD.DISPLAY_IMAGE]);
        }
        return true;
    }

    // File types NetSuite reports for image files in the File Cabinet.
    var IMAGE_FILE_TYPES = {
        JPGIMAGE: 1, PJPGIMAGE: 1, PNGIMAGE: 1, GIFIMAGE: 1, BMPIMAGE: 1, TIFFIMAGE: 1
    };
    function rowIsImageFile(ftype, name) {
        if (ftype && IMAGE_FILE_TYPES[String(ftype).toUpperCase()]) { return true; }
        return /\.(png|jpe?g|gif|webp|bmp|tiff?)(\?|$)/i.test(String(name || ''));
    }

    // PRIMARY image source: the image file ATTACHED to each item (Files subtab).
    // One batched search per page covers every item, so no per-item record loads
    // are needed. Matched by file TYPE first (an attached photo may not have a
    // clean extension in its name), then by extension. Best-effort: if the file
    // join isn't available in this account, items just fall through.
    function attachedImageMap(itemIds) {
        var map = {};
        if (!itemIds.length) { return map; }
        try {
            search.create({
                type: 'item',
                filters: [['internalid', 'anyof', itemIds]],
                columns: [
                    search.createColumn({ name: 'name', join: 'file' }),
                    search.createColumn({ name: 'url', join: 'file' }),
                    search.createColumn({ name: 'filetype', join: 'file' })
                ]
            }).run().each(function (r) {
                if (map[r.id]) { return true; }
                var ftype = r.getValue({ name: 'filetype', join: 'file' }) || '';
                var name = r.getValue({ name: 'name', join: 'file' }) || '';
                var rel = r.getValue({ name: 'url', join: 'file' }) || '';
                if (rel && rowIsImageFile(ftype, name)) { map[r.id] = absoluteUrl(rel); }
                return true;
            });
        } catch (e) {
            log.debug({ title: 'bsgshop: attached-file image lookup unavailable', details: safeErr(e) });
        }
        return map;
    }

    // For matrix PARENTS (which carry no image themselves), find the first attached
    // image among their size/color CHILDREN. Keyed by parent id. One batched search.
    function childAttachedImageMap(parentIds) {
        var map = {};
        if (!parentIds.length) { return map; }
        try {
            search.create({
                type: 'item',
                filters: [[C.ITEM_FIELD.PARENT, 'anyof', parentIds]],
                columns: [
                    search.createColumn({ name: C.ITEM_FIELD.PARENT, sort: search.Sort.ASC }),
                    search.createColumn({ name: 'name', join: 'file' }),
                    search.createColumn({ name: 'url', join: 'file' }),
                    search.createColumn({ name: 'filetype', join: 'file' })
                ]
            }).run().each(function (r) {
                var pid = r.getValue({ name: C.ITEM_FIELD.PARENT });
                if (!pid || map[pid]) { return true; }
                var ftype = r.getValue({ name: 'filetype', join: 'file' }) || '';
                var name = r.getValue({ name: 'name', join: 'file' }) || '';
                var rel = r.getValue({ name: 'url', join: 'file' }) || '';
                if (rel && rowIsImageFile(ftype, name)) { map[pid] = absoluteUrl(rel); }
                return true;
            });
        } catch (e) {
            log.debug({ title: 'bsgshop: child attached-file image lookup unavailable', details: safeErr(e) });
        }
        return map;
    }

    // ---- server-side response cache (page speed) ----
    // Account-wide N/cache of API JSON responses: the grouped catalog runs the
    // FULL item search per request, so without this every shopper paid seconds
    // per page. With it, only the first request after a TTL computes; everyone
    // else is served the cached JSON. Only ok:true responses are cached.
    var apiCache;
    function storeCache() {
        if (!apiCache) { apiCache = cache.getCache({ name: 'bsgshop_api', scope: cache.Scope.PROTECTED }); }
        return apiCache;
    }
    function cachedJson(key, ttl, build) {
        if (!(ttl > 0)) { return build(); }
        try {
            var hit = storeCache().get({ key: key });
            if (hit) { return JSON.parse(hit); }
        } catch (eGet) { /* cache miss/unavailable -> compute below */ }
        var out = build();
        if (out && out.ok) {
            try { storeCache().put({ key: key, value: JSON.stringify(out), ttl: ttl }); }
            catch (ePut) { /* value too large or cache down -> serve uncached */ }
        }
        return out;
    }
    // Key covers every parameter that changes the catalog response.
    function catalogCacheKey(params) {
        var parts = ['cat:v1', String(params.page || '1'), String(params.size || ''),
            String(params.q || '').toLowerCase().slice(0, 60)];
        C.CONFIG.FILTERS.forEach(function (f) { parts.push(String(params[f.key] || '')); });
        return parts.join('|');
    }

    // includeVendorQty=false drops the per-vendor qty OR-chain down to just the
    // native field, as a safety net: the filter (unlike the SELECT columns) is
    // shared across every column-attempt retry, so one bad vendor qty field id
    // would otherwise fail every attempt, not just the richest one.
    // exceptKey (optional): skip that facet's own selection. Used when listing a
    // facet's available options -- a facet must not constrain itself, or choosing
    // "Softball" would collapse the Sport list to just Softball and you could
    // never switch sports.
    function catalogFilters(params, includeVendorQty, exceptKey) {
        var q = (params.q || '').trim();
        var filters = catalogBaseFilters(includeVendorQty !== false);
        // Header filter dropdowns (Department / Class / Category / Vendor). Each is
        // an "anyof" on its item field; unknown/blank selections are ignored.
        C.CONFIG.FILTERS.forEach(function (fdef) {
            if (exceptKey && fdef.key === exceptKey) { return; }
            var val = (params[fdef.key] || '').trim();
            if (val) { filters.push('AND', [fdef.field, 'anyof', val]); }
        });
        if (q) {
            // Search across item number, the NATIVE display name, sales description,
            // and UPC. All four are legal search FILTERS -- the web-store display
            // name (storedisplayname) is NOT (it's column-only) and, because the
            // filter set is shared across every column-attempt retry, having it here
            // failed every attempt and broke search entirely.
            filters.push('AND', [
                [C.ITEM_FIELD.ITEM_ID, 'contains', q], 'OR',
                [C.ITEM_FIELD.NATIVE_DISPLAY_NAME, 'contains', q], 'OR',
                [C.ITEM_FIELD.SALES_DESCRIPTION, 'contains', q], 'OR',
                [C.ITEM_FIELD.UPC, 'contains', q]
            ]);
        }
        return filters;
    }

    // Field ids for BSG's actual per-vendor image/pricing/spec custom fields (see
    // lib/bsgshop.constants.js IMAGE_FILE_FIELDS / IMAGE_URL_FIELDS / MSRP_FIELDS /
    // GTIN_FIELDS), resolved once here so every column-set attempt below and every
    // lookupFields call can just spread this array in.
    var VENDOR_IMAGE_PRICE_FIELDS = C.IMAGE_FILE_FIELDS.concat(C.IMAGE_URL_FIELDS, C.MSRP_FIELDS)
        .map(function (key) { return C.ITEM_FIELD[key]; })
        .concat([C.ITEM_FIELD.ON_SALE])
        // Precomputed image URL (nightly M/R) -- a plain, safe TEXT column the catalog
        // reads first so a page can render with no per-item record loads. Only when
        // enabled; blank falls through to the live resolver.
        .concat(C.CONFIG.USE_PRECOMPUTED_IMAGES ? [C.ITEM_FIELD.SHOP_IMAGE_URL] : [])
        // MAP (min advertised price) column so displayed prices can be floored at it.
        .concat(C.CONFIG.MAP_ENFORCE ? [C.ITEM_FIELD.MAP_PRICE] : []);
    var VENDOR_SPEC_FIELDS = C.GTIN_FIELDS.map(function (key) { return C.ITEM_FIELD[key]; })
        .concat([C.ITEM_FIELD.SS_BRAND, C.ITEM_FIELD.SS_WEIGHT]);
    var VENDOR_QTY_FIELDS = C.QTY_FIELDS.map(function (key) { return C.ITEM_FIELD[key]; });

    // Not every item field is a legal SEARCH column in every account (an invalid one
    // makes the entire search throw, which previously surfaced as a silently empty
    // store). Try the richest column set first, then progressively safer ones; log
    // what got dropped so the root cause is visible in the script log.
    // NOTE: never request `storedisplayimage` here -- it is not a valid saved-search
    // column in this account and throws, taking the whole catalog down with it (the
    // image is read off the record instead). Every attempt MUST include baseprice so
    // price never falls to $0, and attempt 2 is native-only so it's a guaranteed-valid
    // fallback if any custom field (atlas image / vendor fields) is unsearchable.
    var NATIVE_SAFE_COLUMNS = [C.ITEM_FIELD.DISPLAY_NAME, C.ITEM_FIELD.NATIVE_DISPLAY_NAME,
        C.ITEM_FIELD.STORE_DESCRIPTION, C.ITEM_FIELD.SALES_DESCRIPTION, C.CONFIG.PRICE_SEARCH_FIELD,
        C.ITEM_FIELD.QTY_AVAILABLE, C.ITEM_FIELD.MATRIX, C.ITEM_FIELD.TYPE];
    var CATALOG_COLUMN_ATTEMPTS = [
        NATIVE_SAFE_COLUMNS.concat(VENDOR_IMAGE_PRICE_FIELDS, VENDOR_SPEC_FIELDS, VENDOR_QTY_FIELDS),
        NATIVE_SAFE_COLUMNS,
        [C.ITEM_FIELD.SALES_DESCRIPTION, C.CONFIG.PRICE_SEARCH_FIELD, C.ITEM_FIELD.QTY_AVAILABLE, C.ITEM_FIELD.TYPE]
    ];

    function listProducts(params) {
        var res = C.CONFIG.GROUP_FLAT_VARIANTS ? buildGroupedCatalog(params) : buildFlatCatalog(params);
        // A filter combination that matches nothing is a dead end, and shoppers
        // read it as "they don't stock this" rather than "my filters are too
        // narrow". Work out which single filter to drop to get product back, so
        // the empty page can offer that instead of nothing. Only costs a search
        // when the page is already empty.
        if (res && res.ok && (!res.products || !res.products.length)) {
            try { res.broaden = broadenSuggestion(params || {}); }
            catch (e) { res.broaden = null; }
        }
        return res;
    }

    // Default catalog: reads up to MAX_RAW_CATALOG_ROWS raw items, groups any that
    // share a product name into one card (see "flat-variant grouping" above,
    // handles both true matrix parents [already one row each] and vendor-flat
    // size/color siblings), then paginates the resulting CARDS.
    // Per-page size from the request, validated against the configured whitelist
    // (25/50/100); anything else falls back to the default PAGE_SIZE.
    function resolvePageSize(params) {
        var opts = C.CONFIG.PAGE_SIZE_OPTIONS || [];
        var n = parseInt(params && params.size, 10);
        return opts.indexOf(n) !== -1 ? n : C.CONFIG.PAGE_SIZE;
    }

    function buildGroupedCatalog(params) {
        var page = Math.max(1, parseInt(params.page, 10) || 1);

        var rows = null, lastErr = null;
        for (var a = 0; a < CATALOG_COLUMN_ATTEMPTS.length && !rows; a++) {
            try {
                // Vendor qty fields only trusted on the richest (first) attempt --
                // the filter, unlike the columns, is otherwise shared across
                // retries, so one bad field id would fail every attempt, not just
                // this one.
                var filters = catalogFilters(params, a === 0);
                var cols = [search.createColumn({ name: C.ITEM_FIELD.ITEM_ID, sort: search.Sort.ASC })]
                    .concat(CATALOG_COLUMN_ATTEMPTS[a]);
                rows = runCappedSearch(filters, cols).rows;
            } catch (e) {
                lastErr = e;
                rows = null;
                log.error({
                    title: 'bsgshop catalog search failed (attempt ' + (a + 1) + ' of ' + CATALOG_COLUMN_ATTEMPTS.length + ')',
                    details: safeErr(e)
                });
            }
        }
        if (!rows) {
            // Detail (incl. stack) is already in the per-attempt server logs above;
            // the client only gets a generic message, never internal error text.
            return { ok: false, error: 'The catalog is temporarily unavailable. Please try again.' };
        }

        var mapped = rows.map(mapProductRow).filter(Boolean);

        var order = [], groups = {};
        mapped.forEach(function (p) {
            var key = groupKeyFor(p.name, p.id);
            if (!groups[key]) { groups[key] = []; order.push(key); }
            groups[key].push(p);
        });

        var cards = order.map(function (key) {
            var members = groups[key];
            if (members.length === 1) { return members[0]; } // single item OR a true matrix parent -- either way, one card
            return groupedCardFromMembers(members[0].name, members);
        });

        var total = cards.length;
        var size = resolvePageSize(params);
        var pageCount = Math.max(1, Math.ceil(total / size));
        var startIdx = (Math.min(page, pageCount) - 1) * size;
        var pageCards = cards.slice(startIdx, startIdx + size);

        // List images: vendor-url images, then the curated Item Image + Base Price
        // read off each card's record (budget-capped: one page is PAGE_SIZE loads,
        // ~120 units against the 1000-unit Suitelet limit -- the crash that was
        // once blamed on this was actually the Image-type SEARCH COLUMN, which is
        // gone for good), then one batched attached-file search. Best-effort: a
        // failure resolving images must not fail the whole catalog response.
        try { fillImagesForCards(pageCards, true, C.CONFIG.LIST_RECORD_IMAGE_MAX); } catch (eImg) {
            log.error({ title: 'bsgshop: list image fill failed (non-fatal)', details: safeErr(eImg) });
            pageCards.forEach(stripInternal);
        }

        return {
            ok: true,
            page: Math.min(page, pageCount),
            pageCount: pageCount,
            total: total,
            products: pageCards
        };
    }

    // Simpler/cheaper path for accounts where CONFIG.GROUP_FLAT_VARIANTS is off:
    // NetSuite's own paged search, one card per raw item (matrix children are still
    // excluded by catalogBaseFilters so true matrix products still show once).
    function buildFlatCatalog(params) {
        var page = Math.max(1, parseInt(params.page, 10) || 1);

        var paged = null, lastErr = null;
        for (var a = 0; a < CATALOG_COLUMN_ATTEMPTS.length && !paged; a++) {
            try {
                var filters = catalogFilters(params, a === 0);
                var s = search.create({
                    type: 'item',
                    filters: filters,
                    columns: [search.createColumn({ name: C.ITEM_FIELD.ITEM_ID, sort: search.Sort.ASC })]
                        .concat(CATALOG_COLUMN_ATTEMPTS[a])
                });
                paged = s.runPaged({ pageSize: resolvePageSize(params) });
            } catch (e) {
                lastErr = e;
                paged = null;
                log.error({
                    title: 'bsgshop catalog search failed (attempt ' + (a + 1) + ' of ' + CATALOG_COLUMN_ATTEMPTS.length + ')',
                    details: safeErr(e)
                });
            }
        }
        if (!paged) {
            // Detail (incl. stack) is already in the per-attempt server logs above;
            // the client only gets a generic message, never internal error text.
            return { ok: false, error: 'The catalog is temporarily unavailable. Please try again.' };
        }

        var total = paged.count;
        var pageCount = paged.pageRanges.length || 1;
        var rows = (page <= pageCount) ? paged.fetch({ index: page - 1 }).data : [];
        var products = rows.map(mapProductRow).filter(Boolean);
        try { fillImagesForCards(products); } catch (eImg) {
            log.error({ title: 'bsgshop: flat list image fill failed (non-fatal)', details: safeErr(eImg) });
            products.forEach(stripInternal);
        }

        return { ok: true, page: page, pageCount: pageCount, total: total, products: products };
    }

    // Quantity (tier) pricing for a SIMPLE item: read the item's pricing matrix and
    // return the quantity breaks for the configured store price level as
    // [{ minQty, price, priceFormatted }] sorted ascending. Returns [] -- and the
    // store just shows the single base price -- if the account has no quantity
    // pricing, the item has no real breaks, or anything goes wrong. Must NEVER break
    // the detail view, so everything is wrapped and fails closed to [].
    function readQuantityTiers(itemId, typeCode, mapFloor) {
        if (!C.CONFIG.QUANTITY_PRICING_ENABLED) { return []; }
        var rtype = ITEM_RECORD_TYPE_BY_CODE[typeCode];
        if (!rtype) { return []; }
        try {
            var rec = record.load({ type: rtype, id: itemId });
            var src = C.CONFIG.TIER_SOURCE || 'auto';
            var tiers = [];
            // BSG stores volume pricing as separate price LEVELS (Price 1-5), so try
            // those first; fall back to quantity-break matrix on the base level.
            if (src === 'levels' || src === 'auto') { tiers = tiersFromPriceLevels(rec, mapFloor); }
            if (tiers.length < 2 && (src === 'matrix' || src === 'auto')) { tiers = tiersFromMatrix(rec, mapFloor); }
            return tiers.length >= 2 ? tiers : [];
        } catch (e) {
            log.debug({ title: 'bsgshop: quantity tiers unavailable for ' + itemId, details: safeErr(e) });
            return [];
        }
    }

    // Volume tiers from separate price LEVELS (Price 1-5) mapped to break quantities
    // via CONFIG.TIER_PRICE_LEVELS. Reads each level's unit price (matrix column 0).
    // Returns [] unless there's real variation (so 5 identical levels aren't shown).
    function tiersFromPriceLevels(rec, mapFloor) {
        var map = C.CONFIG.TIER_PRICE_LEVELS || [];
        if (!map.length) { return []; }
        var sublistId = 'price1';
        var lineCount = rec.getLineCount({ sublistId: sublistId });
        if (!(lineCount > 0)) { return []; }
        var lineByLevel = {};
        for (var li = 0; li < lineCount; li++) {
            var lvl = String(rec.getSublistValue({ sublistId: sublistId, fieldId: 'pricelevel', line: li }) || '');
            if (lvl && !(lvl in lineByLevel)) { lineByLevel[lvl] = li; }
        }
        var tiers = [], seenQ = {}, prices = {};
        for (var i = 0; i < map.length; i++) {
            var lvlId = String(map[i].level);
            if (!(lvlId in lineByLevel)) { continue; }
            var p = num(rec.getMatrixSublistValue({ sublistId: sublistId, fieldId: 'price', column: 0, line: lineByLevel[lvlId] }));
            if (!(p > 0)) { continue; }
            p = floorAtMap(p, mapFloor);
            var q = num(map[i].minQty) || 1;
            if (seenQ[q]) { continue; }
            seenQ[q] = 1; prices[p] = 1;
            tiers.push({ minQty: q, price: p, priceFormatted: formatMoney(p) });
        }
        // Require real variation -- if every level is the same price, it isn't a
        // volume-pricing table, so don't show one.
        if (Object.keys(prices).length < 2) { return []; }
        tiers.sort(function (a, b) { return a.minQty - b.minQty; });
        return tiers;
    }

    // Fallback: quantity-break matrix on the configured base price level.
    function tiersFromMatrix(rec, mapFloor) {
        var sublistId = 'price1';
        var lineCount = rec.getLineCount({ sublistId: sublistId });
        if (!(lineCount > 0)) { return []; }
        var targetLine = -1, firstLine = -1;
        for (var li = 0; li < lineCount; li++) {
            if (firstLine < 0) { firstLine = li; }
            var lvl = String(rec.getSublistValue({ sublistId: sublistId, fieldId: 'pricelevel', line: li }) || '');
            var lvlName = '';
            try { lvlName = String(rec.getSublistText({ sublistId: sublistId, fieldId: 'pricelevel', line: li }) || ''); } catch (eName) { /* text optional */ }
            if (lvl === String(C.CONFIG.STORE_PRICE_LEVEL_ID) ||
                (C.CONFIG.STORE_PRICE_LEVEL_NAME && lvlName === C.CONFIG.STORE_PRICE_LEVEL_NAME)) {
                targetLine = li; break;
            }
        }
        if (targetLine < 0) { targetLine = firstLine; }
        if (targetLine < 0) { return []; }
        var cols = rec.getMatrixHeaderCount({ sublistId: sublistId, fieldId: 'price' });
        if (!(cols > 0)) { return []; }
        var tiers = [], seen = {};
        for (var c = 0; c < cols; c++) {
            var p = num(rec.getMatrixSublistValue({ sublistId: sublistId, fieldId: 'price', column: c, line: targetLine }));
            if (!(p > 0)) { continue; }
            p = floorAtMap(p, mapFloor); // MAP compliance
            var q = num(rec.getMatrixHeaderValue({ sublistId: sublistId, fieldId: 'price', column: c }));
            if (!(q > 0)) { q = 1; }         // the "1+" column carries no header
            if (seen[q]) { continue; }
            seen[q] = 1;
            tiers.push({ minQty: q, price: p, priceFormatted: formatMoney(p) });
        }
        tiers.sort(function (a, b) { return a.minQty - b.minQty; });
        return tiers;
    }

    function getProduct(id) {
        if (!id) { return { ok: false, error: 'Missing product id.' }; }

        // "g:<encoded name>" ids come from grouped flat-variant cards (see "flat-
        // variant grouping" above) -- an entirely different lookup (by shared name,
        // not by a single item id).
        if (id.indexOf('g:') === 0) {
            return groupedProductDetail(decodeURIComponent(id.slice(2)));
        }

        // Same defense as the list: a single invalid lookup field throws the whole
        // call, so fall back to a minimal field set if the rich one fails.
        var f;
        try {
            f = search.lookupFields({
                type: 'item',
                id: id,
                columns: [
                    C.ITEM_FIELD.ITEM_ID, C.ITEM_FIELD.DISPLAY_NAME, C.ITEM_FIELD.NATIVE_DISPLAY_NAME,
                    C.ITEM_FIELD.STORE_DESCRIPTION, C.ITEM_FIELD.DETAILED_DESCRIPTION,
                    C.ITEM_FIELD.SALES_DESCRIPTION, C.CONFIG.PRICE_SEARCH_FIELD,
                    C.ITEM_FIELD.QTY_AVAILABLE,
                    C.ITEM_FIELD.IS_ONLINE, C.ITEM_FIELD.MATRIX, C.ITEM_FIELD.TYPE,
                    C.ITEM_FIELD.UPC, C.ITEM_FIELD.WEIGHT, C.ITEM_FIELD.VENDOR_NAME,
                    C.ITEM_FIELD.DEPARTMENT
                ].concat(VENDOR_IMAGE_PRICE_FIELDS, VENDOR_SPEC_FIELDS, VENDOR_QTY_FIELDS)
            });
        } catch (eRich) {
            log.error({ title: 'bsgshop product lookup: rich field set failed', details: safeErr(eRich) });
            f = search.lookupFields({
                type: 'item',
                id: id,
                columns: [C.ITEM_FIELD.ITEM_ID, C.ITEM_FIELD.SALES_DESCRIPTION, C.ITEM_FIELD.QTY_AVAILABLE]
            });
        }
        if (!productQualifies(f)) {
            return { ok: false, error: 'This product is not available.' };
        }
        var itemNumber = f[C.ITEM_FIELD.ITEM_ID] || '';
        var salesDesc = stripTags(f[C.ITEM_FIELD.SALES_DESCRIPTION] || '');
        var name = f[C.ITEM_FIELD.DISPLAY_NAME] || f[C.ITEM_FIELD.NATIVE_DISPLAY_NAME] ||
            salesDesc || itemNumber;
        var price = num(f[C.CONFIG.PRICE_SEARCH_FIELD]);

        var getVal = fieldsGetVal(f);
        var qty = resolveQty(getVal);
        var image = resolveImage(getVal);
        // fillImagesForCards also reads the authoritative Item Image + Base Price off
        // the record, so seed the card's price and read both back afterwards.
        var card = { image: image, price: price, _rawIds: [id], _rawTypes: [f[C.ITEM_FIELD.TYPE]], _rawNumbers: [itemNumber] };
        fillImagesForCards([card], true); // detail view: OK to record.load a single item
        image = card.image;
        if (card.price > 0) { price = card.price; }
        // MAP compliance: never advertise below the item's Minimum Advertised Price.
        var mapFloor = resolveMap(getVal);
        price = floorAtMap(price, mapFloor);

        var onSaleFlag = isTrue(f[C.ITEM_FIELD.ON_SALE]);
        var msrp = (onSaleFlag || C.CONFIG.COMPARE_AT_ALWAYS) ? resolveMsrp(getVal, price) : 0;

        var isMatrix = isTrue(f[C.ITEM_FIELD.MATRIX]);
        var variants = isMatrix ? loadVariants(id, itemNumber, price) : [];
        if (isMatrix) {
            // Parent rows don't carry stock; roll availability up from the variants.
            qty = variants.reduce(function (s, v) { return s + v.qty; }, 0);
            // MAP compliance on each size/color price too.
            if (mapFloor > 0) {
                variants.forEach(function (v) {
                    var fp = floorAtMap(v.price, mapFloor);
                    if (fp !== v.price) { v.price = fp; v.priceFormatted = formatMoney(fp); }
                });
            }
        }
        // Quantity price breaks (simple items only; matrix variants price per size/color).
        var tiers = isMatrix ? [] : readQuantityTiers(id, f[C.ITEM_FIELD.TYPE], mapFloor);

        var desc = htmlToLines(f[C.ITEM_FIELD.DETAILED_DESCRIPTION] ||
            f[C.ITEM_FIELD.STORE_DESCRIPTION] || f[C.ITEM_FIELD.SALES_DESCRIPTION] || '');
        if (desc === name) { desc = ''; }

        var upc = f[C.ITEM_FIELD.UPC] || resolveGtin(getVal);
        // Web Store Description for the bottom spec strip (sales description as the
        // fallback so the block isn't empty on items that never had one entered).
        var specDesc = stripTags(f[C.ITEM_FIELD.STORE_DESCRIPTION] || '') || salesDesc;
        var weight = f[C.ITEM_FIELD.WEIGHT] || f[C.ITEM_FIELD.SS_WEIGHT];

        return {
            ok: true,
            product: {
                id: id,
                name: name,
                itemNumber: itemNumber,
                isMatrix: isMatrix,
                description: desc,
                price: price,
                priceFormatted: formatMoney(price),
                onSale: onSaleFlag && msrp > 0,
                msrp: msrp,
                msrpFormatted: msrp > 0 ? formatMoney(msrp) : '',
                image: image,
                images: image ? [image] : [],
                stock: stockStatus(qty),
                variants: variants,
                tiers: tiers,
                gallery: galleryForItem(itemNumber),
                personalize: personalizeFromLookup(f),
                specs: buildSpecs(specDesc, upc, weight)
            }
        };
    }

    // Detail for a GROUPED flat-variant product (see "flat-variant grouping"):
    // re-collects every item sharing the group's product name, presents them as
    // size/color variants exactly like a true matrix parent's children.
    function groupedProductDetail(name) {
        var filters = [
            ['isinactive', 'is', 'F'], 'AND', [C.ITEM_FIELD.MATRIX_CHILD, 'is', 'F'], 'AND',
            [
                [C.ITEM_FIELD.DISPLAY_NAME, 'is', name], 'OR',
                [C.ITEM_FIELD.NATIVE_DISPLAY_NAME, 'is', name], 'OR',
                [C.ITEM_FIELD.SALES_DESCRIPTION, 'is', name]
            ]
        ];
        var cols = [
            search.createColumn({ name: C.ITEM_FIELD.ITEM_ID, sort: search.Sort.ASC }),
            C.ITEM_FIELD.DISPLAY_NAME, C.ITEM_FIELD.NATIVE_DISPLAY_NAME, C.ITEM_FIELD.SALES_DESCRIPTION,
            C.ITEM_FIELD.STORE_DESCRIPTION, C.ITEM_FIELD.DETAILED_DESCRIPTION, C.CONFIG.PRICE_SEARCH_FIELD,
            C.ITEM_FIELD.QTY_AVAILABLE, C.ITEM_FIELD.TYPE, C.ITEM_FIELD.MATRIX,
            C.ITEM_FIELD.UPC, C.ITEM_FIELD.WEIGHT, C.ITEM_FIELD.VENDOR_NAME,
            C.ITEM_FIELD.DEPARTMENT
        ].concat(VENDOR_IMAGE_PRICE_FIELDS, VENDOR_SPEC_FIELDS, VENDOR_QTY_FIELDS);

        var rows;
        try {
            rows = runCappedSearch(filters, cols, 300).rows;
        } catch (e) {
            log.error({ title: 'bsgshop grouped product lookup failed for "' + name + '"', details: safeErr(e) });
            return { ok: false, error: 'This product is not available.' };
        }
        if (!rows.length) { return { ok: false, error: 'This product is not available.' }; }

        var members = rows.map(mapProductRow).filter(Boolean);
        if (!members.length) { return { ok: false, error: 'This product is not available.' }; }
        var prefix = sharedItemPrefix(members);

        var extraCols = { upc: '', weight: '', storeDesc: '', longDesc: '' };
        rows.forEach(function (r) {
            if (!extraCols.upc) { extraCols.upc = rowVal(r, C.ITEM_FIELD.UPC) || resolveGtin(rowGetVal(r)); }
            if (!extraCols.weight) { extraCols.weight = rowVal(r, C.ITEM_FIELD.WEIGHT) || rowVal(r, C.ITEM_FIELD.SS_WEIGHT); }
            // Web Store Description for the bottom spec strip (sales desc fallback).
            if (!extraCols.storeDesc) {
                extraCols.storeDesc = stripTags(rowVal(r, C.ITEM_FIELD.STORE_DESCRIPTION) || '') ||
                    stripTags(rowVal(r, C.ITEM_FIELD.SALES_DESCRIPTION) || '');
            }
            var d = htmlToLines(rowVal(r, C.ITEM_FIELD.DETAILED_DESCRIPTION) || rowVal(r, C.ITEM_FIELD.STORE_DESCRIPTION) || '');
            if (d.length > extraCols.longDesc.length) { extraCols.longDesc = d; }
        });

        // Compute everything that needs each member's .qty/.price BEFORE
        // fillImagesForCards runs -- it strips those internal-only fields (qty is
        // never sent to the client; only the In/Low/Out badge is, by design) once
        // it's done resolving images.
        var prices = members.map(function (m) { return m.price; }).filter(function (p) { return p > 0; });
        var minPrice = prices.length ? Math.min.apply(null, prices) : 0;
        var varies = prices.length > 1 && prices.some(function (p) { return p !== minPrice; });
        var totalQty = members.reduce(function (s, m) { return s + (m.qty || 0); }, 0);
        var cheapest = members.reduce(function (best, m) {
            return (!best || (m.price > 0 && m.price < best.price)) ? m : best;
        }, null);
        var groupMsrp = (cheapest && cheapest.msrp > 0) ? cheapest.msrp : 0;
        var groupOnSale = !!(cheapest && cheapest.onSale) && groupMsrp > 0;
        var variants = members.map(function (m) {
            return {
                id: m.id,
                label: stripSharedPrefix(m.itemNumber, prefix),
                qty: m.qty,
                price: m.price,
                priceFormatted: formatMoney(m.price),
                stock: stockStatus(m.qty)
            };
        });

        fillImagesForCards(members, true); // detail: record.load OK (one product, budget-capped), then strips internal fields

        // Per-color images for the variant rows (click a color -> see it). members
        // and variants are index-aligned (variants was built via members.map).
        variants.forEach(function (v, i) { if (members[i] && members[i].image) { v.image = members[i].image; } });

        var images = [];
        members.forEach(function (m) { if (m.image && images.indexOf(m.image) === -1) { images.push(m.image); } });
        images = images.slice(0, 6);

        return {
            ok: true,
            product: {
                id: 'g:' + encodeURIComponent(name),
                name: name,
                itemNumber: prefix,
                isMatrix: true,
                description: extraCols.longDesc || name,
                price: minPrice,
                priceFormatted: varies ? 'From ' + formatMoney(minPrice) : formatMoney(minPrice),
                onSale: groupOnSale,
                msrp: groupMsrp,
                msrpFormatted: groupMsrp > 0 ? formatMoney(groupMsrp) : '',
                image: images[0] || '',
                images: images,
                stock: stockStatus(totalQty),
                variants: variants,
                gallery: galleryForItem(prefix || (members[0] && members[0].itemNumber)),
                personalize: personalizeFromRow(rows[0]),
                specs: buildSpecs(extraCols.storeDesc, extraCols.upc, extraCols.weight)
            }
        };
    }

    // The bottom spec strip on the product detail. Leads with the item's Web Store
    // Description as a full-width text block (the old "Vendor" row often showed the
    // raw style number on synced items -- noise, not information).
    function buildSpecs(desc, upc, weight) {
        var specs = [];
        if (desc) { specs.push({ label: 'Description', value: String(desc), block: true }); }
        if (upc) { specs.push({ label: 'UPC', value: String(upc) }); }
        if (weight && num(weight) > 0) { specs.push({ label: 'Weight', value: num(weight) + ' lb' }); }
        return specs;
    }

    // Like stripTags, but preserves list items and line/paragraph breaks as "\n"
    // (bullets prefixed "• ") instead of collapsing everything to one line --
    // used for the detail-view description so bullet-point specs stay readable.
    function htmlToLines(html) {
        var s = String(html || '');
        s = s.replace(/<li[^>]*>/gi, '• ').replace(/<\/li>/gi, '\n');
        s = s.replace(/<br\s*\/?>/gi, '\n').replace(/<\/p>/gi, '\n').replace(/<\/div>/gi, '\n');
        s = s.replace(/<[^>]+>/g, '');
        s = s.replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&lt;/gi, '<').replace(/&gt;/gi, '>');
        s = s.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').replace(/[ \t]{2,}/g, ' ').trim();
        return s;
    }

    // All active size/color children of a matrix parent, with per-variant stock and
    // price. The variant label is the child's item number minus the parent prefix
    // (e.g. "1370379-Navy-Small" -> "Navy-Small"); the client splits that into a
    // color x size grid.
    function loadVariants(parentId, parentItemNumber, parentPrice) {
        var variants = [];
        try {
            search.create({
                type: 'item',
                filters: [
                    [C.ITEM_FIELD.PARENT, 'anyof', parentId], 'AND',
                    ['isinactive', 'is', 'F']
                ],
                columns: [
                    search.createColumn({ name: C.ITEM_FIELD.ITEM_ID, sort: search.Sort.ASC }),
                    C.ITEM_FIELD.QTY_AVAILABLE,
                    C.CONFIG.PRICE_SEARCH_FIELD
                ].concat(VENDOR_QTY_FIELDS)
            }).run().each(function (r) {
                var childNumber = r.getValue(C.ITEM_FIELD.ITEM_ID) || '';
                var label = childNumber;
                // Child ids repeat the parent number ("1370379 : 1370379-Navy-Small");
                // strip every leading occurrence plus separators to get "Navy-Small".
                if (parentItemNumber) {
                    while (label.indexOf(parentItemNumber) === 0 ||
                        /^[\s:>-]/.test(label)) {
                        label = label.indexOf(parentItemNumber) === 0
                            ? label.slice(parentItemNumber.length)
                            : label.slice(1);
                    }
                }
                label = label || childNumber;
                var vQty = resolveQty(rowGetVal(r));
                var vPrice = num(r.getValue(C.CONFIG.PRICE_SEARCH_FIELD)) || parentPrice || 0;
                variants.push({
                    id: r.id,
                    label: label,
                    qty: vQty,
                    price: vPrice,
                    priceFormatted: formatMoney(vPrice),
                    stock: stockStatus(vQty)
                });
                return variants.length < 200;
            });
        } catch (e) {
            log.error({ title: 'bsgshop variant load failed for parent ' + parentId, details: safeErr(e) });
        }
        return variants;
    }

    // getValue on a column that was dropped from the search must not blow up the row.
    function rowVal(row, name) {
        try { return row.getValue(name); } catch (e) { return ''; }
    }

    function isTrue(v) { return v === true || v === 'T'; }

    // Image resolution, in priority order, using BSG's actual custom fields (see
    // lib/bsgshop.constants.js): the "Item Image" field BSG maintains as the
    // canonical photo, then the native Web Store image, then whichever vendor feed
    // (SanMar/S&S/Momentec/UA) synced a front/on-model image URL for this item.
    // getVal(fieldKey) abstracts over reading a search Row vs. a lookupFields
    // result object, so the same walk works from both call sites.
    function resolveImage(getVal) {
        var i, id, url_;
        // Precomputed image URL (nightly image-sync M/R) -- a plain, columnable field,
        // so it's the cheapest and highest-priority source. Blank -> fall through to
        // the live vendor/attached/record resolution below.
        if (C.CONFIG.USE_PRECOMPUTED_IMAGES) {
            var pre = String(first(getVal(C.ITEM_FIELD.SHOP_IMAGE_URL)) || '').trim();
            if (pre) { return /^https?:\/\//i.test(pre) ? pre : absoluteUrl(pre); }
        }
        for (i = 0; i < C.IMAGE_FILE_FIELDS.length; i++) {
            id = firstId(getVal(C.ITEM_FIELD[C.IMAGE_FILE_FIELDS[i]]));
            if (id) { url_ = fileUrl(id); if (url_) { return url_; } }
        }
        for (i = 0; i < C.IMAGE_URL_FIELDS.length; i++) {
            var raw = first(getVal(C.ITEM_FIELD[C.IMAGE_URL_FIELDS[i]]));
            var s = String(raw || '').trim();
            if (s) { return /^https?:\/\//i.test(s) ? s : absoluteUrl(s); }
        }
        return '';
    }

    function resolveMsrp(getVal, price) {
        for (var i = 0; i < C.MSRP_FIELDS.length; i++) {
            var v = num(first(getVal(C.ITEM_FIELD[C.MSRP_FIELDS[i]])));
            if (v > 0 && v > price) { return v; }
        }
        return 0;
    }

    // Minimum Advertised Price for the item (0 = none). The store never displays a
    // price below this when MAP_ENFORCE is on. Read from the searchable MAP field.
    function resolveMap(getVal) {
        if (!C.CONFIG.MAP_ENFORCE) { return 0; }
        return num(first(getVal(C.ITEM_FIELD.MAP_PRICE)));
    }
    // Floor a displayed price at MAP (advertise at MAP rather than below it).
    function floorAtMap(price, map) { return (map > 0 && map > price) ? map : price; }

    function resolveGtin(getVal) {
        for (var i = 0; i < C.GTIN_FIELDS.length; i++) {
            var v = first(getVal(C.ITEM_FIELD[C.GTIN_FIELDS[i]]));
            if (v) { return String(v); }
        }
        return '';
    }

    // Full image gallery for an item from the Momentec feed index: every COLOR's
    // angle shots (front/quarters/sides/back) as [{c: colorCode, imgs: [urls]}].
    // Null when the style isn't in the feed -- the client then falls back to the
    // single primary image exactly as before.
    function galleryForItem(itemNumber) {
        try {
            var base = String(itemNumber || '').split(/[-.]/)[0].trim();
            if (!base) { return null; }
            var entry = IMGMAP.styles[base] || IMGMAP.styles[base.replace(/^0+(?=.)/, '')];
            if (!entry) { return null; }
            var style = entry._ || base;
            var out = [];
            for (var color in entry) {
                if (color === '_' || !entry.hasOwnProperty(color)) { continue; }
                var letters = entry[color], imgs = [];
                for (var i = 0; i < letters.length; i++) {
                    var ang = IMGMAP.angles[letters.charAt(i)];
                    if (ang) { imgs.push(IMGMAP.prefix + style + '_' + color + '_' + ang + '.jpg'); }
                }
                if (imgs.length) { out.push({ c: color, imgs: imgs }); }
            }
            return out.length ? out : null;
        } catch (e) { return null; }
    }

    // ---- personalization eligibility (player name/number = apparel dept only) ----
    // The optional Player Name / Player # fields only apply to decorable goods
    // (apparel/uniforms), never equipment. The signal is the item's DEPARTMENT: if
    // it's labeled apparel, personalization applies to every item in it regardless
    // of class (CONFIG.PERSONALIZE_*). Fail-open to FALSE so an item in a
    // non-apparel department just omits the optional fields.
    // Text of a (possibly select) value: lookupFields selects come back as
    // [{value,text}]; plain fields as a string.
    function selectText(v) {
        var x = first(v);
        if (x == null) { return ''; }
        if (typeof x === 'object') { return String(x.text || x.value || ''); }
        return String(x);
    }
    function matchesApparel(texts) {
        if (!C.CONFIG.PERSONALIZE_ENABLED) { return false; }
        var kws = C.CONFIG.PERSONALIZE_KEYWORDS || [];
        if (!kws.length) { return false; }
        var hay = ' ' + (texts || []).join(' ').toLowerCase() + ' ';
        for (var i = 0; i < kws.length; i++) {
            if (hay.indexOf(String(kws[i]).toLowerCase()) !== -1) { return true; }
        }
        return false;
    }
    function personalizeDeptFields() { return C.CONFIG.PERSONALIZE_DEPT_FIELDS || ['DEPARTMENT']; }
    // From a lookupFields result object (selects are [{value,text}]).
    function personalizeFromLookup(f) {
        var fields = personalizeDeptFields();
        var texts = [];
        for (var i = 0; i < fields.length; i++) {
            var fid = C.ITEM_FIELD[fields[i]];
            if (fid) { texts.push(selectText(f[fid])); }
        }
        return matchesApparel(texts);
    }
    // From a search result row (selects: getText gives the label).
    function personalizeFromRow(row) {
        var fields = personalizeDeptFields();
        var texts = [];
        for (var i = 0; i < fields.length; i++) {
            var fid = C.ITEM_FIELD[fields[i]];
            if (fid) { try { texts.push(row.getText(fid) || ''); } catch (e) { /* not textable */ } }
        }
        return matchesApparel(texts);
    }

    // Real availability for drop-ship/vendor items: native Quantity Available if
    // positive, else the first positive per-vendor Qty Available field (see
    // QTY_FIELDS). Mirrors qtyAvailabilityFilter() above so what the search
    // INCLUDES and what the UI DISPLAYS as the qty/badge always agree.
    function resolveQty(getVal) {
        var native = num(first(getVal(C.ITEM_FIELD.QTY_AVAILABLE)));
        if (native > 0) { return native; }
        for (var i = 0; i < C.QTY_FIELDS.length; i++) {
            var v = num(first(getVal(C.ITEM_FIELD[C.QTY_FIELDS[i]])));
            if (v > 0) { return v; }
        }
        return native; // 0 (or negative, treated as 0 downstream)
    }

    function rowGetVal(row) { return function (fieldId) { return fieldId ? rowVal(row, fieldId) : ''; }; }
    function fieldsGetVal(f) { return function (fieldId) { return fieldId ? f[fieldId] : ''; }; }

    // Maps one search row to a product-shaped object. Carries a couple of
    // underscore-prefixed INTERNAL fields (_type, qty) alongside the public ones --
    // used for grouping/image-fallback math -- which callers must strip (see
    // stripInternal) before the object goes out over JSON.
    function mapProductRow(row) {
        try {
            return mapProductRowUnsafe(row);
        } catch (e) {
            // One malformed row (bad field, null adapter, etc.) must never take the
            // whole catalog down -- drop it and keep the rest of the page.
            log.error({ title: 'bsgshop: product row map failed', details: safeErr(e) });
            return null;
        }
    }

    function mapProductRowUnsafe(row) {
        var getVal = rowGetVal(row);
        var qty = resolveQty(getVal);
        var price = num(rowVal(row, C.CONFIG.PRICE_SEARCH_FIELD));
        var isMatrix = isTrue(rowVal(row, C.ITEM_FIELD.MATRIX));
        var itemNumber = rowVal(row, C.ITEM_FIELD.ITEM_ID);
        // Prefer the human product name over the raw item number: web-store display
        // name, then native Display Name, then the sales description (which is where
        // synced items like "Armour Fleece Storm Hoodie" carry their real name).
        var salesDesc = rowVal(row, C.ITEM_FIELD.SALES_DESCRIPTION) || '';
        var name = rowVal(row, C.ITEM_FIELD.DISPLAY_NAME) ||
            rowVal(row, C.ITEM_FIELD.NATIVE_DISPLAY_NAME) ||
            stripTags(salesDesc) || itemNumber;
        var desc = rowVal(row, C.ITEM_FIELD.STORE_DESCRIPTION) || salesDesc || '';
        desc = stripTags(desc);
        if (desc === name) { desc = ''; }

        // MAP compliance: never advertise below the item's Minimum Advertised Price.
        price = floorAtMap(price, resolveMap(getVal));

        var image = resolveImage(getVal);
        var onSaleFlag = isTrue(rowVal(row, C.ITEM_FIELD.ON_SALE));
        // Show the MSRP for a real sale, or as a "Compare at" whenever a higher MSRP
        // exists (COMPARE_AT_ALWAYS). resolveMsrp only returns an MSRP above price.
        var msrp = (onSaleFlag || C.CONFIG.COMPARE_AT_ALWAYS) ? resolveMsrp(getVal, price) : 0;

        return {
            id: row.id,
            name: name,
            itemNumber: itemNumber,
            isMatrix: isMatrix,
            description: truncate(desc, 140),
            price: price,
            priceFormatted: formatMoney(price),
            onSale: onSaleFlag && msrp > 0,
            msrp: msrp,
            msrpFormatted: msrp > 0 ? formatMoney(msrp) : '',
            image: image,
            // Matrix parents hold no stock themselves; the real availability is per
            // size/color on the detail view.
            stock: isMatrix ? { code: 'in', label: 'See Options', orderable: true } : stockStatus(qty),
            qty: qty,
            _type: rowVal(row, C.ITEM_FIELD.TYPE)
        };
    }

    function stripInternal(card) {
        delete card._type; delete card._rawIds; delete card._rawTypes; delete card._rawNumbers; delete card.qty;
        return card;
    }

    // ------------------------------------------------- flat-variant grouping --
    //
    // Some vendor-synced catalogs don't use NetSuite's matrix parent/child feature
    // at all -- every size/color is its own fully standalone item (e.g.
    // "1359344-White/Navy-3X-Large"), all sharing the same product name. Left
    // alone, that floods the catalog with one card per SKU. When
    // CONFIG.GROUP_FLAT_VARIANTS is on (default), items sharing a product name are
    // grouped into a single synthetic "product" the same way a true matrix parent
    // is: one card, a color x size grid on the detail view. Its id is prefixed
    // "g:" + the encoded group name so getProduct() can tell the two apart.

    function runCappedSearch(filters, columns, cap) {
        cap = cap || C.CONFIG.MAX_RAW_CATALOG_ROWS;
        var out = [];
        var s = search.create({ type: 'item', filters: filters, columns: columns });
        var resultSet = s.run();
        var start = 0;
        while (start < cap) {
            var end = Math.min(start + 1000, cap);
            var range = resultSet.getRange({ start: start, end: end });
            if (!range || !range.length) { break; }
            for (var i = 0; i < range.length; i++) { out.push(range[i]); }
            if (range.length < (end - start)) { break; } // fewer than requested -> exhausted
            start = end;
        }
        return { rows: out, truncated: out.length >= cap };
    }

    function groupKeyFor(name, id) {
        var k = String(name || '').trim().toLowerCase();
        return k || ('id:' + id);
    }

    // The leading token items in a group tend to share ("1359344" out of
    // "1359344-White/Navy-3X-Large"), used as a stand-in "item #" for the group.
    function sharedItemPrefix(members) {
        var first = (members[0].itemNumber || '').split('-')[0];
        if (!first) { return ''; }
        var allMatch = members.every(function (m) { return (m.itemNumber || '').split('-')[0] === first; });
        return allMatch ? first : '';
    }

    function stripSharedPrefix(itemNumber, prefix) {
        var label = itemNumber || '';
        if (prefix && label.indexOf(prefix) === 0) {
            label = label.slice(prefix.length);
            while (/^[\s:>-]/.test(label)) { label = label.slice(1); }
        }
        return label || itemNumber || '';
    }

    function groupedCardFromMembers(name, members) {
        var prices = members.map(function (m) { return m.price; }).filter(function (p) { return p > 0; });
        var minPrice = prices.length ? Math.min.apply(null, prices) : 0;
        var varies = prices.length > 1 && prices.some(function (p) { return p !== minPrice; });
        var totalQty = members.reduce(function (s, m) { return s + (m.qty || 0); }, 0);
        var image = '';
        for (var i = 0; i < members.length; i++) { if (members[i].image) { image = members[i].image; break; } }
        var desc = members.reduce(function (best, m) {
            return (m.description && m.description.length > best.length) ? m.description : best;
        }, '');
        var prefix = sharedItemPrefix(members);
        // Represent "on sale" at the card level using whichever member is at the
        // displayed (lowest) price, so the strikethrough MSRP lines up with the
        // price actually shown.
        var cheapest = members.reduce(function (best, m) {
            return (!best || (m.price > 0 && m.price < best.price)) ? m : best;
        }, null);
        var msrp = (cheapest && cheapest.msrp > 0) ? cheapest.msrp : 0;
        var cardOnSale = !!(cheapest && cheapest.onSale) && msrp > 0;
        return {
            id: 'g:' + encodeURIComponent(name),
            name: name,
            itemNumber: prefix,
            isMatrix: true,
            description: desc,
            price: minPrice,
            priceFormatted: varies ? 'From ' + formatMoney(minPrice) : formatMoney(minPrice),
            onSale: cardOnSale,
            msrp: msrp,
            msrpFormatted: msrp > 0 ? formatMoney(msrp) : '',
            image: image,
            stock: totalQty > 0
                ? { code: 'in', label: 'See Options', orderable: true }
                : { code: 'out', label: 'Out of Stock', orderable: false },
            groupCount: members.length,
            _rawIds: members.map(function (m) { return m.id; }),
            _rawTypes: members.map(function (m) { return m._type; }),
            _rawNumbers: members.map(function (m) { return m.itemNumber; })
        };
    }

    // Resolves each card's image, in priority order:
    // Image resolution order (BSG confirmed every item has an image ATTACHED, so
    // attached files are the primary, cheap, batched source):
    //  1. whatever mapProductRow already set (a vendor image-URL column, if any).
    //  2. the image FILE attached to the item (Files subtab) -- one batched search.
    //  3. for matrix parents (no image of their own), the first attached image on a
    //     CHILD -- one more batched search.
    //  4. LAST RESORT: the curated Item Image field off the record via record.load
    //     (an Image-type field that can't be a search column). record.load is ~10
    //     units + a round-trip each, so it's budget-capped and only runs for cards
    //     STILL missing an image (and to fill a missing Base Price). This is why the
    //     list stays cheap: attached files carry the images, not dozens of loads.
    // No fuzzy folder filename-guessing -- that once cross-matched wrong photos.
    function fillImagesForCards(cards, allowRecordLoad, recordBudget) {
        cards.forEach(function (c) {
            if (!c._rawIds) { c._rawIds = [c.id]; c._rawTypes = [c._type]; }
        });

        function applyMap(map) {
            cards.forEach(function (c) {
                if (c.image) { return; }
                for (var i = 0; i < c._rawIds.length; i++) {
                    if (map[c._rawIds[i]]) { c.image = map[c._rawIds[i]]; break; }
                }
            });
        }
        function missingRawIds() {
            var ids = [];
            cards.forEach(function (c) { if (!c.image) { ids = ids.concat(c._rawIds); } });
            return ids;
        }

        // 2. Attached image files (primary).
        var miss = missingRawIds();
        if (miss.length) { applyMap(attachedImageMap(miss)); }

        // 3. Matrix parents: image lives on a child.
        miss = missingRawIds();
        if (miss.length) { applyMap(childAttachedImageMap(miss)); }

        // 4. Last resort: curated Item Image + Base Price off the record.
        var needRecord = cards.some(function (c) { return !c.image || !(c.price > 0); });
        if (allowRecordLoad && needRecord) {
            recordLoadBudget = recordBudget || C.CONFIG.RECORD_IMAGE_LOOKUP_MAX;
            cards.forEach(function (c) {
                var gotImage = !!c.image, gotPrice = c.price > 0;
                for (var i = 0; i < c._rawIds.length && recordLoadBudget > 0 && !(gotImage && gotPrice); i++) {
                    var d = recordItemDetail(c._rawIds[i], c._rawTypes && c._rawTypes[i]);
                    if (!gotImage && d.image) { c.image = d.image; gotImage = true; }
                    if (!gotPrice && d.price > 0) {
                        c.price = d.price;
                        c.priceFormatted = formatMoney(d.price);
                        gotPrice = true;
                    }
                }
            });
        }

        cards.forEach(stripInternal);
    }

    // The curated Item Image AND the Base Price, read straight off the item record
    // via record.load -- the reliable way to get an Image-type field's file id
    // (Image fields don't return through saved-search columns) and the item's base
    // price. For a matrix PARENT (which carries neither -- both live on its size/
    // color children) it pulls from the first child that has them. Cached per
    // request and governed by recordLoadBudget so a page can't blow the limit.
    var recordDetailCache = {};
    var recordLoadBudget = 0;
    function recordItemDetail(itemId, typeCode) {
        if (recordDetailCache.hasOwnProperty(itemId)) { return recordDetailCache[itemId]; }
        var out = { image: '', price: 0 };
        var rtype = ITEM_RECORD_TYPE_BY_CODE[typeCode];
        if (recordLoadBudget <= 0 || !rtype) {
            recordDetailCache[itemId] = out;
            return out;
        }
        try {
            recordLoadBudget--;
            var rec = record.load({ type: rtype, id: itemId });
            var fileId = rec.getValue({ fieldId: C.ITEM_FIELD.ATLAS_IMAGE }) ||
                rec.getValue({ fieldId: C.ITEM_FIELD.DISPLAY_IMAGE });
            if (fileId) { out.image = fileUrl(fileId); }
            var bp = num(rec.getValue({ fieldId: C.ITEM_FIELD.BASE_PRICE }));
            if (bp > 0) { out.price = bp; }

            if ((!out.image || out.price <= 0) && isTrue(rec.getValue({ fieldId: C.ITEM_FIELD.MATRIX }))) {
                var child = firstChildDetail(itemId);
                if (!out.image) { out.image = child.image; }
                if (out.price <= 0) { out.price = child.price; }
            }
        } catch (e) {
            log.debug({ title: 'bsgshop: item detail load failed for ' + itemId, details: safeErr(e) });
        }
        recordDetailCache[itemId] = out;
        return out;
    }

    // First active size/color child (of a matrix parent) that has an image and/or a
    // base price. Bounded so a parent with many empty children can't run away.
    function firstChildDetail(parentId) {
        var got = { image: '', price: 0 }, checked = 0;
        try {
            search.create({
                type: 'item',
                filters: [[C.ITEM_FIELD.PARENT, 'anyof', parentId], 'AND', ['isinactive', 'is', 'F']],
                columns: [search.createColumn({ name: C.ITEM_FIELD.ITEM_ID }), C.ITEM_FIELD.TYPE, C.ITEM_FIELD.BASE_PRICE]
            }).run().each(function (r) {
                if (got.price <= 0) {
                    var cp = num(r.getValue(C.ITEM_FIELD.BASE_PRICE));
                    if (cp > 0) { got.price = cp; }
                }
                if (!got.image && recordLoadBudget > 0 && checked < C.CONFIG.MATRIX_CHILD_IMAGE_SCAN_MAX) {
                    checked++;
                    var cd = recordItemDetail(r.id, r.getValue(C.ITEM_FIELD.TYPE));
                    if (cd.image) { got.image = cd.image; }
                }
                return !(got.image && got.price > 0) && checked < C.CONFIG.MATRIX_CHILD_IMAGE_SCAN_MAX;
            });
        } catch (e) { /* best effort */ }
        return got;
    }

    // Resolves the internal ids of CONFIG.SUPPLIER_IMAGE_FOLDER_NAMES once (folder
    // ids are stable for the account's lifetime; re-resolving every request is
    // cheap and safe either way since NetSuite doesn't guarantee this module-level
    // cache survives across separate requests).
    var imageFolderIdsCache;
    function resolveSupplierImageFolderIds() {
        if (imageFolderIdsCache) { return imageFolderIdsCache; }
        var ids = [];
        var names = C.CONFIG.SUPPLIER_IMAGE_FOLDER_NAMES || [];
        if (names.length) {
            try {
                var filters = [];
                names.forEach(function (n, i) {
                    if (i > 0) { filters.push('OR'); }
                    filters.push(['name', 'is', n]);
                });
                search.create({ type: 'folder', filters: filters, columns: ['internalid'] })
                    .run().each(function (r) { ids.push(r.id); return true; });
            } catch (e) {
                log.debug({ title: 'bsgshop: supplier image folder lookup failed', details: safeErr(e) });
            }
        }
        imageFolderIdsCache = ids;
        return ids;
    }

    // Matches items to a file in the supplier image folders by item number/style
    // number appearing in the file name (e.g. "1359344.jpg" for item
    // "1359344-White-3X-Large"). One batched search covers the whole lookup list.
    function folderImageMap(items) {
        var map = {};
        var folderIds = resolveSupplierImageFolderIds();
        if (!folderIds.length || !items.length) { return map; }

        var byNumber = {};
        items.forEach(function (it) { if (it.itemNumber) { byNumber[it.itemNumber] = it.id; } });
        var numbers = Object.keys(byNumber);
        if (!numbers.length) { return map; }

        try {
            var nameFilter = [];
            numbers.forEach(function (n, i) {
                if (i > 0) { nameFilter.push('OR'); }
                nameFilter.push(['name', 'contains', n]);
            });
            search.create({
                type: 'file',
                filters: [['folder', 'anyof', folderIds], 'AND', nameFilter],
                columns: ['name', 'url']
            }).run().each(function (r) {
                var fname = r.getValue('name') || '';
                for (var i = 0; i < numbers.length; i++) {
                    if (fname.indexOf(numbers[i]) !== -1) {
                        if (!map[byNumber[numbers[i]]]) { map[byNumber[numbers[i]]] = absoluteUrl(r.getValue('url')); }
                    }
                }
                return true;
            });
        } catch (e) {
            log.debug({ title: 'bsgshop: supplier image folder search failed', details: safeErr(e) });
        }
        return map;
    }

    // Best-effort last resort: load the actual item record and scan every field on
    // it for anything that looks like an image (field id or label mentions
    // image/photo/picture) and holds either a URL or a File Cabinet reference.
    // Covers vendor-sync custom fields without knowing their id in advance. Capped
    // by the caller (IMAGE_SCAN_MAX_PER_PAGE) since each call is a record.load.
    var ITEM_RECORD_TYPE_BY_CODE = {
        InvtPart: 'inventoryitem', NonInvtPart: 'noninventoryitem', Kit: 'kititem',
        Assembly: 'assemblyitem', Service: 'serviceitem', OthCharge: 'otherchargeitem',
        Group: 'itemgroup', Description: 'descriptionitem', Markup: 'markupitem',
        Discount: 'discountitem', Payment: 'paymentitem', Subtotal: 'subtotalitem',
        GiftCert: 'giftcertificateitem', LotNumberedInventoryItem: 'lotnumberedinventoryitem',
        SerializedInventoryItem: 'serializedinventoryitem', LotNumberedAssemblyItem: 'lotnumberedassemblyitem',
        SerializedAssemblyItem: 'serializedassemblyitem', DownloadItem: 'downloaditem'
    };
    var deepScanCache = {};
    function deepImageScan(itemId, typeCode) {
        if (deepScanCache.hasOwnProperty(itemId)) { return deepScanCache[itemId]; }
        var result = '';
        var rtype = ITEM_RECORD_TYPE_BY_CODE[typeCode];
        if (rtype) {
            try {
                var rec = record.load({ type: rtype, id: itemId, isDynamic: false });
                var fieldIds = rec.getFields() || [];
                for (var i = 0; i < fieldIds.length; i++) {
                    var fid = fieldIds[i];
                    var imagey = /image|photo|picture/i.test(fid);
                    if (!imagey) {
                        try {
                            var fo = rec.getField({ fieldId: fid });
                            imagey = !!(fo && /image|photo|picture/i.test(fo.label || ''));
                        } catch (eLabel) { /* skip */ }
                    }
                    if (!imagey) { continue; }
                    var val;
                    try { val = rec.getValue({ fieldId: fid }); } catch (eVal) { continue; }
                    if (!val) { continue; }
                    if (typeof val === 'string' && /^https?:\/\//i.test(val)) { result = val; break; }
                    if (typeof val === 'string' && /\.(jpe?g|png|gif|webp)(\?.*)?$/i.test(val)) { result = absoluteUrl(val); break; }
                    if (/^\d+$/.test(String(val))) {
                        var u = fileUrl(val);
                        if (u) { result = u; break; }
                    }
                }
            } catch (e) {
                log.debug({ title: 'bsgshop deep image scan failed for item ' + itemId, details: safeErr(e) });
            }
        }
        deepScanCache[itemId] = result;
        return result;
    }

    function stockStatus(qty) {
        if (qty <= 0) { return { code: 'out', label: 'Out of Stock', orderable: false }; }
        if (qty <= C.CONFIG.LOW_STOCK_THRESHOLD) { return { code: 'low', label: 'Low Stock', orderable: true }; }
        return { code: 'in', label: 'In Stock', orderable: true };
    }

    // -------------------------------------------------- school (customer) search --

    function searchSchools(q) {
        q = (q || '').trim();
        if (q.length < C.CONFIG.SCHOOL_SEARCH_MIN_CHARS) {
            return { ok: true, schools: [] };
        }
        var schools = [];
        var seen = {};
        search.create({
            type: search.Type.CUSTOMER,
            // entityid is the account NUMBER here; the school name is companyname.
            // Match on either so "Mount Horeb" and a known account # both work.
            filters: [
                ['isinactive', 'is', 'F'], 'AND',
                [
                    [C.CUSTOMER_FIELD.COMPANY_NAME, 'contains', q], 'OR',
                    [C.CUSTOMER_FIELD.ENTITY_ID, 'contains', q]
                ]
            ],
            // Only public-ish identity fields are returned -- never pricing, history,
            // or contacts -- so this can't be used to mine the customer base.
            columns: [
                search.createColumn({ name: C.CUSTOMER_FIELD.COMPANY_NAME, sort: search.Sort.ASC }),
                C.CUSTOMER_FIELD.ENTITY_ID,
                C.CUSTOMER_FIELD.CITY,
                C.CUSTOMER_FIELD.STATE
            ]
        }).run().each(function (r) {
            // A customer with more than one address returns one row PER address --
            // the city/state columns join the address sublist -- so the same school
            // would otherwise appear many times. Collapse to one entry per customer.
            var id = r.id;
            if (seen[id]) { return true; }
            seen[id] = true;
            schools.push({
                id: id,
                name: r.getValue(C.CUSTOMER_FIELD.COMPANY_NAME) || r.getValue(C.CUSTOMER_FIELD.ENTITY_ID),
                number: r.getValue(C.CUSTOMER_FIELD.ENTITY_ID) || '',
                city: r.getValue(C.CUSTOMER_FIELD.CITY) || '',
                state: r.getValue(C.CUSTOMER_FIELD.STATE) || ''
            });
            return schools.length < C.CONFIG.SCHOOL_SEARCH_LIMIT;
        });
        return { ok: true, schools: schools };
    }

    // Distinct values of one item field, for a filter rail section. Grouped
    // (summary) search so it's a single efficient query.
    //
    // Scoped to what the shopper has ALREADY narrowed to (params), minus this
    // facet's own selection. That is what stops the rail offering dead ends: a
    // grouped search only returns values that actually have matching rows, so
    // with Sport = Softball the Categories list simply won't contain a class that
    // holds no softball items. Previously every facet listed the whole catalog's
    // values, so "First Aid - Ice Packs" appeared under Softball and led to an
    // empty page -- which reads to a shopper as "they don't stock ice packs".
    //
    // `count` is the number of matching ITEMS. Where flat vendor variants are
    // grouped into one card (GROUP_FLAT_VARIANTS) that can exceed the number of
    // cards shown; it's a relative "where is the product" signal, not a promise.
    // Returns [] (and the UI hides that filter) if the field can't be grouped here.
    function distinctFieldValues(fieldId, params, exceptKey) {
        var out = [];
        function run(includeVendorQty) {
            out.length = 0;
            search.create({
                type: 'item',
                filters: catalogFilters(params || {}, includeVendorQty, exceptKey),
                columns: [
                    search.createColumn({ name: fieldId, summary: search.Summary.GROUP, sort: search.Sort.ASC }),
                    search.createColumn({ name: 'internalid', summary: search.Summary.COUNT })
                ]
            }).run().each(function (r) {
                var id = r.getValue({ name: fieldId, summary: search.Summary.GROUP });
                var name = r.getText({ name: fieldId, summary: search.Summary.GROUP }) || id;
                var n = parseInt(r.getValue({ name: 'internalid', summary: search.Summary.COUNT }), 10) || 0;
                if (id) { out.push({ id: id, name: String(name), count: n }); }
                return out.length < 200;
            });
        }
        try { run(true); }
        catch (e0) {
            try { run(false); }
            catch (e) {
                out.length = 0;
                log.debug({ title: 'bsgshop filter values unavailable (' + fieldId + ')', details: safeErr(e) });
            }
        }
        return out;
    }

    // The header filter set: for each configured filter (Department / Class /
    // Category / Vendor), its distinct in-catalog values. Filters that yield no
    // values are dropped so the UI only shows dimensions BSG actually uses.
    function listFilters(params) {
        params = params || {};
        var filters = C.CONFIG.FILTERS.map(function (fdef) {
            return {
                key: fdef.key, label: fdef.label,
                hierarchical: !!fdef.hierarchical,
                options: distinctFieldValues(fdef.field, params, fdef.key)
            };
        }).filter(function (f) { return f.options.length; });
        return { ok: true, filters: filters };
    }

    // The facet lists now depend on what's selected, so they can't share one
    // cache entry. Keyed on the facet selections only -- deliberately NOT on the
    // free-text search, which is unbounded and would fill the cache with
    // single-use entries.
    function filtersCacheKey(params) {
        var parts = C.CONFIG.FILTERS.map(function (fdef) {
            return fdef.key + '=' + ((params[fdef.key] || '').trim());
        });
        return 'filters:v2:' + parts.join('&');
    }

    // Called when a filter combination matches nothing. Drops one active facet at
    // a time to find a wider search that does have results, so the empty page can
    // offer a way forward instead of dead-ending. Returns the drop that recovers
    // the most product, or null when nothing helps.
    function broadenSuggestion(params) {
        var active = C.CONFIG.FILTERS.filter(function (fdef) {
            return (params[fdef.key] || '').trim();
        });
        // Worth suggesting even when only ONE facet is set: searching "ice pack"
        // with Sport = Softball finds nothing, and dropping the sport finds them.
        // That single-filter case is the likeliest way a shopper hits an empty
        // page now that the rail no longer offers empty combinations.
        if (!active.length) { return null; }
        var best = null;
        active.forEach(function (fdef) {
            var relaxed = {};
            Object.keys(params).forEach(function (k) { relaxed[k] = params[k]; });
            relaxed[fdef.key] = '';
            var n = 0;
            try {
                n = search.create({
                    type: 'item',
                    filters: catalogFilters(relaxed, true),
                    columns: [search.createColumn({ name: 'internalid', summary: search.Summary.COUNT })]
                }).run().getRange({ start: 0, end: 1 });
                n = n && n.length ? (parseInt(n[0].getValue({ name: 'internalid', summary: search.Summary.COUNT }), 10) || 0) : 0;
            } catch (e) { n = 0; }
            if (n > 0 && (!best || n > best.count)) {
                best = { dropKey: fdef.key, dropLabel: fdef.label, count: n };
            }
        });
        return best;
    }

    // --------------------------------------------------------------- submit --

    function submitOrder(payload) {
        payload = payload || {};
        var items = Array.isArray(payload.items) ? payload.items : [];
        var isOrder = payload.type === C.SUBMIT_TYPE.ORDER;

        // ---- abuse guards for the public, anonymous endpoint ----
        // Honeypot: a hidden field no human fills; if it's populated, it's a bot.
        if (payload.hp) {
            log.audit({ title: 'bsgshop: submission blocked (honeypot)', details: '' });
            return { ok: false, error: 'Submission blocked.' };
        }
        // Time gate: a real customer takes seconds to browse+fill; a sub-1.5s submit
        // (measured from page load) is automated.
        var elapsed = num(payload.elapsed);
        if (elapsed > 0 && elapsed < 1500) {
            return { ok: false, error: 'Please take a moment to review your order and submit again.' };
        }
        // Idempotency: a double-click / retry with the same token returns the SAME
        // result instead of creating a second order (also blocks rapid duplicates).
        var idemToken = cleanPersonalization(payload.token, 80);
        if (idemToken) {
            var prior = idempotentGet(idemToken);
            if (prior) { return prior; }
        }

        // A "request" carries team info + roster sizing and/or a custom uniform
        // design (from the Uniform Builder). Either may ride along with cart items
        // or be submitted on its own.
        var team = (payload.team && typeof payload.team === 'object') ? payload.team : null;
        var roster = Array.isArray(payload.roster) ? payload.roster : [];
        var design = (payload.design && typeof payload.design === 'object') ? payload.design : null;
        var teamText = buildTeamSummary(team, roster);
        var designText = buildDesignSummary(design);
        var requestText = [teamText, designText].filter(Boolean).join(' | ');
        var isRequest = !!requestText;
        // Save the builder's mock-up PNG to the File Cabinet (best-effort).
        var mockupFileId = payload.mockup ? saveMockupFile(payload.mockup, String((new Date()).getTime())) : null;

        // ---- validation ----
        if (!payload.schoolId) { return { ok: false, error: 'Please select your school.' }; }
        if (!isValidEmail(payload.email)) { return { ok: false, error: 'Please enter a valid email address.' }; }
        var cleanItems = [];
        for (var i = 0; i < items.length; i++) {
            var qid = items[i] && items[i].id;
            var qty = num(items[i] && items[i].qty);
            if (qid && qty > 0) {
                cleanItems.push({
                    id: qid,
                    qty: qty,
                    playerName: cleanPersonalization(items[i] && items[i].playerName, 40),
                    playerNumber: cleanPersonalization(items[i] && items[i].playerNumber, 10)
                });
            }
        }
        // A pure request (roster and/or design) needs no cart; an order/quote does.
        if (!cleanItems.length && !isRequest) { return { ok: false, error: 'Your cart is empty.' }; }
        if (isOrder && !String(payload.po || '').trim()) {
            return { ok: false, error: 'A Purchase Order number is required to place an order. Choose "Request a Quote" if you do not have a PO yet.' };
        }

        // Request with no cart items (roster and/or custom uniform design): no line
        // items to build a transaction from, so capture it as a follow-up request
        // emailed to the account's rep (and confirmed to the coach), with the
        // mock-up attached. A rep builds the priced quote/proof from the details.
        if (!cleanItems.length) {
            try {
                sendRequestEmail(payload, mockupFileId);
            } catch (e) {
                log.error({ title: 'bsgshop request email failed', details: safeErr(e) });
                return { ok: false, error: 'We could not submit your request. Please try again in a moment.' };
            }
            var reqResult = { ok: true, docType: design ? 'Design Request' : 'Team Request', id: null, docNumber: '', mockupFileId: mockupFileId };
            if (idemToken) { idempotentPut(idemToken, reqResult); }
            return reqResult;
        }

        // ---- create the transaction ----
        var recType = isOrder ? record.Type.SALES_ORDER : record.Type.ESTIMATE;
        var rec = record.create({ type: recType, isDynamic: true });
        rec.setValue({ fieldId: 'entity', value: payload.schoolId });
        rec.setValue({ fieldId: 'email', value: String(payload.email).trim() });
        if (isOrder) {
            rec.setValue({ fieldId: 'otherrefnum', value: String(payload.po).trim() }); // PO #
            // Pending Approval -> a human reviews every anonymous submission before fulfillment.
            trySetValue(rec, 'orderstatus', 'A');
        }
        var memo = C.CONFIG.ORDER_MEMO + ' | Contact: ' + (payload.name || '(none)') + ' <' + payload.email + '>';
        if (requestText) { memo += ' | ' + requestText; }
        if (payload.notes) { memo += ' | Notes: ' + String(payload.notes).slice(0, 300); }
        rec.setValue({ fieldId: 'memo', value: memo.slice(0, 4000) });

        cleanItems.forEach(function (it) {
            rec.selectNewLine({ sublistId: 'item' });
            rec.setCurrentSublistValue({ sublistId: 'item', fieldId: 'item', value: it.id });
            rec.setCurrentSublistValue({ sublistId: 'item', fieldId: 'quantity', value: it.qty });
            // Personalization -> line custom column fields. Best-effort: if the field
            // isn't present in the account (not yet deployed), swallow and move on so
            // order creation never fails over a decoration field.
            if (it.playerName) { trySetSublistValue(rec, 'item', C.LINE_FIELD.PLAYER_NAME, it.playerName); }
            if (it.playerNumber) { trySetSublistValue(rec, 'item', C.LINE_FIELD.PLAYER_NUMBER, it.playerNumber); }
            rec.commitLine({ sublistId: 'item' });
        });

        var recordId = rec.save({ enableSourcing: true, ignoreMandatoryFields: true });

        // Attach the uniform mock-up (if any) to the transaction so the art team
        // sees it right on the order/quote.
        if (mockupFileId) {
            try { record.attach({ record: { type: 'file', id: mockupFileId }, to: { type: recType, id: recordId } }); }
            catch (eA) { log.error({ title: 'bsgshop: mockup attach failed', details: safeErr(eA) }); }
        }

        var docNumber = '';
        try {
            docNumber = search.lookupFields({ type: recType, id: recordId, columns: ['tranid'] }).tranid || '';
        } catch (e) { /* non-fatal */ }

        var docType = isOrder ? 'Order' : 'Quote';
        try {
            sendConfirmation(payload, docType, docNumber, cleanItems, requestText, mockupFileId);
        } catch (e2) {
            log.error({ title: 'bsgshop confirmation email failed', details: safeErr(e2) });
        }
        // An order/quote that also carries a team roster or custom design: give the
        // account's rep the heads-up too, with the mock-up attached.
        if (isRequest) {
            try { notifyRep(payload, docType, docNumber, mockupFileId); }
            catch (e3) { log.error({ title: 'bsgshop rep notify failed', details: safeErr(e3) }); }
        }

        var result = { ok: true, docType: docType, id: recordId, docNumber: docNumber, mockupFileId: mockupFileId };
        if (idemToken) { idempotentPut(idemToken, result); }
        return result;
    }

    // Idempotency store for submissions (double-click / retry safety). A short-lived
    // server cache keyed by the client's per-checkout token; a repeat within the TTL
    // returns the first result instead of creating a duplicate order.
    function submitCache() {
        return cache.getCache({ name: 'bsgshop_submit', scope: cache.Scope.PROTECTED });
    }
    function idempotentGet(token) {
        try { var v = submitCache().get({ key: token }); return v ? JSON.parse(v) : null; }
        catch (e) { return null; }
    }
    function idempotentPut(token, result) {
        try { submitCache().put({ key: token, value: JSON.stringify(result), ttl: 300 }); }
        catch (e) { /* cache best-effort */ }
    }

    // --------------------------------------------------------------- track --
    // Best-effort store analytics: one append-only row per high-signal shopper
    // action. Deliberately forgiving and fully wrapped -- a tracking failure must
    // NEVER surface to the customer or slow the page, so every path returns
    // { ok: true } (even when nothing is written). Only whitelisted event names are
    // persisted, so the public endpoint can't be used to write arbitrary rows.
    function trackEvent(payload) {
        payload = payload || {};
        if (!C.CONFIG.ANALYTICS_ENABLED) { return { ok: true, tracked: false }; }
        var evt = cleanPersonalization(payload.event, 40);
        if (!evt || C.ANALYTICS.EVENTS.indexOf(evt) === -1) { return { ok: true, tracked: false }; }
        if (evt === 'page_view' && !C.CONFIG.ANALYTICS_TRACK_PAGE_VIEWS) { return { ok: true, tracked: false }; }
        try {
            var rec = record.create({ type: C.ANALYTICS.RECORD, isDynamic: false });
            rec.setValue({ fieldId: C.ANALYTICS.FIELD.TYPE, value: evt });
            var ref = cleanPersonalization(payload.ref, 300);
            if (ref) { rec.setValue({ fieldId: C.ANALYTICS.FIELD.REF, value: ref }); }
            var sid = cleanPersonalization(payload.sid, 60);
            if (sid) { rec.setValue({ fieldId: C.ANALYTICS.FIELD.SESSION, value: sid }); }
            var school = cleanPersonalization(payload.school, 200);
            if (school) { rec.setValue({ fieldId: C.ANALYTICS.FIELD.SCHOOL, value: school }); }
            var value = num(payload.value);
            if (value) { rec.setValue({ fieldId: C.ANALYTICS.FIELD.VALUE, value: value }); }
            var detail = cleanPersonalization(payload.detail, 3000);
            if (detail) { rec.setValue({ fieldId: C.ANALYTICS.FIELD.DETAIL, value: detail }); }
            rec.save({ ignoreMandatoryFields: true });
            return { ok: true, tracked: true };
        } catch (e) {
            // Swallow: analytics is never allowed to break the store. Logged at
            // debug level so it doesn't spam the execution log if the record type
            // isn't deployed yet or the role lacks create permission.
            log.debug({ title: 'bsgshop track skipped', details: safeErr(e) });
            return { ok: true, tracked: false };
        }
    }

    // Compact one-line summary of the team info + roster-sizing grid, used in the
    // transaction memo and the emails. Returns '' when nothing usable was entered.
    function buildTeamSummary(team, roster) {
        var parts = [];
        if (team) {
            if (team.name) { parts.push('Team: ' + cleanPersonalization(team.name, 80)); }
            if (team.sport) { parts.push('Sport: ' + cleanPersonalization(team.sport, 40)); }
            if (team.phone) { parts.push('Phone: ' + cleanPersonalization(team.phone, 30)); }
        }
        var rows = (roster || []).map(function (r) {
            var sz = cleanPersonalization(r && r.size, 20);
            var j = num(r && r.jerseys), s = num(r && r.shorts);
            if (!sz || (j <= 0 && s <= 0)) { return ''; }
            var q = [];
            if (j > 0) { q.push(j + ' jersey' + (j === 1 ? '' : 's')); }
            if (s > 0) { q.push(s + ' short' + (s === 1 ? '' : 's')); }
            return sz + ' = ' + q.join(', ');
        }).filter(Boolean);
        if (rows.length) { parts.push('Roster sizing: ' + rows.join('; ')); }
        return parts.join(' | ');
    }

    // Request with no transaction (roster and/or custom uniform design): email the
    // account's rep the details + mock-up, and a confirmation to the coach.
    function sendRequestEmail(payload, mockupFileId) {
        var author = resolveAuthorId();
        var toRep = repRecipient(payload.schoolId, true); // always route somewhere
        var schoolName = lookupSchoolName(payload.schoolId);
        var atts = mockupAttachments(mockupFileId);
        var isDesign = !!(payload.design && typeof payload.design === 'object');
        var kind = isDesign ? 'Custom Uniform' : 'Team Order';
        var body =
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;max-width:600px;">' +
            '<h2 style="color:#ec3013;margin:0 0 8px;">New ' + kind + ' request</h2>' +
            '<p><strong>School:</strong> ' + escapeHtml(schoolName || payload.schoolId) + '</p>' +
            '<p><strong>Contact:</strong> ' + escapeHtml(payload.name || '(none)') + ' &lt;' + escapeHtml(payload.email) + '&gt;</p>' +
            teamRequestHtml(payload) + designHtml(payload) + mockupNoteHtml(mockupFileId) +
            (payload.notes ? '<p><strong>Notes:</strong> ' + escapeHtml(String(payload.notes).slice(0, 1000)) + '</p>' : '') +
            '<p style="color:#888;font-size:12px;">Submitted via the BSG Ordering Site.</p></div>';

        if (author && toRep) {
            email.send({ author: author, recipients: toRep, subject: kind + ' request - ' + (schoolName || 'BSG storefront'), body: body, attachments: atts });
        }
        if (author && isValidEmail(payload.email)) {
            var ack =
                '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;max-width:600px;">' +
                '<h2 style="color:#ec3013;margin:0 0 8px;">Request received' + (payload.name ? ', ' + escapeHtml(payload.name) : '') + '!</h2>' +
                '<p>Thanks &mdash; we&rsquo;ve got your ' + (isDesign ? 'design' : 'team') + ' details. A Badger Sporting Goods rep will ' +
                'follow up with pricing, colorways, and a proof, usually within one business day.</p>' +
                teamRequestHtml(payload) + designHtml(payload) + '</div>';
            email.send({ author: author, recipients: String(payload.email).trim(), subject: 'We received your Badger ' + (isDesign ? 'uniform' : 'team') + ' request', body: ack, attachments: atts });
        }
    }

    // Heads-up to the rep when an order/quote ALSO carried a roster or design.
    function notifyRep(payload, docType, docNumber, mockupFileId) {
        var author = resolveAuthorId();
        var toRep = repRecipient(payload.schoolId, true);
        if (!author || !toRep) { return; }
        var schoolName = lookupSchoolName(payload.schoolId);
        var body =
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;max-width:600px;">' +
            '<h2 style="color:#ec3013;margin:0 0 8px;">' + escapeHtml(docType) + ' submitted' +
            (docNumber ? ' (' + escapeHtml(docNumber) + ')' : '') + '</h2>' +
            '<p><strong>School:</strong> ' + escapeHtml(schoolName || payload.schoolId) + '</p>' +
            '<p><strong>Contact:</strong> ' + escapeHtml(payload.name || '(none)') + ' &lt;' + escapeHtml(payload.email) + '&gt;</p>' +
            teamRequestHtml(payload) + designHtml(payload) + mockupNoteHtml(mockupFileId) + '</div>';
        email.send({ author: author, recipients: toRep, subject: docType + ' - ' + (schoolName || 'BSG storefront'), body: body, attachments: mockupAttachments(mockupFileId) });
    }

    // The team info + roster grid rendered as an HTML block for the emails.
    function teamRequestHtml(payload) {
        var team = (payload.team && typeof payload.team === 'object') ? payload.team : {};
        var roster = Array.isArray(payload.roster) ? payload.roster : [];
        var info = '';
        if (team.name) { info += '<p><strong>Team:</strong> ' + escapeHtml(team.name) + '</p>'; }
        if (team.sport) { info += '<p><strong>Sport:</strong> ' + escapeHtml(team.sport) + '</p>'; }
        if (team.phone) { info += '<p><strong>Phone:</strong> ' + escapeHtml(team.phone) + '</p>'; }
        if (team.product) { info += '<p><strong>Sizing based on:</strong> ' + escapeHtml(String(team.product).slice(0, 120)) + '</p>'; }
        if (team.sampleArt) { info += '<p><strong>Sample art reference:</strong> ' + escapeHtml(String(team.sampleArt).slice(0, 120)) + '</p>'; }
        var rows = roster.map(function (r) {
            var sz = r && r.size ? String(r.size) : '';
            var j = num(r && r.jerseys), s = num(r && r.shorts);
            if (!sz || (j <= 0 && s <= 0)) { return ''; }
            return '<tr><td style="padding:4px 10px;border-bottom:1px solid #eee;">' + escapeHtml(sz) +
                '</td><td style="padding:4px 10px;border-bottom:1px solid #eee;text-align:right;">' + (j > 0 ? j : '&ndash;') +
                '</td><td style="padding:4px 10px;border-bottom:1px solid #eee;text-align:right;">' + (s > 0 ? s : '&ndash;') + '</td></tr>';
        }).filter(Boolean).join('');
        var table = rows
            ? '<table style="border-collapse:collapse;margin-top:6px;"><thead><tr>' +
              '<th style="text-align:left;padding:4px 10px;border-bottom:2px solid #ec3013;">Size</th>' +
              '<th style="text-align:right;padding:4px 10px;border-bottom:2px solid #ec3013;">Jerseys</th>' +
              '<th style="text-align:right;padding:4px 10px;border-bottom:2px solid #ec3013;">Shorts</th>' +
              '</tr></thead><tbody>' + rows + '</tbody></table>'
            : '';
        return info + table;
    }

    // Map a hex swatch back to its friendly colour name (falls back to the hex).
    function colorName(hex) {
        var h = String(hex || '').toLowerCase();
        var list = C.CONFIG.BUILDER_COLORS || [];
        for (var i = 0; i < list.length; i++) {
            if (String(list[i].hex).toLowerCase() === h) { return list[i].name + ' (' + h + ')'; }
        }
        return h || '';
    }

    // Compact one-line design summary for the transaction memo.
    function buildDesignSummary(design) {
        if (!design) { return ''; }
        var parts = [];
        if (design.garment) { parts.push('Garment: ' + cleanPersonalization(design.garment, 60)); }
        if (design.momentecRef) { parts.push('Momentec design ref: ' + cleanPersonalization(design.momentecRef, 80)); }
        if (design.summary) { parts.push(cleanPersonalization(design.summary, 200)); }
        var z = design.zones || {}, zc = [];
        if (z.body) { zc.push('body ' + colorName(z.body)); }
        if (z.sleeves) { zc.push('sleeves ' + colorName(z.sleeves)); }
        if (z.trim) { zc.push('trim ' + colorName(z.trim)); }
        if (zc.length) { parts.push('Colours: ' + zc.join(', ')); }
        if (design.teamName) { parts.push('Team text: ' + cleanPersonalization(design.teamName, 40)); }
        if (design.playerName) { parts.push('Player: ' + cleanPersonalization(design.playerName, 40)); }
        if (design.number) { parts.push('#' + cleanPersonalization(design.number, 10)); }
        if (design.font) { parts.push('Font: ' + cleanPersonalization(design.font, 40)); }
        return parts.length ? ('Custom uniform: ' + parts.join(' | ')) : '';
    }

    // The custom-uniform design spec rendered as an HTML block for the emails.
    function designHtml(payload) {
        var d = (payload.design && typeof payload.design === 'object') ? payload.design : null;
        if (!d) { return ''; }
        var z = d.zones || {}, rows = '';
        function addRow(label, val) {
            if (val) {
                rows += '<tr><td style="padding:3px 10px;color:#666;border-bottom:1px solid #eee;">' + escapeHtml(label) +
                    '</td><td style="padding:3px 10px;font-weight:700;border-bottom:1px solid #eee;">' + escapeHtml(val) + '</td></tr>';
            }
        }
        addRow('Garment', d.garment);
        addRow('Momentec design ref', d.momentecRef);
        addRow('Body colour', z.body ? colorName(z.body) : '');
        addRow('Sleeve colour', z.sleeves ? colorName(z.sleeves) : '');
        addRow('Trim colour', z.trim ? colorName(z.trim) : '');
        addRow('Team text', d.teamName);
        addRow('Player name', d.playerName);
        addRow('Number', d.number);
        addRow('Lettering', d.font);
        if (d.summary) {
            rows += '<tr><td style="padding:3px 10px;color:#666;border-bottom:1px solid #eee;">Details</td>' +
                '<td style="padding:3px 10px;border-bottom:1px solid #eee;">' + escapeHtml(String(d.summary).slice(0, 600)) + '</td></tr>';
        }
        if (!rows) { return ''; }
        return '<h3 style="margin:14px 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#ec3013;">Custom Uniform Design</h3>' +
            '<table style="border-collapse:collapse;">' + rows + '</table>';
    }

    // Note in the email pointing at the attached mock-up (attachments carry the PNG).
    function mockupNoteHtml(mockupFileId) {
        if (!mockupFileId) { return ''; }
        return '<p style="margin-top:10px;"><strong>Mock-up:</strong> attached to this email' +
            ' (also filed in the File Cabinet / on the transaction).</p>';
    }

    // Load the saved mock-up as an email attachment array (or null).
    function mockupAttachments(fileId) {
        if (!fileId) { return null; }
        try { return [file.load({ id: fileId })]; } catch (e) { return null; }
    }

    // Save a builder mock-up (data: URL PNG/JPEG) to the File Cabinet. Best-effort:
    // returns the new file id, or null if anything goes wrong (spec still emails).
    function saveMockupFile(dataUrl, label) {
        try {
            var m = /^data:image\/(png|jpe?g);base64,([\s\S]+)$/i.exec(String(dataUrl || ''));
            if (!m) { return null; }
            var isPng = m[1].toLowerCase() === 'png';
            var folderId = resolveMockupFolder();
            if (!folderId) { return null; }
            var f = file.create({
                name: 'uniform-mockup-' + label + (isPng ? '.png' : '.jpg'),
                fileType: isPng ? file.Type.PNGIMAGE : file.Type.JPGIMAGE,
                contents: m[2],
                folder: folderId,
                isOnline: true
            });
            return f.save();
        } catch (e) {
            log.error({ title: 'bsgshop: mockup save failed', details: safeErr(e) });
            return null;
        }
    }

    // File Cabinet folder for mock-ups: a configured id, else found/created by name.
    var mockupFolderCache;
    function resolveMockupFolder() {
        if (mockupFolderCache !== undefined) { return mockupFolderCache; }
        var id = C.CONFIG.MOCKUP_FOLDER_ID || null;
        if (!id) {
            try {
                search.create({
                    type: 'folder',
                    filters: [['name', 'is', C.CONFIG.MOCKUP_FOLDER_NAME]],
                    columns: ['internalid']
                }).run().each(function (r) { id = r.id; return false; });
                if (!id) {
                    var fr = record.create({ type: 'folder' });
                    fr.setValue({ fieldId: 'name', value: C.CONFIG.MOCKUP_FOLDER_NAME });
                    id = fr.save();
                }
            } catch (e) {
                log.error({ title: 'bsgshop: mockup folder resolve failed', details: safeErr(e) });
                id = null;
            }
        }
        mockupFolderCache = id;
        return id;
    }

    // Best-effort school display name for the emails.
    function lookupSchoolName(schoolId) {
        if (!schoolId) { return ''; }
        try {
            var f = search.lookupFields({
                type: 'customer', id: schoolId,
                columns: [C.CUSTOMER_FIELD.COMPANY_NAME, C.CUSTOMER_FIELD.ENTITY_ID]
            });
            return f[C.CUSTOMER_FIELD.COMPANY_NAME] || f[C.CUSTOMER_FIELD.ENTITY_ID] || '';
        } catch (e) { return ''; }
    }

    // Email of the Sales Rep assigned to the chosen account (customer.salesrep),
    // resolved to the employee's email. '' when there's no assigned/active rep.
    var repEmailCache = {};
    function resolveAssignedRepEmail(schoolId) {
        if (!C.CONFIG.ROUTE_TO_ASSIGNED_REP || !schoolId) { return ''; }
        if (repEmailCache.hasOwnProperty(schoolId)) { return repEmailCache[schoolId]; }
        var out = '';
        try {
            var cf = search.lookupFields({ type: 'customer', id: schoolId, columns: [C.CUSTOMER_FIELD.SALES_REP] });
            var rep = cf[C.CUSTOMER_FIELD.SALES_REP];
            var repId = (Array.isArray(rep) && rep.length) ? rep[0].value : (rep || '');
            if (repId) {
                var ef = search.lookupFields({ type: 'employee', id: repId, columns: ['email', 'isinactive'] });
                if (ef && ef.email && ef.isinactive !== true) { out = String(ef.email); }
            }
        } catch (e) {
            log.debug({ title: 'bsgshop: assigned-rep lookup failed', details: safeErr(e) });
        }
        repEmailCache[schoolId] = out;
        return out;
    }

    // Who gets the internal notification: the account's assigned rep, else the
    // shared notify mailbox. `guaranteed` (team requests) also falls back to the
    // submit author so a roster request is never silently dropped.
    function repRecipient(schoolId, guaranteed) {
        return resolveAssignedRepEmail(schoolId) ||
            C.CONFIG.BSG_NOTIFY_EMAIL ||
            (guaranteed ? C.CONFIG.SUBMIT_AUTHOR_EMAIL : '') || '';
    }

    function sendConfirmation(payload, docType, docNumber, items, requestText, mockupFileId) {
        var author = resolveAuthorId();
        if (!author) {
            log.error({ title: 'bsgshop: no email author resolved', details: 'Set CONFIG.SUBMIT_AUTHOR_EMAIL/EMPLOYEE_ID to a valid employee.' });
            return;
        }
        var teamBlock = requestText ? ('<div style="margin-top:14px;">' + teamRequestHtml(payload) + designHtml(payload) + '</div>') : '';
        var atts = mockupAttachments(mockupFileId);

        var lines = items.map(function (it) {
            var player = [it.playerName, it.playerNumber ? '#' + it.playerNumber : '']
                .filter(Boolean).join(' ');
            var itemCell = escapeHtml(it.name || it.id) +
                (player ? '<br><span style="color:#ec3013;font-size:12px;">Player: ' + escapeHtml(player) + '</span>' : '');
            return '<tr><td style="padding:4px 8px;border-bottom:1px solid #eee;">' + itemCell +
                '</td><td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right;vertical-align:top;">' + it.qty + '</td></tr>';
        }).join('');

        var body =
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;max-width:600px;">' +
            '<h2 style="color:#ec3013;margin:0 0 8px;">Thank you' + (payload.name ? ', ' + escapeHtml(payload.name) : '') + '!</h2>' +
            '<p>We\'ve received your <strong>' + docType.toLowerCase() + '</strong> request' +
            (docNumber ? ' (<strong>' + escapeHtml(docNumber) + '</strong>)' : '') + '.</p>' +
            (payload.po ? '<p>PO #: <strong>' + escapeHtml(payload.po) + '</strong></p>' : '') +
            '<p>A Badger Sporting Goods representative will review it and follow up shortly. ' +
            'This is a confirmation of receipt, not a final invoice &mdash; pricing and availability are confirmed on review.</p>' +
            '<table style="border-collapse:collapse;margin-top:8px;"><thead><tr>' +
            '<th style="text-align:left;padding:4px 8px;border-bottom:2px solid #ec3013;">Item</th>' +
            '<th style="text-align:right;padding:4px 8px;border-bottom:2px solid #ec3013;">Qty</th></tr></thead>' +
            '<tbody>' + lines + '</tbody></table>' +
            teamBlock +
            '<p style="color:#888;font-size:12px;margin-top:16px;">Badger Sporting Goods</p></div>';

        var recipients = [String(payload.email).trim()];
        email.send({
            author: author,
            recipients: recipients,
            subject: 'Badger Sporting Goods &mdash; ' + docType + ' received' + (docNumber ? ' (' + docNumber + ')' : ''),
            body: body,
            attachments: atts
        });

        // Internal copy -> the account's assigned Sales Rep (or the shared notify
        // mailbox). Not "guaranteed": a plain order without an assigned rep and no
        // BSG_NOTIFY_EMAIL simply gets no internal copy (the transaction already
        // exists in NetSuite for staff to see) rather than emailing the author.
        var toRep = repRecipient(payload.schoolId, false);
        if (toRep) {
            email.send({
                author: author,
                recipients: [toRep],
                subject: 'New ordering-site ' + docType + (docNumber ? ' ' + docNumber : '') + ' from ' + (payload.name || payload.email),
                body: body,
                attachments: atts
            });
        }
    }

    // ---------------------------------------------------------- store page --

    function renderStorePage(response) {
        var suiteletUrl = url.resolveScript({
            scriptId: C.SCRIPT.STORE_SUITELET.scriptId,
            deploymentId: C.SCRIPT.STORE_SUITELET.deploymentId,
            returnExternalUrl: true
        });

        var clientJs = '';
        try {
            clientJs = file.load({ id: CLIENT_SCRIPT_PATH }).getContents();
        } catch (e) {
            log.error({ title: 'bsgshop: client script not found', details: CLIENT_SCRIPT_PATH });
        }

        var boot = JSON.stringify({
            apiUrl: suiteletUrl,
            pageSize: C.CONFIG.PAGE_SIZE,
            pageSizes: C.CONFIG.PAGE_SIZE_OPTIONS,
            title: C.CONFIG.STORE_TITLE,
            minSchoolChars: C.CONFIG.SCHOOL_SEARCH_MIN_CHARS,
            sports: C.CONFIG.TEAM_SPORTS,
            rosterSizes: C.CONFIG.ROSTER_SIZES,
            builderEnabled: !!C.CONFIG.UNIFORM_BUILDER_ENABLED,
            builderColors: C.CONFIG.BUILDER_COLORS,
            builderFonts: C.CONFIG.BUILDER_FONTS,
            momentec: C.CONFIG.MOMENTEC_ENABLED ? {
                base: C.CONFIG.MOMENTEC_EMBED_BASE,
                discount: C.CONFIG.MOMENTEC_DISCOUNT,
                addlLeadTime: C.CONFIG.MOMENTEC_ADDL_LEAD_TIME,
                hideHeader: !!C.CONFIG.MOMENTEC_HIDE_HEADER,
                label: C.CONFIG.MOMENTEC_LABEL,
                categories: C.CONFIG.MOMENTEC_CATEGORIES || [],
                groupLanding: C.CONFIG.MOMENTEC_GROUP_LANDING || {}
            } : null,
            analytics: !!C.CONFIG.ANALYTICS_ENABLED,
            analyticsPageViews: !!C.CONFIG.ANALYTICS_TRACK_PAGE_VIEWS,
            catalogs: C.CONFIG.CATALOGS_ENABLED ? (C.CONFIG.CATALOGS || []) : [],
            sampleArt: sampleArtBoot()
        });

        // Don't let the browser cache the store page HTML (which inlines the client
        // JS) -- otherwise a redeploy's client changes won't load on a plain reload.
        response.addHeader({ name: 'Cache-Control', value: 'no-store, no-cache, must-revalidate, max-age=0' });
        response.write(
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
            '<meta name="viewport" content="width=device-width,initial-scale=1">' +
            '<meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate">' +
            '<title>' + escapeHtml(C.CONFIG.STORE_TITLE) + '</title>' +
            '<link rel="preconnect" href="https://fonts.googleapis.com">' +
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>' +
            '<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&display=swap" rel="stylesheet">' +
            // Inject the brand logo once as a CSS custom property; the header logo
            // and the body watermark both reference var(--bsg-logo).
            '<style>:root{--bsg-logo:url("' + LOGO + '")}' + STORE_CSS + '</style></head>' +
            '<body><div id="bsgshopRoot"><div class="bsg-loading">Loading store&hellip;</div></div>' +
            '<script>window.BSGSHOP_BOOT=' + boot + ';</script>' +
            '<script>' + clientJs + '</script>' +
            '</body></html>'
        );
    }

    // ------------------------------------------------------------- helpers --

    // Resolve the Employee id used as the sender of confirmation emails: an
    // explicit id wins; otherwise look one up by email; otherwise fall back to the
    // running user (null for a true anonymous session, which suppresses the email).
    var authorIdCache;
    function resolveAuthorId() {
        if (authorIdCache !== undefined) { return authorIdCache; }
        var id = null;
        if (C.CONFIG.SUBMIT_AUTHOR_EMPLOYEE_ID) {
            id = C.CONFIG.SUBMIT_AUTHOR_EMPLOYEE_ID;
        } else if (C.CONFIG.SUBMIT_AUTHOR_EMAIL) {
            try {
                search.create({
                    type: 'employee',
                    filters: [['email', 'is', C.CONFIG.SUBMIT_AUTHOR_EMAIL], 'AND', ['isinactive', 'is', 'F']],
                    columns: ['internalid']
                }).run().each(function (r) { id = r.id; return false; });
            } catch (e) { /* fall through */ }
        }
        if (!id) {
            var uid = runtime.getCurrentUser().id;
            id = (uid && Number(uid) > 0) ? uid : null;
        }
        authorIdCache = id;
        return id;
    }

    function fileUrl(fileId) {
        try {
            return absoluteUrl(file.load({ id: fileId }).url);
        } catch (e) {
            return '';
        }
    }

    // The application domain, resolved once and cached. url.resolveDomain can throw
    // a ScriptNullObjectAdapter (no resolvable domain) in some Suitelet contexts --
    // that must never bubble up and take the catalog down, so it's swallowed and we
    // fall back to a domain-relative url (the browser resolves that against the page
    // origin, i.e. the same NetSuite domain the store was served from).
    var appDomainCache;
    function appDomain() {
        if (appDomainCache !== undefined) { return appDomainCache; }
        try {
            appDomainCache = url.resolveDomain({ hostType: url.HostType.APPLICATION }) || '';
        } catch (e) {
            log.debug({ title: 'bsgshop: url.resolveDomain unavailable', details: safeErr(e) });
            appDomainCache = '';
        }
        return appDomainCache;
    }

    // File-cabinet urls come back domain-relative (/core/media/media.nl?id=...).
    function absoluteUrl(rel) {
        rel = String(rel || '');
        if (!rel) { return ''; }
        if (/^https?:\/\//i.test(rel)) { return rel; }
        if (rel.indexOf('//') === 0) { return 'https:' + rel; }
        if (rel.charAt(0) !== '/') { rel = '/' + rel; }
        var d = appDomain();
        return d ? ('https://' + d + rel) : rel; // relative fallback works same-origin
    }

    function trySetValue(rec, fieldId, value) {
        try { rec.setValue({ fieldId: fieldId, value: value }); } catch (e) { /* field may not apply */ }
    }

    function trySetSublistValue(rec, sublistId, fieldId, value) {
        try {
            rec.setCurrentSublistValue({ sublistId: sublistId, fieldId: fieldId, value: value });
        } catch (e) {
            log.debug({ title: 'bsgshop: line field not set (' + fieldId + ')', details: safeErr(e) });
        }
    }

    // Personalization typed by an anonymous shopper -> sanitized to a short, plain
    // string safe to store on a transaction line (strip tags/control chars, trim,
    // cap length). Returns '' when there's nothing usable.
    function cleanPersonalization(v, max) {
        var s = String(v == null ? '' : v).replace(/<[^>]+>/g, ' ').replace(/[\x00-\x1f]+/g, ' ').replace(/\s+/g, ' ').trim();
        return s ? s.slice(0, max || 40) : '';
    }

    function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }
    function first(v) { return Array.isArray(v) ? v[0] : v; }
    function firstId(v) { return (Array.isArray(v) && v[0]) ? v[0].value : v; }

    function isValidEmail(e) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(e || '').trim());
    }

    function stripTags(s) {
        return String(s || '').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/gi, ' ').replace(/\s+/g, ' ').trim();
    }

    function truncate(s, n) {
        s = String(s || '');
        return s.length > n ? s.slice(0, n - 1).trim() + '…' : s;
    }

    function formatMoney(n) {
        n = num(n);
        return '$' + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // Modernist storefront styling (matches the Campaign Manager reskin and the
    // BSG "Modernist" design system): ground #f3f2f2, ink #201e1d, red #ec3013
    // accent, Archivo type, flat surfaces, square corners (radius 0), 2px hard
    // dividers, uppercase accent kickers. Product photos stay full-colour on
    // purpose -- colour is functional information for uniforms/apparel, so the
    // grayscale-image treatment from the print templates is intentionally
    // dropped here.
    var STORE_CSS =
        ':root{--ground:#f3f2f2;--surface:#eae9e9;--ink:#201e1d;--accent:#ec3013;--line:rgba(32,30,29,.4);--line-soft:rgba(32,30,29,.14);--muted:#6f6b69;--onaccent:#f6f4f3}' +
        // overflow-x:CLIP, not hidden: overflow-x:hidden on html/body turns them into
        // scroll containers, which silently disables position:sticky for everything
        // inside (the pinned header and filter rail). clip prevents horizontal
        // scrolling without creating a scroll container, so sticky keeps working.
        '*{box-sizing:border-box}html,body{overflow-x:clip}' +
        // Faint centered brand watermark behind everything: a near-opaque ground
        // overlay fades the logo layer to a subtle ghost that shows through the
        // page margins/gaps (opaque cards/hero sit on top of it).
        'body{margin:0;font-family:"Archivo","Helvetica Neue",Arial,Helvetica,sans-serif;color:var(--ink);-webkit-font-smoothing:antialiased;' +
        'background:linear-gradient(rgba(243,242,242,.962),rgba(243,242,242,.962)),var(--bsg-logo) no-repeat center 44%/min(380px,34%) fixed,var(--ground)}' +
        'a{color:var(--accent)}img{max-width:100%}' +
        '.bsg-announce{background:var(--accent);color:var(--onaccent);text-align:center;font-size:11px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;padding:8px 16px}' +
        '.bsg-header{background:var(--ground);color:var(--ink);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:20;border-bottom:2px solid var(--line)}' +
        '.bsg-header .brand{display:flex;flex-direction:column;gap:5px;line-height:1}' +
        '.brand-logo{display:block;width:132px;height:36px;background:var(--bsg-logo) no-repeat left center;background-size:contain;margin-bottom:1px}' +
        '.brand-kicker{font-size:10px;font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--accent)}' +
        '.brand-name{font-weight:800;font-size:18px;letter-spacing:.01em;text-transform:uppercase}' +
        '.brand-name b{color:var(--accent);font-weight:800}' +
        '.bsg-cartbtn{background:var(--accent);color:var(--onaccent);border:0;padding:12px 18px;font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;font-family:inherit}' +
        '.bsg-nav{display:flex;align-items:center;gap:4px}' +
        '.bsg-nav-link{background:none;border:0;padding:12px 14px;font-family:inherit;font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink);cursor:pointer;border-bottom:2px solid transparent}' +
        '.bsg-nav-link:hover{color:var(--accent)}' +
        '.bsg-nav-link.active{color:var(--accent);border-bottom-color:var(--accent)}' +
        /* landing page */
        '.bsg-hero{background:var(--surface);border-bottom:2px solid var(--line)}' +
        '.bsg-hero .bsg-wrap{padding-top:56px;padding-bottom:44px}' +
        '.hero-kicker{display:block;font-size:11px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);margin-bottom:14px}' +
        '.hero-h1{margin:0 0 16px;font-size:52px;line-height:1.02;font-weight:800;letter-spacing:-.01em;text-transform:uppercase}' +
        '.hero-sub{margin:0 0 24px;max-width:560px;font-size:15px;line-height:1.6;color:var(--muted)}' +
        '.hero-ctas{display:flex;gap:12px;flex-wrap:wrap}' +
        '.bsg-btn-primary{background:var(--accent);color:var(--onaccent);border:0;padding:15px 26px;font-family:inherit;font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer}' +
        '.bsg-btn-primary:hover{background:#d22a10}' +
        '.bsg-btn-ghost{background:none;color:var(--ink);border:2px solid var(--ink);padding:13px 24px;font-family:inherit;font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer}' +
        '.bsg-btn-ghost:hover{border-color:var(--accent);color:var(--accent)}' +
        '.bsg-btn-invert{background:var(--ground);color:var(--ink);border:0;padding:15px 26px;font-family:inherit;font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;white-space:nowrap}' +
        '.bsg-stats{display:flex;gap:0;margin-top:36px;border-top:2px solid var(--line);flex-wrap:wrap}' +
        '.stat{flex:1;min-width:150px;padding:16px 18px 0 0}' +
        '.stat+.stat{border-left:1px solid var(--line-soft);padding-left:18px}' +
        '.stat b{display:block;font-size:22px;font-weight:800}' +
        '.stat span{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}' +
        '.home-h2{margin:34px 0 16px;font-size:24px;font-weight:800;text-transform:uppercase;letter-spacing:.01em}' +
        '.bsg-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px;margin-bottom:14px}' +
        '.bsg-tile{background:#fff;border:2px solid var(--line);padding:20px 18px;display:flex;flex-direction:column;gap:8px;cursor:pointer;transition:border-color .15s}' +
        '.bsg-tile:hover{border-color:var(--accent)}' +
        '.tile-num{font-size:11px;font-weight:800;letter-spacing:.14em;color:var(--accent)}' +
        '.tile-name{font-size:17px;font-weight:800;text-transform:uppercase}' +
        '.tile-cta{font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}' +
        '.bsg-tile:hover .tile-cta{color:var(--accent)}' +
        '.bsg-band{background:var(--accent);color:var(--onaccent);margin-top:26px}' +
        '.band-inner{display:flex;align-items:center;justify-content:space-between;gap:24px;padding-top:34px;padding-bottom:34px;flex-wrap:wrap}' +
        '.band-h2{margin:6px 0 8px;font-size:30px;font-weight:800;text-transform:uppercase;line-height:1.05}' +
        '.band-sub{margin:0;font-size:14px;line-height:1.55;max-width:520px;color:rgba(246,244,243,.85)}' +
        '.bsg-footer{border-top:2px solid var(--line);margin-top:34px;background:var(--surface)}' +
        '.footer-inner{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-top:20px;padding-bottom:20px;flex-wrap:wrap}' +
        '.footer-logo{display:block;width:150px;height:40px;background:var(--bsg-logo) no-repeat left center;background-size:contain;flex:none}' +
        '.footer-note{font-size:11.5px;color:var(--muted)}' +
        /* team order form */
        '.bsg-team{padding-top:34px}' +
        '.team-title{margin:6px 0 12px;font-size:34px;font-weight:800;text-transform:uppercase;line-height:1.03}' +
        '.team-grid{display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-top:20px;align-items:start}' +
        '.team-col{background:#fff;border:2px solid var(--line);padding:22px}' +
        '.team-h3{margin:0 0 14px;font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:var(--accent)}' +
        '.team-cartnote{margin-top:8px;font-size:12px;font-weight:700;color:var(--ink);background:var(--surface);border-left:3px solid var(--accent);padding:8px 12px}' +
        '.school-picked{display:flex;justify-content:space-between;align-items:center;border:2px solid #1a7f42;padding:8px 10px;font-size:13px}' +
        '.rgrid{width:100%}' +
        '.rgrid th{background:var(--ink);color:var(--onaccent);text-transform:uppercase;letter-spacing:.04em}' +
        '.rgrid td{border-bottom:1px solid var(--line-soft);padding:6px 8px}' +
        '.rgrid input{width:100%;padding:8px;border:2px solid var(--line);font-family:inherit;text-align:center;border-radius:0}' +
        '.rgrid .vcolor{font-weight:800}' +
        '.linklike{background:none;border:0;padding:0;color:var(--accent);font-weight:800;cursor:pointer;font-family:inherit;font-size:inherit;text-decoration:underline}' +
        '@media(max-width:720px){.team-grid{grid-template-columns:1fr}.team-title{font-size:26px}}' +
        /* uniform builder */
        '.bsg-builder{padding-top:34px}' +
        '.ub-grid{display:grid;grid-template-columns:minmax(280px,1fr) minmax(320px,1.1fr);gap:28px;margin-top:18px;align-items:start}' +
        '.ub-preview{background:var(--surface);border:2px solid var(--line);padding:22px;position:sticky;top:96px}' +
        '.ub-controls{background:#fff;border:2px solid var(--line);padding:22px}' +
        '.ub-field{margin-bottom:14px}' +
        '.ub-field>label{display:block;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;color:var(--ink)}' +
        '.ub-swatches{display:flex;flex-wrap:wrap;gap:6px}' +
        '.ub-swatch{width:26px;height:26px;border:2px solid var(--line);cursor:pointer;padding:0;border-radius:0;box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}' +
        '.ub-swatch.on{border-color:var(--accent);outline:2px solid var(--accent);outline-offset:1px}' +
        '.ub-row{display:flex;gap:12px}' +
        '.ub-hr{border:0;border-top:2px solid var(--line);margin:18px 0 14px}' +
        /* momentec embed */
        // Sublimation is full-width and the configurator fills the viewport height
        // (scrolls internally) rather than being a fixed 1000px block.
        // Sublimation: compact heading + a rail cap SHORTER than the main column.
        // position:sticky only pins while its column has spare height ("slack");
        // the designer column is ~viewport-sized, so without these caps the rail
        // has no slack and scrolls away with the page.
        '.bsg-subl{padding-top:20px;max-width:none}' +
        '.bsg-subl .team-title{font-size:24px;margin:2px 0 6px}' +
        '.bsg-subl .hero-sub{margin:0 0 8px;font-size:12.5px;max-width:none}' +
        '.bsg-subl .bsg-shop-aside{max-height:calc(100vh - 270px)}' +
        '.subl-cats{margin-top:14px}' +
        '.subl-cat-h{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin:16px 0 8px}' +
        '.subl-cats-row{display:flex;flex-wrap:wrap;gap:8px}' +
        '.subl-cats-top{margin-bottom:2px}' +
        '.subl-cat{padding:8px 13px;border:2px solid var(--line);background:#fff;font-family:inherit;font-size:12.5px;font-weight:700;color:var(--ink);cursor:pointer;border-radius:0}' +
        '.subl-cat:hover{border-color:var(--accent);color:var(--accent)}' +
        '.subl-cat.on{background:var(--ink);color:var(--onaccent);border-color:var(--ink)}' +
        '.momentec-wrap{position:relative;margin-top:10px;border:2px solid var(--line);background:#fff;height:calc(100vh - 300px);min-height:520px}' +
        '.momentec-fallback{position:absolute;top:0;left:0;right:0;bottom:0;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;color:var(--muted);padding:40px;z-index:0}' +
        '.momentec-fallback small{display:block;margin-top:8px;max-width:420px;font-size:12px}' +
        '.momentec-captured{margin-top:16px}' +
        /* Design Ideas (BSG sample art): browsable reference designs by sport */
        '.bsg-sampleart{padding-top:22px}' +
        '.art-count{font-size:12px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0}' +
        '.art-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin-bottom:10px}' +
        '.art-card{margin:0;background:#fff;border:2px solid var(--line);cursor:pointer;display:flex;flex-direction:column;transition:border-color .15s}' +
        '.art-card:hover{border-color:var(--accent)}' +
        '.art-card.on{border-color:var(--ink);box-shadow:inset 0 0 0 2px var(--surface)}' +
        // Fixed-ratio box so tiles line up despite the art's varied aspect ratios.
        '.art-card-img{aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:10px}' +
        '.art-card-img img{max-width:100%;max-height:100%;object-fit:contain}' +
        '.art-card figcaption{border-top:1px solid var(--line-soft);padding:7px 9px;font-size:10.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:center}' +
        '.art-card.on figcaption{color:var(--accent)}' +
        '.art-zoom{width:820px}' +
        '.art-zoom-body{display:flex;flex-wrap:wrap;gap:18px;padding:24px}' +
        '.art-zoom-body>img{flex:1 1 340px;min-width:0;max-width:460px;object-fit:contain;background:#fff;border:2px solid var(--line);padding:12px}' +
        '.art-zoom-meta{flex:1 1 260px;min-width:0;display:flex;flex-direction:column;gap:10px;align-items:flex-start}' +
        '.art-zoom-meta .bsg-submit{margin-top:4px}' +
        '.deco-note.art-ref{display:flex;align-items:center;gap:12px}' +
        '.deco-note.art-ref img{width:74px;height:56px;object-fit:contain;background:#fff;border:1px solid var(--line-soft);flex:none}' +
        /* catalogs page (two-column: left group rail + cover-image tiles) */
        '.bsg-catalogs{padding-top:28px}' +
        '.catalog-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px;margin-bottom:8px}' +
        '.catalog-card{background:#fff;border:2px solid var(--line);display:flex;flex-direction:column;transition:border-color .15s}' +
        '.catalog-card:hover{border-color:var(--accent)}' +
        '.catalog-cover{aspect-ratio:3/4;background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden;border-bottom:2px solid var(--line)}' +
        '.catalog-cover img{width:100%;height:100%;object-fit:cover}' +
        // Imageless catalogs (e.g. the Digital Swatch Book has no vendor cover art):
        // a designed brand panel instead of bare placeholder text.
        '.catalog-cover .ph{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;width:78%;height:72%;border:2px solid var(--line);background:var(--surface);color:var(--ink);font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;text-align:center;padding:14px}' +
        '.catalog-cover .ph::before{content:"";width:96px;height:32px;background:var(--bsg-logo) no-repeat center/contain;opacity:.9}' +
        '.catalog-cover .ph::after{content:"Reference PDF";font-size:9.5px;color:var(--accent);letter-spacing:.14em}' +
        '.catalog-body{padding:12px 13px 13px;display:flex;flex-direction:column;gap:10px;flex:1}' +
        '.catalog-name{font-weight:800;font-size:14px;text-transform:uppercase;letter-spacing:.01em;flex:1}' +
        '.catalog-links{display:flex;gap:8px}' +
        '.catalog-btn{flex:1;text-align:center;background:var(--ink);color:var(--onaccent);text-decoration:none;padding:9px 10px;font-weight:800;font-size:11px;letter-spacing:.08em;text-transform:uppercase}' +
        '.catalog-btn:hover{background:#000}' +
        '.catalog-btn.ghost{background:none;color:var(--ink);border:2px solid var(--line);padding:7px 10px}' +
        '.catalog-btn.ghost:hover{border-color:var(--accent);color:var(--accent)}' +
        '.bsg-wrap{max-width:1160px;margin:0 auto;padding:28px 24px}' +
        '.bsg-filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}' +
        '.bsg-filter{padding:10px 12px;border:2px solid var(--line);background:#fff;cursor:pointer;font-weight:600;font-size:13px;min-width:160px;font-family:inherit;color:var(--ink);border-radius:0}' +
        '.bsg-clearfilters{padding:2px 4px;border:0;background:none;color:var(--accent);cursor:pointer;font-weight:800;font-size:10px;letter-spacing:.08em;text-transform:uppercase}' +
        /* catalog: left filter rail + main column */
        // Catalog is fluid full-width (aligned to the full-width header): it fills
        // the browser at any zoom -- zooming out just widens the viewport, so the
        // grid adds columns (denser) instead of leaving empty side margins.
        '.bsg-shop{display:grid;grid-template-columns:222px minmax(0,1fr);gap:26px;align-items:start;max-width:none}' +
        '.bsg-shop-main{min-width:0}' +
        // Filter rail stays pinned below the sticky header and auto-fits the
        // viewport height: the whole rail scrolls as ONE panel (never taller than
        // the screen), so every facet is always reachable in full.
        '.bsg-shop-aside{position:sticky;top:112px;max-height:calc(100vh - 124px);overflow-y:auto;overflow-x:hidden;background:#fff;border:2px solid var(--line);padding:16px 16px 18px}' +
        '.aside-top{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:18px}' +
        '.aside-kicker{font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}' +
        '.facet{margin-top:14px;padding-top:14px;border-top:1px solid var(--line-soft)}' +
        '.facet:first-of-type{margin-top:8px;padding-top:8px;border-top:0}' +
        '.facet-h{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--ink);margin-bottom:6px}' +
        '.facet-toggle{display:flex;width:100%;align-items:center;justify-content:space-between;gap:8px;background:none;border:0;padding:0;cursor:pointer;font-family:inherit;text-align:left}' +
        '.facet-toggle:hover{color:var(--accent)}' +
        '.facet-toggle .facet-caret{width:auto;font-size:10px}' +
        '.facet-list{display:flex;flex-direction:column;gap:1px}' +
        '.facet-opt{display:block;width:100%;text-align:left;background:none;border:0;border-left:2px solid transparent;padding:7px 10px;font-family:inherit;font-size:13px;font-weight:600;color:var(--ink);cursor:pointer;line-height:1.25}' +
        '.facet-opt span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
        '.facet-opt:hover{color:var(--accent);background:var(--ground)}' +
        '.facet-opt.on{color:var(--accent);font-weight:800;border-left-color:var(--accent);background:var(--surface)}' +
        /* collapsible category tree */
        '.cat-tree{display:block}' +
        '.cat-node{display:flex;align-items:center;gap:6px;width:100%;text-align:left;background:none;border:0;border-left:2px solid transparent;padding:7px 10px;font-family:inherit;font-size:13px;font-weight:600;color:var(--ink);cursor:pointer;line-height:1.2}' +
        '.cat-node:hover{color:var(--accent);background:var(--ground)}' +
        '.cat-caret{display:inline-block;width:11px;flex:none;font-size:9px;color:var(--muted);text-align:center}' +
        '.cat-node:hover .cat-caret{color:var(--accent)}' +
        '.cat-parent{font-weight:800}' +
        '.cat-parent.open{color:var(--accent)}' +
        '.cat-leaf.on{color:var(--accent);font-weight:800;border-left-color:var(--accent);background:var(--surface)}' +
        '.cat-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
        '.itemno{font-size:11.5px;color:var(--muted)}' +
        '.bsg-toolbar{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap}' +
        '.bsg-toolbar input{flex:1;min-width:220px;padding:13px 14px;border:2px solid var(--line);font-size:15px;font-family:inherit;color:var(--ink);border-radius:0;background:#fff}' +
        '.bsg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:14px}' +
        '.bsg-card{background:#fff;border:2px solid var(--line);display:flex;flex-direction:column;transition:border-color .15s}' +
        '.bsg-card:hover{border-color:var(--accent)}' +
        '.bsg-card .thumb{aspect-ratio:1/1;background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden;border-bottom:2px solid var(--line);cursor:pointer}' +
        '.bsg-card .thumb img{width:100%;height:100%;object-fit:contain}' +
        '.bsg-card .thumb .ph{color:#bbb;font-size:11px;text-transform:uppercase;letter-spacing:.1em}' +
        '.bsg-card .body{padding:12px 13px 13px;display:flex;flex-direction:column;gap:6px;flex:1}' +
        '.card-kicker{font-size:10px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--accent)}' +
        '.bsg-card .name{font-weight:800;font-size:14.5px;line-height:1.2;cursor:pointer;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}' +
        '.card-foot{margin-top:auto;display:flex;flex-direction:column;gap:8px;padding-top:8px}' +
        '.bsg-card .price{font-weight:800;font-size:17px}' +
        '.badge{display:inline-block;width:-moz-fit-content;width:fit-content;font-size:10px;font-weight:800;text-transform:uppercase;padding:4px 8px;letter-spacing:.09em;border:1px solid currentColor}' +
        '.badge.in{color:#1a7f42}.badge.low{color:#a35b00}.badge.out{color:var(--accent)}' +
        '.bsg-add{background:var(--ink);color:var(--onaccent);border:0;padding:12px;font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;font-family:inherit}' +
        '.bsg-add:hover{background:#000}' +
        '.bsg-add:disabled{background:#c9c7c6;color:#fff;cursor:not-allowed}' +
        '.card-addrow{display:flex;gap:8px;align-items:stretch}' +
        '.card-addrow .bsg-add{flex:1}' +
        '.card-qty{width:58px;padding:10px 8px;border:2px solid var(--line);font-family:inherit;font-size:14px;font-weight:700;text-align:center;color:var(--ink);border-radius:0;background:#fff}' +
        '.card-qty:disabled{background:#f0efee;color:#bbb;cursor:not-allowed}' +
        '.bsg-pager{display:flex;gap:6px;justify-content:center;align-items:center;margin:30px 0;flex-wrap:wrap}' +
        '.bsg-pager button{padding:9px 14px;border:2px solid var(--line);background:#fff;cursor:pointer;font-size:13px;font-weight:700;font-family:inherit;color:var(--ink);border-radius:0}' +
        '.bsg-pager button:hover:not(:disabled){border-color:var(--accent)}' +
        '.bsg-pager button:disabled{opacity:.35;cursor:not-allowed}' +
        '.bsg-pager-num{min-width:42px}' +
        '.bsg-pager-num.active{background:var(--ink);color:var(--onaccent);border-color:var(--ink)}' +
        '.bsg-pager-dots{padding:0 4px;color:var(--muted)}' +
        '.bsg-pager-jump{display:flex;align-items:center;gap:6px;margin-left:10px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}' +
        '.bsg-pager-jump input{width:56px;padding:8px;border:2px solid var(--line);text-align:center;font-family:inherit;border-radius:0}' +
        '.bsg-pager-jump button{padding:8px 12px}' +
        '.bsg-drawer{position:fixed;top:0;right:0;height:100%;width:420px;max-width:94vw;background:var(--ground);box-shadow:-4px 0 30px rgba(32,30,29,.22);transform:translateX(100%);transition:transform .2s;z-index:40;display:flex;flex-direction:column}' +
        '.bsg-drawer.open{transform:none}' +
        '.bsg-drawer h3{margin:0;padding:20px;background:var(--ink);color:var(--onaccent);display:flex;justify-content:space-between;align-items:center;font-size:14px;text-transform:uppercase;letter-spacing:.08em;font-weight:800}' +
        '.bsg-drawer .close{background:none;border:0;color:var(--onaccent);font-size:22px;cursor:pointer;line-height:1}' +
        '.bsg-drawer .items{flex:1;overflow:auto;padding:14px 20px}' +
        '.bsg-line{display:flex;gap:10px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line-soft)}' +
        '.bsg-line .qn{width:56px;padding:7px;border:2px solid var(--line);font-family:inherit;border-radius:0}' +
        '.bsg-line .rm{background:none;border:0;color:var(--accent);cursor:pointer;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.05em}' +
        '.bsg-drawer .foot{padding:18px 20px;border-top:2px solid var(--line)}' +
        '.bsg-field{margin-bottom:14px}.bsg-field label{display:block;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px;color:var(--ink)}' +
        '.bsg-field input,.bsg-field textarea{width:100%;padding:11px;border:2px solid var(--line);font-size:14px;font-family:inherit;border-radius:0;background:#fff;color:var(--ink)}' +
        '.bsg-school-results{border:2px solid var(--line);border-top:0;max-height:170px;overflow:auto;margin-top:-10px;background:#fff}' +
        '.bsg-school-results div{padding:9px 11px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--line-soft)}.bsg-school-results div:hover{background:var(--surface)}' +
        '.bsg-toggle{display:flex;gap:0;margin-bottom:14px}' +
        '.bsg-toggle button{flex:1;padding:12px;border:2px solid var(--ink);background:#fff;cursor:pointer;font-weight:800;font-size:12px;text-transform:uppercase;letter-spacing:.06em;font-family:inherit;color:var(--ink)}' +
        '.bsg-toggle button+button{border-left:0}' +
        '.bsg-toggle button.active{background:var(--ink);color:var(--onaccent)}' +
        '.bsg-submit{width:100%;background:var(--accent);color:var(--onaccent);border:0;padding:15px;font-weight:800;font-size:13px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;font-family:inherit}' +
        '.bsg-submit:disabled{background:#c9c7c6;cursor:not-allowed}' +
        '.bsg-msg{padding:11px 12px;font-size:12.5px;margin-bottom:10px;font-weight:600}.bsg-msg.err{background:#fbe7e7;color:#b3261e}.bsg-msg.ok{background:#e7f5ec;color:#1a7f42}' +
        '.bsg-overlay{position:fixed;inset:0;background:rgba(32,30,29,.42);z-index:35;display:none}.bsg-overlay.open{display:block}' +
        '.bsg-modal-overlay{position:fixed;inset:0;background:rgba(32,30,29,.55);z-index:45}' +
        '.bsg-modal{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:46;background:var(--ground);width:1060px;max-width:96vw;max-height:92vh;overflow:auto;border:2px solid var(--ink)}' +
        '.pd-close{position:absolute;top:10px;right:12px;background:#fff;border:2px solid var(--line);border-radius:0;width:34px;height:34px;font-size:20px;line-height:1;cursor:pointer;color:var(--ink);z-index:3}' +
        '.pd-layout{display:flex;flex-wrap:wrap}' +
        // Image column capped so every product's photo renders at a consistent size
        // (source photos vary wildly in dimensions; without the caps a large image
        // can balloon the column to the full modal width/height).
        '.pd-imgcol{flex:1 1 320px;max-width:420px;min-width:0;padding:22px;display:flex;flex-direction:column;gap:10px;position:sticky;top:0;align-self:flex-start}' +
        '.pd-mainimg{position:relative;background:#fff;aspect-ratio:1/1;max-height:380px;display:flex;align-items:center;justify-content:center;overflow:hidden;border:2px solid var(--line)}' +
        /* decoration builder (customer logo/text on the photo) */
        '.pd-deco-overlay{position:absolute;transform:translate(-50%,-50%);cursor:move;z-index:2;touch-action:none;user-select:none;-webkit-user-drag:none;max-width:none}' +
        '.pd-deco-text{font-weight:800;letter-spacing:.04em;white-space:nowrap;text-shadow:0 0 2px rgba(255,255,255,.4);line-height:1}' +
        '.pd-logomock{width:100%;border-top:1px solid var(--line-soft);padding-top:10px}' +
        '.logo-chips{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}' +
        '.logo-chip{padding:6px 10px;border:2px solid var(--line);background:#fff;font-family:inherit;font-size:11px;font-weight:700;cursor:pointer;border-radius:0}' +
        '.logo-chip:hover{border-color:var(--accent)}' +
        '.logo-chip.on{background:var(--ink);color:var(--onaccent);border-color:var(--ink)}' +
        '.logo-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0}' +
        '.logo-row input[type=text]{flex:1;min-width:160px;padding:9px 10px;border:2px solid var(--line);font-family:inherit;font-size:13px;border-radius:0}' +
        '.logo-row input[type=range]{flex:1;min-width:120px;accent-color:var(--accent)}' +
        '.logo-note{font-size:11.5px;color:var(--muted)}' +
        '.deco-note{margin:0 0 10px;font-size:12px;font-weight:700;background:var(--surface);border-left:3px solid var(--accent);padding:8px 12px}' +
        // Design-template overlay (inline SVG) fills its positioned box width; the
        // picker chips letterbox a live monochrome preview of each layout.
        '.pd-deco-overlay svg{width:100%;height:auto;pointer-events:none}' +
        // Long labels ("Established") must wrap inside the chip, not spill past it.
        '.pd-mainimg img{width:100%;height:100%;object-fit:contain}.pd-mainimg .ph{color:#bbb}' +
        '.pd-thumbs{display:flex;gap:6px;flex-wrap:wrap}' +
        '.pd-thumbs img{width:58px;height:58px;object-fit:contain;background:#fff;border:2px solid var(--line);cursor:pointer}' +
        '.pd-thumbs img.active{border-color:var(--accent)}' +
        /* gallery arrows + counter + color strip */
        '.pd-nav{position:absolute;top:50%;transform:translateY(-50%);z-index:3;width:34px;height:46px;border:0;background:rgba(32,30,29,.55);color:#fff;font-size:26px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0 0 4px}' +
        '.pd-nav.prev{left:0}.pd-nav.next{right:0}' +
        '.pd-nav:hover{background:var(--accent)}' +
        '.pd-count{position:absolute;right:8px;bottom:6px;z-index:3;font-size:10.5px;font-weight:800;letter-spacing:.06em;background:rgba(255,255,255,.85);padding:2px 7px}' +
        '.pd-colors{margin-top:2px}' +
        '.pd-colorrow{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}' +
        '.pd-colorrow img{width:44px;height:44px;object-fit:contain;background:#fff;border:2px solid var(--line);cursor:pointer}' +
        '.pd-colorrow img.active{border-color:var(--accent)}' +
        '.vcolor.clickable,.vlabel.clickable{cursor:pointer;text-decoration:underline;text-underline-offset:2px}' +
        '.vcolor.clickable:hover,.vlabel.clickable:hover{color:var(--accent)}' +
        '.pd-body{flex:1 1 340px;min-width:0;padding:22px 26px;display:flex;flex-direction:column;gap:9px;align-items:flex-start}' +
        '.pd-name{margin:0;font-size:23px;font-weight:800;line-height:1.15}' +
        '.price-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}' +
        '.pd-price{font-weight:800;font-size:23px}.price{font-weight:800;font-size:17px}' +
        '.pd-tiers{width:100%;border:2px solid var(--line);margin-top:4px}' +
        '.pd-tiers-h{font-size:10px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);padding:8px 12px;border-bottom:1px solid var(--line-soft)}' +
        '.pd-tier{display:flex;justify-content:space-between;font-size:13px;font-weight:600;padding:7px 12px;border-bottom:1px solid var(--line-soft)}' +
        '.pd-tier span:last-child{font-weight:800}' +
        '.pd-tiercalc{padding:9px 12px;font-size:13px;background:var(--ground);min-height:16px}' +
        '.pd-tiercalc strong{font-size:15px}' +
        '.msrp{color:var(--muted);text-decoration:line-through;font-size:14px;font-weight:600}' +
        '.compare-at{color:var(--muted);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}' +
        '.compare-at .msrp{font-size:13px}' +
        '.price-tbd{color:var(--muted);font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.06em}' +
        '.sale-badge{background:var(--accent);color:var(--onaccent);font-size:10px;font-weight:800;text-transform:uppercase;padding:4px 8px;letter-spacing:.08em}' +
        '.pd-desc{color:var(--muted);line-height:1.5;font-size:14px;margin:0}' +
        '.pd-bullets{margin:2px 0 0;padding-left:18px;color:var(--muted);font-size:13.5px;line-height:1.55}' +
        '.pd-bullets li{margin-bottom:2px}' +
        '.pd-specs{width:100%;border-top:2px solid var(--line);margin-top:10px;padding-top:12px}' +
        '.pd-spec-row{display:flex;justify-content:space-between;font-size:12.5px;padding:4px 0;color:var(--muted)}' +
        '.pd-spec-row b{color:var(--ink);font-weight:800}' +
        '.pd-spec-block{padding:4px 0 8px}' +
        '.pd-spec-block span{display:block;font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin-bottom:4px}' +
        '.pd-spec-block p{margin:0;font-size:13px;line-height:1.55;color:var(--ink)}' +
        '.vhead{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:var(--ink);margin-top:8px}' +
        '.vgrid-wrap{width:100%;overflow-x:auto}' +
        '.vgrid{border-collapse:collapse;font-size:12px;min-width:100%}' +
        '.vgrid th{background:var(--ink);color:var(--onaccent);padding:7px 6px;text-align:center;font-weight:800;text-transform:uppercase;letter-spacing:.03em;white-space:nowrap}' +
        '.vgrid th:first-child{text-align:left}' +
        '.vgrid td{border-bottom:1px solid var(--line-soft);padding:6px 5px;text-align:center}' +
        '.vgrid .vcolor{text-align:left;font-weight:800;white-space:nowrap}' +
        '.vq{display:flex;flex-direction:column;align-items:center;gap:3px;color:#1a7f42;font-weight:700}' +
        '.vq input{width:44px;padding:5px 4px;border:2px solid var(--line);text-align:center;font-family:inherit;border-radius:0}' +
        '.vq.out{color:var(--accent)}.vq.na{color:#ccc}' +
        '.vlist{width:100%}.vrow{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line-soft)}' +
        '.vlabel{flex:1;font-weight:800;font-size:13px}' +
        '.pd-personalize{width:100%;margin-top:6px}' +
        '.pd-personalize-row{display:flex;gap:10px;margin-top:6px}' +
        '.pd-personalize-note{font-size:11px;color:var(--muted);margin-top:5px}' +
        '.pd-qtyrow{display:flex;gap:12px;align-items:flex-end;width:100%;margin-top:14px}' +
        '.pd-qty{width:90px;padding:11px;border:2px solid var(--line);font-family:inherit;border-radius:0;font-size:15px}' +
        '.pd-addbtn{flex:1;padding:14px 20px}' +
        '.bsg-line-player{font-size:11px;color:var(--accent);font-weight:800;text-transform:uppercase;letter-spacing:.05em;margin-top:2px}' +
        '.bsg-loading{padding:64px;text-align:center;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-size:12px}' +
        '.bsg-empty{padding:52px;text-align:center;color:var(--muted);grid-column:1/-1}' +
        // Count badge on a filter option, and the "widen your search" rescue
        // shown instead of a bare empty grid.
        '.facet-opt,.cat-node{display:flex;align-items:center;gap:6px;justify-content:space-between}' +
        '.facet-n{flex:none;font-size:11px;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums}' +
        '.facet-opt.on .facet-n,.cat-node.on .facet-n{color:inherit}' +
        '.cat-node .cat-label{flex:1 1 auto;text-align:left}' +
        '.bsg-empty-more{margin:14px auto 4px;max-width:520px;padding:14px 16px;background:var(--surface);border:1px solid var(--line);color:var(--ink);font-size:14px;line-height:1.5}' +
        '.bsg-empty-more .bsg-btn-ghost{margin-left:10px}' +

        '@media(max-width:820px){.bsg-shop{grid-template-columns:1fr;gap:18px}.bsg-shop-aside{position:static;top:auto;max-height:none;overflow:visible}.facet-list{flex-direction:row;flex-wrap:wrap;gap:6px;max-height:none}.facet-opt{width:auto;border:2px solid var(--line);border-left-width:2px;padding:7px 11px}.facet-opt span{white-space:normal}.facet-opt.on{border-color:var(--accent)}}' +
        // Sublimation rail carries an extra max-height cap (higher specificity than
        // the generic aside rule), so it must be released explicitly on mobile --
        // otherwise the capped box keeps overflow:visible and the categories spill
        // out and overlap the configurator below.
        '@media(max-width:820px){.bsg-subl .bsg-shop-aside{max-height:none;overflow:visible}.momentec-wrap{height:auto;min-height:74vh}}' +
        '@media(max-width:560px){.bsg-grid{grid-template-columns:1fr 1fr}}' +
        '@media(max-width:400px){.bsg-grid{grid-template-columns:1fr}}' +
        '@media(max-width:760px){.hero-h1{font-size:36px}.bsg-nav-link{padding:10px 8px;font-size:11px}.band-h2{font-size:24px}}' +
        '@media(max-width:520px){.bsg-wrap{padding:18px 14px}.bsg-header{padding:12px 14px;flex-wrap:wrap;gap:6px}.brand-name{font-size:15px}.hero-h1{font-size:30px}.bsg-hero .bsg-wrap{padding-top:34px;padding-bottom:28px}}' +
        // Product pop-up on phones: go full-screen (a centered 1060px card doesn't
        // fit), stop pinning the image column (position:sticky wastes the top of a
        // stacked layout), float the close button so it's always reachable, and
        // stack the personalization/qty controls that sit side-by-side on desktop.
        '@media(max-width:640px){' +
        // The nav holds up to 6 links + the Cart button in one non-wrapping row;
        // on a phone that overruns the width and overflow-x:clip would hide the
        // Cart button off the right edge. Wrap the header so the brand sits on its
        // own row and the nav flows full-width below it, Cart pinned to the right.
        '.bsg-header{flex-wrap:wrap;gap:8px;padding:12px 14px}' +
        '.bsg-nav{flex-wrap:wrap;width:100%;justify-content:flex-start;gap:2px 4px}' +
        '.bsg-nav-link{padding:9px 9px}' +
        '.bsg-cartbtn{margin-left:auto}' +
        '.bsg-modal{top:0;left:0;transform:none;width:100%;max-width:100%;height:100%;max-height:100%;border:0}' +
        '.pd-close{position:fixed;top:8px;right:8px;box-shadow:0 1px 6px rgba(32,30,29,.25)}' +
        '.pd-imgcol{position:static;max-width:none;flex:1 1 auto;padding:16px 16px 6px}' +
        '.pd-mainimg{max-height:300px}' +
        '.pd-body{flex:1 1 auto;padding:12px 18px 26px}' +
        '.pd-name{font-size:20px}' +
        '.pd-personalize-row{flex-direction:column}' +
        '.pd-qtyrow{flex-wrap:wrap}.pd-addbtn{flex:1 1 100%}' +
        '.art-zoom-body{padding:16px;gap:12px}.art-zoom-body>img{max-width:100%}' +
        '.art-grid{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}' +
        '}';

    return { onRequest: onRequest };
});
