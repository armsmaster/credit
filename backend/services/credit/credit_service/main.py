from cds_common.web import create_app, mount_public_api

from .api import router
from .cds.api import calendars as cds_calendars
from .cds.api import router as cds_router
from .cds.trades_api import router as trades_router
from .config import get_settings
from .iss import IssClient
from .market import MarketData
from .public_api import router as public_router

settings = get_settings()
app = create_app(settings, [router, trades_router, cds_router, cds_calendars], title="CDS — кредитные кривые, CDS и сделки")
mount_public_api(
    app, [public_router], title="CDS — открытый API котировок CDS",
    description=(
        "Публикация модельных котировок CDS на стандартных сроках: par spread (модельный мид), QS и PUF при стандартных "
        "купонах стандартных контрактов СПФИ по CDS-кривым контрольных лиц — онлайн (текущая кривая) и за дату "
        "(кривая, действовавшая на дату). Только чтение, авторизация не нужна."
    ),
)
app.state.iss = IssClient(
    settings.iss_base_url,
    requests_per_second=settings.iss_requests_per_second,
    timeout=settings.iss_timeout_seconds,
    ca_bundle=settings.iss_ca_bundle,
)
app.state.market = MarketData(app.state.db.session_factory, settings, app.state.iss, settings.iss_delay_minutes)
