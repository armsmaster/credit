from cds_common.web import create_app

from .api import router
from .cds.api import calendars as cds_calendars
from .cds.api import router as cds_router
from .cds.trades_api import router as trades_router
from .config import get_settings
from .iss import IssClient
from .market import MarketData

settings = get_settings()
app = create_app(settings, [router, trades_router, cds_router, cds_calendars], title="CDS — кредитные кривые, CDS и сделки")
app.state.iss = IssClient(
    settings.iss_base_url,
    requests_per_second=settings.iss_requests_per_second,
    timeout=settings.iss_timeout_seconds,
    ca_bundle=settings.iss_ca_bundle,
)
app.state.market = MarketData(app.state.db.session_factory, settings, app.state.iss, settings.iss_delay_minutes)
