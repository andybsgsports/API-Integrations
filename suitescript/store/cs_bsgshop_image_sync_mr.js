/**
 * BSG Ordering Site - nightly image-URL sync.
 *
 * Precomputes each item's best product-image URL into a plain TEXT field
 * (custitem_bsgshop_image_url) so the storefront catalog can read the image from
 * a normal, safe SEARCH COLUMN -- instead of loading each record at request time
 * to reach the Image-type "Item Image" field (custitem_atlas_item_image), which
 * crashes NetSuite when requested as a column.
 *
 * Resolution priority per item (mirrors the Suitelet's live resolver):
 *   1. A per-vendor image URL field (SanMar / S&S / Momentec / UA).
 *   2. The image file ATTACHED to the item (Files subtab), by file type.
 *   3. The curated Item Image field, then the native Web Store image, off the
 *      loaded record. For a matrix PARENT with none, the first child that has one.
 *
 * Safe + idempotent:
 *   - FILL-ONLY: writes only when the stored value is BLANK. The supplier
 *     pipeline owns already-populated values (it writes the per-SKU FRONT-view
 *     URL); re-resolving those here would fight it nightly.
 *   - Never clears an existing value when it resolves nothing (leaves it alone).
 *   - Every item is isolated in try/catch; one bad item never fails the run.
 *   - Purely additive: if this never runs, the storefront still resolves images
 *     live per page exactly as before.
 *
 * Run nightly (scheduled on the deployment) or on demand via Save & Execute.
 *
 * @NApiVersion 2.1
 * @NScriptType MapReduceScript
 */
