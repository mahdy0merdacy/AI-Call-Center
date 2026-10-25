// Initial structure of the documents database (MongoDB 7). No data.
//
// Runs once, when the data volume is empty (/docker-entrypoint-initdb.d), with the
// root credentials. It creates exactly what `manage.py migrate` would create from
// documents/migrations/0001_initial.py (same collections, index names and options),
// plus the matching django_migrations record, so Django knows the migration is applied
// and the app works without running migrate.
//
// The app's own user is still created by the `mongo-setup` job (docker/mongo-setup.js),
// which is idempotent and also applies password changes.
//
// Regenerate this file whenever a migration is added (see ../../README.md).

const dbName = process.env.MONGODB_DB || 'documents';
const target = db.getSiblingDB(dbName);

// String-typed unique indexes: django-mongodb-backend makes them partial so that
// missing/null values do not collide.
const uniqueString = (field) => ({
  unique: true,
  partialFilterExpression: { [field]: { $type: 'string' } },
});

target.createCollection('documents_document');
target.documents_document.createIndex(
  { id: 1 },
  { name: 'documents_document_id_d673acfb_uniq', ...uniqueString('id') },
);
target.documents_document.createIndex(
  { ingestion_status: 1 },
  { name: 'documents_document_ingestion_status_87045d54' },
);
target.documents_document.createIndex(
  { tenant_id: 1, created_at: -1 },
  { name: 'documents_d_tenant__5d0994_idx' },
);

target.createCollection('documents_sessionlink');
target.documents_sessionlink.createIndex(
  { id: 1 },
  { name: 'documents_sessionlink_id_3e60fb5d_uniq', ...uniqueString('id') },
);
target.documents_sessionlink.createIndex(
  { token: 1 },
  { name: 'documents_sessionlink_token_7cd544ca_uniq', ...uniqueString('token') },
);
target.documents_sessionlink.createIndex(
  { tenant_id: 1, created_at: -1 },
  { name: 'documents_s_tenant__059748_idx' },
);

// Applied migrations (metadata only, not application data).
target.createCollection('django_migrations');
target.django_migrations.insertOne({ app: 'documents', name: '0001_initial', applied: new Date() });

print(`initialized ${dbName}: ${target.getCollectionNames().sort().join(', ')}`);
