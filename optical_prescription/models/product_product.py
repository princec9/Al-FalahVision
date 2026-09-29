from odoo import api
from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    followup = fields.Boolean(
        string="Follow Up"
    )

    activity_type_id = fields.Many2one(
        "mail.activity.type",
        string="Activity",
    )

    followup_month = fields.Integer(
        string="Month",
        default=1,
    )


class ProductProduct(models.Model):
    _inherit = "product.product"
    is_lab_order = fields.Boolean(
        string="Is Lab Order",
        related="product_tmpl_id.is_lab_order",
        store=True,
    )
    activity_type_id = fields.Many2one(
        "mail.activity.type",
        string="Activity",
    )

    followup = fields.Boolean(
        string="Follow Up",
        default=False,
    )

    followup_month = fields.Integer(
        string="Month",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        return records

    def write(self, vals):
        result = super().write(vals)

        return result


class ProductTemplate(models.Model):
    _inherit = "product.template"
    is_lab_order = fields.Boolean("Is Lab Order", defult=False)

    activity_type_id = fields.Many2one(
        "mail.activity.type",
        string="Activity",
    )

    followup = fields.Boolean(
        string="Follow Up",
        default=False,
    )

    followup_month = fields.Integer(
        string="Month",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        return records

    def write(self, vals):
        result = super().write(vals)

        #

        return result
