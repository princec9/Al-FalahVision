from odoo import fields, api, models, _


class FittingParameters(models.Model):
    _name = "fitting.parameters"
    _description = "Fitting Parameters"
    _rec_name = "sale_line_id"

    sale_line_id = fields.Many2one(
        "sale.order.line",
        required=True,
        ondelete="cascade",
    )

    production_id = fields.Many2one(
        "mrp.production",
        string="Manufacturing Order",
        compute="_compute_production_id",
        store=True,
    )

    @api.depends("sale_line_id.production_id")
    def _compute_production_id(self):
        for rec in self:
            rec.production_id = rec.sale_line_id.production_id

    prescription_id = fields.Many2one(
        "prescription.records",
        string="Prescription",
    )

    frame_shape = fields.Char("Shape", required=True)
    frame_type = fields.Char("Frame Type", required=True)
    frame_length = fields.Float("A Size", required=True)
    frame_height = fields.Float("B Size", required=True)
    frame_dbl = fields.Char("DBL", required=True)
    frame_diameter = fields.Char("Diameter", required=True)
    frame_thickness_type = fields.Char("Thickness Type", required=True)
    frame_pd_height_r = fields.Char("Right PD", required=True)
    frame_pd_height_l = fields.Char("Left PD", required=True)
    frame_index = fields.Char("Index", required=True)
    frame_ipd = fields.Char("IPD", required=True)
    frame_segment_height = fields.Char("FH-SH", required=True)
