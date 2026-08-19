# Enforce feature-flag states in PostgreSQL

## Observed gap

`FeatureFlag.State` constrains Django forms and model validation, but PostgreSQL does not reject a
value outside `off`, `staff`, and `on`. A direct database write or an ORM save that bypasses
validation could therefore persist an unknown value. Runtime evaluation still fails that value
closed.

## Why this does not block the current task

Django Admin is the only supported mutation path and its model form enforces the declared choices.
Unknown values evaluate to disabled, so the gap cannot expose an unfinished feature through any
current production path.

## Revisit trigger

Add a named database `CheckConstraint` and a rejection regression before introducing another flag
mutation interface, a direct data import, or any operational path that writes flag state without
the Django Admin form.
