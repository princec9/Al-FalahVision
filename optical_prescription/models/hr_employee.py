from odoo import models, fields, _


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    is_optometrist = fields.Boolean(string="Optometrist", default=False)
    is_technician = fields.Boolean(string="Technician", default=False)
    license_key = fields.Char(
        string="License ",

    )

    sale_discount_limit = fields.Float(
        string="Max Sale Discount Limit (%)",
        default=0.0,
        help="Maximum percentage of discount this employee can approve.",
    )
    discount_secret_key = fields.Char(
        string="Discount Secret Key",
        copy=False,
        groups="sales_team.group_sale_manager",
        help="Secret key required to approve extra line discounts.",
    )
    
