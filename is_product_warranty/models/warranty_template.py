from odoo import models, fields

class WarrantyTemplate(models.Model):
    _name = 'warranty.template'
    _description = 'Warranty Template'

    # Template details
    name = fields.Char(string='Template Name', required=True)
    description = fields.Text(string='Description')
    duration = fields.Integer(string='Duration (Months)')

    # HTML content for certificate
    content = fields.Html(string='Template Content')
    