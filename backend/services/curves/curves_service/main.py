from cds_common.web import create_app, mount_public_api

from .api import router
from .config import get_settings
from .public_api import router as public_router

app = create_app(get_settings(), [router], title="CDS — процентные кривые")
mount_public_api(
    app, [public_router], title="CDS — открытый API процентных кривых",
    description=(
        "Публикация модельных мидов процентных свопов: par-ставки свопов на котируемых сроках по построенным кривым — "
        "онлайн (текущая кривая) и за дату (кривая, действовавшая на дату). Только чтение, авторизация не нужна. "
        "Ставки — в процентах годовых."
    ),
)
