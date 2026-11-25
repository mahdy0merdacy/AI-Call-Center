// One-shot, idempotent: make sure the application's own MongoDB user exists.
// Run by the `mongo-setup` service in docker-compose.yml with the ROOT credentials; the
// app itself never sees them. The user can only read and write ONE database (its own),
// so a leaked app password cannot reach other databases, users or server settings.
//
// Safe to re-run: creates the user the first time, resets password/roles afterwards.
// (A plain /docker-entrypoint-initdb.d script would only run on an empty volume.)

const dbName = process.env.APP_DB;
const user = process.env.APP_USER;
const pwd = process.env.APP_PASSWORD;

if (!dbName || !user || !pwd) {
  print('APP_DB, APP_USER and APP_PASSWORD must be set');
  quit(1);
}

const target = db.getSiblingDB(dbName);
const roles = [{ role: 'readWrite', db: dbName }];

if (target.getUser(user)) {
  target.updateUser(user, { pwd, roles });
  print(`updated user ${user} on ${dbName}`);
} else {
  target.createUser({ user, pwd, roles });
  print(`created user ${user} on ${dbName}`);
}
