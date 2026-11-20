from odoo import fields, models


class VendorInOutReport(models.Model):
    """Live report (database view): one row per vendor, product and month."""
    _name = 'vendor.in.out.report'
    _description = 'Vendor In/Out Report'
    _auto = False
    _rec_name = 'product_id'
    _order = 'partner_id, product_id'

    partner_id = fields.Many2one('res.partner', string='Vendor', readonly=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    product_tmpl_id = fields.Many2one('product.template', string='Product Template', readonly=True)
    categ_id = fields.Many2one('product.category', string='Product Category', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    date = fields.Date(string='Month', readonly=True)
    qty_purchased = fields.Float(
        string='Purchased', readonly=True, aggregator='sum', digits='Product Unit',
        help="Receipts from the vendor minus returns to the vendor.")
    qty_adjusted = fields.Float(
        string='Adjustments', readonly=True, aggregator='sum', digits='Product Unit',
        help="Inventory adjustments (+/-) and manufacturing.")
    qty_sold = fields.Float(
        string='Sold', readonly=True, aggregator='sum', digits='Product Unit',
        help="Deliveries to customers minus customer returns.")
    qty_available = fields.Float(
        string='Available', readonly=True, aggregator='sum', digits='Product Unit',
        help="Purchased + Adjustments - Sold.")

    @property
    def _table_query(self):
        return """
            SELECT row_number() OVER () AS id,
                   l.tracking_vendor_id AS partner_id,
                   l.product_id AS product_id,
                   pp.product_tmpl_id AS product_tmpl_id,
                   pt.categ_id AS categ_id,
                   l.company_id AS company_id,
                   date_trunc('month', l.date)::date AS date,
                   SUM(CASE l.move_kind
                           WHEN 'in' THEN l.quantity_product_uom
                           WHEN 'vendor_return' THEN -l.quantity_product_uom
                           ELSE 0 END) AS qty_purchased,
                   SUM(CASE WHEN l.move_kind IN ('adjust_in', 'adjust_out')
                            THEN l.qty_signed ELSE 0 END) AS qty_adjusted,
                   SUM(CASE l.move_kind
                           WHEN 'out' THEN l.quantity_product_uom
                           WHEN 'customer_return' THEN -l.quantity_product_uom
                           ELSE 0 END) AS qty_sold,
                   SUM(l.qty_signed) AS qty_available
              FROM stock_move_line l
              JOIN product_product pp ON pp.id = l.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE l.state = 'done'
               AND l.move_kind IS NOT NULL
             GROUP BY l.tracking_vendor_id, l.product_id, pp.product_tmpl_id,
                      pt.categ_id, l.company_id, date_trunc('month', l.date)
        """
