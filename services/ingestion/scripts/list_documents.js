// List every uploaded document (all tenants) with its ingestion status.
// READ-ONLY: changes nothing.
//
// Run from services/documents (where the "mongo" service is), as ONE line:
//   Get-Content ../ingestion/scripts/list_documents.js | docker compose exec -T mongo sh -c 'cat > /tmp/s.js && mongosh --quiet -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin documents /tmp/s.js'
//
// Optional filter (environment variable, add before "mongo"):
//   -e STATUS=pending      only documents with this status

const filter = {};
if (process.env.STATUS) {
  filter.ingestion_status = process.env.STATUS;
}

const docs = db.documents_document
  .find(filter, {
    id: 1, tenant_id: 1, original_filename: 1, ingestion_status: 1,
    error_message: 1, created_at: 1, _id: 0,
  })
  .sort({ tenant_id: 1, created_at: 1 })
  .toArray();

const cell = (value, width) => String(value ?? '').slice(0, width).padEnd(width);

print(
  cell('DOCUMENT ID', 34) + cell('TENANT ID', 34) + cell('STATUS', 12)
  + cell('FILE', 32) + 'ERROR',
);
for (const d of docs) {
  print(
    cell(d.id, 34) + cell(d.tenant_id, 34) + cell(d.ingestion_status, 12)
    + cell(d.original_filename, 32) + (d.error_message || ''),
  );
}

// Totals per status, e.g. "completed: 3, failed: 3".
const totals = {};
for (const d of docs) {
  totals[d.ingestion_status] = (totals[d.ingestion_status] || 0) + 1;
}
print(`\n${docs.length} document(s). ` + Object.entries(totals).map(([s, n]) => `${s}: ${n}`).join(', '));