define([
    'N/record',
    'N/search',
    'N/file',
    'N/url',
    'N/log',
    './lib/bsgshop.constants.js'
], function (record, search, file, url, log, C) {

    var IMAGE_FILE_TYPES = { JPGIMAGE: 1, PJPGIMAGE: 1, PNGIMAGE: 1, GIFIMAGE: 1, BMPIMAGE: 1, TIFFIMAGE: 1 };
    var ITEM_RECORD_TYPE_BY_CODE = {
        InvtPart: 'inventoryitem', NonInvtPart: 'noninventoryitem', Kit: 'kititem',
        Assembly: 'assemblyitem', Service: 'serviceitem', OthCharge: 'otherchargeitem',
        Group: 'itemgroup', GiftCert: 'giftcertificateitem',
        LotNumberedInventoryItem: 'lotnumberedinventoryitem',
        SerializedInventoryItem: 'serializedinventoryitem',
        LotNumberedAssemblyItem: 'lotnumberedassemblyitem',
        SerializedAssemblyItem: 'serializedassemblyitem', DownloadItem: 'downloaditem'
    };

    function rowIsImageFile(ftype, name) {
        if (ftype && IMAGE_FILE_TYPES[String(ftype).toUpperCase()]) { return true; }
        return /\.(png|jpe?g|gif|webp|bmp|tiff?)(\?|$)/i.test(String(name || ''));
    }

    // Application domain, resolved once. In a scheduled context this is reliable;
    // if it ever isn't, we fall back to a domain-relative url (the storefront's
    // read-time resolver absolutizes it against the page origin).
    var appDomainCache;
    function appDomain() {
        if (appDomainCache !== undefined) { return appDomainCache; }
        try { appDomainCache = url.resolveDomain({ hostType: url.HostType.APPLICATION }) || ''; }
        catch (e) { appDomainCache = ''; }
        return appDomainCache;
    }
    function absoluteUrl(rel) {
        rel = String(rel || '');
        if (!rel) { return ''; }
        if (/^https?:\/\//i.test(rel)) { return rel; }
        if (rel.indexOf('//') === 0) { return 'https:' + rel; }
        if (rel.charAt(0) !== '/') { rel = '/' + rel; }
        var d = appDomain();
        return d ? ('https://' + d + rel) : rel;
    }
    function fileUrl(fileId) {
        try { return absoluteUrl(file.load({ id: fileId }).url); }
        catch (e) { return ''; }
    }

    // First attached image-file URL on a single item (or set of items), by file type.
    function attachedImageUrl(itemId) {
        var found = '';
        try {
            search.create({
                type: 'item',
                filters: [['internalid', 'anyof', itemId]],
                columns: [
                    search.createColumn({ name: 'name', join: 'file' }),
                    search.createColumn({ name: 'url', join: 'file' }),
                    search.createColumn({ name: 'filetype', join: 'file' })
                ]
            }).run().each(function (r) {
                var ftype = r.getValue({ name: 'filetype', join: 'file' }) || '';
                var name = r.getValue({ name: 'name', join: 'file' }) || '';
                var rel = r.getValue({ name: 'url', join: 'file' }) || '';
                if (rel && rowIsImageFile(ftype, name)) { found = absoluteUrl(rel); return false; }
                return true;
            });
        } catch (e) { /* file join unavailable -> fall through */ }
        return found;
    }

    // Best image url reachable off the loaded record: curated Item Image, then the
    // native Web Store image.
    function recordImageUrl(itemId, typeCode) {
        var rtype = ITEM_RECORD_TYPE_BY_CODE[typeCode];
        if (!rtype) { return ''; }
        try {
            var rec = record.load({ type: rtype, id: itemId });
            var fileId = rec.getValue({ fieldId: C.ITEM_FIELD.ATLAS_IMAGE }) ||
                rec.getValue({ fieldId: C.ITEM_FIELD.DISPLAY_IMAGE });
            return fileId ? fileUrl(fileId) : '';
        } catch (e) { return ''; }
    }

    // For a matrix parent with no image of its own: first child that has one
    // (the child's vendor/precomputed URL fields first -- SanMar/S&S sync photo
    // URLs onto children only -- then attached file, then the child's Item Image
    // record field).
    function childImageUrl(parentId) {
        var found = '';
        try {
            var urlCols = [search.createColumn({ name: C.ITEM_FIELD.SHOP_IMAGE_URL })];
            for (var ui = 0; ui < C.IMAGE_URL_FIELDS.length; ui++) {
                urlCols.push(search.createColumn({ name: C.ITEM_FIELD[C.IMAGE_URL_FIELDS[ui]] }));
            }
            search.create({
                type: 'item',
                filters: [[C.ITEM_FIELD.PARENT, 'anyof', parentId], 'AND', ['isinactive', 'is', 'F']],
                columns: urlCols
            }).run().each(function (r) {
                var vals = {};
                vals[C.ITEM_FIELD.SHOP_IMAGE_URL] = r.getValue(C.ITEM_FIELD.SHOP_IMAGE_URL);
                for (var vi = 0; vi < C.IMAGE_URL_FIELDS.length; vi++) {
                    var fid = C.ITEM_FIELD[C.IMAGE_URL_FIELDS[vi]];
                    vals[fid] = r.getValue(fid);
                }
                var pre = String(vals[C.ITEM_FIELD.SHOP_IMAGE_URL] || '').trim();
                if (pre) { found = absoluteUrl(pre); return false; }
                var u = vendorUrl(vals);
                if (u) { found = u; return false; }
                return true;
            });
            if (found) { return found; }
        } catch (e) { /* fall through to attached-file passes */ }
        try {
            search.create({
                type: 'item',
                filters: [[C.ITEM_FIELD.PARENT, 'anyof', parentId]],
                columns: [
                    search.createColumn({ name: 'internalid' }),
                    search.createColumn({ name: 'type' }),
                    search.createColumn({ name: 'name', join: 'file' }),
                    search.createColumn({ name: 'url', join: 'file' }),
                    search.createColumn({ name: 'filetype', join: 'file' })
                ]
            }).run().each(function (r) {
                var ftype = r.getValue({ name: 'filetype', join: 'file' }) || '';
                var name = r.getValue({ name: 'name', join: 'file' }) || '';
                var rel = r.getValue({ name: 'url', join: 'file' }) || '';
                if (rel && rowIsImageFile(ftype, name)) { found = absoluteUrl(rel); return false; }
                return true;
            });
            if (found) { return found; }
            // No attached child image -> try the first child's record image.
            search.create({
                type: 'item',
                filters: [[C.ITEM_FIELD.PARENT, 'anyof', parentId]],
                columns: [search.createColumn({ name: 'internalid' }), search.createColumn({ name: 'type' })]
            }).run().each(function (r) {
                var img = recordImageUrl(r.getValue({ name: 'internalid' }), r.getValue({ name: 'type' }));
                if (img) { found = img; return false; }
                return true;
            });
        } catch (e) { /* fall through */ }
        return found;
    }

    function vendorUrl(values) {
        for (var i = 0; i < C.IMAGE_URL_FIELDS.length; i++) {
            var raw = values[C.ITEM_FIELD[C.IMAGE_URL_FIELDS[i]]];
            var s = String(raw || '').trim();
            if (s) { return /^https?:\/\//i.test(s) ? s : absoluteUrl(s); }
        }
        return '';
    }

    function getInputData() {
        var cols = [
            search.createColumn({ name: C.ITEM_FIELD.TYPE }),
            search.createColumn({ name: C.ITEM_FIELD.MATRIX }),
            search.createColumn({ name: C.ITEM_FIELD.SHOP_IMAGE_URL })
        ];
        for (var i = 0; i < C.IMAGE_URL_FIELDS.length; i++) {
            cols.push(search.createColumn({ name: C.ITEM_FIELD[C.IMAGE_URL_FIELDS[i]] }));
        }
        // Active items, excluding matrix CHILDREN (their parent is what the catalog
        // lists; children are resolved via the parent's child-scan when needed).
        return search.create({
            type: 'item',
            filters: [['isinactive', 'is', 'F'], 'AND', [C.ITEM_FIELD.MATRIX_CHILD, 'is', 'F']],
            columns: cols
        });
    }

    function map(context) {
        try {
            var row = JSON.parse(context.value);
            var values = row.values || {};
            var typeCode = (values.type && values.type.value) ? values.type.value : values.type;
            var isMatrix = values.matrix === true || values.matrix === 'T';
            var current = String(values[C.ITEM_FIELD.SHOP_IMAGE_URL] || '');

            // record.submitFields needs the SPECIFIC item record type, never the
            // generic 'item' (same reason record.load is mapped in the Suitelet). If
            // we can't map this type code, we can't write -- skip it.
            var rtype = ITEM_RECORD_TYPE_BY_CODE[typeCode];
            if (!rtype) { context.write({ key: 'skipped', value: row.id }); return; }

            // FILL-ONLY: an already-populated value is left alone. The supplier
            // pipeline writes the FRONT-view URL into this field per SKU, while
            // this script's vendor-field tier would resolve the (back-view)
            // custitem_sanmar_front_image_url -- re-resolving here would flip
            // such items back and forth every night. Blank is the only state
            // this script fills.
            if (current) { context.write({ key: 'unchanged', value: row.id }); return; }

            var resolved = vendorUrl(values);
            if (!resolved) { resolved = attachedImageUrl(row.id); }
            if (!resolved) { resolved = recordImageUrl(row.id, typeCode); }
            if (!resolved && isMatrix) { resolved = childImageUrl(row.id); }

            if (!resolved) { context.write({ key: 'noimage', value: row.id }); return; }

            var v = {};
            v[C.ITEM_FIELD.SHOP_IMAGE_URL] = resolved;
            record.submitFields({
                type: rtype,
                id: row.id,
                values: v,
                options: { enablesourcing: false, ignoreMandatoryFields: true }
            });
            context.write({ key: 'set', value: row.id });
        } catch (e) {
            log.error({ title: 'bsgshop image-sync map failed, item=' + (context && context.key), details: e });
        }
    }

    function reduce(context) {
        // One row per bucket (set / unchanged / noimage / skipped) with its count.
        // Emit the count to the output stage so summarize can total it (a key may be
        // split across multiple reduce calls, so summarize sums rather than replaces).
        var n = context.values.length;
        log.audit({ title: 'bsgshop image-sync: ' + context.key, details: n + ' item(s)' });
        context.write({ key: context.key, value: String(n) });
    }

    function summarize(summary) {
        var totals = {};
        summary.output.iterator().each(function (key, value) {
            totals[key] = (totals[key] || 0) + (parseInt(value, 10) || 0);
            return true;
        });
        summary.mapSummary.errors.iterator().each(function (key, err) {
            log.error({ title: 'bsgshop image-sync map error, item=' + key, details: err });
            return true;
        });
        log.audit({
            title: 'bsgshop image-sync complete',
            details: 'set=' + (totals.set || 0) + ', unchanged=' + (totals.unchanged || 0) +
                ', noimage=' + (totals.noimage || 0) + ', skipped=' + (totals.skipped || 0)
        });
    }

    return {
        getInputData: getInputData,
        map: map,
        reduce: reduce,
        summarize: summarize
    };
});
