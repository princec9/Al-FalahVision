# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Plans still holding the OLD default template (sale order wording) get the
    new default; formulas the user has edited (no old marker) are not touched."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    Plan = env['sale.commission.plan'].with_context(active_test=False)
    new = Plan.new({})._default_commission_python_compute()
    old = Plan.search([('commission_python_compute', 'ilike',
                        "Sale Order = untaxed total of the partner's orders")])
    old.write({'commission_python_compute': new})
