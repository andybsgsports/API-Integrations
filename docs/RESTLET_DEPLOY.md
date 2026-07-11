# Deploying the matrix RESTlet (and firing one real child)

The matrix RESTlet (`suitescript/bsg_sanmar_matrix.js`) creates NetSuite matrix
**children** under existing parents. It's the one path that handles
**numeric-style parents** (e.g. `2000`) correctly: it resolves the parent by
name in SuiteScript search, which — unlike the CSV Import Assistant — never
mistakes a numeric name for an internal id. See the "why" in
[`NETSUITE_SETUP.md`](NETSUITE_SETUP.md).

This is a short, do-it-once checklist to deploy the script and fire a single
`2000` child so you can watch it land.

---

## A. Deploy the script in NetSuite (admin, ~5 min)

1. **Upload the file.** Documents > Files > File Cabinet → SuiteScripts folder →
   **Add File** → upload `suitescript/bsg_sanmar_matrix.js`.
2. **Create the Script record.** Customization > Scripting > Scripts > **New** →
   select the uploaded file → **Create Script Record** → type **RESTlet**.
   - On the **Scripts** subtab, confirm the **POST Function** is `post`.
   - Name it e.g. `BSG SanMar Matrix`. **Save.**
   - Note the **Script ID** (e.g. `customscript_bsg_sanmar_matrix`) — and the
     numeric **internal id** shown in the URL; either works as `script=`.
3. **Deploy it.** On the script record → **Deployments** subtab → **New
   Deployment**:
   - **Status** = `Released`
   - **Log Level** = `Debug` (for the first run)
   - **Roles** = the role your integration token uses (or `Administrator` to
     start). **Save.**
   - Note the **Deployment ID** (e.g. `customdeploy1`) and the **External URL**
     at the bottom — it contains `script=…&deploy=…`.
4. **Permissions.** The integration role needs: **Inventory Item** = Full,
   **Lists / Items** access, and **Custom Lists** (for the color/size matrix
   lists). If a run errors with `INSUFFICIENT_PERMISSION`, this is why.

## B. Point the tool at the deployment

Add to `.env` (the script/deploy ids from step A; the host is derived from your
account id automatically):

```bash
NETSUITE_MATRIX_SCRIPT_ID=customscript_bsg_sanmar_matrix   # or the numeric id
NETSUITE_MATRIX_DEPLOY_ID=customdeploy1
# NETSUITE_RESTLET_BASE=https://1234567-sb1.restlets.api.netsuite.com  # optional override
```

## C. Dry-run the payload (no NetSuite call)

`SYNC_DRY_RUN` defaults to **true**, so this just prints the JSON it *would*
send — a good last look before any write:

```bash
sanmar-sync push-children --file downloads/SanMar_SDL_N.csv --style 2000 --limit 1
```

You should see one item with `"style": "2000"`, the child `itemId`, color, size,
accounts, and price.

## D. Fire one real child (sandbox first)

Flip the safety off **in the sandbox account**, then run the same command:

```bash
# .env: SYNC_DRY_RUN=false   (and NETSUITE_ACCOUNT_ID pointing at the sandbox)
sanmar-sync push-children --file downloads/SanMar_SDL_N.csv --style 2000 --limit 1
```

Expected output:

```
SANMAR-<key>             created  id=<new internal id>

1 item(s): 1 ok, 0 error(s).
```

## E. Verify in NetSuite

Open the parent item **2000** → its **Matrix** subtab → the new color/size cell
is now populated and links to the child you just created. That's `2000` landing
through the path that the CSV importer couldn't do.

---

### Going to production

Only after sandbox sign-off: point `NETSUITE_ACCOUNT_ID` at production, set
`NETSUITE_ALLOW_PRODUCTION_WRITES=true`, redeploy the script in the production
account (the script/deploy ids differ per account), and update `.env`. Drop
`--limit` to push the full backlog of numeric-style children; existing children
are updated (matched by external id), not duplicated.
