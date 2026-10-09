from odoo import fields, models

from odoo import fields, models, api
from odoo.fields import Domain



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


class ResCompany(models.Model):
    _inherit = "res.company"

    short_code = fields.Char(string="Short Code", size=10, copy=False, index=True)

    _short_code_uniq = models.Constraint(
        "UNIQUE(short_code)", "The company short code must be unique."
    )

    # lets users find a company by typing the code or the name in the dropdown
    _rec_names_search = ["name", "short_code"]

    @api.model
    def _search_display_name(self, operator, value):
        if operator in ("ilike", "like", "=", "=ilike", "=like") and value:
            return Domain("short_code", operator, value) | (
                    Domain("short_code", "=", False) & Domain("name", operator, value)
            )
        return super()._search_display_name(operator, value)
