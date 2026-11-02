# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Plans still holding the previous default template get the new one;
    edited formulas are not touched."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    Plan = env['sale.commission.plan'].with_context(active_test=False)
    new = Plan.new({})._default_commission_python_compute()
    old = Plan.search([('commission_python_compute', 'ilike',
                        "Invoice Line = THIS invoice line only")])
    old.write({'commission_python_compute': new})
