from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    tracking_vendor_id = fields.Many2one(
        'res.partner', string='Tracking Vendor', copy=False, index='btree_not_null',
        help="All in/out of this product (receipts, returns, sales and inventory "
             "adjustments) is reported under this vendor in the Vendor Wise Report.")

    def action_view_vendor_report(self):
        """Open the Vendor Wise Report for this product."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'vendor_in_out.action_vendor_in_out_report')
        action['domain'] = [('product_tmpl_id', '=', self.id)]
        action['context'] = {'search_default_g_product': 1}
        return action
