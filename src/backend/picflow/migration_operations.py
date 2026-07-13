from django.conf import settings
from django.db.migrations.operations.base import Operation

BEGIN_LOCK_TIMEOUT = "BEGIN; SET LOCAL lock_timeout = '2s';"
COMMIT = "COMMIT;"


class AddNotValidUploadedByForeignKey(Operation):
    reduces_to_sql = True
    reversible = True

    def state_forwards(self, app_label, state):
        del app_label, state

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        del app_label, from_state
        user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".", 1)
        historical_user = to_state.apps.get_model(user_app_label, user_model_name)
        referenced_table = schema_editor.quote_name(historical_user._meta.db_table)
        referenced_pk = schema_editor.quote_name(historical_user._meta.pk.column)
        schema_editor.execute(
            f"{BEGIN_LOCK_TIMEOUT} "
            "ALTER TABLE picflow_photo ADD CONSTRAINT "
            "picflow_photo_uploaded_by_fk FOREIGN KEY (uploaded_by_id) "
            f"REFERENCES {referenced_table} ({referenced_pk}) "
            f"DEFERRABLE INITIALLY DEFERRED NOT VALID; {COMMIT}"
        )

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        del app_label, from_state, to_state
        schema_editor.execute(
            f"{BEGIN_LOCK_TIMEOUT} "
            "ALTER TABLE picflow_photo DROP CONSTRAINT "
            f"picflow_photo_uploaded_by_fk; {COMMIT}"
        )

    def describe(self):
        return "Add the Photo uploader foreign key without validating existing rows"
