from odoo import fields, models
from odoo.tools import SQL


class PurchaseReport(models.Model):
    _inherit = 'purchase.report'

    brand_id = fields.Many2one('product.brand', string='Brand', readonly=True)
    short_code = fields.Char(string='Short Code', readonly=True)

    def _select(self):
        return SQL(
            "%s, t.brand_id AS brand_id, rc_code.short_code AS short_code",
            super()._select(),
        )

    def _from(self):
        return SQL(
            "%s LEFT JOIN res_company rc_code ON rc_code.id = po.company_id",
            super()._from(),
        )

    def _group_by(self):
        return SQL("%s, t.brand_id, rc_code.short_code", super()._group_by())