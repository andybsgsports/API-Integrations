/**
 * BSG Ordering Site - storefront client.
 *
 * Plain browser JavaScript (NOT a NetSuite module). The public Suitelet inlines
 * this file's contents into the store page and it talks back to that same
 * Suitelet URL (window.BSGSHOP_BOOT.apiUrl) for catalog, school search, and
 * order/quote submission. Cart lives in the browser (localStorage) until submit.
 */
(function () {
    'use strict';

    var BOOT = window.BSGSHOP_BOOT || {};
    // Always call the SAME url the page was served from. The server-resolved
    // external URL is a different DOMAIN than the internal one staff preview on
    // (app.netsuite.com vs extforms), and a cross-origin fetch is blocked by the
    // browser ("Failed to fetch"). Same-origin works in both contexts.
    var API = (function () {
        try {
            var base = location.origin + location.pathname + location.search;
            return base.indexOf('?') === -1 ? base + '?_=1' : base;
        } catch (e) {
            return BOOT.apiUrl || '';
        }
    })();
    var CART_KEY = 'bsgshop_cart_v1';
    var VIEW_KEY = 'bsgshop_view_v1';
    // Pages the shopper can be returned to after a refresh. Anything not in this
    // list (or a stale value from an older build) falls back to Home.
    var VIEWS = ['home', 'shop', 'sublimation', 'catalogs', 'sampleart', 'team', 'builder'];
    // loadView is a hoisted function declaration, so this runs safely here.
    var START_VIEW = loadView();

    var state = {
        // Refresh keeps you on the page you were on; a new tab/session starts Home.
        view: START_VIEW || 'home',
        products: [], page: 1, pageCount: 1, total: 0, q: '', pageSize: BOOT.pageSize || 25,
        filterDefs: [], filters: {}, catExpanded: {}, catalogGroup: '', facetCollapsed: {},
        broaden: null,
        cart: loadCart(), school: null, schoolResults: [],
        submitType: 'order', drawerOpen: false, submitting: false, message: null, loading: true,
        detail: null, detailLoading: false, loadError: null,
        team: {}, roster: {},
        rosterProduct: null, rosterProdResults: [], rosterProdQ: '',
        submitToken: null,
        momentecRef: '', momentecCaptured: false, momentecCategory: '',
        logoMock: null, textMock: null, deco: null, gal: null,
        subj: null, subjFor: '',
        artGroup: 0, artPick: null, artZoom: null,
        design: {
            garment: 'Team Jersey',
            zones: { body: '#1b2a4a', sleeves: '#ec3013', trim: '#ffffff' },
            teamName: 'BADGERS', playerName: '', number: '00',
            numberColor: '#ffffff', textColor: '#ffffff',
            font: (BOOT.builderFonts && BOOT.builderFonts[0] && BOOT.builderFonts[0].name) || 'Athletic Block',
            logoDataUrl: ''
        }
    };

    var searchTimer = null, schoolTimer = null, rosterTimer = null;
    // Window scroll position to restore after a re-render kicked off by a filter/
    // search change -- so clicking a facet doesn't dump the shopper back at the top.
    // Held (not cleared) through the intermediate "loading" render, then cleared
    // once the results render lands.
    var keepScrollY = null;

    // ------------------------------------------------------------- data io --

    var reqSeq = 0;
    function api(action, params) {
        var qs = Object.keys(params || {}).map(function (k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
        }).join('&');
        // _ts busts any HTTP cache that may have stored a prior (error) response --
        // a cached response is indistinguishable from "nothing changed" after a fix.
        var bust = '&_ts=' + (++reqSeq);
        return fetch(API + '&action=' + action + (qs ? '&' + qs : '') + bust, { credentials: 'same-origin', cache: 'no-store' })
            .then(function (r) { return r.text(); })
            .then(function (t) {
                try { return JSON.parse(t); }
                catch (e) {
                    // The service returned non-JSON (usually a NetSuite HTML error
                    // page). Surface a short, readable snippet instead of a raw
                    // "Unexpected token '<'" so the real cause is visible.
                    var snippet = String(t || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
                    return { ok: false, error: 'Server returned a non-JSON response for "' + action + '". ' + (snippet || '(empty response)') };
                }
            });
    }

    var PAGE_TS = Date.now();
    function honeypotValue() { var e = document.getElementById('bsgHp'); return e ? e.value : ''; }

    // ------------------------------------------------------------ analytics --
    // Anonymous per-visit session id (sessionStorage) that ties a visitor's events
    // together for a simple funnel -- no cookies, no PII, cleared when the tab closes.
    var SID = (function () {
        try {
            var k = 'bsgshop_sid';
            var v = sessionStorage.getItem(k);
            if (!v) { v = 's_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8); sessionStorage.setItem(k, v); }
            return v;
        } catch (e) { return 's_' + Date.now().toString(36); }
    })();
    var lastTrackedView = null;

    // Fire-and-forget analytics. Best-effort: never blocks the UI, never surfaces an
    // error, and is a no-op unless the server enabled analytics in BOOT.
    function track(event, extra) {
        if (!BOOT.analytics) { return; }
        if (event === 'page_view' && !BOOT.analyticsPageViews) { return; }
        try {
            var body = {
                action: 'track', event: event, sid: SID,
                school: state.school ? (state.school.name || '') : ''
            };
            if (extra) {
                if (extra.ref != null) { body.ref = String(extra.ref).slice(0, 300); }
                if (extra.value != null) { body.value = extra.value; }
                if (extra.detail != null) { body.detail = String(extra.detail).slice(0, 3000); }
            }
            fetch(API + '&action=track', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin', cache: 'no-store', keepalive: true,
                body: JSON.stringify(body)
            }).catch(function () { });
        } catch (e) { /* analytics never breaks the store */ }
    }

    function apiPost(payload) {
        // Attach the public-endpoint guards to every submission: honeypot value,
        // time-since-load, and a per-checkout idempotency token (double-click safe).
        if (payload && payload.action === 'submit') {
            if (!state.submitToken) {
                state.submitToken = 'tok_' + Date.now() + '_' + Math.random().toString(36).slice(2);
            }
            payload.hp = honeypotValue();
            payload.elapsed = Date.now() - PAGE_TS;
            payload.token = state.submitToken;
        }
        return fetch(API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            cache: 'no-store',
            body: JSON.stringify(payload)
        }).then(function (r) { return r.text(); })
            .then(function (t) {
                var res;
                try { res = JSON.parse(t); }
                catch (e) {
                    var snippet = String(t || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
                    res = { ok: false, error: 'Server returned a non-JSON response. ' + (snippet || '(empty response)') };
                }
                // Clear the idempotency token once a submit resolves, so the next
                // distinct submission (or a manual retry after failure) gets a fresh
                // one; a concurrent double-click already carried the same token.
                if (payload && payload.action === 'submit') { state.submitToken = null; }
                return res;
            });
    }

    // ------------------------------------------------------------- cart io --

    function loadCart() {
        try { return JSON.parse(localStorage.getItem(CART_KEY)) || {}; }
        catch (e) { return {}; }
    }
    function saveCart() {
        try { localStorage.setItem(CART_KEY, JSON.stringify(state.cart)); } catch (e) { /* private mode */ }
    }

    // ---- which page you're on, across a refresh -----------------------------
    // Only the CURRENT PAGE is remembered, and only in sessionStorage. Refreshing
    // keeps you on the page you were looking at but hands you that page in its
    // first-visit state -- filter sections closed, no filters applied -- because
    // nothing else is stored. Closing the tab (or opening a new one) empties
    // sessionStorage, so a new session starts on Home, fully reset.
    //
    // A hard refresh (Ctrl+Shift+R) CANNOT be told apart from a normal one: the
    // browser reports both as navigation type "reload" and the only real
    // difference (bypassing the HTTP cache) is never exposed to page scripts. So
    // a hard refresh behaves like a normal refresh here -- it keeps the page. Use
    // the Home nav, or a new tab, for a clean slate.
    function loadView() {
        try {
            var v = sessionStorage.getItem(VIEW_KEY);
            return VIEWS.indexOf(v) === -1 ? '' : v;
        } catch (e) { return ''; }
    }
    function saveView() {
        try { sessionStorage.setItem(VIEW_KEY, state.view); } catch (e) { /* private mode */ }
    }
    function cartCount() {
        return Object.keys(state.cart).reduce(function (n, k) { return n + (state.cart[k].qty || 0); }, 0);
    }
    function cartTotal() {
        return Object.keys(state.cart).reduce(function (n, k) {
            var c = state.cart[k]; return n + (parseFloat(c.price) || 0) * (c.qty || 0);
        }, 0);
    }
    // player (optional): { name, number }. When present, the line is keyed
    // separately so two of the same item with different names/numbers stay on
    // their own cart lines (and their own order lines) instead of merging.
    function addLine(id, name, price, qty, player) {
        qty = Math.max(1, parseInt(qty, 10) || 1);
        var pn = player && player.name ? String(player.name).trim() : '';
        var pnum = player && player.number ? String(player.number).trim() : '';
        var key = (pn || pnum) ? id + '::' + pn + '::' + pnum : String(id);
        var line = state.cart[key];
        if (line) { line.qty += qty; }
        else {
            state.cart[key] = {
                key: key, id: id, name: name, price: parseFloat(price) || 0, qty: qty,
                playerName: pn, playerNumber: pnum
            };
        }
        saveCart();
        track('add_to_cart', { ref: name, value: (parseFloat(price) || 0) * qty, detail: 'qty=' + qty });
    }

    function addToCart(p) {
        addLine(p.id, p.name, p.price, 1);
        render();
    }
    function setQty(id, qty) {
        qty = Math.max(0, parseInt(qty, 10) || 0);
        if (!state.cart[id]) { return; }
        if (qty === 0) { delete state.cart[id]; } else { state.cart[id].qty = qty; }
        saveCart(); render();
    }

    // ---------------------------------------------------------- rendering --

    function money(n) {
        n = parseFloat(n) || 0;
        return '$' + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }
    // Unit price for a given quantity given the product's volume tiers (from the
    // server). Picks the price of the highest tier whose minQty <= qty; below the
    // first break it's the base price. No tiers -> base price (unchanged behavior).
    function tierPrice(p, qty) {
        var price = parseFloat(p && p.price) || 0;
        if (p && p.tiers && p.tiers.length) {
            for (var i = 0; i < p.tiers.length; i++) {
                if (qty >= p.tiers[i].minQty) { price = p.tiers[i].price; }
            }
        }
        return price;
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function render() {
        var root = document.getElementById('bsgshopRoot');
        if (!root) { return; }
        // Every navigation goes through render(), so this is the one place the
        // current page needs recording for the refresh-stays-put behaviour.
        saveView();
        // The filter rail and the detail modal are their own scroll boxes;
        // innerHTML replacement would reset them to the top on every render.
        // Capture and restore both.
        var prevAside = root.querySelector('.bsg-shop-aside');
        var asideScroll = prevAside ? prevAside.scrollTop : 0;
        var prevModal = root.querySelector('.bsg-modal');
        var modalScroll = prevModal ? prevModal.scrollTop : 0;
        root.innerHTML =
            header() +
            (state.view === 'home'
                ? homePage()
                : state.view === 'team'
                    ? teamPage()
                    : state.view === 'builder'
                        ? builderPage()
                        : state.view === 'sublimation'
                            ? sublimationPage()
                            : state.view === 'catalogs'
                                ? catalogsPage()
                                : state.view === 'sampleart'
                                    ? sampleArtPage()
                                    : shopPage()) +
            footer() +
            overlay() + drawer() + detailModal() + artZoomModal() +
            // Honeypot: off-screen, hidden from AT and tab order. Bots fill it; humans can't.
            '<input id="bsgHp" name="company_website" type="text" tabindex="-1" autocomplete="off" aria-hidden="true" ' +
            'style="position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;opacity:0" value="">';
        var newAside = root.querySelector('.bsg-shop-aside');
        if (newAside && asideScroll) { newAside.scrollTop = asideScroll; }
        var newModal = root.querySelector('.bsg-modal');
        if (newModal && modalScroll) { newModal.scrollTop = modalScroll; }
        if (keepScrollY !== null) {
            window.scrollTo(0, keepScrollY);
            if (!state.loading) { keepScrollY = null; }
        }
        wire();
        postRenderFocus();
        // Count a page_view only when the top-level view actually changes -- render()
        // runs on every state change, so this avoids inflating the view count.
        if (state.view !== lastTrackedView) {
            lastTrackedView = state.view;
            track('page_view', { ref: state.view });
        }
    }

    // Move focus into the cart drawer / detail modal when it opens (accessibility).
    // Focus moves into the modal/drawer only when it OPENS. Re-focusing on every
    // render (each gallery click or keystroke re-renders) yanked the modal's
    // scroll back to the close button at the top -- the "bouncing" effect.
    var focusState = '';
    function postRenderFocus() {
        try {
            var now = (state.detailLoading || state.detail) ? 'detail' : (state.drawerOpen ? 'drawer' : '');
            if (now && now !== focusState) {
                var target = now === 'detail'
                    ? document.querySelector('.bsg-modal .pd-close')
                    : document.querySelector('.bsg-drawer .close');
                if (target) { try { target.focus({ preventScroll: true }); } catch (e2) { target.focus(); } }
            }
            focusState = now;
        } catch (e) { /* focus is best-effort */ }
    }

    function header() {
        var nav = function (act, label, active) {
            return '<button class="bsg-nav-link' + (active ? ' active' : '') + '" data-act="' + act + '">' + label + '</button>';
        };
        return '<div class="bsg-announce">Team &amp; Bulk Ordering Portal &nbsp;&middot;&nbsp; Purchase Orders &amp; Quotes Welcome &nbsp;&middot;&nbsp; Live Inventory &amp; Pricing</div>' +
            '<div class="bsg-header">' +
            '<div class="brand" data-act="go-home" style="cursor:pointer" role="button" aria-label="Badger Sporting Goods home">' +
            '<span class="brand-logo" role="img" aria-label="Badger Sporting Goods"></span>' +
            '<span class="brand-kicker">Team Ordering Center</span>' +
            '<span class="brand-name">Badger <b>Sporting Goods</b></span>' +
            '</div>' +
            '<div class="bsg-nav">' +
            nav('go-home', 'Home', state.view === 'home') +
            nav('go-shop', 'Shop', state.view === 'shop') +
            (BOOT.builderEnabled ? nav('go-builder', 'Designer', state.view === 'builder') : '') +
            (BOOT.momentec ? nav('go-sublimation', 'Sublimation', state.view === 'sublimation') : '') +
            ((BOOT.sampleArt && BOOT.sampleArt.length) ? nav('go-sampleart', 'Design Ideas', state.view === 'sampleart') : '') +
            ((BOOT.catalogs && BOOT.catalogs.length) ? nav('go-catalogs', 'Catalogs', state.view === 'catalogs') : '') +
            nav('team-quote', 'Team Orders', state.view === 'team') +
            '<button class="bsg-cartbtn" data-act="open-cart">Cart (' + cartCount() + ')</button>' +
            '</div>' +
            '</div>';
    }

    // ---- landing page (Modernist template structure) ------------------------
    // Hero + shop-by-category tiles + team-order band. Category tiles come from
    // the REAL Department filter values; the item count in the stats row is the
    // live catalog total -- nothing invented.
    function homePage() {
        var deptDef = state.filterDefs.filter(function (f) { return f.key === 'department'; })[0];
        var tiles = ((deptDef && deptDef.options) || []).slice(0, 8).map(function (o, i) {
            var no = (i + 1 < 10 ? '0' : '') + (i + 1);
            return '<div class="bsg-tile" data-shopdept="' + esc(o.id) + '">' +
                '<span class="tile-num">' + no + '</span>' +
                '<span class="tile-name">' + esc(o.name) + '</span>' +
                '<span class="tile-cta">Shop ' + esc(o.name) + ' &rarr;</span>' +
                '</div>';
        }).join('');

        var stats = '<div class="bsg-stats">' +
            (state.total > 0 ? '<div class="stat"><b>' + state.total.toLocaleString() + '</b><span>Products in the catalog</span></div>' : '') +
            '<div class="stat"><b>Live</b><span>Inventory &amp; pricing from NetSuite</span></div>' +
            '<div class="stat"><b>PO / Quote</b><span>Order with a PO or request a quote</span></div>' +
            '<div class="stat"><b>Personalized</b><span>Player names &amp; numbers on marked items</span></div>' +
            '</div>';

        return '<div class="bsg-home">' +
            '<section class="bsg-hero"><div class="bsg-wrap">' +
            '<span class="hero-kicker">Team &amp; Athlete Outfitters</span>' +
            '<h1 class="hero-h1">Gear that shows up<br>game day ready.</h1>' +
            '<p class="hero-sub">Uniforms, equipment, and fan gear for every roster &mdash; with live stock, ' +
            'school lookup, and player name &amp; number personalization. Built for coaches ordering for forty ' +
            'and athletes ordering for one.</p>' +
            '<div class="hero-ctas">' +
            '<button class="bsg-btn-primary" data-act="go-shop">Shop All Gear</button>' +
            '<button class="bsg-btn-ghost" data-act="team-quote">Start a Team Order</button>' +
            '</div>' +
            stats +
            '</div></section>' +
            (tiles ? '<section class="bsg-wrap"><h2 class="home-h2">Shop by category</h2><div class="bsg-tiles">' + tiles + '</div></section>' : '') +
            '<section class="bsg-band"><div class="bsg-wrap band-inner">' +
            '<div><span class="hero-kicker" style="color:var(--onaccent)">Team &amp; bulk orders</span>' +
            '<h2 class="band-h2">Outfit your whole roster.</h2>' +
            '<p class="band-sub">Load the cart with what your program needs and submit it as a quote &mdash; ' +
            'a Badger rep follows up with pricing and availability.</p></div>' +
            '<button class="bsg-btn-invert" data-act="team-quote">Start a Team Order</button>' +
            '</div></section>' +
            '</div>';
    }

    function footer() {
        return '<div class="bsg-footer"><div class="bsg-wrap footer-inner">' +
            '<span class="footer-logo" role="img" aria-label="Badger Sporting Goods"></span>' +
            '<span class="footer-note">Orders are reviewed by BSG before fulfillment &mdash; pricing and availability confirmed on review.</span>' +
            '</div></div>';
    }

    // ---- Catalogs page (Momentec / Augusta / Pacific Headwear digital catalogs) --
    // Grouped tiles; each opens the online flipbook (View) and/or the PDF download in
    // a new tab. All outbound links to the vendor CDN -- nothing is hosted by BSG.
    function catalogsPage() {
        var cats = BOOT.catalogs || [];
        var order = [], groups = {};
        cats.forEach(function (c) {
            var g = c.group || 'Catalogs';
            if (!groups[g]) { groups[g] = []; order.push(g); }
            groups[g].push(c);
        });
        var cur = state.catalogGroup || '';
        // Left rail group filter -- same look as the Shop filter rail for consistency.
        var facetBtn = function (val, label, on) {
            return '<button class="facet-opt' + (on ? ' on' : '') + '" data-catgroup="' + esc(val) + '"' +
                (on ? ' aria-current="true"' : '') + '><span>' + esc(label) + '</span></button>';
        };
        var aside = '<aside class="bsg-shop-aside" aria-label="Catalog groups">' +
            '<div class="aside-top"><span class="aside-kicker">Catalogs</span></div>' +
            '<div class="facet"><div class="facet-list">' +
            facetBtn('', 'All Catalogs', !cur) +
            order.map(function (g) { return facetBtn(g, g, cur === g); }).join('') +
            '</div></div></aside>';

        var card = function (c) {
            var links = '';
            if (c.view) { links += '<a class="catalog-btn" href="' + esc(c.view) + '" target="_blank" rel="noopener">View</a>'; }
            if (c.pdf) { links += '<a class="catalog-btn ghost" href="' + esc(c.pdf) + '" target="_blank" rel="noopener">PDF</a>'; }
            var cover = c.img
                ? '<div class="catalog-cover"><img src="' + esc(c.img) + '" alt="' + esc(c.title) + '" loading="lazy"' + IMG_FALLBACK + '></div>'
                : '<div class="catalog-cover"><span class="ph">' + esc(c.title) + '</span></div>';
            return '<div class="catalog-card">' + cover +
                '<div class="catalog-body"><div class="catalog-name">' + esc(c.title) + '</div>' +
                '<div class="catalog-links">' + links + '</div></div></div>';
        };
        var shown = cur ? order.filter(function (g) { return g === cur; }) : order;
        var sections = shown.map(function (g) {
            return '<h2 class="home-h2">' + esc(g) + '</h2><div class="catalog-grid">' +
                groups[g].map(card).join('') + '</div>';
        }).join('');

        var head = '<span class="hero-kicker">Digital Catalogs</span>' +
            '<h1 class="team-title">Browse the full line.</h1>' +
            '<p class="hero-sub">View or download the latest Momentec, Augusta, and Pacific Headwear catalogs. ' +
            'See something you like? Find it in the shop or send a team request and a BSG rep will quote it.</p>';
        return '<div class="bsg-wrap bsg-shop bsg-catalogs">' + aside +
            '<div class="bsg-shop-main">' + head + sections + '</div></div>';
    }

    // ---- Design Ideas (BSG sample art) --------------------------------------
    // BSG's sample-art books: real work done for other schools, browsable by
    // sport. Picking one is a REFERENCE for the rep ("something like FB7"), who
    // then rebuilds it with this school's name/mascot/colors -- so the picker
    // says exactly that rather than implying the art ships as-is.
    function artUrl(fileName) {
        return API + '&action=art&f=' + encodeURIComponent(fileName);
    }

    function artGroups() { return BOOT.sampleArt || []; }

    function sampleArtPage() {
        var groups = artGroups();
        if (!groups.length) { return '<div class="bsg-wrap"><p class="hero-sub">No sample art is published yet.</p></div>'; }
        var gi = Math.max(0, Math.min(state.artGroup, groups.length - 1));
        var g = groups[gi];

        var aside = '<aside class="bsg-shop-aside">' +
            '<div class="aside-top"><span class="aside-kicker">Sample Art</span></div>' +
            '<div class="facet"><div class="facet-list">' +
            groups.map(function (x, i) {
                return '<button class="facet-opt' + (i === gi ? ' on' : '') + '" data-artgroup="' + i + '">' +
                    '<span>' + esc(x.sport) + '</span></button>';
            }).join('') +
            '</div></div></aside>';

        var tiles = (g.designs || []).map(function (d) {
            var on = state.artPick && state.artPick.code === d.code && state.artPick.file === d.file;
            return '<figure class="art-card' + (on ? ' on' : '') + '" data-artpick=\'' +
                esc(JSON.stringify({ code: d.code, file: d.file, sport: g.sport })) + '\'>' +
                '<div class="art-card-img"><img src="' + esc(artUrl(d.file)) + '" alt="Design ' + esc(d.code) +
                '" loading="lazy" decoding="async"' + IMG_FALLBACK + '></div>' +
                '<figcaption>' + esc(d.code) + '</figcaption></figure>';
        }).join('');

        var picked = state.artPick
            ? '<div class="deco-note">Reference selected: <strong>' + esc(state.artPick.code) + '</strong> (' +
              esc(state.artPick.sport) + ') &mdash; ' +
              '<button class="linklike" data-act="art-request">start a team request with it</button> or ' +
              '<button class="linklike" data-act="artpick-clear">clear</button></div>'
            : '';

        var head = '<span class="hero-kicker">Design Ideas</span>' +
            '<h1 class="team-title">Sample art from our shop.</h1>' +
            '<p class="hero-sub">Real designs the BSG art team has built for teams like yours. Find one you like, ' +
            'note its code, and send it with your request &mdash; your rep rebuilds it with <em>your</em> school&rsquo;s ' +
            'name, mascot, and colors. Nothing here prints as-is.</p>';

        return '<div class="bsg-wrap bsg-shop bsg-sampleart">' + aside +
            '<div class="bsg-shop-main">' + head + picked +
            '<h2 class="home-h2">' + esc(g.sport) +
            ' <span class="art-count">(' + (g.designs || []).length + ' designs)</span></h2>' +
            '<div class="art-grid">' + tiles + '</div></div></div>';
    }

    // Full-size look at one design (its own overlay, so the grid stays put).
    function artZoomModal() {
        var z = state.artZoom;
        if (!z) { return ''; }
        return '<div class="bsg-modal-overlay open" data-act="artzoom-close"></div>' +
            '<div class="bsg-modal art-zoom" role="dialog" aria-modal="true" aria-label="Design ' + esc(z.code) + '">' +
            '<button class="pd-close" data-act="artzoom-close" aria-label="Close">&times;</button>' +
            '<div class="art-zoom-body">' +
            '<img src="' + esc(artUrl(z.file)) + '" alt="Design ' + esc(z.code) + '"' + IMG_FALLBACK + '>' +
            '<div class="art-zoom-meta"><span class="card-kicker">' + esc(z.sport) + '</span>' +
            '<h3 class="pd-name">Design ' + esc(z.code) + '</h3>' +
            '<p class="pd-desc">Quote this code with your request and your rep will rebuild this style with your ' +
            'school&rsquo;s name, mascot, and colors.</p>' +
            '<button class="bsg-submit" data-act="art-request">Request this design</button></div>' +
            '</div></div>';
    }

    // ---- Team & Bulk Orders form (template: team info + roster-sizing grid) ----
    function teamVal(k) { return state.team[k] || ''; }

    // Sizes shown in the roster grid: the picked item's real size run when the
    // coach has chosen one (and it parsed), otherwise the full house list.
    function activeRosterSizes() {
        var rp = state.rosterProduct;
        if (rp && rp.sizes && rp.sizes.length) { return rp.sizes; }
        return BOOT.rosterSizes || ['YS', 'YM', 'YL', 'AS', 'AM', 'AL', 'AXL', 'A2XL', 'A3XL'];
    }

    function rosterProdLabel(rp) {
        return rp.name + (rp.itemNumber && rp.itemNumber !== rp.name ? ' (#' + String(rp.itemNumber).replace(/^#+/, '') + ')' : '');
    }

    function rosterProdResultsHtml() {
        if (!state.rosterProdResults.length) { return ''; }
        return '<div class="bsg-school-results">' + state.rosterProdResults.slice(0, 8).map(function (p) {
            var d = { id: p.id, name: p.name, itemNumber: p.itemNumber || '' };
            return '<div data-rosterprod=\'' + esc(JSON.stringify(d)) + '\'>' + esc(p.name) +
                (d.itemNumber && d.itemNumber !== p.name ? ' <span style="color:#888">&mdash; #' + esc(String(d.itemNumber).replace(/^#+/, '')) + '</span>' : '') +
                '</div>';
        }).join('') + '</div>';
    }

    function rosterSizeNote() {
        var rp = state.rosterProduct;
        var t;
        if (rp && rp.sizes === null) {
            t = 'Loading the size run for ' + esc(rosterProdLabel(rp)) + '&hellip;';
        } else if (rp && rp.sizes.length) {
            t = 'Showing only the sizes ' + esc(rosterProdLabel(rp)) + ' actually comes in.';
        } else if (rp) {
            t = 'We couldn&rsquo;t read a size chart from that item, so the full range is shown &mdash; ' +
                'your BSG rep confirms availability when quoting.';
        } else {
            t = 'Sizes are requests &mdash; your BSG rep confirms each item&rsquo;s available size range when quoting ' +
                '(not every style runs the full YXS&ndash;6XL). Pick an item above to see its real size run.';
        }
        return '<p style="font-size:11px;color:#888;margin:8px 0 0">' + t + '</p>';
    }

    function teamPage() {
        var sports = BOOT.sports || [];
        var sizes = activeRosterSizes();
        var sportOpts = '<option value="">Select a sport&hellip;</option>' + sports.map(function (s) {
            return '<option' + (teamVal('sport') === s ? ' selected' : '') + '>' + esc(s) + '</option>';
        }).join('');
        var rows = sizes.map(function (sz) {
            var r = state.roster[sz] || {};
            return '<tr><td class="vcolor">' + esc(sz) + '</td>' +
                '<td><input type="number" min="0" class="rqty" data-roster="' + esc(sz) + '" data-col="jerseys" value="' + esc(r.jerseys || '') + '" placeholder="0"></td>' +
                '<td><input type="number" min="0" class="rqty" data-roster="' + esc(sz) + '" data-col="shorts" value="' + esc(r.shorts || '') + '" placeholder="0"></td></tr>';
        }).join('');
        var cartN = cartCount();

        return '<div class="bsg-wrap bsg-team">' +
            '<span class="hero-kicker">Team &amp; Bulk Orders</span>' +
            '<h1 class="team-title">Outfit your whole roster.</h1>' +
            '<p class="hero-sub">Submit your team&rsquo;s details and sizing and a Badger rep will follow up with pricing, ' +
            'colorways, and a proof &mdash; no minimum roster size. Prefer to pick exact items? ' +
            '<button class="linklike" data-act="go-shop">Browse the catalog</button> and check out as a quote.</p>' +
            (state.message ? '<div class="bsg-msg ' + state.message.type + '">' + esc(state.message.text) + '</div>' : '') +
            (state.artPick
                ? '<div class="deco-note art-ref"><img src="' + esc(artUrl(state.artPick.file)) + '" alt=""' + IMG_FALLBACK + '>' +
                  '<span>Sample art <strong>' + esc(state.artPick.code) + '</strong> (' + esc(state.artPick.sport) +
                  ') goes with this request &mdash; your rep will rebuild it in your colors. ' +
                  '<button class="linklike" data-act="artpick-clear">Remove</button></span></div>'
                : '') +
            (cartN > 0 ? '<div class="team-cartnote">' + cartN + ' item' + (cartN === 1 ? '' : 's') + ' in your cart will be included with this request.</div>' : '') +
            '<div class="team-grid">' +
            '<div class="team-col">' +
            '<h3 class="team-h3">Team Info</h3>' +
            '<div class="bsg-field"><label>Your School</label>' +
            (state.school
                ? '<div class="school-picked"><span>' + esc(state.school.name) + (state.school.state ? ' (' + esc(state.school.state) + ')' : '') + '</span><button class="rm" data-act="clear-school">change</button></div>'
                : '<input id="bsgTeamSchool" type="text" placeholder="Type your school name&hellip;" autocomplete="off">' + schoolResultsHtml()) +
            '</div>' +
            '<div class="bsg-field"><label>Team Name</label><input id="tmName" type="text" value="' + esc(teamVal('name')) + '"></div>' +
            '<div class="bsg-field"><label>Sport</label><select id="tmSport" class="bsg-filter" style="min-width:100%">' + sportOpts + '</select></div>' +
            '<div class="bsg-field"><label>Contact Name</label><input id="tmContact" type="text" value="' + esc(fieldVal('name')) + '"></div>' +
            '<div class="bsg-field"><label>Email</label><input id="tmEmail" type="email" value="' + esc(fieldVal('email')) + '"></div>' +
            '<div class="bsg-field"><label>Phone</label><input id="tmPhone" type="tel" value="' + esc(teamVal('phone')) + '"></div>' +
            '</div>' +
            '<div class="team-col">' +
            '<h3 class="team-h3">Roster Sizing</h3>' +
            '<div class="bsg-field"><label>Base sizes on an item &mdash; optional</label>' +
            (state.rosterProduct
                ? '<div class="school-picked"><span>' + esc(rosterProdLabel(state.rosterProduct)) + '</span><button class="rm" data-act="clear-roster-prod">change</button></div>'
                : '<input id="tmRosterItem" type="text" value="' + esc(state.rosterProdQ) + '" placeholder="Search an item to load its real size run&hellip;" autocomplete="off">' + rosterProdResultsHtml()) +
            '</div>' +
            '<div class="vgrid-wrap"><table class="vgrid rgrid"><thead><tr>' +
            '<th>Size</th><th>Jerseys</th><th>Shorts</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
            rosterSizeNote() +
            '<div class="bsg-field" style="margin-top:14px"><label>Notes (optional)</label>' +
            '<textarea id="tmNotes" rows="3" placeholder="Colors, number range, deadline, anything else&hellip;">' + esc(fieldVal('notes')) + '</textarea></div>' +
            '<button class="bsg-submit" data-act="submit-team"' + (state.submitting ? ' disabled' : '') + '>' +
            (state.submitting ? 'Submitting&hellip;' : 'Submit Team Request') + '</button>' +
            '</div>' +
            '</div></div>';
    }

    function cacheTeamFields() {
        var g = function (id) { var e = document.getElementById(id); return e ? e.value : undefined; };
        if (g('tmName') !== undefined) { state.team.name = g('tmName'); }
        if (g('tmSport') !== undefined) { state.team.sport = g('tmSport'); }
        if (g('tmPhone') !== undefined) { state.team.phone = g('tmPhone'); }
        if (g('tmContact') !== undefined) { fieldCache.name = g('tmContact'); }
        if (g('tmEmail') !== undefined) { fieldCache.email = g('tmEmail'); }
        if (g('tmNotes') !== undefined) { fieldCache.notes = g('tmNotes'); }
        document.querySelectorAll('.rqty').forEach(function (inp) {
            var sz = inp.getAttribute('data-roster'), col = inp.getAttribute('data-col');
            if (!state.roster[sz]) { state.roster[sz] = {}; }
            state.roster[sz][col] = inp.value;
        });
    }

    function submitTeam() {
        cacheTeamFields();
        if (!state.school) { return setMsg('err', 'Please select your school.'); }
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(fieldVal('email'))) { return setMsg('err', 'Please enter a valid email so we can follow up.'); }

        // Only sizes visible in the current grid count -- quantities typed before
        // switching to an item's (shorter) size run shouldn't ride along hidden.
        var sizesNow = activeRosterSizes();
        var roster = Object.keys(state.roster).map(function (sz) {
            var r = state.roster[sz] || {};
            return { size: sz, jerseys: parseInt(r.jerseys, 10) || 0, shorts: parseInt(r.shorts, 10) || 0 };
        }).filter(function (r) { return sizesNow.indexOf(r.size) !== -1 && (r.jerseys > 0 || r.shorts > 0); });

        var hasTeamInfo = !!(teamVal('name') || teamVal('sport') || teamVal('phone') || roster.length || state.artPick);
        if (!hasTeamInfo) { return setMsg('err', 'Add your team name, sport, or roster sizing so a rep can help.'); }

        var items = Object.keys(state.cart).map(function (key) {
            var c = state.cart[key];
            return { id: c.id, qty: c.qty, name: c.name, playerName: c.playerName || '', playerNumber: c.playerNumber || '' };
        });

        // Carry the item the roster sizing was based on (if one was picked) so
        // the rep's email says exactly which size run the quantities refer to.
        var team = {};
        Object.keys(state.team).forEach(function (k) { team[k] = state.team[k]; });
        if (state.rosterProduct) { team.product = rosterProdLabel(state.rosterProduct); }
        if (state.artPick) {
            team.sampleArt = state.artPick.code + ' (' + state.artPick.sport + ')';
        }

        state.submitting = true; state.message = null; render();
        apiPost({
            action: 'submit', type: 'quote', schoolId: state.school.id,
            name: fieldVal('name'), email: fieldVal('email'), notes: fieldVal('notes'),
            items: items, team: team, roster: roster
        }).then(function (res) {
            state.submitting = false;
            if (res && res.ok) {
                track('team_request', { ref: teamVal('name') || res.docNumber || '', value: cartTotal(), detail: roster.length + ' roster row(s), ' + items.length + ' line(s)' });
                state.cart = {}; saveCart();
                state.team = {}; state.roster = {}; fieldCache = {};
                state.rosterProduct = null; state.rosterProdResults = []; state.rosterProdQ = '';
                state.artPick = null;
                state.view = 'home';
                render(); window.scrollTo(0, 0);
                showConfirmation({ docType: res.docType || 'Team Request', docNumber: res.docNumber });
            } else {
                setMsg('err', (res && res.error) || 'Something went wrong. Please try again.');
            }
        }).catch(function () { state.submitting = false; setMsg('err', 'Network error. Please try again.'); });
    }

    // ---- Uniform Builder (customer apparel mock-up POC) ---------------------
    function fontCss(name) {
        var fs = BOOT.builderFonts || [];
        for (var i = 0; i < fs.length; i++) { if (fs[i].name === name) { return fs[i].css; } }
        return 'Arial, sans-serif';
    }

    // The live jersey preview as inline SVG. Element ids let us update fills/text
    // directly (smooth) and also let the whole thing rasterize to a PNG on submit.
    function buildJerseySvg(d) {
        var z = d.zones || {}, ff = fontCss(d.font);
        return '<svg id="ubSvg" viewBox="0 0 380 460" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block;background:#fff">' +
            '<path id="zSleeveL" d="M110,64 L58,96 L44,168 L104,188 L138,132 Z" fill="' + esc(z.sleeves) + '" stroke="rgba(0,0,0,.18)"/>' +
            '<path id="zSleeveR" d="M270,64 L322,96 L336,168 L276,188 L242,132 Z" fill="' + esc(z.sleeves) + '" stroke="rgba(0,0,0,.18)"/>' +
            '<path id="zBody" d="M110,64 Q150,54 190,86 Q230,54 270,64 L262,150 L272,430 L108,430 L118,150 Z" fill="' + esc(z.body) + '" stroke="rgba(0,0,0,.18)"/>' +
            '<rect id="zHem" x="108" y="414" width="164" height="14" fill="' + esc(z.trim) + '"/>' +
            '<path id="zTrim" d="M158,66 Q190,96 222,66 Q190,84 158,66 Z" fill="' + esc(z.trim) + '"/>' +
            '<image id="gLogo" x="120" y="102" width="52" height="52" preserveAspectRatio="xMidYMid meet" href="' + esc(d.logoDataUrl || '') + '"' + (d.logoDataUrl ? '' : ' style="display:none"') + '/>' +
            '<text id="tTeam" x="190" y="150" text-anchor="middle" font-family=\'' + esc(ff) + '\' font-weight="800" font-size="26" fill="' + esc(d.textColor) + '">' + esc(d.teamName || '') + '</text>' +
            '<text id="tNumber" x="190" y="302" text-anchor="middle" font-family=\'' + esc(ff) + '\' font-weight="800" font-size="120" fill="' + esc(d.numberColor) + '">' + esc(d.number || '') + '</text>' +
            '<text id="tName" x="190" y="346" text-anchor="middle" font-family=\'' + esc(ff) + '\' font-weight="700" font-size="20" fill="' + esc(d.textColor) + '">' + esc((d.playerName || '').toUpperCase()) + '</text>' +
            '</svg>';
    }

    function swatchRow(target, current) {
        var colors = BOOT.builderColors || [];
        return '<div class="ub-swatches">' + colors.map(function (c) {
            var on = String(c.hex).toLowerCase() === String(current).toLowerCase();
            return '<button class="ub-swatch' + (on ? ' on' : '') + '" title="' + esc(c.name) + '" ' +
                'data-target="' + esc(target) + '" data-hex="' + esc(c.hex) + '" ' +
                'style="background:' + esc(c.hex) + '"></button>';
        }).join('') + '</div>';
    }

    function builderPage() {
        var d = state.design;
        var fonts = BOOT.builderFonts || [];
        var fontOpts = fonts.map(function (f) {
            return '<option' + (d.font === f.name ? ' selected' : '') + '>' + esc(f.name) + '</option>';
        }).join('');
        return '<div class="bsg-wrap bsg-builder">' +
            '<span class="hero-kicker">Uniform Designer &mdash; Preview</span>' +
            '<h1 class="team-title">Mock up your kit.</h1>' +
            '<p class="hero-sub">Pick colors, add your team name, number, and logo, and see it live. Send the mock-up to a ' +
            'Badger rep and we&rsquo;ll follow up with a production-ready proof and pricing. ' +
            '<em>This is a design preview, not the final proof.</em></p>' +
            (state.message ? '<div class="bsg-msg ' + state.message.type + '">' + esc(state.message.text) + '</div>' : '') +
            '<div class="ub-grid">' +
            '<div class="ub-preview">' + buildJerseySvg(d) + '</div>' +
            '<div class="ub-controls">' +
            '<div class="ub-field"><label>Body color</label>' + swatchRow('body', d.zones.body) + '</div>' +
            '<div class="ub-field"><label>Sleeve color</label>' + swatchRow('sleeves', d.zones.sleeves) + '</div>' +
            '<div class="ub-field"><label>Trim / collar</label>' + swatchRow('trim', d.zones.trim) + '</div>' +
            '<div class="ub-row">' +
            '<div class="bsg-field" style="margin:0;flex:2"><label>Team text</label><input id="ubTeam" type="text" maxlength="18" value="' + esc(d.teamName) + '"></div>' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Number</label><input id="ubNumber" type="text" maxlength="3" value="' + esc(d.number) + '"></div>' +
            '</div>' +
            '<div class="bsg-field"><label>Player name (optional)</label><input id="ubPlayer" type="text" maxlength="18" value="' + esc(d.playerName) + '"></div>' +
            '<div class="ub-field"><label>Text color</label>' + swatchRow('textColor', d.textColor) + '</div>' +
            '<div class="ub-field"><label>Number color</label>' + swatchRow('numberColor', d.numberColor) + '</div>' +
            '<div class="ub-row">' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Lettering</label><select id="ubFont" class="bsg-filter" style="min-width:100%">' + fontOpts + '</select></div>' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Logo (optional)</label><input id="ubLogo" type="file" accept="image/*"></div>' +
            '</div>' +
            '<hr class="ub-hr">' +
            '<h3 class="team-h3">Where to send it</h3>' +
            '<div class="bsg-field"><label>Your School</label>' +
            (state.school
                ? '<div class="school-picked"><span>' + esc(state.school.name) + (state.school.state ? ' (' + esc(state.school.state) + ')' : '') + '</span><button class="rm" data-act="clear-school">change</button></div>'
                : '<input id="bsgBuilderSchool" type="text" placeholder="Type your school name&hellip;" autocomplete="off">' + schoolResultsHtml()) +
            '</div>' +
            '<div class="ub-row">' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Your Name</label><input id="ubContact" type="text" value="' + esc(fieldVal('name')) + '"></div>' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Email</label><input id="ubEmail" type="email" value="' + esc(fieldVal('email')) + '"></div>' +
            '</div>' +
            '<div class="bsg-field"><label>Notes (optional)</label><textarea id="ubNotes" rows="2" placeholder="Sizes, quantities, deadline&hellip;">' + esc(fieldVal('notes')) + '</textarea></div>' +
            '<button class="bsg-submit" data-act="submit-design"' + (state.submitting ? ' disabled' : '') + '>' +
            (state.submitting ? 'Sending&hellip;' : 'Send Mock-up to a Rep') + '</button>' +
            '</div>' +
            '</div></div>';
    }

    function cacheBuilderFields() {
        var g = function (id) { var e = document.getElementById(id); return e ? e.value : undefined; };
        if (g('ubTeam') !== undefined) { state.design.teamName = g('ubTeam'); }
        if (g('ubPlayer') !== undefined) { state.design.playerName = g('ubPlayer'); }
        if (g('ubNumber') !== undefined) { state.design.number = g('ubNumber'); }
        if (g('ubFont') !== undefined) { state.design.font = g('ubFont'); }
        if (g('ubContact') !== undefined) { fieldCache.name = g('ubContact'); }
        if (g('ubEmail') !== undefined) { fieldCache.email = g('ubEmail'); }
        if (g('ubNotes') !== undefined) { fieldCache.notes = g('ubNotes'); }
    }

    function designPayload() {
        var d = state.design;
        return {
            garment: d.garment,
            zones: { body: d.zones.body, sleeves: d.zones.sleeves, trim: d.zones.trim },
            teamName: d.teamName, playerName: d.playerName, number: d.number,
            numberColor: d.numberColor, textColor: d.textColor, font: d.font
        };
    }

    // Rasterize the live SVG preview to a PNG data URL for saving/attaching.
    function exportMockup() {
        return new Promise(function (resolve) {
            try {
                var svg = document.getElementById('ubSvg');
                if (!svg) { return resolve(''); }
                var xml = new XMLSerializer().serializeToString(svg);
                var src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(xml)));
                var img = new Image();
                img.onload = function () {
                    try {
                        var c = document.createElement('canvas');
                        c.width = 760; c.height = 920;
                        var ctx = c.getContext('2d');
                        ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, c.width, c.height);
                        ctx.drawImage(img, 0, 0, c.width, c.height);
                        resolve(c.toDataURL('image/png'));
                    } catch (e) { resolve(''); }
                };
                img.onerror = function () { resolve(''); };
                img.src = src;
            } catch (e) { resolve(''); }
        });
    }

    function submitDesign() {
        cacheBuilderFields();
        if (!state.school) { return setMsg('err', 'Please pick your school so we can route your design.'); }
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(fieldVal('email'))) { return setMsg('err', 'Please enter a valid email so we can send the proof.'); }
        state.submitting = true; state.message = null; render();
        exportMockup().then(function (dataUrl) {
            apiPost({
                action: 'submit', type: 'quote', schoolId: state.school.id,
                name: fieldVal('name'), email: fieldVal('email'), notes: fieldVal('notes'),
                items: [], design: designPayload(), mockup: dataUrl
            }).then(function (res) {
                state.submitting = false;
                if (res && res.ok) {
                    state.design.logoDataUrl = '';
                    fieldCache = {};
                    state.view = 'home';
                    render(); window.scrollTo(0, 0);
                    showConfirmation({ docType: res.docType || 'Design Request', docNumber: res.docNumber });
                } else {
                    setMsg('err', (res && res.error) || 'Something went wrong. Please try again.');
                }
            }).catch(function () { state.submitting = false; setMsg('err', 'Network error. Please try again.'); });
        });
    }

    // ---- Momentec custom-sublimation configurator (embedded) ----------------
    // Build the embed URL. A category `slug` (from BOOT.momentec.categories) selects
    // a specific sport by REPLACING the last path segment of the base URL, exactly
    // as Momentec's own plug-in does (e.g. custom-sublimation -> custom-sublimation-
    // baseball); empty slug = the base configurator.
    function momentecSrc(slug) {
        var m = BOOT.momentec || {};
        var base = m.base || '';
        var parts = base.split('?');
        var path = parts[0], query = parts[1] || '';
        if (slug) {
            var seg = path.split('/');
            if (seg.length) { seg[seg.length - 1] = slug; path = seg.join('/'); }
        }
        var u = path + (query ? '?' + query : '');
        var sep = u.indexOf('?') === -1 ? '?' : '&';
        if (m.discount) { u += sep + 'discount=' + encodeURIComponent(m.discount); sep = '&'; }
        if (m.addlLeadTime) { u += sep + 'addlLeadTime=' + encodeURIComponent(m.addlLeadTime); sep = '&'; }
        if (m.hideHeader) { u += sep + 'hideHeader=true'; sep = '&'; }
        return u;
    }

    // Category picker as a LEFT RAIL (same look as the Shop/Catalogs filter rail),
    // sectioned by group (Sublimated Apparel / Custom Headwear).
    function momentecCategories() {
        var cats = (BOOT.momentec && BOOT.momentec.categories) || [];
        if (!cats.length) { return ''; }
        var cur = state.momentecCategory || '';
        var opt = function (slug, label, on) {
            return '<button class="facet-opt' + (on ? ' on' : '') + '" data-sublcat="' + esc(slug) + '"' +
                (on ? ' aria-current="true"' : '') + '><span>' + esc(label) + '</span></button>';
        };
        var order = [], groups = {};
        cats.forEach(function (c) {
            var g = c.group || 'Categories';
            if (!groups[g]) { groups[g] = []; order.push(g); }
            groups[g].push(c);
        });
        var html = '<aside class="bsg-shop-aside" aria-label="Sublimation categories">' +
            '<div class="aside-top"><span class="aside-kicker">Categories</span></div>' +
            '<div class="facet"><div class="facet-list">' + opt('', 'All Styles', !cur) + '</div></div>';
        var landing = (BOOT.momentec && BOOT.momentec.groupLanding) || {};
        order.forEach(function (g) {
            html += facetSection(g,
                '<div class="facet-list">' +
                groups[g].map(function (c) { return opt(c.slug, c.label, String(c.slug) === cur); }).join('') +
                '</div>',
                Object.prototype.hasOwnProperty.call(landing, g) ? landing[g] : undefined);
        });
        return html + '</aside>';
    }

    function sublimationPage() {
        var m = BOOT.momentec || {};
        var head =
            '<span class="hero-kicker">' + esc(m.label || 'Custom Sublimation') + ' &mdash; Powered by Momentec</span>' +
            '<h1 class="team-title">Design your custom kit.</h1>' +
            '<p class="hero-sub">Fully sublimated uniforms, fanwear, and headwear &mdash; choose a style, colors, roster, and artwork. ' +
            'When your design is ready, send it to your BSG rep for pricing and a production proof.</p>';

        if (state.momentecCaptured) {
            return '<div class="bsg-wrap bsg-subl">' + head + momentecSendPanel() + '</div>';
        }
        var iframe = '<div id="momentecWrap" class="momentec-wrap">' +
            '<div class="momentec-fallback">Loading the designer&hellip;<br>' +
            '<small>If it doesn&rsquo;t appear, the Momentec connection for this store is still being finalized ' +
            '(Momentec needs to allow this site to embed it).</small></div>' +
            '<iframe id="momentecFrame" title="Custom Sublimation Designer" src="' + esc(momentecSrc(state.momentecCategory)) + '" ' +
            'scrolling="yes" allow="fullscreen" style="width:100%;height:100%;border:0;position:relative;z-index:1;background:#fff"></iframe>' +
            '</div>';
        // Two-column layout: category rail on the left, heading + configurator right --
        // consistent with the Shop and Catalogs pages.
        return '<div class="bsg-wrap bsg-shop bsg-subl">' + momentecCategories() +
            '<div class="bsg-shop-main">' + head + iframe + '</div></div>';
    }

    function momentecSendPanel() {
        return '<div class="momentec-captured">' +
            '<div class="bsg-msg ok">Your Momentec design is ready to send. Reference: <strong>' + esc(state.momentecRef || '(created in Momentec)') + '</strong></div>' +
            (state.message ? '<div class="bsg-msg ' + state.message.type + '">' + esc(state.message.text) + '</div>' : '') +
            '<div class="team-col" style="max-width:520px">' +
            '<h3 class="team-h3">Send this design to your rep</h3>' +
            '<div class="bsg-field"><label>Your School</label>' +
            (state.school
                ? '<div class="school-picked"><span>' + esc(state.school.name) + (state.school.state ? ' (' + esc(state.school.state) + ')' : '') + '</span><button class="rm" data-act="clear-school">change</button></div>'
                : '<input id="bsgMomSchool" type="text" placeholder="Type your school name&hellip;" autocomplete="off">' + schoolResultsHtml()) +
            '</div>' +
            '<div class="ub-row">' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Your Name</label><input id="momContact" type="text" value="' + esc(fieldVal('name')) + '"></div>' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Email</label><input id="momEmail" type="email" value="' + esc(fieldVal('email')) + '"></div>' +
            '</div>' +
            '<div class="bsg-field"><label>Notes (optional)</label><textarea id="momNotes" rows="2" placeholder="Sizes, quantities, deadline&hellip;">' + esc(fieldVal('notes')) + '</textarea></div>' +
            '<button class="bsg-submit" data-act="submit-momentec"' + (state.submitting ? ' disabled' : '') + '>' +
            (state.submitting ? 'Sending&hellip;' : 'Send Design to My Rep') + '</button>' +
            '<button class="bsg-btn-ghost" data-act="momentec-restart" style="margin-top:10px;width:100%">Design something else</button>' +
            '</div></div>';
    }

    // Handle postMessages from the embedded Momentec iframe (its documented bridge:
    // height/scroll, plus the finished-design/cart handoff which we route to a rep
    // instead of Momentec's own /cart.html).
    function handleMomentecMessage(e) {
        if (!BOOT.momentec) { return; }
        var data = e.data;
        var msg = typeof data === 'string' ? data : '';
        if (msg.indexOf('asgIframeHeight') > -1) {
            var h = msg.split(':')[1];
            var fr = document.getElementById('momentecFrame');
            if (fr && h) { fr.style.height = h + 'px'; }
            return;
        }
        if (msg.indexOf('scrollToTop') > -1) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
        if (msg.indexOf('cartData') > -1) { captureMomentec({ raw: msg }); return; }
        if (data && typeof data === 'object') {
            if (data.type === 'cartData') { captureMomentec({ payload: data.payload }); return; }
            if (data.type === 'webRefData') { captureMomentec({ ref: data.payload }); return; }
            if (data.type === 'DESIGN_VIEW' || data.type === 'DETAILS_PAGE' || data.type === 'PRODUCT_SPECIFIC_PAGE' || data.type === 'LOAD_PAGE') {
                if (data.webRef) { captureMomentec({ ref: data.webRef }); }
                return;
            }
            if (data.type === 'resize' && data.height) {
                var fr2 = document.getElementById('momentecFrame');
                if (fr2) { fr2.style.height = data.height + 'px'; }
                return;
            }
        }
    }

    function captureMomentec(info) {
        var ref = '';
        try {
            if (info.ref) { ref = String(info.ref); }
            else if (info.raw) { ref = (info.raw.split('#')[1] || ''); }
            else if (info.payload) { ref = String(info.payload.webRef || info.payload.designid || info.payload.designId || JSON.stringify(info.payload)); }
        } catch (e) { ref = ''; }
        state.momentecRef = ref ? ref.slice(0, 400) : '(design created in Momentec)';
        state.momentecCaptured = true;
        state.message = null;
        render(); window.scrollTo(0, 0);
    }

    function cacheMomFields() {
        var g = function (id) { var e = document.getElementById(id); return e ? e.value : undefined; };
        if (g('momContact') !== undefined) { fieldCache.name = g('momContact'); }
        if (g('momEmail') !== undefined) { fieldCache.email = g('momEmail'); }
        if (g('momNotes') !== undefined) { fieldCache.notes = g('momNotes'); }
    }

    function submitMomentec() {
        cacheMomFields();
        if (!state.school) { return setMsg('err', 'Please pick your school so we can route your design.'); }
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(fieldVal('email'))) { return setMsg('err', 'Please enter a valid email so we can send the proof.'); }
        state.submitting = true; state.message = null; render();
        apiPost({
            action: 'submit', type: 'quote', schoolId: state.school.id,
            name: fieldVal('name'), email: fieldVal('email'), notes: fieldVal('notes'), items: [],
            design: { garment: 'Momentec Custom Sublimation', momentecRef: state.momentecRef, summary: 'Designed in the Momentec configurator.' }
        }).then(function (res) {
            state.submitting = false;
            if (res && res.ok) {
                track('design_request', { ref: state.momentecRef || res.docNumber || '' });
                state.momentecCaptured = false; state.momentecRef = ''; fieldCache = {};
                state.view = 'home';
                render(); window.scrollTo(0, 0);
                showConfirmation({ docType: res.docType || 'Design Request', docNumber: res.docNumber });
            } else {
                setMsg('err', (res && res.error) || 'Something went wrong. Please try again.');
            }
        }).catch(function () { state.submitting = false; setMsg('err', 'Network error. Please try again.'); });
    }

    // Left-hand faceted filter rail (Department / Class / Category / Vendor).
    // Each dimension is a group of clickable options -- a modern storefront facet
    // nav rather than a row of <select> dropdowns -- styled to match the landing
    // page. The active option in each facet is highlighted; "All <Label>" clears
    // that facet. Renders nothing until the filter values have loaded.
    function shopAside() {
        if (!state.filterDefs.length) { return ''; }
        var anyActive = Object.keys(state.filters).some(function (k) { return state.filters[k]; });
        var groups = state.filterDefs.map(function (f) {
            return facetSection(f.label, f.hierarchical ? categoryTree(f) : flatFacet(f));
        }).join('');
        return '<aside class="bsg-shop-aside" aria-label="Product filters">' +
            '<div class="aside-top"><span class="aside-kicker">Filter</span>' +
            (anyActive ? '<button class="bsg-clearfilters" data-act="clear-filters">Clear all</button>' : '') +
            '</div>' + groups + '</aside>';
    }

    // A collapsible rail section: clicking the heading folds/unfolds its body.
    // Collapse state is per-label and survives re-renders (state.facetCollapsed).
    // Sections start CLOSED and only open when the shopper opens them, so a long
    // rail isn't a wall of expanded filters on arrival. state.facetCollapsed holds
    // an explicit `false` for sections deliberately opened; anything unset is
    // closed. This lives only in memory, so it lasts while they browse around and
    // resets on refresh -- the page always reopens looking like a first visit.
    function facetIsOpen(label) { return state.facetCollapsed[label] === false; }
    // `landing` (optional): opening this section also loads that page in the
    // configurator, so clicking e.g. "Custom Headwear" shows headwear rather than
    // only revealing its sub-list. '' is a real value (the base configurator), so
    // presence is signalled separately.
    function facetSection(label, body, landing) {
        var closed = !facetIsOpen(label);
        var landAttr = (landing === undefined || landing === null)
            ? '' : ' data-facetland="' + esc(landing) + '"';
        return '<div class="facet">' +
            '<button class="facet-h facet-toggle" data-facettoggle="' + esc(label) + '"' + landAttr +
            ' aria-expanded="' + (closed ? 'false' : 'true') + '">' +
            '<span>' + esc(label) + '</span><span class="facet-caret">' + (closed ? '&#9656;' : '&#9662;') + '</span></button>' +
            (closed ? '' : body) + '</div>';
    }

    // Flat facet: one clickable option per value, active highlighted.
    function flatFacet(f) {
        var cur = String(state.filters[f.key] || '');
        var allBtn = '<button class="facet-opt' + (cur ? '' : ' on') + '" data-facet="' + esc(f.key) +
            '" data-facetval=""><span>All ' + esc(f.label) + '</span></button>';
        var opts = f.options.map(function (o) {
            var on = String(o.id) === cur;
            return '<button class="facet-opt' + (on ? ' on' : '') + '" data-facet="' + esc(f.key) +
                '" data-facetval="' + esc(o.id) + '"' + (on ? ' aria-current="true"' : '') +
                '><span>' + esc(o.name) + '</span>' + facetCount(o.count) + '</button>';
        }).join('');
        return '<div class="facet-list">' + allBtn + opts + '</div>';
    }

    // How much product sits behind an option. Shown so a shopper can see where the
    // catalog actually is before clicking. Counts matching ITEMS, which can exceed
    // the cards shown where flat vendor variants group into one card.
    function facetCount(n) {
        return n ? '<span class="facet-n">' + n + '</span>' : '';
    }

    // Split a NetSuite classification path ("Accessories : Scorebooks") into levels.
    function catSegments(name) {
        return String(name || '').split(/\s*:\s*/).map(function (s) { return s.trim(); }).filter(Boolean);
    }

    // Build a nested tree from the flat list of full-path options. Each node carries
    // its own value only if that exact path is a real (item-bearing) class.
    function buildCatTree(options) {
        var root = { children: {}, order: [] };
        options.forEach(function (o) {
            var node = root, path = [];
            catSegments(o.name).forEach(function (seg) {
                path.push(seg);
                if (!node.children[seg]) {
                    node.children[seg] = { label: seg, fullName: path.join(' : '), value: '', children: {}, order: [] };
                    node.order.push(seg);
                }
                node = node.children[seg];
                // Credit every node on the path, so a leaf carries its own count
                // and each ancestor totals everything beneath it.
                node.count = (node.count || 0) + (o.count || 0);
            });
            node.value = o.id;
        });
        return root;
    }

    function catNodeHtml(node, key, depth) {
        var pad = 10 + depth * 13;
        var hasKids = node.order.length > 0;
        if (hasKids) {
            // Parent: the whole row toggles the next level (never filters), matching
            // "click Level 1 to bring down the next level".
            var open = !!state.catExpanded[node.fullName];
            var kids = open
                ? '<div class="cat-children">' + node.order.map(function (k) {
                    return catNodeHtml(node.children[k], key, depth + 1);
                }).join('') + '</div>'
                : '';
            return '<button class="cat-node cat-parent' + (open ? ' open' : '') + '" data-catexpand="' + esc(node.fullName) +
                '" aria-expanded="' + (open ? 'true' : 'false') + '" style="padding-left:' + pad + 'px">' +
                '<span class="cat-caret">' + (open ? '&#9662;' : '&#9656;') + '</span>' +
                '<span class="cat-label">' + esc(node.label) + '</span>' + facetCount(node.count) + '</button>' + kids;
        }
        // Leaf: filters by its exact class value.
        var on = node.value && String(node.value) === String(state.filters[key] || '');
        return '<button class="cat-node cat-leaf' + (on ? ' on' : '') + '" data-facet="' + esc(key) +
            '" data-facetval="' + esc(node.value) + '"' + (on ? ' aria-current="true"' : '') +
            ' style="padding-left:' + pad + 'px">' +
            '<span class="cat-caret"></span><span class="cat-label">' + esc(node.label) + '</span>' +
            facetCount(node.count) + '</button>';
    }

    // Collapsible category tree. Only top-level nodes show until expanded, so a long
    // class list isn't one giant scroll. Ancestors of the active leaf auto-expand.
    function categoryTree(f) {
        var cur = String(state.filters[f.key] || '');
        if (cur) {
            var match = f.options.filter(function (o) { return String(o.id) === cur; })[0];
            if (match) {
                var segs = catSegments(match.name), acc = [];
                segs.forEach(function (s, i) { acc.push(s); if (i < segs.length - 1) { state.catExpanded[acc.join(' : ')] = true; } });
            }
        }
        var tree = buildCatTree(f.options);
        var allBtn = '<button class="cat-node cat-leaf' + (cur ? '' : ' on') + '" data-facet="' + esc(f.key) +
            '" data-facetval=""><span class="cat-caret"></span><span class="cat-label">All ' + esc(f.label) + '</span></button>';
        var nodes = tree.order.map(function (k) { return catNodeHtml(tree.children[k], f.key, 0); }).join('');
        return '<div class="cat-tree">' + allBtn + nodes + '</div>';
    }

    function toolbar() {
        return '<div class="bsg-toolbar">' +
            '<input id="bsgSearch" type="search" placeholder="Search products&hellip;" value="' + esc(state.q) + '">' +
            '</div>';
    }

    // Catalog view: a modern two-column layout -- filter rail on the left, search +
    // product grid + pager on the right. Falls back to a single full-width column
    // until the filter facets have loaded (or if the account exposes none).
    function shopPage() {
        var aside = shopAside();
        if (!aside) {
            return '<div class="bsg-wrap">' + toolbar() + grid() + pager() + '</div>';
        }
        return '<div class="bsg-wrap bsg-shop">' + aside +
            '<div class="bsg-shop-main">' + toolbar() + grid() + pager() + '</div></div>';
    }

    function grid() {
        // Tall placeholder while loading so the page height doesn't collapse (a
        // collapsed page clamps the scroll position to the top mid-filter-click).
        if (state.loading) { return '<div class="bsg-grid"><div class="bsg-empty" style="min-height:70vh">Loading products&hellip;</div></div>'; }
        if (state.loadError) {
            return '<div class="bsg-grid"><div class="bsg-empty" style="color:#b3261e">Store error: ' + esc(state.loadError) +
                '<br><small>Please share this message with BSG support.</small></div></div>';
        }
        if (!state.products.length) {
            // An empty page reads as "they don't stock this", so when the server
            // found a wider search that DOES have product, offer it rather than
            // leaving the shopper to conclude there's none. (A softball coach
            // filtering Softball + First Aid should be told the ice packs exist,
            // not shown a blank page.)
            var b = state.broaden;
            var rescue = '';
            if (b && b.count) {
                var kept = filterLabelFor(b.dropKey === 'department' ? 'class' : 'department');
                rescue = '<div class="bsg-empty-more">' +
                    '<strong>' + b.count + '</strong> ' + (b.count === 1 ? 'item is' : 'items are') +
                    ' available if you widen ' + esc(String(b.dropLabel).toLowerCase()) + '.' +
                    '<button class="bsg-btn-ghost" data-act="broaden" data-broadenkey="' + esc(b.dropKey) + '">' +
                    'Show them</button></div>';
            }
            return '<div class="bsg-grid"><div class="bsg-empty">No products found' +
                (state.q ? ' for &ldquo;' + esc(state.q) + '&rdquo;' : '') +
                ' with these filters.' + rescue +
                '<br><small>Active items with available inventory appear here automatically.</small></div></div>';
        }
        return '<div class="bsg-grid">' + state.products.map(card).join('') + '</div>';
    }

    // If an image URL 404s or requires login (file not public), swap in the
    // placeholder instead of showing a broken-image icon.
    var IMG_FALLBACK = ' onerror="var t=this.parentNode;this.remove();t.innerHTML=\'<span class=ph>No image</span>\'"';

    // Current price, plus a struck-through MSRP alongside it when the item is
    // marked on sale and has a higher list price synced in from a vendor feed.
    function priceHtml(p, big) {
        // Some items legitimately carry no Base Price (vendor-synced records).
        // Show "Price on request" instead of a misleading $0.00 -- BSG confirms
        // pricing when reviewing the order/quote anyway.
        if (!(p.price > 0)) {
            return '<span class="' + (big ? 'pd-price' : 'price') + ' price-tbd">Price on request</span>';
        }
        var cur = '<span class="' + (big ? 'pd-price' : 'price') + '">' + esc(p.priceFormatted || money(p.price)) + '</span>';
        if (!p.msrpFormatted) { return cur; }
        // Real sale (on-sale flag) shows a "Sale" badge; otherwise the higher MSRP is
        // shown as a neutral "Compare at" (no misleading Sale badge).
        if (p.onSale) {
            return '<span class="price-row">' + cur + '<span class="msrp">' + esc(p.msrpFormatted) + '</span>' +
                '<span class="sale-badge">Sale</span></span>';
        }
        return '<span class="price-row">' + cur +
            '<span class="compare-at">Compare at <span class="msrp">' + esc(p.msrpFormatted) + '</span></span></span>';
    }

    function card(p) {
        var thumb = p.image
            ? '<div class="thumb" data-view="' + esc(p.id) + '"><img src="' + esc(p.image) + '" alt="' + esc(p.name) + '" loading="lazy"' + IMG_FALLBACK + '></div>'
            : '<div class="thumb" data-view="' + esc(p.id) + '"><span class="ph">No image</span></div>';
        var canAdd = p.stock && p.stock.orderable;
        var noPrice = !(p.price > 0);
        var action;
        if (p.isMatrix) {
            // Matrix product: sizes/colors are chosen on the detail view.
            action = '<button class="bsg-add" data-view="' + esc(p.id) + '">Select Options</button>';
        } else {
            // Simple item: quantity stepper + add. Price-on-request items say
            // "Add to Quote" (still added to the cart; checkout defaults to a quote).
            var addLabel = canAdd ? (noPrice ? 'Add to Quote' : 'Add to Cart') : 'Out of Stock';
            action = '<div class="card-addrow">' +
                '<input class="card-qty" type="number" min="1" value="1" aria-label="Quantity"' + (canAdd ? '' : ' disabled') + '>' +
                '<button class="bsg-add" data-add="' + esc(p.id) + '"' + (canAdd ? '' : ' disabled') + '>' + addLabel + '</button>' +
                '</div>';
        }
        // Some item numbers already start with '#' -- don't double it up ("##21").
        var kicker = (p.itemNumber && p.itemNumber !== p.name)
            ? '#' + esc(String(p.itemNumber).replace(/^#+/, '')) + (p.groupCount > 1 ? ' &middot; ' + p.groupCount + ' options' : '')
            : (p.groupCount > 1 ? esc(p.groupCount) + ' options' : 'BSG Team Store');
        return '<div class="bsg-card">' + thumb +
            '<div class="body">' +
            '<span class="card-kicker">' + kicker + '</span>' +
            '<div class="name" data-view="' + esc(p.id) + '">' + esc(p.name) + '</div>' +
            '<span class="badge ' + esc(p.stock ? p.stock.code : 'out') + '">' + esc(p.stock ? p.stock.label : 'Out of Stock') + '</span>' +
            '<div class="card-foot">' +
            priceHtml(p, false) +
            action +
            '</div>' +
            '</div></div>';
    }

    // ---- matrix variant grid ------------------------------------------------
    // Variant labels look like "Navy-Small" / "Royal-2X-Large". Split them into
    // color + size by matching a known size vocabulary at the end of the label.
    var SIZE_VOCAB = ['6X-LARGE', '5X-LARGE', '4X-LARGE', '3X-LARGE', '2X-LARGE', 'X-LARGE', 'XX-SMALL', 'X-SMALL',
        'XXXXXXL', 'XXXXXL', 'XXXXL', 'XXXL', 'XXL', 'XXS', 'XL',
        'YOUTH X-SMALL', 'YOUTH X-LARGE', 'YOUTH LARGE', 'YOUTH MEDIUM', 'YOUTH SMALL', 'ONE SIZE', 'OSFA',
        'LARGE', 'MEDIUM', 'SMALL', '6XL', '5XL', '4XL', '3XL', '2XL', 'YXS', 'YXL', 'YL', 'YM', 'YS', 'XS', 'L', 'M', 'S'];

    function splitVariant(label) {
        var up = String(label || '').toUpperCase();
        for (var i = 0; i < SIZE_VOCAB.length; i++) {
            var s = SIZE_VOCAB[i];
            if (up === s) { return { color: '', size: label }; }
            if (up.length > s.length + 1 && up.slice(-s.length) === s && /[-_ ]/.test(up.charAt(up.length - s.length - 1))) {
                return {
                    color: label.slice(0, label.length - s.length - 1),
                    size: label.slice(label.length - s.length)
                };
            }
        }
        return null;
    }

    // Youth-to-6XL ordering shared by the matrix grid and the roster-sizing grid.
    var SIZE_ORDER = ['YOUTH X-SMALL', 'YXS', 'YOUTH SMALL', 'YS', 'YOUTH MEDIUM', 'YM', 'YOUTH LARGE', 'YL', 'YOUTH X-LARGE', 'YXL',
        'XX-SMALL', 'XXS', 'X-SMALL', 'XS', 'SMALL', 'S', 'MEDIUM', 'M', 'LARGE', 'L', 'X-LARGE', 'XL',
        '2X-LARGE', 'XXL', '2XL', '3X-LARGE', 'XXXL', '3XL', '4X-LARGE', 'XXXXL', '4XL',
        '5X-LARGE', 'XXXXXL', '5XL', '6X-LARGE', 'XXXXXXL', '6XL',
        'ONE SIZE', 'OSFA'];

    function sortSizes(arr) {
        arr.sort(function (a, b) {
            var ia = SIZE_ORDER.indexOf(String(a).toUpperCase());
            var ib = SIZE_ORDER.indexOf(String(b).toUpperCase());
            return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
        });
        return arr;
    }

    // Optional Player Name / Number captured on the product page and carried onto
    // the order/quote line (and the confirmation email). Shared by simple items
    // and the matrix grid; for a matrix add, the same name/number applies to each
    // selected size/color line (an individual athlete picking their one size).
    function personalizeFields() {
        return '<div class="pd-personalize">' +
            '<div class="vhead">Personalization &mdash; optional</div>' +
            '<div class="pd-personalize-row">' +
            '<div class="bsg-field" style="margin:0;flex:2"><label>Player Name</label>' +
            '<input id="pdPlayerName" type="text" maxlength="40" placeholder="e.g. SMITH"></div>' +
            '<div class="bsg-field" style="margin:0;flex:1"><label>Player #</label>' +
            '<input id="pdPlayerNumber" type="text" maxlength="10" placeholder="e.g. 12"></div>' +
            '</div>' +
            '<div class="pd-personalize-note">Leave blank for stock (undecorated) items.</div>' +
            '</div>';
    }

    function readPersonalization() {
        var n = document.getElementById('pdPlayerName');
        var num = document.getElementById('pdPlayerNumber');
        return { name: n ? n.value : '', number: num ? num.value : '' };
    }

    // ---- "Preview With Your Logo" (apparel decoration mockup) ---------------
    // The customer uploads a logo, drags/sizes it on the product photo, and the
    // decoration rides along with the order/quote to the rep (design summary +
    // mockup attachment -- the same server plumbing the Uniform Builder uses).
    // Placement presets are fractions of the GARMENT (the subject detected in the
    // photo), not of the photo box: x/y = artwork centre, w = artwork width. A
    // model shot where the garment fills half the frame therefore gets artwork
    // half the size of the same preset on a flat-lay shot -- matching how the
    // decoration really scales to the product. x > .5 = the wearer's left chest
    // (their left is the right-hand side of a front-facing photo).
    var LOGO_PLACEMENTS = {
        'Left Chest': { x: 0.62, y: 0.27, w: 0.16 },
        'Center Chest': { x: 0.50, y: 0.29, w: 0.34 },
        'Full Front': { x: 0.50, y: 0.38, w: 0.60 }
    };

    // Find the product's bounding box inside the square photo box by trimming the
    // near-white studio background. Returns fractions of the SAME contained-square
    // box the overlays and the flattened mockup use, so one set of numbers drives
    // both. Cross-origin photos taint the canvas (getImageData throws) -> null,
    // and placement falls back to whole-box fractions (previous behaviour).
    function detectSubject(url, cb) {
        if (!url) { return cb(null); }
        var img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = function () {
            try {
                var W = 140; // small analysis canvas -- plenty for a bounding box
                var s = Math.min(W / img.width, W / img.height);
                var iw = Math.max(1, Math.round(img.width * s));
                var ih = Math.max(1, Math.round(img.height * s));
                var c = document.createElement('canvas');
                c.width = W; c.height = W;
                var ctx = c.getContext('2d');
                ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, W);
                ctx.drawImage(img, (W - iw) / 2, (W - ih) / 2, iw, ih);
                var d = ctx.getImageData(0, 0, W, W).data;
                var minX = W, minY = W, maxX = -1, maxY = -1, x, y, i;
                for (y = 0; y < W; y++) {
                    for (x = 0; x < W; x++) {
                        i = (y * W + x) * 4;
                        if (d[i] < 236 || d[i + 1] < 236 || d[i + 2] < 236) {
                            if (x < minX) { minX = x; }
                            if (x > maxX) { maxX = x; }
                            if (y < minY) { minY = y; }
                            if (y > maxY) { maxY = y; }
                        }
                    }
                }
                // Nothing found, or the "subject" is the whole frame (a photo with a
                // non-white backdrop) -- not a usable garment box.
                if (maxX < 0 || (maxX - minX + 1) / W > 0.97) { return cb(null); }
                cb({
                    x: minX / W, y: minY / W,
                    w: (maxX - minX + 1) / W, h: (maxY - minY + 1) / W
                });
            } catch (e) { cb(null); }  // tainted canvas (cross-origin CDN)
        };
        img.onerror = function () { cb(null); };
        img.src = url;
    }

    // Convert a garment-relative preset into photo-box fractions for the overlay
    // and the flattened mockup. No detected subject -> presets apply to the whole
    // box, exactly as before.
    function placeIn(pl) {
        var s = state.subj;
        if (!s) { return { x: pl.x, y: pl.y, w: pl.w }; }
        return { x: s.x + pl.x * s.w, y: s.y + pl.y * s.h, w: pl.w * s.w };
    }

    // Refresh the garment box when the shown photo changes (colour/angle switch).
    // Only re-renders when an overlay is on screen and the box actually moved.
    function refreshSubject(url) {
        if (!url || url === state.subjFor) { return; }
        state.subjFor = url;
        detectSubject(url, function (box) {
            state.subj = box;
        });
    }

    // Decoration text color choices (name shown to the rep, hex drawn on screen).
    var TEXT_COLORS = [
        { name: 'Black', hex: '#201e1d' }, { name: 'White', hex: '#ffffff' },
        { name: 'Red', hex: '#ec3013' }, { name: 'Royal', hex: '#1d4ed8' },
        { name: 'Navy', hex: '#1e2a4a' }, { name: 'Gold', hex: '#f59e0b' },
        { name: 'Green', hex: '#1a7f42' }, { name: 'Maroon', hex: '#7f1d1d' },
        { name: 'Purple', hex: '#6d28d9' }, { name: 'Gray', hex: '#9ca3af' }
    ];

    function logoMockFields() {
        var lm = state.logoMock, tm = state.textMock;
        var html = '<div class="pd-logomock">' +
            '<div class="vhead">Customize This Item &mdash; optional</div>';
        // -- design/logo upload --
        if (!lm) {
            html += '<div class="logo-row"><label class="logo-note" style="flex:none" for="pdLogoFile">Your design or logo:</label>' +
                '<input id="pdLogoFile" type="file" accept="image/*"></div>';
        } else {
            var chips = Object.keys(LOGO_PLACEMENTS).map(function (k) {
                return '<button class="logo-chip' + (lm.placement === k ? ' on' : '') + '" data-logoplace="' + esc(k) + '">' + esc(k) + '</button>';
            }).join('');
            html += '<div class="logo-chips">' + chips + '</div>' +
                '<div class="logo-row"><label class="logo-note" for="pdLogoSize">Logo size</label>' +
                '<input id="pdLogoSize" type="range" min="3" max="70" value="' + Math.round(lm.w * 100) + '">' +
                '<button class="logo-chip" data-act="logo-remove">Remove logo</button></div>';
        }
        // -- custom text --
        html += '<div class="logo-row" style="margin-top:6px">' +
            '<label class="logo-note" style="flex:none" for="pdDecoText">Custom text:</label>' +
            '<input id="pdDecoText" type="text" maxlength="30" placeholder="e.g. BADGERS" value="' + esc(tm ? tm.text : '') + '"></div>';
        if (tm && tm.text) {
            var sw = TEXT_COLORS.map(function (c) {
                return '<button class="ub-swatch' + (tm.color === c.hex ? ' on' : '') + '" data-textcolor="' + esc(c.hex) + '" data-textcolorname="' + esc(c.name) + '" title="' + esc(c.name) + '" style="background:' + esc(c.hex) + '"></button>';
            }).join('');
            html += '<div class="logo-row"><span class="logo-note">Color:</span><span class="ub-swatches">' + sw + '</span></div>' +
                '<div class="logo-row"><label class="logo-note" for="pdTextSize">Text size</label>' +
                '<input id="pdTextSize" type="range" min="2" max="18" value="' + Math.round(tm.size * 100) + '">' +
                '<button class="logo-chip" data-act="text-remove">Remove text</button></div>';
        }
        html += '<div class="logo-note">Drag your design or text on the photo to position it, or use the ' +
            'placement buttons and the size slider. It\'s added to your request when you add this item to the ' +
            'cart &mdash; your BSG rep receives the mockup and finalizes the artwork.</div>' +
            '</div>';
        return html;
    }

    // The text overlay's on-screen font size is a fraction of the photo box width
    // (matching the flattened mockup). Set in px after each render/resize tweak.
    function sizeTextOverlay() {
        var tov = document.getElementById('pdTextOverlay');
        if (!tov || !state.textMock) { return; }
        var box = tov.parentElement.getBoundingClientRect();
        if (box.width > 0) { tov.style.fontSize = Math.round(box.width * state.textMock.size) + 'px'; }
    }

    // Downscale an uploaded image to a compact PNG data URL (keeps transparency,
    // caps the payload the submit carries).
    function readLogoFile(f, cb) {
        var r = new FileReader();
        r.onload = function () {
            var img = new Image();
            img.onload = function () {
                try {
                    var max = 600;
                    var s = Math.min(1, max / Math.max(img.width, img.height));
                    var c = document.createElement('canvas');
                    c.width = Math.max(1, Math.round(img.width * s));
                    c.height = Math.max(1, Math.round(img.height * s));
                    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
                    cb(c.toDataURL('image/png'));
                } catch (e) { cb(String(r.result || '')); }
            };
            img.onerror = function () { cb(''); };
            img.src = String(r.result || '');
        };
        r.readAsDataURL(f);
    }

    // Flatten photo + logo + text into ONE mockup image, reproducing exactly what
    // the shopper saw: a square canvas with the photo contained (like the CSS box),
    // the logo and text at their box fractions. Cross-origin photos can taint the
    // canvas; in that case cb('') and the submit attaches the logo file instead
    // (placement and text are still described in the design summary).
    function flattenDecoMockup(imageUrl, lm, tm, cb) {
        var img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = function () {
            try {
                var side = 900;
                var c = document.createElement('canvas');
                c.width = side; c.height = side;
                var ctx = c.getContext('2d');
                ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, side, side);
                var s = Math.min(side / img.width, side / img.height);
                var iw = img.width * s, ih = img.height * s;
                ctx.drawImage(img, (side - iw) / 2, (side - ih) / 2, iw, ih);
                var finish = function () { try { cb(c.toDataURL('image/jpeg', 0.85)); } catch (e3) { cb(''); } };
                var drawText = function () {
                    try {
                        if (tm && tm.text) {
                            ctx.font = '800 ' + Math.round(side * tm.size) + 'px Archivo, Arial, sans-serif';
                            ctx.fillStyle = tm.color || '#201e1d';
                            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                            ctx.fillText(String(tm.text).toUpperCase(), side * tm.x, side * tm.y);
                        }
                    } catch (e3) { /* keep going */ }
                    finish();
                };
                if (lm && lm.dataUrl) {
                    var logo = new Image();
                    logo.onload = function () {
                        try {
                            var lw = side * lm.w;
                            var lh = lw * (logo.height / logo.width);
                            ctx.drawImage(logo, side * lm.x - lw / 2, side * lm.y - lh / 2, lw, lh);
                        } catch (e2) { /* keep going -- text + photo still useful */ }
                        drawText();
                    };
                    logo.onerror = drawText;
                    logo.src = lm.dataUrl;
                } else { drawText(); }
            } catch (e) { cb(''); }
        };
        img.onerror = function () { cb(''); };
        img.src = imageUrl;
    }


    // Commit the on-screen preview as the order's decoration: summary immediately,
    // flattened mockup filled in async (submit falls back to the logo file, then
    // to a clean render of the design).
    function commitDeco(p) {
        var lm = state.logoMock, tm = state.textMock;
        if ((!lm || !lm.dataUrl) && (!tm || !tm.text)) { return; }
        var deco = {
            item: p.name, itemNumber: p.itemNumber || '',
            placement: lm ? (lm.placement || 'Custom position') : '',
            text: tm && tm.text ? tm.text : '',
            textColor: tm && tm.text ? (tm.colorName || tm.color) : '',
            logoDataUrl: lm ? lm.dataUrl : '', mockup: ''
        };
        state.deco = deco;
        if (p.image) {
            flattenDecoMockup(p.image, lm, tm, function (dataUrl) {
                if (dataUrl) { deco.mockup = dataUrl; }
            });
        }
        state.logoMock = null; state.textMock = null;
    }

    function variantSection(p) {
        var vs = p.variants || [];
        if (!vs.length) { return '<p class="pd-desc">No size/color options are currently available.</p>'; }

        var parsed = vs.map(function (v) { return { v: v, cs: splitVariant(v.label) }; });
        var allParsed = parsed.every(function (x) { return !!x.cs; });

        var qtyCell = function (v) {
            var out = v.qty <= 0
                ? '<div class="vq out">0</div>'
                : '<div class="vq">' + v.qty + '<input type="number" min="0" placeholder="0" class="vqty" ' +
                'data-vid="' + esc(v.id) + '" data-vprice="' + esc(v.price) + '" data-vlabel="' + esc(v.label) + '"></div>';
            return out;
        };

        var body;
        if (allParsed) {
            // color rows x size columns, SanMar-style
            var sizes = [], colors = [];
            parsed.forEach(function (x) {
                if (sizes.indexOf(x.cs.size) === -1) { sizes.push(x.cs.size); }
                var c = x.cs.color || '(one color)';
                if (colors.indexOf(c) === -1) { colors.push(c); }
            });
            sortSizes(sizes);
            var byKey = {}, imgByColor = {};
            parsed.forEach(function (x) {
                var ck = x.cs.color || '(one color)';
                byKey[ck + '||' + x.cs.size] = x.v;
                if (!imgByColor[ck] && x.v.image) { imgByColor[ck] = x.v.image; }
            });
            body = '<div class="vgrid-wrap"><table class="vgrid"><thead><tr><th>Color</th>' +
                sizes.map(function (s) { return '<th>' + esc(s) + '</th>'; }).join('') +
                '</tr></thead><tbody>' +
                colors.map(function (c) {
                    // A color with its own photo is clickable -> shows that color.
                    var ci = imgByColor[c]
                        ? '<td class="vcolor clickable" data-vimg="' + esc(imgByColor[c]) + '" title="Show this color">' + esc(c) + '</td>'
                        : '<td class="vcolor">' + esc(c) + '</td>';
                    return '<tr>' + ci + sizes.map(function (s) {
                        var v = byKey[c + '||' + s];
                        return '<td>' + (v ? qtyCell(v) : '<div class="vq na">&mdash;</div>') + '</td>';
                    }).join('') + '</tr>';
                }).join('') +
                '</tbody></table></div>';
        } else {
            // labels didn't parse cleanly -- plain per-variant rows
            body = '<div class="vlist">' + vs.map(function (v) {
                var lbl = v.image
                    ? '<span class="vlabel clickable" data-vimg="' + esc(v.image) + '" title="Show this option">' + esc(v.label) + '</span>'
                    : '<span class="vlabel">' + esc(v.label) + '</span>';
                return '<div class="vrow">' + lbl +
                    '<span class="badge ' + esc(v.stock.code) + '">' + esc(v.stock.label) + '</span>' +
                    qtyCell(v) + '</div>';
            }).join('') + '</div>';
        }

        return '<div class="vhead">Enter quantities (available shown above each box):</div>' + body +
            '<button class="bsg-add" style="padding:12px 20px;margin-top:10px" data-act="add-variants">Add Selected to Cart</button>';
    }

    // Splits a description string on the server's "\n" line breaks (bullets are
    // pre-marked "• ") into a mix of <p> paragraphs and a <ul> bullet list.
    function renderDescription(desc) {
        if (!desc) { return ''; }
        var lines = String(desc).split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
        var html = '', inList = false;
        lines.forEach(function (line) {
            if (line.indexOf('•') === 0) {
                if (!inList) { html += '<ul class="pd-bullets">'; inList = true; }
                html += '<li>' + esc(line.replace(/^•\s*/, '')) + '</li>';
            } else {
                if (inList) { html += '</ul>'; inList = false; }
                html += '<p class="pd-desc">' + esc(line) + '</p>';
            }
        });
        if (inList) { html += '</ul>'; }
        return html;
    }

    function specsHtml(specs) {
        if (!specs || !specs.length) { return ''; }
        return '<div class="pd-specs">' + specs.map(function (s) {
            // Block specs (the Web Store Description) render as a labeled full-width
            // paragraph; plain specs stay as label/value rows.
            if (s.block) {
                return '<div class="pd-spec-block"><span>' + esc(s.label) + '</span>' +
                    '<p>' + esc(s.value) + '</p></div>';
            }
            return '<div class="pd-spec-row"><span>' + esc(s.label) + '</span><b>' + esc(s.value) + '</b></div>';
        }).join('') + '</div>';
    }

    // Gallery model for the detail modal: [{c: colorCode, imgs: [urls]}] with a
    // current color (ci) and image (ii). Built from the server's per-color gallery
    // when the item is in the Momentec feed; falls back to the plain image list.
    function buildGallery(p) {
        var base = (p.images && p.images.length) ? p.images.slice() : (p.image ? [p.image] : []);
        var colors = (p.gallery && p.gallery.length) ? p.gallery.slice() : null;
        if (!colors) { return base.length ? { colors: [{ c: '', imgs: base }], ci: 0, ii: 0 } : null; }
        // Start on the color whose front shot is the item's primary image.
        var ci = 0;
        for (var i = 0; i < colors.length; i++) {
            if (p.image && colors[i].imgs[0] === p.image) { ci = i; break; }
        }
        return { colors: colors, ci: ci, ii: 0 };
    }

    // Swap the displayed gallery image IN PLACE -- no re-render, so nothing
    // flashes or jumps when flipping through angles.
    function galShow(ii) {
        var g = state.gal;
        if (!g || !g.colors[g.ci]) { return; }
        var imgs = g.colors[g.ci].imgs;
        if (!imgs.length) { return; }
        g.ii = ((ii % imgs.length) + imgs.length) % imgs.length;
        var main = document.getElementById('pdMainImg');
        if (main) { main.src = imgs[g.ii]; }
        var cnt = document.querySelector('.pd-count');
        if (cnt) { cnt.textContent = (g.ii + 1) + ' / ' + imgs.length; }
        document.querySelectorAll('[data-galii]').forEach(function (t) {
            t.classList.toggle('active', parseInt(t.getAttribute('data-galii'), 10) === g.ii);
        });
    }

    function imageColumn(p) {
        var g = state.gal;
        var imgs = (g && g.colors[g.ci]) ? g.colors[g.ci].imgs
            : ((p.images && p.images.length) ? p.images : (p.image ? [p.image] : []));
        var idx = g ? Math.max(0, Math.min(g.ii, imgs.length - 1)) : 0;
        var src = imgs[idx] || '';
        // Keep the garment box in step with whichever photo is showing, so a new
        // placement pick is sized against THIS colour/angle.
        refreshSubject(src);
        // Live decoration overlays (draggable logo + custom text + design) on the photo.
        var lm = state.logoMock, tm = state.textMock, overlays = '';
        if (lm && lm.dataUrl) {
            overlays += '<img id="pdLogoOverlay" class="pd-deco-overlay" src="' + esc(lm.dataUrl) + '" alt="Your logo" ' +
                'style="left:' + (lm.x * 100) + '%;top:' + (lm.y * 100) + '%;width:' + (lm.w * 100) + '%">';
        }
        if (tm && tm.text) {
            overlays += '<div id="pdTextOverlay" class="pd-deco-overlay pd-deco-text" ' +
                'style="left:' + (tm.x * 100) + '%;top:' + (tm.y * 100) + '%;color:' + esc(tm.color) + '">' +
                esc(String(tm.text).toUpperCase()) + '</div>';
        }
        // Left/right arrows + counter when the current color has multiple shots.
        var arrows = imgs.length > 1
            ? '<button class="pd-nav prev" data-act="gal-prev" aria-label="Previous image">&lsaquo;</button>' +
            '<button class="pd-nav next" data-act="gal-next" aria-label="Next image">&rsaquo;</button>' +
            '<span class="pd-count">' + (idx + 1) + ' / ' + imgs.length + '</span>'
            : '';
        var main = src
            ? '<div class="pd-mainimg"><img id="pdMainImg" src="' + esc(src) + '" alt="' + esc(p.name) + '"' + IMG_FALLBACK + '>' + overlays + arrows + '</div>'
            : '<div class="pd-mainimg"><span class="ph">No image</span></div>';
        var thumbs = imgs.length > 1
            ? '<div class="pd-thumbs">' + imgs.map(function (u, i) {
                return '<img src="' + esc(u) + '" data-galii="' + i + '" alt="" class="' + (i === idx ? 'active' : '') + '" onerror="this.remove()">';
            }).join('') + '</div>'
            : '';
        // Color strip: one thumbnail per available color; click to switch.
        var colorStrip = (g && g.colors.length > 1)
            ? '<div class="pd-colors"><span class="logo-note">Colors (' + g.colors.length + '):</span>' +
            '<div class="pd-colorrow">' + g.colors.map(function (c, i) {
                return '<img src="' + esc(c.imgs[0]) + '" data-galci="' + i + '" alt="" title="' + esc(c.c) + '" class="' + (i === g.ci ? 'active' : '') + '" onerror="this.remove()">';
            }).join('') + '</div></div>'
            : '';
        return '<div class="pd-imgcol">' + main + thumbs + colorStrip + '</div>';
    }

    // Volume-pricing tier table for a simple item, plus a live "qty x unit = total"
    // line (updated by updateTierCalc as the shopper changes quantity). Renders
    // nothing when the product has no tiers.
    function tierTable(p) {
        if (p.isMatrix || !p.tiers || !p.tiers.length) { return ''; }
        var rows = p.tiers.map(function (t) {
            return '<div class="pd-tier"><span>' + esc(t.minQty) + '+</span>' +
                '<span>' + esc(t.priceFormatted || money(t.price)) + ' ea</span></div>';
        }).join('');
        return '<div class="pd-tiers"><div class="pd-tiers-h">Volume pricing</div>' +
            rows + '<div class="pd-tiercalc" id="pdTierCalc"></div></div>';
    }

    function updateTierCalc() {
        var p = state.detail;
        if (!p || p.isMatrix) { return; }
        var el = document.getElementById('pdTierCalc');
        if (!el) { return; }
        var qi = document.getElementById('pdQty');
        var qty = qi ? (parseInt(qi.value, 10) || 1) : 1;
        var unit = tierPrice(p, qty);
        if (!(unit > 0) || !p.tiers || !p.tiers.length) { el.innerHTML = ''; return; }
        el.innerHTML = esc(qty) + ' &times; ' + esc(money(unit)) + ' = <strong>' + esc(money(unit * qty)) + '</strong>';
    }

    function detailModal() {
        if (!state.detailLoading && !state.detail) { return ''; }
        var inner;
        if (state.detailLoading) {
            inner = '<div class="bsg-loading">Loading&hellip;</div>';
        } else {
            var p = state.detail;
            var canAdd = p.stock && p.stock.orderable;
            // Player name/number is only offered on apparel/decorable items (the
            // server flags these via p.personalize); equipment shows no such fields.
            var pers = p.personalize ? personalizeFields() : '';
            // Logo/text decoration builder: apparel with a real photo only.
            var deco = (p.personalize && p.image) ? logoMockFields() : '';
            var buyUi = p.isMatrix
                ? pers + deco + variantSection(p)
                : pers + deco +
                '<div class="pd-qtyrow">' +
                '<div class="bsg-field" style="margin:0"><label>Quantity</label>' +
                '<input id="pdQty" class="pd-qty" type="number" min="1" value="1"' + (canAdd ? '' : ' disabled') + '></div>' +
                '<button class="bsg-add pd-addbtn" data-act="add-detail"' + (canAdd ? '' : ' disabled') + '>' +
                (canAdd ? 'Add to Cart' : 'Out of Stock') + '</button>' +
                '</div>';
            inner =
                '<button class="pd-close" data-act="close-detail" aria-label="Close">&times;</button>' +
                '<div class="pd-layout">' + imageColumn(p) +
                '<div class="pd-body">' +
                '<span class="badge ' + esc(p.stock ? p.stock.code : 'out') + '">' + esc(p.stock ? p.stock.label : 'Out of Stock') + '</span>' +
                '<h2 class="pd-name">' + esc(p.name) + '</h2>' +
                (p.itemNumber && p.itemNumber !== p.name ? '<div class="itemno">Item #' + esc(p.itemNumber) + '</div>' : '') +
                priceHtml(p, true) +
                tierTable(p) +
                renderDescription(p.description) +
                buyUi +
                specsHtml(p.specs) +
                '</div></div>';
        }
        return '<div class="bsg-modal-overlay open" data-act="close-detail"></div>' +
            '<div class="bsg-modal" role="dialog" aria-modal="true" aria-label="Product details">' + inner + '</div>';
    }

    // Classic "1 2 3 ... 8 9 10 ... 48 49 50" numbered pager: always shows the
    // first/last couple of pages plus a window around the current page, with an
    // ellipsis for the gaps, so jumping to page 4 (or page 47) is a single click
    // instead of clicking Next dozens of times.
    function pageList(cur, total) {
        var delta = 2, range = [], withDots = [], last;
        for (var i = 1; i <= total; i++) {
            if (i === 1 || i === total || (i >= cur - delta && i <= cur + delta)) { range.push(i); }
        }
        range.forEach(function (i) {
            if (last) {
                if (i - last === 2) { withDots.push(last + 1); }
                else if (i - last !== 1) { withDots.push('…'); }
            }
            withDots.push(i);
            last = i;
        });
        return withDots;
    }

    function pager() {
        var sizeOpts = BOOT.pageSizes || [25, 50, 100];
        // Show the bar when there's more than one page, or whenever the per-page
        // choice matters (more products than the smallest option).
        if (state.pageCount <= 1 && state.total <= sizeOpts[0]) { return ''; }
        var nums = '';
        if (state.pageCount > 1) {
            var cur = state.page, total = state.pageCount;
            var numbers = pageList(cur, total).map(function (p) {
                if (p === '…') { return '<span class="bsg-pager-dots">&hellip;</span>'; }
                return '<button class="bsg-pager-num' + (p === cur ? ' active' : '') + '" data-page="' + p + '">' + p + '</button>';
            }).join('');
            nums = '<button data-page="' + (cur - 1) + '"' + (cur <= 1 ? ' disabled' : '') + '>&larr; Prev</button>' +
                numbers +
                '<button data-page="' + (cur + 1) + '"' + (cur >= total ? ' disabled' : '') + '>Next &rarr;</button>' +
                '<span class="bsg-pager-jump">Go to <input type="number" min="1" max="' + total + '" id="bsgPageJump" placeholder="' + cur + '"> of ' + total +
                '<button data-act="jump-page">Go</button></span>';
        }
        var sizes = sizeOpts.map(function (n) {
            return '<button class="bsg-pager-num' + (n === state.pageSize ? ' active' : '') + '" data-pagesize="' + n + '">' + n + '</button>';
        }).join('');
        return '<div class="bsg-pager">' + nums +
            '<span class="bsg-pager-jump">Show' + sizes + 'per page</span></div>';
    }

    function overlay() {
        return '<div class="bsg-overlay' + (state.drawerOpen ? ' open' : '') + '" data-act="close-cart"></div>';
    }

    function drawer() {
        var ids = Object.keys(state.cart);
        var lines = ids.length ? ids.map(function (id) {
            var c = state.cart[id];
            var player = [c.playerName, c.playerNumber ? '#' + c.playerNumber : ''].filter(Boolean).join(' ');
            return '<div class="bsg-line">' +
                '<div style="flex:1"><div style="font-weight:700;font-size:13px">' + esc(c.name) + '</div>' +
                (player ? '<div class="bsg-line-player">Player: ' + esc(player) + '</div>' : '') +
                '<div style="font-size:12px;color:#666">' + money(c.price) + ' each</div></div>' +
                '<input class="qn" type="number" min="0" value="' + c.qty + '" data-qty="' + esc(id) + '">' +
                '<button class="rm" data-rm="' + esc(id) + '">Remove</button></div>';
        }).join('') : '<p style="color:#888">Your cart is empty.</p>';

        var subtotal = ids.reduce(function (s, id) { return s + state.cart[id].price * state.cart[id].qty; }, 0);

        // Committed decoration (logo/text mockup) that will ride along with the order.
        var decoNote = '';
        if (state.deco) {
            var dbits = [];
            if (state.deco.logoDataUrl) { dbits.push('your logo (' + (state.deco.placement || 'custom position') + ')'); }
            if (state.deco.text) { dbits.push('text "' + state.deco.text + '"' + (state.deco.textColor ? ' in ' + state.deco.textColor : '')); }
            if (state.deco.design) { dbits.push(state.deco.design + ' design'); }
            decoNote = '<div class="deco-note">Includes ' + esc(dbits.join(' + ')) + ' on ' + esc(state.deco.item) +
                ' &mdash; the mockup goes to your BSG rep with this request. ' +
                '<button class="linklike" data-act="deco-remove">Remove</button></div>';
        }

        return '<div class="bsg-drawer' + (state.drawerOpen ? ' open' : '') + '" role="dialog" aria-modal="true" aria-label="Your cart">' +
            '<h3>Your Cart <button class="close" data-act="close-cart" aria-label="Close cart">&times;</button></h3>' +
            '<div class="items">' + decoNote + lines +
            (ids.length ? '<div style="text-align:right;margin-top:12px;font-weight:800">Est. subtotal: ' + money(subtotal) + '</div>' +
                '<p style="font-size:11px;color:#888">Final pricing is confirmed by BSG on review.</p>' : '') +
            '</div>' +
            (ids.length ? checkout() : '') +
            '</div>';
    }

    function checkout() {
        var isOrder = state.submitType === 'order';
        return '<div class="foot">' +
            (state.message ? '<div class="bsg-msg ' + state.message.type + '">' + esc(state.message.text) + '</div>' : '') +
            '<div class="bsg-toggle">' +
            '<button data-type="order" class="' + (isOrder ? 'active' : '') + '">Place Order (PO)</button>' +
            '<button data-type="quote" class="' + (!isOrder ? 'active' : '') + '">Request a Quote</button>' +
            '</div>' +
            '<div class="bsg-field"><label>Your School</label>' +
            (state.school
                ? '<div style="display:flex;justify-content:space-between;align-items:center;border:1px solid #1a7f42;padding:8px 10px"><span style="font-size:13px">' + esc(state.school.name) + (state.school.state ? ' (' + esc(state.school.state) + ')' : '') + '</span><button class="rm" data-act="clear-school">change</button></div>'
                : '<input id="bsgSchool" type="text" placeholder="Type your school name&hellip;" autocomplete="off">' + schoolResultsHtml()) +
            '</div>' +
            '<div class="bsg-field"><label>Your Name</label><input id="bsgName" type="text" value="' + esc(fieldVal('name')) + '"></div>' +
            '<div class="bsg-field"><label>Email (for confirmation)</label><input id="bsgEmail" type="email" value="' + esc(fieldVal('email')) + '"></div>' +
            (isOrder ? '<div class="bsg-field"><label>Purchase Order # (required)</label><input id="bsgPo" type="text" value="' + esc(fieldVal('po')) + '"></div>' : '') +
            '<div class="bsg-field"><label>Notes (optional)</label><textarea id="bsgNotes" rows="2">' + esc(fieldVal('notes')) + '</textarea></div>' +
            '<button class="bsg-submit" data-act="submit"' + (state.submitting ? ' disabled' : '') + '>' +
            (state.submitting ? 'Submitting&hellip;' : (isOrder ? 'Submit Order' : 'Submit Quote Request')) + '</button>' +
            '</div>';
    }

    function schoolResultsHtml() {
        if (!state.schoolResults.length) { return ''; }
        return '<div class="bsg-school-results">' + state.schoolResults.map(function (s) {
            var loc = [s.city, s.state].filter(Boolean).join(', ');
            return '<div data-school=\'' + esc(JSON.stringify(s)) + '\'>' + esc(s.name) + (loc ? ' <span style="color:#888">&mdash; ' + esc(loc) + '</span>' : '') + '</div>';
        }).join('') + '</div>';
    }

    // preserve typed checkout field values across re-renders
    var fieldCache = {};
    function fieldVal(k) { return fieldCache[k] || ''; }
    function cacheFields() {
        ['name', 'email', 'po', 'notes'].forEach(function (k) {
            var id = 'bsg' + k.charAt(0).toUpperCase() + k.slice(1);
            var e = document.getElementById(id);
            if (e) { fieldCache[k] = e.value; }
        });
    }

    // ------------------------------------------------------------- events --

    function wire() {
        var root = document.getElementById('bsgshopRoot');

        root.querySelectorAll('[data-add]').forEach(function (b) {
            b.addEventListener('click', function () {
                var p = state.products.filter(function (x) { return String(x.id) === b.getAttribute('data-add'); })[0];
                if (!p) { return; }
                var row = b.parentNode;
                var qi = row ? row.querySelector('.card-qty') : null;
                var qty = qi ? (parseInt(qi.value, 10) || 1) : 1;
                addLine(p.id, p.name, p.price, qty);
                openDrawer();
            });
        });
        root.querySelectorAll('[data-view]').forEach(function (b) {
            b.style.cursor = 'pointer';
            b.addEventListener('click', function () { openDetail(b.getAttribute('data-view')); });
        });
        // Live volume-price calc on the product detail's quantity field.
        var pdQty = document.getElementById('pdQty');
        if (pdQty) {
            pdQty.addEventListener('input', updateTierCalc);
            updateTierCalc();
        }
        // Gallery: angle thumbnails, color strip, and variant-color clicks.
        root.querySelectorAll('[data-galii]').forEach(function (t) {
            t.addEventListener('click', function () {
                if (!state.gal) { return; }
                galShow(parseInt(t.getAttribute('data-galii'), 10) || 0);
            });
        });
        root.querySelectorAll('[data-galci]').forEach(function (t) {
            t.addEventListener('click', function () {
                if (!state.gal) { return; }
                state.gal.ci = parseInt(t.getAttribute('data-galci'), 10) || 0;
                state.gal.ii = 0;
                render();
            });
        });
        root.querySelectorAll('[data-vimg]').forEach(function (t) {
            t.addEventListener('click', function () {
                var u = t.getAttribute('data-vimg');
                if (!u) { return; }
                if (!state.gal) { state.gal = { colors: [], ci: 0, ii: 0 }; }
                var g2 = state.gal, found = -1;
                for (var i = 0; i < g2.colors.length; i++) {
                    if (g2.colors[i].imgs[0] === u) { found = i; break; }
                }
                if (found === -1) { g2.colors.unshift({ c: '', imgs: [u] }); found = 0; }
                g2.ci = found; g2.ii = 0;
                render();
            });
        });
        root.querySelectorAll('[data-shopdept]').forEach(function (t) {
            t.addEventListener('click', function () {
                state.filters = { department: t.getAttribute('data-shopdept') };
                state.page = 1;
                state.view = 'shop';
                loadProducts();
                window.scrollTo(0, 0);
            });
        });
        root.querySelectorAll('[data-facet]').forEach(function (b) {
            b.addEventListener('click', function () {
                var key = b.getAttribute('data-facet');
                var val = b.getAttribute('data-facetval') || '';
                if (String(state.filters[key] || '') === String(val)) { return; } // already active
                keepScrollY = window.pageYOffset || 0;
                state.filters[key] = val;
                state.page = 1;
                loadProducts();
                loadFilters();   // options are scoped to the selection -- re-scope them
            });
        });
        // Design Ideas: switch sport, and open a design full size.
        root.querySelectorAll('[data-artgroup]').forEach(function (b) {
            b.addEventListener('click', function () {
                var i = parseInt(b.getAttribute('data-artgroup'), 10) || 0;
                if (i === state.artGroup) { return; }
                keepScrollY = window.pageYOffset || 0;
                state.artGroup = i;
                render();
            });
        });
        root.querySelectorAll('[data-artpick]').forEach(function (fig) {
            fig.addEventListener('click', function () {
                keepScrollY = window.pageYOffset || 0;
                state.artZoom = JSON.parse(fig.getAttribute('data-artpick'));
                state.artPick = state.artZoom;
                track('sample_art_view', { ref: state.artZoom.code });
                render();
            });
        });
        // Catalogs page: filter the catalog grid by group (client-side, no server call).
        root.querySelectorAll('[data-catgroup]').forEach(function (b) {
            b.addEventListener('click', function () {
                keepScrollY = window.pageYOffset || 0;
                state.catalogGroup = b.getAttribute('data-catgroup') || '';
                render();
            });
        });
        // Collapse/expand a whole filter section (Sport / Categories / Brand / ...).
        root.querySelectorAll('[data-facettoggle]').forEach(function (b) {
            b.addEventListener('click', function () {
                var k = b.getAttribute('data-facettoggle');
                var opening = !facetIsOpen(k);
                state.facetCollapsed[k] = opening ? false : true;
                // Opening a group that names a landing page also loads it, so the
                // header acts like a real category link and not just a disclosure.
                // Collapsing never navigates.
                var land = b.getAttribute('data-facetland');
                if (opening && land !== null && land !== state.momentecCategory) {
                    state.momentecCategory = land;
                    state.momentecCaptured = false;
                }
                render();
            });
        });
        // Category tree: expand/collapse a level in place (no server call).
        root.querySelectorAll('[data-catexpand]').forEach(function (b) {
            b.addEventListener('click', function () {
                var k = b.getAttribute('data-catexpand');
                state.catExpanded[k] = !state.catExpanded[k];
                render();
            });
        });
        // ---- decoration builder (logo/text on the product photo) ----
        var lf = document.getElementById('pdLogoFile');
        if (lf) {
            lf.addEventListener('change', function () {
                var f = lf.files && lf.files[0];
                if (!f) { return; }
                readLogoFile(f, function (dataUrl) {
                    if (!dataUrl) { return; }
                    var pl = placeIn(LOGO_PLACEMENTS['Left Chest']);
                    state.logoMock = { dataUrl: dataUrl, x: pl.x, y: pl.y, w: pl.w, placement: 'Left Chest' };
                    render();
                });
            });
        }
        root.querySelectorAll('[data-logoplace]').forEach(function (b) {
            b.addEventListener('click', function () {
                var key = b.getAttribute('data-logoplace');
                if (!LOGO_PLACEMENTS[key] || !state.logoMock) { return; }
                var pl = placeIn(LOGO_PLACEMENTS[key]);
                state.logoMock.x = pl.x; state.logoMock.y = pl.y; state.logoMock.w = pl.w;
                state.logoMock.placement = key;
                render();
            });
        });
        var lsz = document.getElementById('pdLogoSize');
        if (lsz) {
            lsz.addEventListener('input', function () {
                if (!state.logoMock) { return; }
                state.logoMock.w = (parseInt(lsz.value, 10) || 16) / 100;
                var ov1 = document.getElementById('pdLogoOverlay');
                if (ov1) { ov1.style.width = (state.logoMock.w * 100) + '%'; }
            });
        }
        var dtx = document.getElementById('pdDecoText');
        if (dtx) {
            dtx.addEventListener('input', function () {
                var v = dtx.value || '';
                if (!v.trim()) {
                    if (state.textMock) { state.textMock.text = ''; }
                    var tov0 = document.getElementById('pdTextOverlay');
                    if (tov0) { tov0.textContent = ''; }
                    return;
                }
                if (!state.textMock) {
                    // First keystroke creates the overlay on the chest, scaled to the
                    // garment in the photo; re-render once, keep focus.
                    var tp = placeIn(LOGO_PLACEMENTS['Center Chest']);
                    state.textMock = {
                        text: v, color: '#201e1d', colorName: 'Black',
                        x: tp.x, y: tp.y, size: 0.09 * (state.subj ? state.subj.w : 1)
                    };
                    render();
                    var el = document.getElementById('pdDecoText');
                    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
                    return;
                }
                state.textMock.text = v;
                var tov = document.getElementById('pdTextOverlay');
                if (tov) { tov.textContent = v.toUpperCase(); }
            });
        }
        root.querySelectorAll('[data-textcolor]').forEach(function (b) {
            b.addEventListener('click', function () {
                if (!state.textMock) { return; }
                state.textMock.color = b.getAttribute('data-textcolor');
                state.textMock.colorName = b.getAttribute('data-textcolorname');
                render();
            });
        });
        var tsz = document.getElementById('pdTextSize');
        if (tsz) {
            tsz.addEventListener('input', function () {
                if (!state.textMock) { return; }
                state.textMock.size = (parseInt(tsz.value, 10) || 9) / 100;
                sizeTextOverlay();
            });
        }
        // ---- pre-set design templates ----
        // Pick (or switch) a template. First pick drops it near the chest; switching
        // keeps the customer's text/colour/position so they can compare layouts.
        // Drag either overlay to position it (fractions of the photo box, so the
        // placement survives re-renders and matches the flattened mockup exactly).
        function wireDecoDrag(el, store) {
            if (!el) { return; }
            el.addEventListener('pointerdown', function (e) {
                e.preventDefault();
                var box = el.parentElement.getBoundingClientRect();
                function move(ev) {
                    var x = Math.max(0.02, Math.min(0.98, (ev.clientX - box.left) / box.width));
                    var y = Math.max(0.02, Math.min(0.98, (ev.clientY - box.top) / box.height));
                    el.style.left = (x * 100) + '%'; el.style.top = (y * 100) + '%';
                    var st = store();
                    if (st) { st.x = x; st.y = y; if (st.placement) { st.placement = 'Custom position'; } }
                }
                function up() {
                    window.removeEventListener('pointermove', move);
                    window.removeEventListener('pointerup', up);
                }
                window.addEventListener('pointermove', move);
                window.addEventListener('pointerup', up);
            });
        }
        wireDecoDrag(document.getElementById('pdLogoOverlay'), function () { return state.logoMock; });
        wireDecoDrag(document.getElementById('pdTextOverlay'), function () { return state.textMock; });
        sizeTextOverlay();

        // Sublimation sport picker: reload the configurator for the chosen category.
        root.querySelectorAll('[data-sublcat]').forEach(function (b) {
            b.addEventListener('click', function () {
                var slug = b.getAttribute('data-sublcat') || '';
                if (slug === (state.momentecCategory || '')) { return; }
                state.momentecCategory = slug;
                state.momentecCaptured = false;
                render();
            });
        });
        root.querySelectorAll('[data-page]').forEach(function (b) {
            b.addEventListener('click', function () { gotoPage(parseInt(b.getAttribute('data-page'), 10)); });
        });
        // Per-page size picker (25/50/100). Keeps the scroll position.
        root.querySelectorAll('[data-pagesize]').forEach(function (b) {
            b.addEventListener('click', function () {
                var n = parseInt(b.getAttribute('data-pagesize'), 10);
                if (!n || n === state.pageSize) { return; }
                keepScrollY = window.pageYOffset || 0;
                state.pageSize = n; state.page = 1;
                loadProducts();
            });
        });
        root.querySelectorAll('[data-act]').forEach(function (b) {
            b.addEventListener('click', function () { handleAct(b.getAttribute('data-act'), b); });
        });
        root.querySelectorAll('[data-qty]').forEach(function (inp) {
            inp.addEventListener('change', function () { setQty(inp.getAttribute('data-qty'), inp.value); });
        });
        root.querySelectorAll('[data-rm]').forEach(function (b) {
            b.addEventListener('click', function () { setQty(b.getAttribute('data-rm'), 0); });
        });
        root.querySelectorAll('[data-type]').forEach(function (b) {
            b.addEventListener('click', function () { cacheFields(); state.submitType = b.getAttribute('data-type'); state.message = null; render(); });
        });
        root.querySelectorAll('[data-school]').forEach(function (d) {
            d.addEventListener('click', function () {
                cacheFields();
                state.school = JSON.parse(d.getAttribute('data-school'));
                state.schoolResults = [];
                render();
            });
        });

        var srch = document.getElementById('bsgSearch');
        if (srch) {
            srch.addEventListener('input', function () {
                clearTimeout(searchTimer);
                var v = srch.value;
                searchTimer = setTimeout(function () {
                    keepScrollY = window.pageYOffset || 0;
                    state.q = v; state.page = 1; loadProducts();
                    if (v && v.trim().length >= 2) { track('search', { ref: v.trim() }); }
                }, 300);
            });
        }
        var pageJump = document.getElementById('bsgPageJump');
        if (pageJump) {
            pageJump.addEventListener('keydown', function (e) { if (e.key === 'Enter') { jumpToPage(); } });
        }
        var sch = document.getElementById('bsgSchool');
        if (sch) {
            sch.addEventListener('input', function () {
                clearTimeout(schoolTimer);
                var v = sch.value;
                schoolTimer = setTimeout(function () { doSchoolSearch(v); }, 300);
            });
            // keep focus/caret when results re-render
            sch.focus();
        }
        var tsch = document.getElementById('bsgTeamSchool');
        if (tsch) {
            tsch.addEventListener('input', function () {
                clearTimeout(schoolTimer);
                var v = tsch.value;
                schoolTimer = setTimeout(function () { doSchoolSearch(v); }, 300);
            });
            // preventScroll: roster-picker renders on this page must not yank the
            // viewport up to the school field.
            tsch.focus({ preventScroll: true });
        }
        // Roster item picker: search the catalog, load the picked item's real sizes.
        var tri = document.getElementById('tmRosterItem');
        if (tri) {
            tri.addEventListener('input', function () {
                clearTimeout(rosterTimer);
                state.rosterProdQ = tri.value;
                rosterTimer = setTimeout(function () { doRosterProdSearch(state.rosterProdQ); }, 300);
            });
        }
        root.querySelectorAll('[data-rosterprod]').forEach(function (d) {
            d.addEventListener('click', function () {
                cacheTeamFields();
                var p = JSON.parse(d.getAttribute('data-rosterprod'));
                state.rosterProdResults = []; state.rosterProdQ = '';
                state.rosterProduct = { id: p.id, name: p.name, itemNumber: p.itemNumber || '', sizes: null };
                render();
                api('product', { id: p.id }).then(function (res) {
                    var rp = state.rosterProduct;
                    if (!rp || String(rp.id) !== String(p.id)) { return; } // changed while loading
                    var found = [];
                    var vs = (res && res.ok && res.product && res.product.variants) || [];
                    vs.forEach(function (v) {
                        var cs = splitVariant(v.label);
                        if (cs && cs.size && found.indexOf(cs.size) === -1) { found.push(cs.size); }
                    });
                    cacheTeamFields();
                    rp.sizes = sortSizes(found);
                    render();
                });
            });
        });
        ['tmName', 'tmSport', 'tmContact', 'tmEmail', 'tmPhone', 'tmNotes'].forEach(function (id) {
            var e = document.getElementById(id);
            if (e) { e.addEventListener('input', cacheTeamFields); e.addEventListener('change', cacheTeamFields); }
        });
        root.querySelectorAll('.rqty').forEach(function (inp) {
            inp.addEventListener('input', cacheTeamFields);
        });

        // ---- Uniform Builder controls ----
        root.querySelectorAll('.ub-swatch').forEach(function (b) {
            b.addEventListener('click', function () {
                var target = b.getAttribute('data-target'), hex = b.getAttribute('data-hex');
                cacheBuilderFields();
                if (target === 'body' || target === 'sleeves' || target === 'trim') { state.design.zones[target] = hex; }
                else { state.design[target] = hex; }
                render();
            });
        });
        var ubText = function (id, apply) {
            var e = document.getElementById(id);
            if (e) {
                e.addEventListener('input', function () {
                    apply(e.value);           // live-update the SVG node directly (smooth typing)
                });
            }
        };
        ubText('ubTeam', function (v) { state.design.teamName = v; var t = document.getElementById('tTeam'); if (t) { t.textContent = v; } });
        ubText('ubNumber', function (v) { state.design.number = v; var t = document.getElementById('tNumber'); if (t) { t.textContent = v; } });
        ubText('ubPlayer', function (v) { state.design.playerName = v; var t = document.getElementById('tName'); if (t) { t.textContent = String(v).toUpperCase(); } });
        var ubFont = document.getElementById('ubFont');
        if (ubFont) { ubFont.addEventListener('change', function () { cacheBuilderFields(); render(); }); }
        var ubLogo = document.getElementById('ubLogo');
        if (ubLogo) {
            ubLogo.addEventListener('change', function () {
                var f = ubLogo.files && ubLogo.files[0];
                if (!f) { return; }
                if (f.size > 3 * 1024 * 1024) { setMsg('err', 'That logo is a bit large — please use an image under 3 MB.'); return; }
                cacheBuilderFields();
                var r = new FileReader();
                r.onload = function () { state.design.logoDataUrl = String(r.result || ''); render(); };
                r.readAsDataURL(f);
            });
        }
        ['ubContact', 'ubEmail', 'ubNotes'].forEach(function (id) {
            var e = document.getElementById(id);
            if (e) { e.addEventListener('input', cacheBuilderFields); }
        });
        var bsch = document.getElementById('bsgBuilderSchool');
        if (bsch) {
            bsch.addEventListener('input', function () {
                clearTimeout(schoolTimer);
                var v = bsch.value;
                schoolTimer = setTimeout(function () { doSchoolSearch(v); }, 300);
            });
            bsch.focus();
        }
        ['bsgName', 'bsgEmail', 'bsgPo', 'bsgNotes'].forEach(function (id) {
            var e = document.getElementById(id);
            if (e) { e.addEventListener('input', cacheFields); }
        });

        // ---- Momentec embed + send-panel wiring ----
        var mfr = document.getElementById('momentecFrame');
        if (mfr) {
            mfr.addEventListener('load', function () {
                // Momentec's bridge expects the parent to announce itself.
                try {
                    var c = mfr.contentWindow;
                    if (c && c.postMessage) {
                        c.postMessage('parentHeight:1000', '*');
                        c.postMessage({ type: 'fullURL', value: location.href }, '*');
                        c.postMessage('parentDomain:' + location.hostname, '*');
                    }
                } catch (e) { /* cross-origin: fine, iframe drives itself */ }
            });
        }
        var msch = document.getElementById('bsgMomSchool');
        if (msch) {
            msch.addEventListener('input', function () {
                clearTimeout(schoolTimer);
                var v = msch.value;
                schoolTimer = setTimeout(function () { doSchoolSearch(v); }, 300);
            });
            msch.focus();
        }
        ['momContact', 'momEmail', 'momNotes'].forEach(function (id) {
            var e = document.getElementById(id);
            if (e) { e.addEventListener('input', cacheMomFields); }
        });
    }

    // `el` is the button that was clicked -- only some actions need it (e.g.
    // 'broaden' reads which filter to drop off the button itself).
    function handleAct(act, el) {
        if (act === 'open-cart') { openDrawer(); }
        else if (act === 'close-cart') { cacheFields(); state.drawerOpen = false; render(); }
        else if (act === 'clear-school') { cacheFields(); state.school = null; render(); }
        else if (act === 'clear-roster-prod') {
            cacheTeamFields();
            state.rosterProduct = null; state.rosterProdResults = []; state.rosterProdQ = '';
            render();
        }
        else if (act === 'submit') { submit(); }
        else if (act === 'close-detail') { state.detail = null; state.detailLoading = false; render(); }
        else if (act === 'add-detail') { addDetailToCart(); }
        else if (act === 'add-variants') { addSelectedVariants(); }
        else if (act === 'jump-page') { jumpToPage(); }
        else if (act === 'broaden') {
            var bk = el ? el.getAttribute('data-broadenkey') : '';
            if (bk) {
                keepScrollY = window.pageYOffset || 0;
                state.filters[bk] = '';
                state.page = 1;
                loadProducts();
                loadFilters();
            }
        }
        else if (act === 'clear-filters') { keepScrollY = window.pageYOffset || 0; state.filters = {}; state.page = 1; loadProducts(); loadFilters(); }
        else if (act === 'gal-prev' || act === 'gal-next') {
            if (state.gal) { galShow(state.gal.ii + (act === 'gal-next' ? 1 : -1)); }
        }
        else if (act === 'logo-remove') { state.logoMock = null; render(); }
        else if (act === 'text-remove') { state.textMock = null; render(); }
        else if (act === 'deco-remove') { state.deco = null; render(); }
        else if (act === 'go-home') { cacheFields(); state.view = 'home'; render(); window.scrollTo(0, 0); }
        else if (act === 'go-shop') { cacheFields(); state.view = 'shop'; render(); window.scrollTo(0, 0); }
        else if (act === 'go-catalogs') { cacheFields(); state.view = 'catalogs'; state.message = null; render(); window.scrollTo(0, 0); }
        else if (act === 'go-sampleart') { cacheFields(); state.view = 'sampleart'; state.message = null; render(); window.scrollTo(0, 0); }
        else if (act === 'artzoom-close') { keepScrollY = window.pageYOffset || 0; state.artZoom = null; render(); }
        else if (act === 'artpick-clear') { keepScrollY = window.pageYOffset || 0; state.artPick = null; render(); }
        else if (act === 'art-request') {
            // Carry the chosen reference into a team request so the rep gets the code.
            state.artZoom = null;
            state.view = 'team';
            state.message = null;
            if (state.artPick) { track('sample_art_request', { ref: state.artPick.code }); }
            render(); window.scrollTo(0, 0);
        }
        else if (act === 'team-quote') {
            cacheFields();
            state.view = 'team';
            state.message = null;
            render(); window.scrollTo(0, 0);
        }
        else if (act === 'submit-team') { submitTeam(); }
        else if (act === 'go-builder') { cacheFields(); state.view = 'builder'; state.message = null; render(); window.scrollTo(0, 0); }
        else if (act === 'submit-design') { submitDesign(); }
        else if (act === 'go-sublimation') { cacheFields(); state.view = 'sublimation'; state.momentecCaptured = false; state.message = null; render(); window.scrollTo(0, 0); }
        else if (act === 'submit-momentec') { submitMomentec(); }
        else if (act === 'momentec-restart') { state.momentecCaptured = false; state.momentecRef = ''; state.message = null; render(); }
    }

    function jumpToPage() {
        var inp = document.getElementById('bsgPageJump');
        var n = inp && parseInt(inp.value, 10);
        if (n) { gotoPage(n); }
    }

    function addDetailToCart() {
        var p = state.detail;
        if (!p || !(p.stock && p.stock.orderable)) { return; }
        var qtyEl = document.getElementById('pdQty');
        var qty = qtyEl ? (parseInt(qtyEl.value, 10) || 1) : 1;
        // Use the volume-tier unit price for the chosen quantity (base price if no tiers).
        addLine(p.id, p.name, tierPrice(p, qty), qty, readPersonalization());
        commitDeco(p);
        state.detail = null; state.detailLoading = false;
        openDrawer();
    }

    function addSelectedVariants() {
        var p = state.detail;
        if (!p) { return; }
        var player = readPersonalization();
        var inputs = document.querySelectorAll('.vqty');
        var added = 0;
        inputs.forEach(function (inp) {
            var qty = parseInt(inp.value, 10) || 0;
            if (qty > 0) {
                addLine(inp.getAttribute('data-vid'),
                    p.name + ' — ' + inp.getAttribute('data-vlabel'),
                    inp.getAttribute('data-vprice'), qty, player);
                added += qty;
            }
        });
        if (!added) { return; }
        commitDeco(p);
        state.detail = null; state.detailLoading = false;
        openDrawer();
    }

    function openDrawer() { cacheFields(); state.drawerOpen = true; state.message = null; render(); }

    function openDetail(id) {
        // Fresh decoration canvas per product view (a committed deco is kept).
        state.logoMock = null; state.textMock = null;
        state.subj = null; state.subjFor = '';
        // Use the already-listed product for an instant open, then refresh with the
        // fuller detail (full description) from the server.
        var listed = state.products.filter(function (x) { return String(x.id) === String(id); })[0];
        state.detail = listed || null;
        state.detailLoading = !listed;
        state.gal = listed ? buildGallery(listed) : null;
        track('product_view', { ref: (listed && listed.name) || id });
        render();
        api('product', { id: id }).then(function (res) {
            state.detailLoading = false;
            if (res && res.ok && res.product) {
                var d = res.product;
                // Keep the grid card's already-resolved image if the detail lookup
                // came back without one: the catalog list resolves images in a
                // batched attached-file pass that a single-item detail can miss, so
                // the picture must not vanish when the fuller detail replaces the card.
                if (listed && listed.image) {
                    if (!d.image) { d.image = listed.image; }
                    if (!d.images || !d.images.length) { d.images = [listed.image]; }
                }
                state.detail = d;
                state.gal = buildGallery(d);
            }
            render();
        }).catch(function () { state.detailLoading = false; render(); });
    }

    // ------------------------------------------------------------- actions --

    function loadProducts() {
        state.loading = true; state.loadError = null; render();
        var params = { page: state.page, q: state.q, size: state.pageSize };
        Object.keys(state.filters).forEach(function (k) { if (state.filters[k]) { params[k] = state.filters[k]; } });
        api('products', params).then(function (res) {
            state.loading = false;
            if (res && res.ok) {
                state.products = res.products || [];
                state.broaden = res.broaden || null;
                state.page = res.page || 1;
                state.pageCount = res.pageCount || 1;
                state.total = res.total || 0;
            } else {
                state.products = []; state.pageCount = 1; state.total = 0;
                state.loadError = (res && res.error) || 'Unexpected response from the server.';
            }
            render();
        }).catch(function (e) {
            state.loading = false; state.products = []; state.pageCount = 1; state.total = 0;
            state.loadError = 'Could not reach the store service (' + ((e && e.message) || 'network error') + ').';
            render();
        });
    }

    function gotoPage(p) {
        if (p < 1 || p > state.pageCount) { return; }
        state.page = p; loadProducts();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function doSchoolSearch(q) {
        if (!q || q.length < (BOOT.minSchoolChars || 3)) { state.schoolResults = []; render(); return; }
        api('schools', { q: q }).then(function (res) {
            state.schoolResults = (res && res.ok) ? res.schools : [];
            render();
        });
    }

    // Catalog search behind the roster item picker. Re-renders drop focus (and the
    // school input grabs it back in wire()), so restore it to this input after.
    function refocusRosterInput() {
        var e = document.getElementById('tmRosterItem');
        if (e) {
            e.focus({ preventScroll: true });
            try { e.setSelectionRange(e.value.length, e.value.length); } catch (err) { /* number/hidden inputs */ }
        }
    }

    function doRosterProdSearch(q) {
        var v = String(q || '').trim();
        if (v.length < 2) {
            if (state.rosterProdResults.length) { state.rosterProdResults = []; render(); refocusRosterInput(); }
            return;
        }
        api('products', { q: v, size: 25 }).then(function (res) {
            if (state.rosterProdQ !== q) { return; } // stale response
            state.rosterProdResults = (res && res.ok) ? (res.products || []) : [];
            render();
            refocusRosterInput();
        });
    }

    function submit() {
        cacheFields();
        var isOrder = state.submitType === 'order';
        if (!state.school) { return setMsg('err', 'Please select your school.'); }
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(fieldVal('email'))) { return setMsg('err', 'Please enter a valid email.'); }
        if (isOrder && !fieldVal('po').trim()) { return setMsg('err', 'A PO number is required for an order. Or choose "Request a Quote".'); }
        var items = Object.keys(state.cart).map(function (key) {
            var c = state.cart[key];
            return { id: c.id, qty: c.qty, name: c.name, playerName: c.playerName || '', playerNumber: c.playerNumber || '' };
        });
        if (!items.length) { return setMsg('err', 'Your cart is empty.'); }

        state.submitting = true; state.message = null; render();
        var payload = {
            action: 'submit', type: state.submitType, schoolId: state.school.id,
            name: fieldVal('name'), email: fieldVal('email'), po: fieldVal('po'),
            notes: fieldVal('notes'), items: items
        };
        // Customer decoration (logo/text mockup built on the product page) rides
        // along as a design summary + mockup attachment for the rep.
        if (state.deco) {
            var dparts = [];
            if (state.deco.logoDataUrl) { dparts.push('customer logo at ' + (state.deco.placement || 'custom position')); }
            if (state.deco.text) { dparts.push('text "' + state.deco.text + '"' + (state.deco.textColor ? ' in ' + state.deco.textColor : '')); }
            var attachNote = state.deco.mockup
                ? '. Mockup attached.'
                : (state.deco.logoDataUrl ? '. Logo file attached (see placement above).' : '. See description (no mockup could be generated).');
            payload.design = {
                garment: state.deco.item + (state.deco.itemNumber ? ' (#' + state.deco.itemNumber + ')' : ''),
                summary: 'Decoration request: ' + dparts.join(', ') + attachNote
            };
            payload.mockup = state.deco.mockup || state.deco.logoDataUrl || '';
        }
        apiPost(payload).then(function (res) {
            state.submitting = false;
            if (res && res.ok) {
                track(isOrder ? 'submit_order' : 'submit_quote',
                    { ref: res.docNumber || '', value: cartTotal(), detail: items.length + ' line(s)' });
                state.cart = {}; saveCart(); state.deco = null;
                fieldCache = {};
                state.drawerOpen = false;
                render();
                showConfirmation(res);
            } else {
                setMsg('err', (res && res.error) || 'Something went wrong. Please try again.');
            }
        }).catch(function () { state.submitting = false; setMsg('err', 'Network error. Please try again.'); });
    }

    function setMsg(type, text) { state.message = { type: type, text: text }; render(); }

    function showConfirmation(res) {
        var root = document.getElementById('bsgshopRoot');
        var banner = document.createElement('div');
        banner.className = 'bsg-msg ok';
        banner.style.cssText = 'position:fixed;left:50%;top:24px;transform:translateX(-50%);z-index:60;max-width:520px;box-shadow:0 6px 24px rgba(0,0,0,.2);padding:16px 20px;font-size:14px';
        banner.innerHTML = '<strong>' + esc(res.docType) + ' received' + (res.docNumber ? ' &mdash; ' + esc(res.docNumber) : '') +
            '!</strong><br>A confirmation was emailed to you. A BSG rep will follow up shortly.';
        document.body.appendChild(banner);
        setTimeout(function () { banner.style.transition = 'opacity .5s'; banner.style.opacity = '0'; }, 6000);
        setTimeout(function () { if (banner.parentNode) { banner.parentNode.removeChild(banner); } }, 6800);
    }

    // Facet options are scoped server-side to the current selection, so this must
    // carry the active filters AND re-run whenever they change -- otherwise the
    // rail would keep offering combinations that no longer match anything.
    function loadFilters() {
        var p = {};
        Object.keys(state.filters).forEach(function (k) {
            if (state.filters[k]) { p[k] = state.filters[k]; }
        });
        api('filters', p).then(function (res) {
            if (res && res.ok && res.filters && res.filters.length) {
                state.filterDefs = res.filters;
                render();
            }
        }).catch(function () { /* filters are optional */ });
    }

    function filterLabelFor(key) {
        for (var i = 0; i < state.filterDefs.length; i++) {
            if (state.filterDefs[i].key === key) { return state.filterDefs[i].label; }
        }
        return key;
    }

    // ------------------------------------------------------------- startup --

    if (BOOT.momentec) {
        window.addEventListener('message', handleMomentecMessage, false);
    }
    // Escape closes the product modal, then the cart drawer (accessibility).
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' && e.keyCode !== 27) { return; }
        if (state.detail || state.detailLoading) { handleAct('close-detail'); }
        else if (state.drawerOpen) { handleAct('close-cart'); }
    });
    render();
    loadProducts();
    loadFilters();
})();
