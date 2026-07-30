/**
 * BSG Ordering Site - shared constants.
 *
 * Single source of truth for the storefront. The catalog is driven entirely by
 * NATIVE NetSuite item fields (the item record's "Web Store" tab) -- there is no
 * custom "show on site" flag. An item appears on the store when its native
 * "Display in Web Store" checkbox (isonline) is checked. Name, description,
 * image, and price all come from the native web-store fields too, so the catalog
 * is managed from the standard item screen your team already uses.
 *
 * @NApiVersion 2.1
 */
define([], function () {

    // Native NetSuite item field ids used to build the catalog. None of these are
    // custom -- they are standard fields on the item record's "Web Store" tab and
    // pricing/inventory sublists.
    var ITEM_FIELD = {
        IS_ONLINE: 'isonline',                       // "Display in Web Store" checkbox -> the catalog gate
        ITEM_ID: 'itemid',                           // item name/number (fallback display name)
        DISPLAY_NAME: 'storedisplayname',            // "Web Store Display Name"
        NATIVE_DISPLAY_NAME: 'displayname',          // native "Display Name/Code" (often the product name on synced items)
        STORE_DESCRIPTION: 'storedescription',       // "Web Store Description" (short)
        DETAILED_DESCRIPTION: 'storedetaileddescription', // "Detailed Description"
        SALES_DESCRIPTION: 'salesdescription',       // plain sales description (fallback)
        DISPLAY_IMAGE: 'storedisplayimage',          // "Item Display Image" (File Cabinet file)
        DISPLAY_THUMBNAIL: 'storedisplaythumbnail',  // "Item Display Thumbnail"
        BASE_PRICE: 'baseprice',                     // Base Price (search column)
        QTY_AVAILABLE: 'quantityavailable',          // Quantity Available (search column)
        MATRIX: 'matrix',                            // T = matrix PARENT (has size/color children)
        MATRIX_CHILD: 'matrixchild',                 // T = matrix CHILD (one size/color combination)
        PARENT: 'parent',                            // child -> parent item link
        TYPE: 'type',                                // internal item-type code (InvtPart, NonInvtPart, Kit, ...)
        UPC: 'upccode',                               // UPC/barcode, if present
        WEIGHT: 'weight',                            // shipping weight, if present
        VENDOR_NAME: 'vendorname',                   // preferred vendor's name/code (text)
        WEB_CATEGORY: 'category',                    // (reserved) web site category, if used later
        // Classification fields the header filters are built from.
        DEPARTMENT: 'department',                    // e.g. Volleyball, Football
        CLASS: 'class',                              // e.g. Accessories : Scorebooks
        VENDOR: 'vendor',                            // preferred vendor (entity) -> SanMar, S&S, ...

        // --- BSG's actual custom fields (from the account's item field export) ---
        // "Item Image" -- an Image-type field BSG maintains as THE canonical photo
        // for an item (its field help literally says "Custom Record for Item
        // Images"). Checked before the native Web Store image field.
        ATLAS_IMAGE: 'custitem_atlas_item_image',
        // A plain TEXT field that mirrors the resolved best image URL, precomputed by
        // the image-sync Map/Reduce (cs_bsgshop_image_sync_mr.js). Unlike ATLAS_IMAGE
        // (an Image-type field that crashes as a search column), this IS columnable,
        // so the catalog reads it directly and skips per-item record loads. Defined in
        // src/Objects/custitem_bsgshop_image_url.xml. Safe to be blank -> live fallback.
        SHOP_IMAGE_URL: 'custitem_bsgshop_image_url',
        // Per-vendor image URLs (Hyperlink fields, already full https URLs), synced
        // in from each supplier feed. Checked in this order as a fallback chain.
        SANMAR_IMAGE_URL: 'custitem_sanmar_front_image_url',
        SS_IMAGE_URL: 'custitem_ss_front_image_url',
        SS_IMAGE_URL_MODEL: 'custitem_ss_on_model_image_url',
        MTEC_IMAGE_URL: 'custitem_mtec_front_image_url',
        UA_IMAGE_URL: 'custitem_ua_front_image_url',
        // Sale pricing: BSG_ON_SALE gates whether to show a strikethrough MSRP next
        // to the current price; the MSRP itself comes from whichever vendor synced it.
        ON_SALE: 'custitem_bsg_on_sale',
        SANMAR_MSRP: 'custitem_sanmar_msrp',
        SS_MSRP: 'custitem_ss_msrp',
        MTEC_MSRP: 'custitem_mtec_msrp',
        // General "Compare At" / MSRP the store can show as a strikethrough even when
        // an item isn't vendor-synced -- populated from the vendor price lists (which
        // carry MSRP by style). Defined in src/Objects/custitem_bsgshop_msrp.xml.
        MSRP: 'custitem_bsgshop_msrp',
        // Minimum Advertised Price (from the vendor MAP list, matched by UPC). The
        // store never advertises below this. Defined in custitem_bsgshop_map.xml.
        MAP_PRICE: 'custitem_bsgshop_map',
        // Spec fields sourced from whichever vendor feed populated the item.
        SS_BRAND: 'custitem_ss_brand',
        SS_WEIGHT: 'custitem_ss_weight',
        SANMAR_GTIN: 'custitem_sanmar_gtin',
        SS_GTIN: 'custitem_ss_gtin',
        MTEC_GTIN: 'custitem_mtec_gtin',
        UA_GTIN: 'custitem_ua_gtin',
        CHAMPRO_GTIN: 'custitem_champro_gtin',

        // Per-vendor "Qty Available" fields. Drop-ship / vendor-synced items often
        // track their real availability HERE, not on NetSuite's native Quantity
        // Available (which stays 0/blank for stock BSG never physically holds) --
        // without this fallback, such items would look permanently out of stock
        // (and worse, be excluded from the catalog outright by the inventory filter).
        SANMAR_QTY: 'custitem_sanmar_qty_available',
        SS_QTY: 'custitem_ss_qty_available',
        MTEC_QTY: 'custitem_mtec_qty_available',
        UA_QTY: 'custitem_ua_qty_available',
        CHAMPRO_QTY: 'custitem_champro_qty_available',
        USB_QTY: 'custitem_usb_qty_available',
        TCK_QTY: 'custitem_tck_qty_available',
        CAPAMERICA_QTY: 'custitem_capamerica_qty_available',
        MIZUNO_QTY: 'custitem_mizuno_qty_available',
        RIPIT_QTY: 'custitem_ripit_qty_available',
        BADEN_QTY: 'custitem_baden_qty_available'
    };

    // Ordered fallback chains built from the ITEM_FIELD ids above -- kept as plain
    // arrays so the resolver functions can just walk them.
    // Image-type fields must NEVER be requested as saved-search columns or
    // lookupFields columns. The native storedisplayimage throws a clean "invalid
    // column" at create time; the CUSTOM custitem_atlas_item_image is worse -- it
    // passes search.create() validation and then crashes row materialization with
    // an uncatchable Java ScriptNullObjectAdapter NPE, which leaks out of the
    // Suitelet as a NetSuite HTML Notice page and took the whole catalog down.
    // Both are read exclusively off a LOADED record (record.load, detail view
    // only). This list therefore stays EMPTY on purpose -- do not add Image-type
    // fields back here.
    var IMAGE_FILE_FIELDS = [];
    var IMAGE_URL_FIELDS = ['SANMAR_IMAGE_URL', 'SS_IMAGE_URL', 'SS_IMAGE_URL_MODEL', 'MTEC_IMAGE_URL', 'UA_IMAGE_URL']; // Hyperlink-type fields (already a URL)
    var MSRP_FIELDS = ['MSRP', 'SANMAR_MSRP', 'SS_MSRP', 'MTEC_MSRP'];
    var GTIN_FIELDS = ['SANMAR_GTIN', 'SS_GTIN', 'MTEC_GTIN', 'UA_GTIN', 'CHAMPRO_GTIN'];
    var QTY_FIELDS = ['SANMAR_QTY', 'SS_QTY', 'MTEC_QTY', 'UA_QTY', 'CHAMPRO_QTY', 'USB_QTY',
        'TCK_QTY', 'CAPAMERICA_QTY', 'MIZUNO_QTY', 'RIPIT_QTY', 'BADEN_QTY'];

    // Native customer fields for the "search your school" autofill. entityid is the
    // account NUMBER in this account; the human-readable school name lives in
    // companyname (same as the Campaign Manager's audience display).
    var CUSTOMER_FIELD = {
        ENTITY_ID: 'entityid',       // account number
        COMPANY_NAME: 'companyname', // school / organization name (display + search)
        CITY: 'city',
        STATE: 'state',
        EMAIL: 'email',
        SALES_REP: 'salesrep'        // assigned account manager (Employee) -> gets notified
    };

    var SCRIPT = {
        STORE_SUITELET: {
            scriptId: 'customscript_bsgshop_store_su',
            deploymentId: 'customdeploy_bsgshop_store_su'
        },
        IMAGE_SYNC_MR: {
            scriptId: 'customscript_bsgshop_image_sync_mr',
            deploymentId: 'customdeploy_bsgshop_image_sync_mr'
        },
        PRICE_SUGGEST_MR: {
            scriptId: 'customscript_bsgshop_price_suggest_mr',
            deploymentId: 'customdeploy_bsgshop_price_suggest_mr'
        }
    };

    // API action names the client posts back to the same public Suitelet URL.
    var ACTION = {
        PRODUCTS: 'products',     // paginated catalog list
        PRODUCT: 'product',       // single product detail (incl. matrix variants)
        FILTERS: 'filters',       // distinct Department/Class/Category/Vendor values
        SCHOOLS: 'schools',       // school (customer) name search -> autofill
        SUBMIT: 'submit',         // place an order or request a quote
        TRACK: 'track',           // best-effort analytics event (fire-and-forget)
        ART: 'art'                // stream a sample-art image (binary, not JSON)
    };

    // Lightweight, opt-out store analytics. One append-only custom-record row per
    // high-signal shopper action; written best-effort by the Suitelet's "track"
    // action and never allowed to slow or break a customer-facing response. The
    // record is defined in src/Objects/customrecord_bsgshop_event.xml. BSG can build
    // saved searches/reports off it for a simple funnel. Turn the whole thing off
    // with CONFIG.ANALYTICS_ENABLED = false (the record can then be deleted).
    var ANALYTICS = {
        RECORD: 'customrecord_bsgshop_event',
        FIELD: {
            TYPE: 'custrecord_bsgshopevt_type',
            REF: 'custrecord_bsgshopevt_ref',
            SESSION: 'custrecord_bsgshopevt_session',
            SCHOOL: 'custrecord_bsgshopevt_school',
            VALUE: 'custrecord_bsgshopevt_value',
            DETAIL: 'custrecord_bsgshopevt_detail'
        },
        // The only event names the server will persist; anything else is ignored so
        // the public endpoint can't be used to write arbitrary rows.
        EVENTS: ['page_view', 'product_view', 'add_to_cart', 'search',
            'submit_order', 'submit_quote', 'team_request', 'design_request']
    };

    var SUBMIT_TYPE = {
        ORDER: 'order',   // -> Sales Order (PO Required)
        QUOTE: 'quote'    // -> Estimate / Quote
    };

    // Custom TRANSACTION COLUMN (line-level) fields written when an order/quote is
    // created, carrying the personalization a shopper types on the product page.
    // Defined as SDF objects in src/Objects/custcol_bsgshop_player_*.xml and set
    // best-effort (a missing field never blocks order creation).
    var LINE_FIELD = {
        PLAYER_NAME: 'custcol_bsgshop_player_name',
        PLAYER_NUMBER: 'custcol_bsgshop_player_number'
    };

    var CONFIG = {
        // What counts as "on the storefront":
        //   'inventory' (default) - ANY active item with Quantity Available > 0. No
        //                     per-item setup: stock it and it appears; it drops off when
        //                     it sells out. Images are best-effort (see below) and never
        //                     block an item from listing.
        //   'inventory_image' - like 'inventory' but ONLY items that also have a Web
        //                     Store display image set.
        //   'web_store_flag' - only items whose native "Display in Web Store" (isonline)
        //                     box is checked (curated control, more manual).
        //
        // Image resolution per item (first hit wins): the Web Store "Item Display
        // Image" field -> the first image file attached to the item (Files subtab) ->
        // a "No image" placeholder in the UI.
        CATALOG_MODE: 'inventory',

        // Header filter dropdowns, in display order. Each is built from the DISTINCT
        // values of its item field across the in-stock catalog; a filter whose field
        // yields no values is hidden automatically. Adjust `field` ids here if BSG's
        // categorization lives elsewhere (e.g. a custom category field).
        // Consumer-facing labels (not the raw NetSuite field names). `hierarchical`
        // renders the facet as a collapsible tree built from NetSuite's " : "
        // classification paths (e.g. "Accessories : Scorebooks") -- only top-level
        // nodes show until you click to drill in, so a long class list isn't one
        // giant scroll. The `key` still maps to the underlying field for filtering.
        FILTERS: [
            { key: 'department', label: 'Sport', field: 'department' },
            { key: 'class', label: 'Categories', field: 'class', hierarchical: true },
            { key: 'vendor', label: 'Brand', field: 'vendor' }
        ],

        // Vendor-synced items are often FLAT (one standalone item per size/color,
        // e.g. "1359344-White/Navy-3X-Large") rather than true NetSuite matrix
        // items. When true, the catalog groups items sharing a product name
        // (display name / sales description) into ONE card whose detail view shows
        // all of them as size/color options. Falls back to flat listing if the
        // grouped search isn't supported.
        GROUP_FLAT_VARIANTS: true,

        // File Cabinet folders (matched by NAME) where supplier/vendor images are
        // actually filed, one image file per item -- matched to an item by its
        // item number/style number appearing in the file name (e.g.
        // "1359344.jpg" or "1359344-front.jpg" for item "1359344-White-3X-Large").
        // Checked after the direct field-based image resolution and before the
        // last-resort generic field scan.
        SUPPLIER_IMAGE_FOLDER_NAMES: ['Images', 'Supplier Item Images'],

        // Max raw items looked up per request in the supplier image folders (one
        // batched file search covers the whole batch, but very large flat-variant
        // groups are capped so the search filter doesn't grow unbounded).
        SUPPLIER_IMAGE_LOOKUP_MAX: 200,

        // Max per-item record.load reads used to pull the authoritative "Item Image"
        // (custitem_atlas_item_image, an Image-type field that can't be a SEARCH column)
        // + Base Price straight off the record. record.load is ~10 governance units;
        // a Suitelet's ceiling is 10,000 units, so this can comfortably cover a whole
        // page. Sized so every card on a page (PAGE_SIZE) still gets a look even when
        // the first few cards are matrix parents that each spend a child search + a
        // few child loads -- too small a cap starved the later cards and showed them
        // as "No image" even though they had one. ~80 loads = ~800 units.
        RECORD_IMAGE_LOOKUP_MAX: 80,

        // Per matrix PARENT, how many size/color children to record.load looking for
        // an image before giving up. Kept low so one many-variant parent can't eat
        // the whole page's RECORD_IMAGE_LOOKUP_MAX budget and starve later cards.
        MATRIX_CHILD_IMAGE_SCAN_MAX: 3,

        // Max record.load-based image-discovery scans per request (used only for
        // items with no Web Store image and no attached image file -- e.g. a
        // vendor-sync custom field -- each scan costs governance, so it's capped).
        IMAGE_SCAN_MAX_PER_PAGE: 10,

        // Upper bound on raw item rows read per catalog request when grouping flat
        // variants (GROUP_FLAT_VARIANTS). Bounds governance on very large catalogs;
        // raise if BSG's live catalog exceeds this and items stop appearing.
        MAX_RAW_CATALOG_ROWS: 3000,

        // Catalog paging, applied AFTER grouping (i.e. N PRODUCT CARDS per page, not
        // N raw items -- a grouped product with 10 colors/sizes still counts as one
        // card). The shopper picks how many per page from the pager (25 / 50 / 100);
        // PAGE_SIZE is the default and PAGE_SIZE_OPTIONS is the server-validated
        // whitelist of choices (an arbitrary size in the URL is ignored).
        PAGE_SIZE: 25,
        PAGE_SIZE_OPTIONS: [25, 50, 100],

        // ---- Server-side response cache (page speed) ----
        // The grouped catalog re-runs the FULL item search on every request, which
        // made pages feel slow. API responses (catalog pages, filters, product
        // details) are cached account-wide via N/cache: the first shopper after a
        // TTL pays the search cost, everyone else loads near-instantly. Item
        // changes (price/stock/images) appear within the TTL. Errors are never
        // cached. Set a TTL to 0 to disable that cache.
        CACHE_CATALOG_TTL: 300,   // catalog pages (5 min)
        CACHE_FILTERS_TTL: 3600,  // filter dropdown values (1 hr)
        CACHE_DETAIL_TTL: 180,    // product detail (3 min -- keeps qty fresh-ish)
        // Per-page record.load budget for LIST pages when an item still has no
        // precomputed/attached/vendor image -- each load is slow, and a page of
        // imageless items could burn the full RECORD_IMAGE_LOOKUP_MAX (80) per
        // request. Lists cap lower; stragglers just show "No image" on the card
        // (the detail view still resolves them with the full budget).
        LIST_RECORD_IMAGE_MAX: 10,

        // Inventory badge thresholds against Quantity Available.
        // qty <= 0            -> "Out of Stock"
        // qty <= LOW_STOCK    -> "Low Stock"
        // otherwise           -> "In Stock"
        LOW_STOCK_THRESHOLD: 6,

        // MAP (Minimum Advertised Price) compliance. When on, the storefront floors
        // every publicly displayed price -- the base price AND each quantity-tier
        // price -- at the item's MAP (custitem_bsgshop_map), so BSG never advertises
        // below a vendor's MAP. Fail-safe: items with no MAP set are unaffected (most
        // items). BSG can still quote below MAP privately; this only governs what the
        // public store displays. Turn off if a vendor exempts volume pricing.
        MAP_ENFORCE: true,

        // "Compare At" strikethrough. The store already shows a struck-through MSRP
        // for on-sale items; with this on, it shows the MSRP as a "Compare at $X"
        // whenever a (higher) MSRP is present -- not just when the on-sale flag is
        // set -- reading the MSRP fallback chain (general MSRP field first, then the
        // vendor MSRP fields). Fail-safe: no MSRP -> just the price, as before.
        COMPARE_AT_ALWAYS: true,

        // ---- Player name/number personalization (apparel departments only) ----
        // The optional "Player Name / Player #" fields on the product page only make
        // sense for decorable goods -- apparel/uniforms -- not equipment like balls
        // or nets. The signal is the item's DEPARTMENT: if the department is labeled
        // apparel, personalization applies to every item in it regardless of class.
        // An item qualifies when a PERSONALIZE_DEPT_FIELDS value contains one of
        // PERSONALIZE_KEYWORDS (case-insensitive substring), so a parent department
        // "Apparel" and any sub-department ("Apparel : Team Uniforms") both match.
        // Fully fail-safe: defaults to FALSE, so an item in a non-apparel department
        // simply hides the optional fields. Add department synonyms below if BSG uses
        // any (e.g. "Uniforms").
        PERSONALIZE_ENABLED: true,
        // Which item field(s) decide personalization (ITEM_FIELD keys). Department
        // only, by design -- class does not matter.
        PERSONALIZE_DEPT_FIELDS: ['DEPARTMENT'],
        PERSONALIZE_KEYWORDS: ['apparel', 'uniform'],

        // Which native price the public store shows. Defaults to Base Price, which
        // every item has. If you keep a dedicated "Online Price" price level and
        // want that shown instead, change this to that price level's search field.
        // Contract (customer-specific) pricing is Phase 2 (Customer Center login).
        PRICE_SEARCH_FIELD: 'baseprice',

        // ---- Quantity (tier) pricing ----
        // When on, the product DETAIL reads the item's quantity price breaks off the
        // pricing matrix and shows a tier table; the unit price + line total update
        // live as the shopper changes quantity. Requires NetSuite's "Multiple Prices"
        // + "Quantity Pricing" features and breaks entered on the item's Pricing
        // subtab for STORE_PRICE_LEVEL_ID. Fully fail-safe: an item with no breaks
        // just shows its single price, exactly as before. Per-customer/contract
        // levels come with Phase 2 (Customer Center login).
        QUANTITY_PRICING_ENABLED: true,
        // Which price level's quantity breaks the store reads. '1' is the built-in
        // "Base Price" level in every account; point this at a dedicated web/list
        // level if you keep one. Matched by internal id first, then by the name below.
        STORE_PRICE_LEVEL_ID: '1',
        STORE_PRICE_LEVEL_NAME: 'Base Price',

        // How the product detail builds the volume-tier table:
        //   'levels' - read the item's separate price LEVELS (Price 1-5) as tiers,
        //              mapped to break quantities by TIER_PRICE_LEVELS below. This is
        //              how BSG's web feed carries volume pricing (Price 1-5 = a
        //              0/-10/-15/-20/-25% curve).
        //   'matrix' - read quantity price breaks off the Base Price level's matrix.
        //   'auto'   - (default) try levels first, fall back to the matrix.
        // Fail-safe either way: no real variation -> no tier table, just the price.
        TIER_SOURCE: 'auto',
        // Maps NetSuite price LEVEL internal id -> the minimum quantity it represents.
        // These are BSG's ACTUAL tier price levels (Setup > Accounting > Accounting
        // Lists > Price Level), confirmed from the account:
        //   1  = Base Price - Tier 1   -> 1+
        //   6  = Tier Base 2           -> 12+
        //   7  = Tier Base 3           -> 24+
        //   8  = Tier Base 4           -> 48+
        //   9  = Tier Base 5           -> 144+
        // The former duplicate "Tier Base 5" (id 10) was repurposed to another level
        // in the account, so it is intentionally NOT mapped here -- only the real
        // volume tiers 1/6/7/8/9 are read.
        // Levels 2 (10% Off), 3 (Alternate Price 2), 4 (Suggested Team Price) are NOT
        // volume tiers and are intentionally excluded.
        TIER_PRICE_LEVELS: [
            { level: '1', minQty: 1 },
            { level: '6', minQty: 12 },
            { level: '7', minQty: 24 },
            { level: '8', minQty: 48 },
            { level: '9', minQty: 144 }
        ],

        // ---- Suggested quantity-pricing generator (cs_bsgshop_price_suggest_mr.js) ----
        // A DRY-RUN, report-only job: reads each item's Base Price, applies the curve
        // + rounding below, and writes a suggested-pricing CSV to the File Cabinet.
        // It NEVER changes item prices -- it's for review before you import/apply.
        PRICE_SUGGEST: {
            // Discount off Base Price by minimum quantity (the tier break points).
            TIERS: [
                { minQty: 1, discountPct: 0 },
                { minQty: 12, discountPct: 8 },
                { minQty: 24, discountPct: 12 },
                { minQty: 48, discountPct: 18 },
                { minQty: 144, discountPct: 25 }
            ],
            // Price rounding. 'bands' (default) rounds by price magnitude using
            // ROUNDING_BANDS below -- finer on small prices, coarser in the hundreds,
            // matching BSG's convention (e.g. 9.20->9.25, 8.80->8.75; but 130.28->130,
            // 103.37->105). Other modes ignore the bands: 'quarter' = nearest $0.25,
            // 'quarter_down' = round down to $0.25, 'half' = nearest $0.50,
            // 'dollar' = nearest $1.
            ROUNDING: 'bands',
            // Ordered smallest-first; each band applies to prices strictly BELOW
            // maxPrice (maxPrice null = the catch-all top band). step = increment the
            // price is rounded to the nearest of.
            ROUNDING_BANDS: [
                { maxPrice: 100, step: 0.25 }, // under $100  -> nearest $0.25
                { maxPrice: null, step: 5 }    // $100 and up -> nearest $5 (0s and 5s)
            ],
            // Where the CSV report is written (resolved/created by name).
            FOLDER_NAME: 'BSG Pricing',
            // Only include items priced at/above this (skip $0 / price-on-request).
            MIN_BASE_PRICE: 0.01,
            // Margin awareness. When the item has a Cost, the report adds a Cost
            // column and the margin % at each tier, and never suggests a tier price
            // below Cost x (1 + MIN_MARGIN_PCT/100) -- so volume discounts can't erode
            // past your floor. Set MIN_MARGIN_PCT to 0 to disable the floor (still
            // reports margins). Cost is read from the item's Purchase Price, then
            // Average Cost, then Last Purchase Price.
            MIN_MARGIN_PCT: 20,
            COST_FIELDS: ['cost', 'averagecost', 'lastpurchaseprice']
        },

        // Minimum characters before the school search runs (avoids returning the
        // whole customer base and keeps it from being an enumeration endpoint).
        SCHOOL_SEARCH_MIN_CHARS: 3,
        SCHOOL_SEARCH_LIMIT: 15,

        // New storefront submissions are created in a needs-review state so BSG
        // staff confirm identity/pricing before anything is fulfilled. (Public,
        // anonymous submissions must never auto-fulfill.)
        ORDER_MEMO: 'Submitted via BSG Ordering Site',

        // Rep notification routing. When ROUTE_TO_ASSIGNED_REP is on, a new
        // submission notifies the Sales Rep assigned to the chosen school/account
        // (the customer record's "salesrep") at their employee email. If the
        // account has no assigned rep (or the rep is inactive), it falls back to
        // BSG_NOTIFY_EMAIL. Team requests additionally fall back to the submit
        // author so a roster request is never dropped. Set BSG_NOTIFY_EMAIL to a
        // shared "Web Orders" mailbox to catch unassigned accounts.
        ROUTE_TO_ASSIGNED_REP: true,
        BSG_NOTIFY_EMAIL: '',

        // Who confirmation emails are sent "from". NetSuite requires the author of
        // N/email.send to be an Employee. Two ways to set it (checked in order):
        //   1. SUBMIT_AUTHOR_EMPLOYEE_ID - the employee's internal id, if you know it.
        //   2. SUBMIT_AUTHOR_EMAIL       - an employee's email; resolved to the id at
        //      run time (handy when you don't want to look up an internal id).
        // Defaults to the account owner's address; change to a shared "Web Orders"
        // mailbox (an Employee record with that email) whenever you like.
        SUBMIT_AUTHOR_EMPLOYEE_ID: null,
        SUBMIT_AUTHOR_EMAIL: 'andy@bsgsports.com',

        STORE_TITLE: 'Badger Sporting Goods - Order Center',

        // Sports offered in the Team Order form's "Sport" dropdown, and the size
        // rows shown in its roster-sizing grid (Size x Jerseys/Shorts quantities).
        TEAM_SPORTS: ['Football', 'Basketball', 'Baseball', 'Softball', 'Soccer',
            'Volleyball', 'Track & Field', 'Cross Country', 'Wrestling', 'Lacrosse',
            'Hockey', 'Golf', 'Tennis', 'Swimming', 'Cheer / Dance', 'Other'],
        // Full youth + adult size run for the roster REQUEST grid. These are asks,
        // not inventory: a roster-only request creates no transaction -- the rep
        // confirms per-item availability when quoting (the product detail's variant
        // grid is what shows an item's REAL sizes). Youth XS-XL, then adult XXS-6XL.
        ROSTER_SIZES: ['YXS', 'YS', 'YM', 'YL', 'YXL', 'XXS', 'XS', 'S', 'M', 'L', 'XL', '2XL', '3XL', '4XL', '5XL', '6XL'],

        // ---- Uniform Builder (customer apparel mock-up tool) ----
        // A client-side configurator: recolor a garment template's zones, add team
        // name / player name & number, pick a font, drop on a logo, live-preview,
        // then save the mock-up (PNG -> File Cabinet) with a design spec routed to
        // the account's rep as a custom-uniform request. It is a mock-up for
        // approval, NOT a production art file -- the BSG art team finalizes the proof.
        // DISABLED: BSG follows each vendor's templates for uniforms (handled by the
        // embedded Momentec configurator), so the generic cartoon builder is hidden.
        // Code retained but off; the storefront shows real product images instead.
        UNIFORM_BUILDER_ENABLED: false,
        // Team-colour swatches offered in the builder (name shown on hover).
        BUILDER_COLORS: [
            { name: 'Badger Red', hex: '#ec3013' }, { name: 'Black', hex: '#201e1d' },
            { name: 'White', hex: '#ffffff' }, { name: 'Navy', hex: '#1b2a4a' },
            { name: 'Royal', hex: '#1f4ed8' }, { name: 'Columbia', hex: '#5aa9e6' },
            { name: 'Kelly', hex: '#1a7f42' }, { name: 'Forest', hex: '#14532d' },
            { name: 'Vegas Gold', hex: '#c2a24a' }, { name: 'Gold', hex: '#f2b705' },
            { name: 'Orange', hex: '#e8590c' }, { name: 'Maroon', hex: '#7a1f2b' },
            { name: 'Purple', hex: '#5b2a86' }, { name: 'Silver', hex: '#c9c7c6' },
            { name: 'Graphite', hex: '#4a4745' }
        ],
        // Lettering styles (label + CSS font stack that also rasterizes to PNG).
        BUILDER_FONTS: [
            { name: 'Athletic Block', css: '"Arial Black", Impact, sans-serif' },
            { name: 'Varsity Serif', css: 'Georgia, "Times New Roman", serif' },
            { name: 'Modern', css: 'Archivo, Arial, sans-serif' }
        ],
        // File Cabinet folder for saved mock-ups. If null, resolved/created by name.
        MOCKUP_FOLDER_ID: null,
        MOCKUP_FOLDER_NAME: 'BSG Uniform Mockups',

        // ---- Momentec Brands custom-sublimation configurator (embedded) ----
        // BSG's authorized Momentec plug-in, embedded as an iframe. The configurator
        // (catalog, roster, artwork, saved designs) runs on Momentec; when a customer
        // finishes, Momentec posts the design + webRef and we route it to the account's
        // rep to place the production order (Phase 2 = priced line items / drop-ship).
        // NOTE: Momentec must whitelist the NetSuite store domain for iframe embedding,
        // or the frame renders blank. EMBED_BASE + the account params come from the
        // supplied plug-in snippet (discount 10%, +2 day lead time, header hidden).
        MOMENTEC_ENABLED: true,
        MOMENTEC_EMBED_BASE: 'https://www.momentecbrands.com/custom-sublimation',
        MOMENTEC_DISCOUNT: 10,
        MOMENTEC_ADDL_LEAD_TIME: 2,
        MOMENTEC_HIDE_HEADER: true,
        MOMENTEC_LABEL: 'Custom Sublimation',
        // Category quick-picker for the sublimation screen. Each entry loads the
        // Momentec configurator for that category by REPLACING the last path segment
        // of MOMENTEC_EMBED_BASE with `slug` -- the exact value Momentec's own
        // plug-in resolves for that category (standard sports => "custom-sublimation-
        // <sport>"; specials and headwear use their own rewrite rules, e.g.
        // "custom-cheer", "uniforms--1", "headwear-custom-snapback-hats"). Empty
        // selection = the base configurator. `group` sections the picker in the UI.
        // Slugs are derived from the plug-in mapping; verify any new ones live.
        MOMENTEC_CATEGORIES: [
            { group: 'Sublimated Apparel', label: 'Baseball', slug: 'custom-sublimation-baseball' },
            { group: 'Sublimated Apparel', label: 'Softball', slug: 'custom-sublimation-softball' },
            { group: 'Sublimated Apparel', label: 'Basketball', slug: 'custom-sublimation-basketball' },
            { group: 'Sublimated Apparel', label: 'Football', slug: 'custom-sublimation-football' },
            { group: 'Sublimated Apparel', label: 'Soccer', slug: 'custom-sublimation-soccer' },
            { group: 'Sublimated Apparel', label: 'Lacrosse', slug: 'custom-sublimation-lacrosse' },
            { group: 'Sublimated Apparel', label: 'Volleyball', slug: 'custom-sublimation-volleyball' },
            { group: 'Sublimated Apparel', label: 'Hockey', slug: 'custom-sublimation-hockey' },
            { group: 'Sublimated Apparel', label: 'Track', slug: 'custom-sublimation-track' },
            { group: 'Sublimated Apparel', label: 'Training', slug: 'custom-sublimation-training' },
            { group: 'Sublimated Apparel', label: 'Training Turbo', slug: 'training-turbo-sublimation' },
            { group: 'Sublimated Apparel', label: 'Compression', slug: 'custom-sublimation-compression' },
            { group: 'Sublimated Apparel', label: 'Fanwear', slug: 'custom-sublimation-fanwear' },
            { group: 'Sublimated Apparel', label: 'Outerwear & Fleece', slug: 'custom-sublimation-fleece' },
            { group: 'Sublimated Apparel', label: 'Polos', slug: 'custom-sublimation-polo' },
            { group: 'Sublimated Apparel', label: 'Accessories', slug: 'custom-sublimation-accessories' },
            { group: 'Sublimated Apparel', label: 'Cheer / Dance', slug: 'custom-cheer' },
            { group: 'Sublimated Apparel', label: 'Semi-Sublimated', slug: 'semi-sublimated' },
            { group: 'Sublimated Apparel', label: 'Uniforms', slug: 'uniforms--1' },
            { group: 'Sublimated Apparel', label: 'Pop Warner', slug: 'pop-warner-sublimation' },
            { group: 'Sublimated Apparel', label: 'Off-Field', slug: 'off-field-turbo-sublimation' },
            { group: 'Sublimated Apparel', label: 'Babe Ruth', slug: 'custom-sublimation-babe-ruth-turbo' },
            { group: 'Custom Headwear', label: 'All Headwear', slug: 'freestyle-custom-headwear' },
            { group: 'Custom Headwear', label: 'Snapback', slug: 'headwear-custom-snapback-hats' },
            { group: 'Custom Headwear', label: 'Flexfit', slug: 'headwear-custom-flexfit-hats' },
            { group: 'Custom Headwear', label: 'Trucker / Mesh', slug: 'headwear-custom-trucker-hats' },
            { group: 'Custom Headwear', label: 'Structured', slug: 'headwear-custom-structured-hats' },
            { group: 'Custom Headwear', label: 'Unstructured', slug: 'headwear-custom-unstructured-hats' },
            { group: 'Custom Headwear', label: 'Visors', slug: 'headwear-custom-visors' },
            { group: 'Custom Headwear', label: 'Beanies / Knits', slug: 'headwear-custom-beanies-knits' },
            { group: 'Custom Headwear', label: 'Camo', slug: 'headwear-custom-camo-hats' },
            { group: 'Custom Headwear', label: 'On-Field', slug: 'headwear-custom-on-field-hats' },
            { group: 'Custom Headwear', label: 'Sideline / Coaches', slug: 'headwear-custom-sideline-coaches-hats' },
            { group: 'Custom Headwear', label: 'Active / Lightweight', slug: 'headwear-custom-running-hats' },
            { group: 'Custom Headwear', label: 'Outdoor / Lifestyle', slug: 'headwear-custom-lifestyle-outdoor-hats' },
            { group: 'Custom Headwear', label: 'Wide Brim / Boonie', slug: 'headwear-custom-boonie-hats' },
            { group: 'Custom Headwear', label: 'Styles', slug: 'headwear-styles' },
            { group: 'Custom Headwear', label: 'Features', slug: 'headwear-features' },
            { group: 'Custom Headwear', label: 'Elite Series', slug: 'headwear-elite-series' }
        ],
        // The "main page" each rail group lands on when its header is clicked, so
        // opening a group also loads that section in the configurator instead of
        // just revealing its sub-list. Key = the `group` value above; value = a
        // slug resolved exactly like MOMENTEC_CATEGORIES (empty string = the base
        // configurator, i.e. all sublimated styles). A group with no entry here
        // just expands, as before.
        MOMENTEC_GROUP_LANDING: {
            'Sublimated Apparel': '',
            'Custom Headwear': 'freestyle-custom-headwear'
        },

        // ---- Vendor catalogs (Catalogs page) ----
        // A "Catalogs" nav page listing the Momentec / Augusta / Pacific Headwear
        // digital catalogs. Each entry has an online `view` (flipbook) and/or a `pdf`
        // download; both open in a new tab. These are OUTBOUND links to the vendor's
        // CDN -- nothing is hosted by BSG, and a customer's browser reaches them fine
        // (this sandbox can't, by network policy, but that only affects previews).
        // BSG's own sample-art books ("Design Ideas"): the coded designs BSG has
        // produced for other schools, extracted one image per design from the
        // printed books and served through this Suitelet. A shopper picks one as a
        // REFERENCE ("build me something like FB7") -- their rep then recreates the
        // style with the school's own name, mascot, and colors. The design list
        // lives in lib/bsgshop.sample_art.js; adding a new book means dropping its
        // images in /art and adding a group there. Off = the page and the product
        // page's reference picker both disappear.
        SAMPLE_ART_ENABLED: true,

        // Turn the whole page off with CATALOGS_ENABLED = false.
        CATALOGS_ENABLED: true,
        CATALOGS: [
            { group: 'Featured', title: 'New Products (2026)', pdf: 'https://static.momentecbrands.com/marketing/2026/6/resources/May%202026%20New%20Products%20Look%20Book_update.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-new-styles-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/5/resources/may-new-styles.webp' },
            { group: 'Featured', title: 'Big Book 2025/26', pdf: 'https://static.momentecbrands.com/marketing/2025/9/momentec-brands-big-book-2025-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-big-book-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/9/momentec-catalog.png' },
            { group: 'Featured', title: '5-Day Sublimation Lookbook', pdf: 'https://static.momentecbrands.com/marketing/2026/2/5-day-sublimation.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-5-day-sublimation-lookbook-us', img: 'https://static.momentecbrands.com/marketing/2026/2/5-day-sublimation-cover.webp' },
            { group: 'Featured', title: 'Performancewear', pdf: 'https://static.momentecbrands.com/marketing/2025/11/momentec-brands-performancewear-2025-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-performancewear-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/2025-2026-performance-wear.png' },
            { group: 'Featured', title: 'Corporate & Events 2026', pdf: 'https://static.momentecbrands.com/marketing/2025/12/momentec-brands-corporate-and-events-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-corporate-and-events-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/12/2026%20Corporate%20Book_cover.jpg' },
            { group: 'Featured', title: 'Licensed Sales 2026', pdf: 'https://static.momentecbrands.com/marketing/2025/12/momentec-brands-licensed-sales-catalog-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-licensed-sales-catalog-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/12/2026%20License%20Sale%20Catalog.jpg' },
            { group: 'By Sport', title: 'Baseball', pdf: 'https://static.momentecbrands.com/marketing/2025/11/2025%20Baseball%20Book_web.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-baseball-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/2025-baseball.png' },
            { group: 'By Sport', title: 'Softball', pdf: 'https://static.momentecbrands.com/marketing/2026/1/2025%20Digital%20Softball%20Book_web.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-softball-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/2025-softball.png' },
            { group: 'By Sport', title: 'Soccer', pdf: 'https://static.momentecbrands.com/marketing/2025/12/2025%20Digital%20Soccer%20Book_web.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-soccer-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/12/soccer.jpg' },
            { group: 'By Sport', title: 'Basketball', pdf: 'https://static.momentecbrands.com/marketing/2025/12/momentec-brands-basketball-2025-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-basketball-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/2025-2026-basketball.png' },
            { group: 'By Sport', title: 'Football', pdf: 'https://static.momentecbrands.com/marketing/2025/12/momentec-brands-football-2025-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-football-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/football-2025-2026.png' },
            { group: 'By Sport', title: 'Volleyball', pdf: 'https://static.momentecbrands.com/marketing/2026/6/resources/Digital%20Volleyball%202026.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-volleyball-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/6/resources/Digital%20Volleyball%202026_cover.jpg' },
            { group: 'By Sport', title: 'Hockey', pdf: 'https://static.momentecbrands.com/marketing/2025/10/momentec-brands-hockey-2025-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-hockey-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/hockey-25-26.jpeg' },
            { group: 'By Sport', title: 'Lacrosse', pdf: 'https://static.momentecbrands.com/marketing/2025/12/Digital%20LAX%20Book_web.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-lacrosse-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/lacrosse-25-26.jpeg' },
            { group: 'By Sport', title: 'Wrestling', pdf: 'https://static.momentecbrands.com/marketing/2025/10/momentec-brands-wrestling-2025-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-wrestling-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/wrestling-25-26.jpeg' },
            { group: 'By Sport', title: 'Track & Field', pdf: 'https://static.momentecbrands.com/marketing/2025/12/Digital%20Track%20and%20Field%202025_web.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-track-and-field-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/track-2025-2026.png' },
            { group: 'By Sport', title: 'Cheer', pdf: 'https://static.momentecbrands.com/marketing/2025/12/cheer.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-cheer-book-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/10/cheer-2025-2026.png' },
            { group: 'By Sport', title: 'Pop Warner', pdf: 'https://static.momentecbrands.com/marketing/2025/10/momentec-brands-pop-warner-product-catalog-2025-2026.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-pop-warner-product-catalog-2025-2026', img: 'https://static.momentecbrands.com/marketing/2025/10/pop-warner.png' },
            { group: 'Apparel', title: 'Polos', pdf: 'https://static.momentecbrands.com/marketing/2026/7/resources/2026%20Digital%20Polos.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-polos-catalog-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/7/resources/2026%20Digital%20Polos%20Cover.jpg' },
            { group: 'Apparel', title: 'Lifestyle', pdf: 'https://static.momentecbrands.com/marketing/2025/11/momentec-brands-lifestyle-2025-2026-us.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-lifestyle-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2025/11/2025%20Digital%20Lifestyle_cover.jpg' },
            { group: 'Apparel', title: 'Outerwear', pdf: 'https://static.momentecbrands.com/marketing/2026/7/resources/Digital%20Outerwear%202026.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-outerwear-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/7/resources/Digital%20Outerwear%202026_cover.jpg' },
            { group: 'Apparel', title: 'Fleece', pdf: 'https://static.momentecbrands.com/marketing/2026/7/resources/2026%20Digital%20Fleece%20Book.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-fleece-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/7/resources/2026%20Digital%20Fleece%20Book_cover.jpg' },
            { group: 'Apparel', title: 'Bags', pdf: 'https://static.momentecbrands.com/marketing/2026/1/2025%20Digital%20Bags_web.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-bags-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/1/2025%20Digital%20Bags_cover.jpg' },
            { group: 'Apparel', title: 'Accessories & Aprons', pdf: 'https://static.momentecbrands.com/marketing/2026/1/Digital%20Accessories%202025_web.pdf', view: 'https://viewer.zoomcatalog.com/momentec-brands-accessories-and-aprons-2025-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/1/Digital%20Accessories%202025_cover.jpg' },
            { group: 'Headwear', title: 'Pacific Headwear 2026', pdf: 'https://static.momentecbrands.com/marketing/2026/6/resources/Pacific%20Headwear%20Catalog%202026.pdf', view: 'https://viewer.zoomcatalog.com/pacific-headwear-2026-us', img: 'https://static.momentecbrands.com/marketing/2026/6/resources/Pacific%20Headwear%202026%20Catalog_cover.jpg' },
            { group: 'Headwear', title: 'Custom Headwear Guide', pdf: 'https://static.momentecbrands.com/marketing/Catalogs/2025/US/Pacific%20Headwear%20Custom%20Headwear%20Guide%20%281%29.pdf', view: 'https://viewer.zoomcatalog.com/augusta-custom-headwear-guide-2025-us', img: 'https://static.momentecbrands.com/marketing/Catalogs/2025/US/Pacific%20Headwear%20Custom%20Headwear%20Guide_cover.jpg' },
            { group: 'Reference', title: 'Digital Swatch Book', pdf: 'https://static.momentecbrands.com/marketing/00_WEB_ASSETS/knowledgebase/MomentecSwatches_042525.pdf' }
        ],

        // ---- Precomputed catalog images (see cs_bsgshop_image_sync_mr.js) ----
        // When on, the catalog reads each item's image from the searchable
        // custitem_bsgshop_image_url column FIRST (populated nightly by the image-sync
        // Map/Reduce), which lets a page render with no per-item record loads. Purely
        // an optimization -- any item whose column is still blank falls through to the
        // live resolver (attached file -> child -> record.load) exactly as before.
        USE_PRECOMPUTED_IMAGES: true,

        // ---- Product image URL importer (cs_bsgshop_image_import_mr.js) ----
        // Writes custitem_bsgshop_image_url from the prebuilt lib/bsgshop.image_map.js
        // (Momentec/Augusta product photos). DEFAULT false = DRY RUN: the M/R only
        // writes a review report to the File Cabinet folder below and changes NOTHING
        // on items. Review that report, then set this true and re-run to apply.
        IMAGE_IMPORT_APPLY: false,
        IMAGE_IMPORT_REPORT_FOLDER: 'BSG Images',

        // ---- Store analytics (see ANALYTICS map above) ----
        // Master switch for the best-effort event log. Off = the "track" action is a
        // no-op and the client stops sending events.
        ANALYTICS_ENABLED: true,
        // page_view is the noisiest event; set false to log only the higher-signal
        // actions (product views, add-to-cart, search, submits) and cut record volume.
        ANALYTICS_TRACK_PAGE_VIEWS: true
    };

    return {
        ITEM_FIELD: ITEM_FIELD,
        CUSTOMER_FIELD: CUSTOMER_FIELD,
        SCRIPT: SCRIPT,
        ACTION: ACTION,
        SUBMIT_TYPE: SUBMIT_TYPE,
        LINE_FIELD: LINE_FIELD,
        ANALYTICS: ANALYTICS,
        CONFIG: CONFIG,
        IMAGE_FILE_FIELDS: IMAGE_FILE_FIELDS,
        IMAGE_URL_FIELDS: IMAGE_URL_FIELDS,
        MSRP_FIELDS: MSRP_FIELDS,
        GTIN_FIELDS: GTIN_FIELDS,
        QTY_FIELDS: QTY_FIELDS
    };
});
