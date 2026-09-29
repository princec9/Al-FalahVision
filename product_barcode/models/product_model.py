from odoo import fields, models


class ProductModel(models.Model):
    _name = "product.model"
    _description = "Product Model"
    _order = "name"

    name = fields.Char(
        string="Model Name",
        required=True,
        index=True,
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )