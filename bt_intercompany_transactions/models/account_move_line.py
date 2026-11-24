# models/account_move_line.py

from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    intercompany_purchase_line_id = fields.Many2one(
        "purchase.order.line",
        string="Intercompany Purchase Line",
        copy=False,
        index=True,
    )

    def _compute_account_id(self):
        super()._compute_account_id()

        for line in self:
            purchase_line = (
                line.purchase_line_id
                or line.intercompany_purchase_line_id
            )
            purchase_order = purchase_line.order_id
            picking_company = purchase_order.picking_type_id.warehouse_id.company_id

            if (
                not purchase_order
                or not picking_company
                or picking_company == purchase_order.company_id
                or line.display_type != "product"
                or not line.product_id
                or not line.move_id.is_purchase_document(include_receipts=True)
            ):
                continue

            accounts = line.with_company(
                line.company_id
            ).product_id.product_tmpl_id.get_product_accounts(
                fiscal_pos=line.move_id.fiscal_position_id
            )

            if accounts["expense"]:
                line.account_id = accounts["expense"]
