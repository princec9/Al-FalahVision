from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Warranty application and trigger settings
    apply_warranty_on = fields.Selection([
        ('product', 'Product'),
        ('variant', 'Product Variants'),
        ('category', 'Product Category'),
    ], string="Apply Warranty On",
       config_parameter='is_product_warranty.apply_on',
       default='product')

    create_warranty_from = fields.Selection([
        ('sale_order', 'Sale Order'),
        ('po', 'Purchase Order'),
        ('invoice', 'Invoice'),
        ('manual', 'Manual'),
    ], string="Create Warranty From",
       config_parameter='is_product_warranty.create_warranty_from',
       default='manual')

    create_warranty_sale_order = fields.Boolean(
        string="Create Warranty from Sale Order",
        config_parameter='is_product_warranty.create_warranty_sale_order'
    )

    # Renewal scheduling settings
    renew_interval_number = fields.Integer(string="Renew Interval Number", config_parameter='is_product_warranty.renew_interval_number')
    renew_interval_unit = fields.Selection([
        ('days', 'Days'),
        ('months', 'Months'),
        ('years', 'Years'),
    ], string="Renew Interval Unit", config_parameter='is_product_warranty.renew_interval_unit', default='days')

    # Email notification templates
    warranty_renew_mail_template_id = fields.Many2one(
        'mail.template',
        string="Warranty Renew Mail Template",
        domain="[('model', '=', 'product.warranty')]",
        config_parameter='is_product_warranty.warranty_renew_mail_template_id'
    )

    warranty_registration_mail_template_id = fields.Many2one(
        'mail.template',
        string="Warranty Registration Mail Template",
        domain="[('model', '=', 'product.warranty')]",
        config_parameter='is_product_warranty.warranty_registration_mail_template_id'
    )
    