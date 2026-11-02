# -*- coding: utf-8 -*-
def migrate(cr, version):
    """Sale order attribution levels no longer exist: map them to the invoice ones."""
    cr.execute("UPDATE sale_commission_plan SET commission_based_on = 'account_move' "
               "WHERE commission_based_on IS NULL OR commission_based_on = 'sale_order'")
    cr.execute("UPDATE sale_commission_plan SET commission_based_on = 'account_move_line' "
               "WHERE commission_based_on = 'sale_order_line'")
