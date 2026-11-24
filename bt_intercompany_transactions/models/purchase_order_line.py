from collections import defaultdict

from odoo import models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _prepare_qty_invoiced(self):

        qtys = super()._prepare_qty_invoiced()

        lines_by_company = defaultdict(lambda: self.env["purchase.order.line"])
        for line in self.filtered(
            lambda l: (
                l.order_id.is_intercompany_bill
                and l.order_id.company_to_bill_id
            )
        ):
            lines_by_company[line.order_id.company_to_bill_id] |= line

        mapped_qty = {}
        AccountMoveLine = self.env["account.move.line"].sudo()

        for company, lines in lines_by_company.items():
            data = AccountMoveLine.with_company(company).read_group(
                [
                    ("company_id", "=", company.id),
                    ("intercompany_purchase_line_id", "in", lines.ids),
                    ("move_id.state", "!=", "cancel"),
                    ("move_id.move_type", "=", "in_invoice"),
                ],
                ["intercompany_purchase_line_id", "quantity:sum"],
                ["intercompany_purchase_line_id"],
            )

            mapped_qty.update({
                d["intercompany_purchase_line_id"][0]: d["quantity"]
                for d in data
            })

        for line in self.filtered(
            lambda l: l.order_id.is_intercompany_bill
        ):
            qtys[line] = mapped_qty.get(line.id, 0.0)

        return qtys


    def _prepare_stock_move_vals(
            self,
            picking,
            price_unit,
            product_uom_qty,
            product_uom,
        ):

            vals = super()._prepare_stock_move_vals(
                picking,
                price_unit,
                product_uom_qty,
                product_uom,
            )

            vals.update({
                'company_id': picking.company_id.id,
            })

            return vals
