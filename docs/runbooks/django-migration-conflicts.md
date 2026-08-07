# Resolving parallel Django migration conflicts

When a branch and `origin/main` both add migrations to the same Django app, preserve the migration
identities that have already been merged. Do not try to make the graph linear by changing them.

1. Fetch the current base: `git fetch origin main`.
2. List each app's leaves with `python src/backend/manage.py showmigrations` and inspect the upgrade
   with `python src/backend/manage.py migrate --plan`.
3. Preserve every migration filename from the base exactly as it is.
4. Create the convergence node with `python src/backend/manage.py makemigrations --merge`, or add
   an explicit empty merge node when Django cannot create it automatically.
5. Put any later operations in migrations after the merge node.
6. Test a database stopped at the base frontier upgrading to the candidate migration graph.

Never rename, renumber, edit, or squash a merged migration. The pull-request migration identity
check rejects changes and deletions under `src/backend/<app>/migrations/` so that environments with
the base migration already applied can upgrade safely.
