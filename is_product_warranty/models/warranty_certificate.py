from odoo import models, fields, api


class WarrantyCertificate(models.Model):
    _name = 'warranty.certificate'
    _description = 'Warranty Certificate'

    # Certificate number auto-generate
    name = fields.Char(string='Certificate No', readonly=True, copy=False, default='New')

    # Link to warranty record
    warranty_id = fields.Many2one('product.warranty', string='Warranty')

    # Auto-fill fields from warranty
    lot_serial_no = fields.Char(related='warranty_id.serial_number', string='Lot/Serial No', store=True)
    product_id = fields.Many2one(related='warranty_id.product_id', string='Product Name', store=True)
    customer_id = fields.Many2one(related='warranty_id.partner_id', string='Customer Name', store=True)

    manufacturing_date = fields.Datetime(string='Manufacturing Date')

    # Lab result attachment
    lab_result_certificate = fields.Binary(string='Lab Result Certificate')
    lab_result_filename = fields.Char(string='Lab Result Filename')

    # Template setup
    warranty_template_id = fields.Many2one('warranty.template', string='Warranty Template')
    template_content = fields.Html(string='Template Content')

    # Certificate status
    state = fields.Selection([
        ('new', 'New'),
        ('updated', 'Updated'),
    ], string='Status', default='new')

    # Load content from template
    @api.onchange('warranty_template_id')
    def _onchange_warranty_template(self):
        if self.warranty_template_id:
            self.template_content = self.warranty_template_id.content

    # Sequence generation
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'warranty.certificate'
                ) or 'New'
        return super(WarrantyCertificate, self).create(vals_list)

    # Status update action
    def action_update(self):
        self.write({'state': 'updated'})
        