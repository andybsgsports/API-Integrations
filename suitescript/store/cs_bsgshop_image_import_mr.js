/**
 * BSG Ordering Site - product image URL importer (Momentec/Augusta feed).
 *
 * Sets custitem_bsgshop_image_url on each item from a prebuilt item-number ->
 * image map (lib/bsgshop.image_map.js), so the "NO IMAGE" cards fill in and the
 * catalog stops doing per-item record reads for images (faster pages).
 *
 * DRY RUN by default: it writes NOTHING to items and instead produces a report
 * CSV in the File Cabinet listing exactly which items matched and what URL WOULD
 * be set (plus the match/no-match counts). Review that report, then set
 * CONFIG.IMAGE_IMPORT_APPLY = true and run again to actually write the field.
 *
 * Matching is defensive: each item's item number is looked up in the map by its
 * exact value, its zero-stripped value, and the part before any -/. suffix -- so
 * whether the account stores "020000" or "20000" it still matches.
 *
 * Safe + idempotent: only writes when the URL differs from what's stored; never
 * clears a value; every item isolated in try/catch. Purely additive.
 *
 * Run via Save & Execute on the deployment.
 *
 * @NApiVersion 2.1
 * @NScriptType MapReduceScript
 */
define([
    'N/record',
    'N/search',
    'N/file',
    'N/log',
    './lib/bsgshop.constants.js',
    './lib/bsgshop.image_map.js'
], function (record, search, file, log, C, IMG) {

    var APPLY = !!C.CONFIG.IMAGE_IMPORT_APPLY;
    var PREFIX = IMG.prefix || '';
    var MAP = IMG.map || {};
    var FOLDER_NAME = C.CONFIG.IMAGE_IMPORT_REPORT_FOLDER || 'BSG Images';

    var ITEM_RECORD_TYPE_BY_CODE = {
        InvtPart: 'inventoryitem', NonInvtPart: 'noninventoryitem', Kit: 'kititem',
        Assembly: 'assemblyitem', Service: 'serviceitem', OthCharge: 'otherchargeitem',
        Group: 'itemgroup', GiftCert: 'giftcertificateitem',
        LotNumberedInventoryItem: 'lotnumberedinventoryitem',
        SerializedInventoryItem: 'serializedinventoryitem',
        LotNumberedAssemblyItem: 'lotnumberedassemblyitem',
        SerializedAssemblyItem: 'serializedassemblyitem', DownloadItem: 'downloaditem'
    };

    function zerostrip(s) { return String(s || '').replace(/^0+(?=.)/, ''); }

    // File name for an item number, trying exact / zero-stripped / pre-suffix forms.
    function lookupFile(itemid) {
        itemid = String(itemid || '').trim();
        if (!itemid) { return ''; }
        var cands = [itemid, zerostrip(itemid)];
        var base = itemid.split(/[-.]/)[0];
        if (base && base !== itemid) { cands.push(base, zerostrip(base)); }
        for (var i = 0; i < cands.length; i++) {
            if (MAP.hasOwnProperty(cands[i])) { return MAP[cands[i]]; }
        }
        return '';
    }

    // CSV cell: quote/escape only when needed.
    function csv(v) {
        var s = String(v == null ? '' : v);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }

    function getInputData() {
        // Active items, excluding matrix CHILDREN (the parent is the catalog card).
        return search.create({
            type: 'item',
            filters: [['isinactive', 'is', 'F'], 'AND', [C.ITEM_FIELD.MATRIX_CHILD, 'is', 'F']],
            columns: [
                search.createColumn({ name: C.ITEM_FIELD.ITEM_ID }),
                search.createColumn({ name: C.ITEM_FIELD.TYPE }),
                search.createColumn({ name: C.ITEM_FIELD.SHOP_IMAGE_URL })
            ]
        });
    }

    function map(context) {
        try {
            var row = JSON.parse(context.value);
            var v = row.values || {};
            var itemid = v[C.ITEM_FIELD.ITEM_ID] || '';
            var fn = lookupFile(itemid);
            if (!fn) { context.write({ key: 'nomatch', value: '1' }); return; }

            var url = PREFIX + fn;
            var current = String(v[C.ITEM_FIELD.SHOP_IMAGE_URL] || '');
            if (url === current) { context.write({ key: 'already', value: '1' }); return; }

            // Report line (both dry-run and apply): item -> URL that will/would be set.
            context.write({ key: 'row', value: csv(itemid) + ',' + csv(url) });

            if (APPLY) {
                var typeCode = (v.type && v.type.value) ? v.type.value : v.type;
                var rtype = ITEM_RECORD_TYPE_BY_CODE[typeCode];
                if (!rtype) { context.write({ key: 'skipped_type', value: '1' }); return; }
                var vals = {};
                vals[C.ITEM_FIELD.SHOP_IMAGE_URL] = url;
                record.submitFields({
                    type: rtype, id: row.id, values: vals,
                    options: { enablesourcing: false, ignoreMandatoryFields: true }
                });
                context.write({ key: 'applied', value: '1' });
            }
        } catch (e) {
            log.error({ title: 'bsgshop image-import map failed, item=' + (context && context.key), details: e });
        }
    }

    function reduce(context) {
        if (context.key === 'row') {
            for (var i = 0; i < context.values.length; i++) { context.write({ key: 'row', value: context.values[i] }); }
        }
        context.write({ key: 'count:' + context.key, value: String(context.values.length) });
    }

    function resolveFolder(name) {
        var id = null;
        try {
            search.create({ type: 'folder', filters: [['name', 'is', name]], columns: ['internalid'] })
                .run().each(function (r) { id = r.id; return false; });
            if (!id) {
                var f = record.create({ type: 'folder' });
                f.setValue({ fieldId: 'name', value: name });
                id = f.save();
            }
        } catch (e) { log.error({ title: 'bsgshop image-import folder resolve failed', details: e }); }
        return id;
    }

    function summarize(summary) {
        var rows = [], counts = {};
        summary.output.iterator().each(function (key, value) {
            if (key === 'row') { rows.push(value); }
            else if (key.indexOf('count:') === 0) {
                var k = key.slice(6);
                counts[k] = (counts[k] || 0) + (parseInt(value, 10) || 0);
            }
            return true;
        });
        summary.mapSummary.errors.iterator().each(function (k, err) {
            log.error({ title: 'bsgshop image-import map error, item=' + k, details: err });
            return true;
        });

        rows.sort();
        var contents = 'Item,Image URL\n' + rows.join('\n') + '\n';
        var folderId = resolveFolder(FOLDER_NAME);
        var fileName = 'bsg-image-import-report.csv';
        try {
            search.create({
                type: 'file',
                filters: [['name', 'is', fileName], 'AND', ['folder', 'anyof', folderId]],
                columns: ['internalid']
            }).run().each(function (r) { try { file.delete({ id: r.id }); } catch (eD) { /* best effort */ } return true; });
        } catch (eSearch) { /* no prior file -> just create */ }
        try {
            var f = file.create({
                name: fileName, fileType: file.Type.CSV, contents: contents, folder: folderId,
                description: (APPLY ? 'APPLIED' : 'DRY RUN') + ': item -> custitem_bsgshop_image_url. ' +
                    'matched=' + rows.length + ', applied=' + (counts.applied || 0) +
                    ', already=' + (counts.already || 0) + ', no-match=' + (counts.nomatch || 0)
            });
            var fileId = f.save();
            log.audit({
                title: 'bsgshop image-import complete (' + (APPLY ? 'APPLIED' : 'DRY RUN') + ')',
                details: 'would-set/matched=' + rows.length + ', applied=' + (counts.applied || 0) +
                    ', already-set=' + (counts.already || 0) + ', no-match=' + (counts.nomatch || 0) +
                    ', skipped-type=' + (counts.skipped_type || 0) +
                    ' -> report "' + fileName + '" in "' + FOLDER_NAME + '" (id ' + fileId + ')'
            });
        } catch (e) {
            log.error({ title: 'bsgshop image-import: report write failed', details: e });
        }
    }

    return {
        getInputData: getInputData,
        map: map,
        reduce: reduce,
        summarize: summarize
    };
});
