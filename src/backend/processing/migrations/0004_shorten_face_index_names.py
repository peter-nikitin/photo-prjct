from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("processing", "0003_add_preview_derivative_schema"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        DO $$
                        BEGIN
                            IF to_regclass('proc_face_det_attempt_idx') IS NULL THEN
                                IF to_regclass('proc_face_detection_attempt_idx') IS NOT NULL THEN
                                    ALTER INDEX proc_face_detection_attempt_idx
                                        RENAME TO proc_face_det_attempt_idx;
                                ELSIF to_regclass('proc_face_detect_attempt_idx') IS NOT NULL THEN
                                    ALTER INDEX proc_face_detect_attempt_idx
                                        RENAME TO proc_face_det_attempt_idx;
                                ELSE
                                    RAISE EXCEPTION
                                        'missing face detection source index';
                                END IF;
                            END IF;

                            IF to_regclass('proc_face_embed_det_idx') IS NULL THEN
                                IF to_regclass('proc_face_embedding_detection_idx') IS NOT NULL THEN
                                    ALTER INDEX proc_face_embedding_detection_idx
                                        RENAME TO proc_face_embed_det_idx;
                                ELSIF to_regclass('proc_face_embed_detection_idx') IS NOT NULL THEN
                                    ALTER INDEX proc_face_embed_detection_idx
                                        RENAME TO proc_face_embed_det_idx;
                                ELSE
                                    RAISE EXCEPTION
                                        'missing face embedding source index';
                                END IF;
                            END IF;
                        END $$;
                    """,
                    reverse_sql="""
                        DO $$
                        BEGIN
                            IF to_regclass('proc_face_detection_attempt_idx') IS NULL THEN
                                IF to_regclass('proc_face_det_attempt_idx') IS NOT NULL THEN
                                    ALTER INDEX proc_face_det_attempt_idx
                                        RENAME TO proc_face_detection_attempt_idx;
                                ELSE
                                    RAISE EXCEPTION
                                        'missing current face detection index';
                                END IF;
                            END IF;

                            IF to_regclass('proc_face_embedding_detection_idx') IS NULL THEN
                                IF to_regclass('proc_face_embed_det_idx') IS NOT NULL THEN
                                    ALTER INDEX proc_face_embed_det_idx
                                        RENAME TO proc_face_embedding_detection_idx;
                                ELSE
                                    RAISE EXCEPTION
                                        'missing current face embedding index';
                                END IF;
                            END IF;
                        END $$;
                    """,
                ),
            ],
            state_operations=[
                migrations.RenameIndex(
                    model_name="photofacedetection",
                    old_name="proc_face_detection_attempt_idx",
                    new_name="proc_face_det_attempt_idx",
                ),
                migrations.RenameIndex(
                    model_name="faceembedding",
                    old_name="proc_face_embedding_detection_idx",
                    new_name="proc_face_embed_det_idx",
                ),
            ],
        ),
    ]
