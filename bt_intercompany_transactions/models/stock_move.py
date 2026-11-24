from collections import defaultdict

from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)

        products_by_company = defaultdict(lambda: self.env["product.product"])

        for move in moves:
            purchase_order = move.purchase_line_id.order_id
            picking_company = move.picking_id.company_id
            if (
                not purchase_order
                or not picking_company
                or not move.is_in
                or picking_company == purchase_order.company_id
            ):
                continue

            products_by_company[picking_company] |= move.product_id

        for company, products in products_by_company.items():
            products.with_company(company)._update_standard_price()

        return moves
