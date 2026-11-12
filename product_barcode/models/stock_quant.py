from odoo import fields, models


class StockQuant(models.Model):
    _inherit = "stock.quant"

    brand_id = fields.Many2one(
        "product.brand",
        string="Brand",
        related="product_id.brand_id",
        store=True,
        index=True,
    )
    list_price = fields.Float(
        string="Sales Price",
        related="product_tmpl_id.list_price",
        store=True,
    )
