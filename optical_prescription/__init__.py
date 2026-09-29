from . import models
from . import wizard
# Dormant. report/achievement_report.py rewrites the commission SQL to
# attribute achievements per Sale Order Line. It is intentionally NOT
# imported: the current requirement forbids changing Odoo's commission
# flow. Uncomment only if that decision is reversed.
# from . import report
