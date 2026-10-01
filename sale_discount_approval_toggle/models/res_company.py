from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sale_discount_approval = fields.Boolean(
        string='Salesperson Discount Approval',
        default=False,
        help="Require approval when a line discount exceeds the salesperson's limit.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sale_discount_approval = fields.Boolean(
        related='company_id.sale_discount_approval',
        readonly=False,
    )
