from odoo import api, fields, models

# (source location usage, destination location usage) -> kind
_KINDS = {
    ('supplier', 'internal'): 'in',
    ('internal', 'supplier'): 'vendor_return',
    ('internal', 'customer'): 'out',
    ('customer', 'internal'): 'customer_return',
    ('inventory', 'internal'): 'adjust_in',
    ('internal', 'inventory'): 'adjust_out',
    # manufacturing is treated like an adjustment
    ('production', 'internal'): 'adjust_in',
    ('internal', 'production'): 'adjust_out',
}
_PLUS = ('in', 'customer_return', 'adjust_in')


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    tracking_vendor_id = fields.Many2one(
        'res.partner', string='Vendor',
        related='product_id.product_tmpl_id.tracking_vendor_id',
        store=True, index='btree_not_null', readonly=True)
    move_kind = fields.Selection([
        ('in', 'Purchased (IN)'),
        ('vendor_return', 'Returned to Vendor'),
        ('out', 'Sold (OUT)'),
        ('customer_return', 'Customer Return'),
        ('adjust_in', 'Adjustment (+)'),
        ('adjust_out', 'Adjustment (-)'),
    ], string='Movement Type', compute='_compute_move_kind', store=True, index=True)
    qty_signed = fields.Float(
        string='Balance Qty', compute='_compute_qty_signed', store=True,
        digits='Product Unit',
        help="+ for IN, customer returns and positive adjustments; "
             "- for sales, vendor returns and negative adjustments.")

    @api.depends('location_id.usage', 'location_dest_id.usage')
    def _compute_move_kind(self):
        for line in self:
            line.move_kind = _KINDS.get(
                (line.location_id.usage, line.location_dest_id.usage), False)

    @api.depends('move_kind', 'quantity_product_uom')
    def _compute_qty_signed(self):
        for line in self:
            if not line.move_kind:
                line.qty_signed = 0.0
            elif line.move_kind in _PLUS:
                line.qty_signed = line.quantity_product_uom
            else:
                line.qty_signed = -line.quantity_product_uom
