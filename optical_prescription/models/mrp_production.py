from odoo import fields, models, _

from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = "mrp.production"
    technician_id = fields.Many2one('hr.employee',

                                    string="Technician", readonly=True

                                    )
    technician_contact_id = fields.Many2one('res.partner',

                                            string="Technician",

                                            )
    related_sale_order_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Sale Order Line",
        compute="_compute_related_sale_order_line_id",
    )

    fitting_parameter_ids = fields.One2many(
        comodel_name="fitting.parameters",
        inverse_name="production_id",
        string="Fitting Parameters",
    )

    def action_start(self):
        for production in self:
            # Only validate if Quality Checks exist
            if production.check_ids:
                pending_checks = production.check_ids.filtered(
                    lambda c: c.quality_state != "pass"
                )

                if pending_checks:
                    raise UserError(_("You still need to complete all Quality Checks."))

        return super().action_start()

    def button_mark_done(self):
        for production in self:
            if production.check_ids:
                pending_checks = production.check_ids.filtered(
                    lambda c: c.quality_state != "pass"
                )

                if pending_checks:
                    raise UserError(_(
                        "You cannot mark this Lab Order as Done.\n\n"
                        "All Quality Checks must be completed and passed first."
                    ))

        res = super().button_mark_done()

        for production in self:
            sale_line = self.env["sale.order.line"].search(
                [("production_id", "=", production.id)],
                limit=1,
            )

            if not sale_line:
                continue

            delivery_moves = sale_line.move_ids.filtered(
                lambda m: m.state not in ("done", "cancel")
            )

            if delivery_moves:
                delivery_moves._action_assign()

        return res

    def _compute_related_sale_order_line_id(self):
        """A Manufacturing Order has no direct link back to the Sale
        Order - but the sale.order.line that created it does, via
        production_id (set in _create_lab_manufacturing_orders)."""
        SaleLine = self.env["sale.order.line"]
        for production in self:
            production.related_sale_order_line_id = SaleLine.search(
                [("production_id", "=", production.id)],
                limit=1,
            )

    prescription_ids = fields.One2many(
        comodel_name="prescription.records",
        compute="_compute_prescription_ids",
        string="Prescriptions",
    )
    prescription_count = fields.Integer(
        string="Prescription Count",
        compute="_compute_prescription_ids",
    )

    def _compute_prescription_ids(self):
        """A Manufacturing Order has no direct link back to the Sale
        Order - but the sale.order.line that created it does, via
        production_id (set in _create_lab_manufacturing_orders). Find
        that line, then reuse the Sale Order's own prescription_ids."""
        SaleLine = self.env["sale.order.line"]
        for production in self:
            sale_line = SaleLine.search(
                [("production_id", "=", production.id)], limit=1
            )
            prescriptions = (
                sale_line.order_id.prescription_ids
                if sale_line
                else self.env["prescription.records"]
            )
            production.prescription_ids = prescriptions
            production.prescription_count = len(prescriptions)
