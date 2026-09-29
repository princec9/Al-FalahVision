from odoo import fields, models
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_insurance = fields.Boolean(
        string="Insurance Company"
    )
    # customer_company = fields.Char("Company")
