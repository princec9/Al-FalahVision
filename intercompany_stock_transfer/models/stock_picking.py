from odoo import fields, models
from odoo import models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        res = super().button_validate()

        # Only continue when validation has actually completed
        for picking in self:
            if (
                    picking.state == "done"
                    and picking.intercompany_transfer_id
            ):
                transfer = picking.intercompany_transfer_id

                if transfer.state == "confirmed":
                    transfer.action_send_to_transit()

        return res

    intercompany_transfer_id = fields.Many2one(
        "intercompany.stock.transfer",
        string="Inter-Company Transfer",
        copy=False,
        index=True,
        ondelete="set null",
    )


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    code = fields.Char('Short Name', size=12, required=True)