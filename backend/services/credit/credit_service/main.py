from cds_common.web import create_app

from .api import router
from .cds.api import calendars as cds_calendars
from .cds.api import router as cds_router
from .config import get_settings
from .iss import IssClient
from .market import MarketData

settings = get_settings()
app = create_app(settings, [router, cds_router, cds_calendars], title="CDS — кредитные кривые и CDS")
app.state.iss = IssClient(
    settings.iss_base_url,
    requests_per_second=settings.iss_requests_per_second,
    timeout=settings.iss_timeout_seconds,
    verify=settings.iss_ca_bundle or True,
)
app.state.market = MarketData(app.state.db.session_factory, settings, app.state.iss, settings.iss_delay_minutes)
