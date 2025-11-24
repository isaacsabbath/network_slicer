from django.apps import AppConfig


class SlicerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'slicer'

    def ready(self):
        # Metrics/signals removed per project simplification.
        return
