"""Entry point of the credit background worker: python -m credit_service.worker"""
from cds_common.db import Database
from cds_common.logging import setup_logging
from cds_common.tasks import Worker

from .config import get_settings
from .tasks import registry


def main() -> None:
    settings = get_settings()
    setup_logging(f"{settings.service_name}-worker", settings.log_level)
    db = Database(settings.database_url, application_name=f"{settings.service_name}-worker")
    Worker(registry, db.session_factory, schedule_timezone=settings.timezone).run_forever()


if __name__ == "__main__":
    main()
