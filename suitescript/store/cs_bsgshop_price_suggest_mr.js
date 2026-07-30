/**
 * BSG Ordering Site - suggested quantity-pricing generator (DRY RUN / REPORT ONLY).
 *
 * Reads each active item's Base Price, applies the discount curve + rounding from
 * CONFIG.PRICE_SUGGEST, and writes a **suggested-pricing CSV** to the File Cabinet
 * (folder CONFIG.PRICE_SUGGEST.FOLDER_NAME). It does NOT change any item prices --
 * it exists so BSG can review proposed quantity breaks across the real catalog
 * before entering/importing them. Safe to run repeatedly.
 *
 * Run on demand: Save & Execute on the deployment. Output: one CSV per run.
 *
 * @NApiVersion 2.1
 * @NScriptType MapReduceScript
 */
define(['N/search', 'N/record', 'N/file', 'N/log', './lib/bsgshop.constants.js'],
function (search, record, file, log, C) {

    var CFG = C.CONFIG.PRICE_SUGGEST || {};
    var TIERS = (CFG.TIERS || []).slice().sort(function (a, b) { return a.minQty - b.minQty; });

    function roundToStep(x, step) { return step > 0 ? Math.round(x / step) * step : x; }

    function roundPrice(x, mode) {
        if (!(x > 0)) { return 0; }
        if (mode === 'dollar') { return Math.round(x); }
        if (mode === 'half') { return roundToStep(x, 0.5); }
        if (mode === 'quarter_down') { return Math.floor(x * 4) / 4; }
        if (mode === 'bands') {
            // Magnitude-based: pick the first band whose maxPrice the price is under
            // (null maxPrice = catch-all top band), then round to that band's step.
            var bands = CFG.ROUNDING_BANDS || [];
            for (var i = 0; i < bands.length; i++) {
                if (bands[i].maxPrice == null || x < bands[i].maxPrice) {
                    return roundToStep(x, bands[i].step);
                }
            }
            return roundToStep(x, 0.25); // no band matched -> sensible default
        }
        return roundToStep(x, 0.25); // 'quarter' (default) -- nearest $0.25
    }

    function money(n) { return (parseFloat(n) || 0).toFixed(2); }

    // CSV field: quote and escape only when needed.
    function csv(v) {
        var s = String(v == null ? '' : v);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }

    var COST_FIELDS = CFG.COST_FIELDS || ['cost', 'averagecost', 'lastpurchaseprice'];
    var MIN_MARGIN_PCT = num(CFG.MIN_MARGIN_PCT);

    function num(x) { var n = parseFloat(x); return isNaN(n) ? 0 : n; }

    function getInputData() {
        // Active, non-matrix-child items. Base price is checked in map (some items
        // legitimately have none -- those are skipped, not errored).
        var cols = [
            C.ITEM_FIELD.ITEM_ID,
            C.ITEM_FIELD.DISPLAY_NAME,
            C.ITEM_FIELD.NATIVE_DISPLAY_NAME,
            C.ITEM_FIELD.SALES_DESCRIPTION,
            C.CONFIG.PRICE_SEARCH_FIELD // baseprice
        ].concat(COST_FIELDS) // cost basis for margin awareness
            .concat([C.ITEM_FIELD.MAP_PRICE]); // MAP floor
        return search.create({
            type: 'item',
            filters: [['isinactive', 'is', 'F'], 'AND', [C.ITEM_FIELD.MATRIX_CHILD, 'is', 'F']],
            columns: cols
        });
    }

    function map(context) {
        try {
            var row = JSON.parse(context.value);
            var v = row.values || {};
            var base = num(v[C.CONFIG.PRICE_SEARCH_FIELD]);
            if (!(base >= (CFG.MIN_BASE_PRICE || 0.01))) { context.write({ key: 'skipped', value: '1' }); return; }

            var itemNo = v[C.ITEM_FIELD.ITEM_ID] || row.id;
            var name = v[C.ITEM_FIELD.DISPLAY_NAME] || v[C.ITEM_FIELD.NATIVE_DISPLAY_NAME] ||
                v[C.ITEM_FIELD.SALES_DESCRIPTION] || itemNo;

            // Cost = first positive of the configured cost fields.
            var cost = 0;
            for (var ci = 0; ci < COST_FIELDS.length && !(cost > 0); ci++) { cost = num(v[COST_FIELDS[ci]]); }
            // Floors: never below cost x (1 + MIN_MARGIN_PCT/100), and never below MAP
            // (Minimum Advertised Price) -- take whichever is higher.
            var marginFloor = (cost > 0 && MIN_MARGIN_PCT > 0) ? cost * (1 + MIN_MARGIN_PCT / 100) : 0;
            var mapFloor = num(v[C.ITEM_FIELD.MAP_PRICE]);
            var floor = Math.max(marginFloor, mapFloor);

            var cells = [csv(itemNo), csv(name), money(base), cost > 0 ? money(cost) : ''];
            var lastPrice = base;
            for (var i = 0; i < TIERS.length; i++) {
                var raw = base * (1 - (TIERS[i].discountPct || 0) / 100);
                if (floor > 0 && raw < floor) { raw = floor; } // hold margin/MAP floor
                var p = roundPrice(raw, CFG.ROUNDING);
                cells.push(money(p));
                lastPrice = p;
            }
            // Margin at the deepest tier (the tightest) -- blank when cost is unknown.
            var marginPct = (cost > 0 && lastPrice > 0) ? Math.round((lastPrice - cost) / lastPrice * 100) : '';
            cells.push(marginPct === '' ? '' : (marginPct + '%'));
            context.write({ key: 'row', value: cells.join(',') });
        } catch (e) {
            log.error({ title: 'bsgshop price-suggest map failed', details: e });
        }
    }

    function reduce(context) {
        // Pass rows through to the output stage for summarize to assemble.
        for (var i = 0; i < context.values.length; i++) {
            context.write({ key: context.key, value: context.values[i] });
        }
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
        } catch (e) { log.error({ title: 'bsgshop price-suggest folder resolve failed', details: e }); }
        return id;
    }

    function summarize(summary) {
        var rows = [];
        summary.output.iterator().each(function (key, value) {
            if (key === 'row') { rows.push(value); }
            return true;
        });

        // Header from the configured tiers (+ Cost and deepest-tier margin).
        var header = ['Item', 'Name', 'Base Price', 'Cost'];
        for (var i = 0; i < TIERS.length; i++) { header.push('Qty ' + TIERS[i].minQty + '+ (-' + (TIERS[i].discountPct || 0) + '%)'); }
        header.push('Margin @ ' + TIERS[TIERS.length - 1].minQty + '+');
        rows.sort();
        var contents = header.join(',') + '\n' + rows.join('\n') + '\n';

        var folderId = resolveFolder(CFG.FOLDER_NAME || 'BSG Pricing');
        var fileName = 'bsg-suggested-pricing.csv';
        // Replace any prior report so re-runs stay clean -- the File Cabinet won't
        // allow two files with the same name in one folder, which would make a second
        // run's create fail.
        try {
            search.create({
                type: 'file',
                filters: [['name', 'is', fileName], 'AND', ['folder', 'anyof', folderId]],
                columns: ['internalid']
            }).run().each(function (r) { try { file.delete({ id: r.id }); } catch (eD) { /* best effort */ } return true; });
        } catch (eSearch) { /* no prior file / search unavailable -> just create below */ }
        try {
            var f = file.create({
                name: fileName,
                fileType: file.Type.CSV,
                contents: contents,
                folder: folderId,
                description: 'DRY-RUN suggested quantity pricing (curve ' +
                    TIERS.map(function (t) { return t.minQty + ':' + (t.discountPct || 0) + '%'; }).join(' ') +
                    ', rounding ' + (CFG.ROUNDING || 'quarter') + '). No item prices were changed.'
            });
            var fileId = f.save();
            log.audit({
                title: 'bsgshop price-suggest complete (DRY RUN - no prices changed)',
                details: rows.length + ' items -> File Cabinet folder "' + (CFG.FOLDER_NAME || 'BSG Pricing') +
                    '", file "' + fileName + '" (id ' + fileId + ')'
            });
        } catch (e) {
            log.error({ title: 'bsgshop price-suggest: CSV write failed', details: e });
        }
    }

    return {
        getInputData: getInputData,
        map: map,
        reduce: reduce,
        summarize: summarize
    };
});
