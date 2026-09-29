from odoo import fields, models

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    intercompany_transit_location_id = fields.Many2one(
        "stock.location",
        string="Inter-Company Transit Location",
        domain="[('usage', '=', 'transit'), ('company_id', '=', False)]",
        config_parameter="intercompany_stock_transfer.transit_location_id",
        help=(
            "Shared transit location used between companies. It must have no company "
            "assigned so it can be used by multiple companies."
        ),
    )
