// Change the ingestion status of chosen documents (dev/test tool).
//
// Uses: mark old test uploads "failed" so the worker skips them, or put
// documents back to "pending" so the worker processes them again.
//
// SAFE BY DEFAULT: without CONFIRM=yes it only SHOWS what would change.
//
// Run from services/documents (where the "mongo" service is), as ONE line:
//   Get-Content ../ingestion/scripts/set_status.js | docker compose exec -T -e TO=pending -e FILES=stock_document.pdf mongo sh -c 'cat > /tmp/s.js && mongosh --quiet -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin documents /tmp/s.js'
// Then the same line with  -e CONFIRM=yes  added to really change them.
//
// Environment variables (add each as  -e NAME=value  before "mongo"):
//   TO=pending|failed      required: the new status
//   FILES=a.pdf,b.pdf      documents with these file names (original_filename), OR
//   IDS=5c33...,6aeb...    documents with these ids (32 hex chars, as in list_documents.js)
//   ONLY_STATUS=pending    optional: only documents currently in this status
//   MESSAGE=...            optional, for TO=failed (default below). With spaces, quote
//                          the WHOLE argument:  -e "MESSAGE=Old test upload"
//   CONFIRM=yes            really apply the change

const ALLOWED = ['pending', 'failed'];  // "completed" is only ever set by the worker
const env = process.env;
const list = (value) => (value || '').split(',').map((x) => x.trim()).filter(Boolean);

const to = env.TO;
if (!ALLOWED.includes(to)) {
  print(`TO must be one of: ${ALLOWED.join(', ')} (got: ${to || 'nothing'})`);
  quit(1);
}

const files = list(env.FILES);
const ids = list(env.IDS);
// Refuse to touch "everything": a selector is required.
if (files.length === 0 && ids.length === 0) {
  print('Give FILES=... or IDS=... (refusing to change every document).');
  quit(1);
}

const filter = {};
if (files.length) filter.original_filename = { $in: files };
if (ids.length) filter.id = { $in: ids };
if (env.ONLY_STATUS) filter.ingestion_status = env.ONLY_STATUS;

const matches = db.documents_document
  .find(filter, { id: 1, tenant_id: 1, original_filename: 1, ingestion_status: 1, _id: 0 })
  .toArray();

print(`${matches.length} document(s) match:`);
for (const d of matches) {
  print(`  ${d.id}  tenant ${d.tenant_id}  ${d.original_filename}  (${d.ingestion_status} -> ${to})`);
}

if (env.CONFIRM !== 'yes') {
  print('\nNothing changed (dry run). Add  -e CONFIRM=yes  to apply.');
  quit(0);
}

const update = { ingestion_status: to, updated_at: new Date() };
// "pending": clear the old error. "failed": say why, so the portal shows it.
update.error_message = to === 'failed'
  ? (env.MESSAGE || 'Old test upload, set to failed by hand.')
  : '';

const result = db.documents_document.updateMany(filter, { $set: update });
print(`\nChanged ${result.modifiedCount} document(s) to "${to}".`);
