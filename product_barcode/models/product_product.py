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
        """
        Override create to automatically generate barcodes for products
        that don't already have one.
        """
        products = super().create(vals_list)

        for product in products:
            # Only generate barcode if the product doesn't already have one
            if not product.barcode:
                generated_barcode,default_code = self._generate_barcode()
                if generated_barcode:
                    product.barcode = generated_barcode
                if default_code and not product.default_code:
                    product.default_code = default_code
        return products

    def _generate_barcode(self):
        """
        Generate a barcode based on configured prefix, padding, and suffix.
        
        Returns:
            str: Generated barcode, or False if configuration is not available
        """
        # Get configuration parameters from settings (company-specific)
        config = self.env['ir.config_parameter'].sudo()
        company = self.env.company
        
        # Build parameter keys with company context
        prefix_key = f'product_barcode.barcode_prefix_{company.id}'
        padding_key = f'product_barcode.barcode_padding_{company.id}'
        suffix_key = f'product_barcode.barcode_suffix_{company.id}'
        sequence_key = f'product_barcode.barcode_sequence_id_{company.id}'

        # Try to get company-specific config, fallback to global
        prefix = (
            config.get_param(prefix_key) or 
            config.get_param('product_barcode.barcode_prefix') or 
            ''
        )
        padding = int(
            config.get_param(padding_key) or 
            config.get_param('product_barcode.barcode_padding') or 
            '6'
        )
        suffix = (
            config.get_param(suffix_key) or 
            config.get_param('product_barcode.barcode_suffix') or 
            ''
        )
        sequence_id = (
            config.get_param(sequence_key) or 
            config.get_param('product_barcode.barcode_sequence_id')
        )

        # Get the sequence object
        if not sequence_id:
            return False

        try:
            sequence = self.env['ir.sequence'].browse(int(sequence_id))
            if not sequence.exists():
                return False
        except (ValueError, TypeError):
            return False

        # Generate the next sequence number
        max_attempts = 100
        attempt = 0
        
        while attempt < max_attempts:
            # Get next number from sequence
            sequence_number = sequence.next_by_id()
            
            # Format the sequence number with padding
            formatted_sequence = str(sequence_number).zfill(padding)

            default_code = formatted_sequence
            # Combine prefix + sequence + suffix
            generated_barcode = f"{prefix}{formatted_sequence}{suffix}"
            
            # Check if barcode already exists
            existing = self.env['product.product'].search(
                [('barcode', '=', generated_barcode)],
                limit=1
            )
            
            if not existing:
                # Barcode is unique, return it
                return generated_barcode,default_code
            
            # Barcode exists, try again with next sequence
            attempt += 1
        
        # If we've exhausted attempts, return False
        return False

    @api.model
    def _generate_barcode_background(self, limit=None):
        """
        Utility method to generate missing barcodes for existing products.
        Useful for backfill operations.
        
        Args:
            limit: Maximum number of products to process
        """
        domain = [('barcode', '=', False)]
        products = self.search(domain, limit=limit)
        
        for product in products:
            barcode = self._generate_barcode()
            if barcode:
                product.barcode = barcode
        
        return len(products)
