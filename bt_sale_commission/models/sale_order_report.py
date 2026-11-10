from odoo import fields, models


class SaleReport(models.Model):
    _inherit = 'sale.report'

    order_salesperson_partner_id = fields.Many2one(
        'res.partner', string='Store in Charge', readonly=True)
    line_user_id = fields.Many2one('res.users', string='Sales Staff', readonly=True)
    salesperson_partner_id = fields.Many2one(
        'res.partner', string='Line Sales Person', readonly=True)
    brand_id = fields.Many2one('product.brand', string='Brand', readonly=True)
    warehouse_code = fields.Char(string='Location', readonly=True)
    down_payment_amount = fields.Monetary('Down Payment', readonly=True)

    def _select_additional_fields(self):
        res = super()._select_additional_fields()
        res['order_salesperson_partner_id'] = "s.salesperson_partner_id"
        res['salesperson_partner_id'] = "l.salesperson_partner_id"
        res['brand_id'] = "t.brand_id"
        res['warehouse_code'] = "sale_wh.code"
        res['down_payment_amount'] = """
            CASE WHEN l.is_downpayment THEN SUM(l.price_unit) ELSE 0 END
        """
        return res

    def _from_sale(self):
        res = super()._from_sale()
        res += " LEFT JOIN stock_warehouse sale_wh ON sale_wh.id = s.warehouse_id"
        return res

    def _group_by_sale(self):
        res = super()._group_by_sale()
        res += (", s.salesperson_partner_id, l.salesperson_partner_id,"
                " t.brand_id, sale_wh.code")
        return res
