from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    store_in_charge_id = fields.Many2one(
        "res.partner",
        string="Store In Charge",
        domain="[('employee_ids', '!=', False)]",
    )
