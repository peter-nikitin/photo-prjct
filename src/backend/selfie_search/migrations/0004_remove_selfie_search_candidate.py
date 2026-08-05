from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("selfie_search", "0003_result_provenance_and_clusters")]

    operations = [migrations.DeleteModel(name="SelfieSearchCandidate")]
