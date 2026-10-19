from odoo import api, models, fields


class ProductTemplate(models.Model):
    _inherit = "product.template"

    model_id = fields.Many2one(
        "product.model",
        string="Model"
    )
    brand_id = fields.Many2one(
        "product.brand",
        string="Brand"
    )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    brand_id = fields.Many2one(
        "product.brand",
        string="Brand",
        related="product_tmpl_id.brand_id",
        store=True,
        readonly=True
    )

    model_id = fields.Many2one(
        "product.model",
        string="Model",
        related="product_tmpl_id.model_id",
        store=True,
        readonly=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)

        for product in products:
            if not product.barcode:
                generated_barcode, default_code = self._generate_barcode()
                if generated_barcode:
                    product.barcode = generated_barcode
                if default_code and not product.default_code:
                    product.default_code = default_code

        return products

    def _generate_barcode(self):
        """Return (barcode, default_code). Returns (False, False) if it cannot be generated."""
        config = self.env['ir.config_parameter'].sudo()
        company = self.env.company

        def param(name):
            return (config.get_param(f'product_barcode.{name}_{company.id}')
                    or config.get_param(f'product_barcode.{name}'))

        prefix = param('barcode_prefix') or ''
        suffix = param('barcode_suffix') or ''
        try:
            padding = int(param('barcode_padding') or 6)
            sequence_id = int(param('barcode_sequence_id') or 0)
        except (ValueError, TypeError):
            return False, False

        if not sequence_id:
            return False, False
        sequence = self.env['ir.sequence'].browse(sequence_id)
        if not sequence.exists():
            return False, False

        for _attempt in range(100):
            number = str(sequence.next_by_id()).zfill(padding)
            barcode = f"{prefix}{number}{suffix}"
            if not self.env['product.product'].search([('barcode', '=', barcode)], limit=1):
                return barcode, number  # keep your existing default_code rule here if it differs
        return False, False

    @api.model
    def _generate_barcode_background(self, limit=None):
        products = self.search([('barcode', '=', False)], limit=limit)
        for product in products:
            barcode, default_code = self._generate_barcode()
            if barcode:
                product.barcode = barcode
                if default_code and not product.default_code:
                    product.default_code = default_code
        return len(products)
