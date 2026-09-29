from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    barcode_prefix = fields.Char(
        string='Barcode Prefix',
        help='Prefix to prepend to generated barcodes (e.g., "21")',
        config_parameter='product_barcode.barcode_prefix',
    )

    barcode_padding = fields.Integer(
        string='Barcode Padding',
        default=6,
        help='Number of digits to pad the sequence number with leading zeros (e.g., 6 → "000001")',
        config_parameter='product_barcode.barcode_padding',
    )

    barcode_suffix = fields.Char(
        string='Barcode Suffix',
        help='Suffix to append to generated barcodes (e.g., "99")',
        config_parameter='product_barcode.barcode_suffix',
    )

    barcode_sequence_id = fields.Many2one(
        comodel_name='ir.sequence',
        string='Barcode Sequence',
        domain=[('code', '=', 'product.barcode')],
        help='Select the sequence to use for barcode generation',
        config_parameter='product_barcode.barcode_sequence_id',
    )

    @api.constrains('barcode_padding')
    def _check_barcode_padding(self):
        """Validate that padding is greater than 0"""
        for record in self:
            if record.barcode_padding and record.barcode_padding <= 0:
                raise ValueError(
                    'Barcode Padding must be greater than 0'
                )
