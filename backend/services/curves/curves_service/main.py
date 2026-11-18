from cds_common.web import create_app

from .api import router
from .config import get_settings

app = create_app(get_settings(), [router], title="CDS — процентные кривые")
