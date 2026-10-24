import logging

_logger = logging.getLogger(__name__)
from odoo import api, fields, models, _


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    user_id = fields.Many2one(
        "res.users",
        string="Salesperson",
    )
    discount_amount = fields.Monetary(
        string="Disc. Amount",
        currency_field="currency_id",
        compute="_compute_discount_amount",
        inverse="_inverse_discount_amount",
        store=True,
        readonly=False,
        copy=False,
        help="Discount expressed as an amount. Mirror of the Discount (%) "
             "field - only Discount (%) is used in the accounting, tax and "
             "total computations.",
    )

    # ------------------------------------------------------------------
    # DISCOUNT % <-> DISCOUNT AMOUNT MIRROR (display only)
    # ------------------------------------------------------------------

    def _get_discount_base(self):
        """Gross line value the discount applies to (Unit Price x Qty)."""
        self.ensure_one()
        return (self.price_unit or 0.0) * (self.quantity or 0.0)

    def _discount_pct_from_amount(self):
        self.ensure_one()
        base = self._get_discount_base()
        if not base:
            return None
        pct = (self.discount_amount or 0.0) * 100.0 / base
        return min(max(pct, 0.0), 100.0)

    @api.depends("price_unit", "quantity", "discount")
    def _compute_discount_amount(self):
        for line in self:
            base = line._get_discount_base()
            line.discount_amount = base * (line.discount or 0.0) / 100.0

    def _inverse_discount_amount(self):
        for line in self:
            pct = line._discount_pct_from_amount()
            if pct is None:
                continue
            if line.discount != pct:
                line.discount = pct

    @api.onchange("discount_amount")
    def _onchange_discount_amount(self):
        for line in self:
            pct = line._discount_pct_from_amount()
            if pct is None:
                continue
            line.discount = pct



class AccountMove(models.Model):
    _inherit = "account.move"

    prescription_count = fields.Integer(
        string="Prescription Count",
        compute="_compute_prescription_count",
    )

    def _get_prescription_domain(self):
        """Prescriptions linked directly to this invoice, or to the sale
        order(s) it was created from (same lookup as the report)."""
        self.ensure_one()
        domain = [
            "&",
            ("res_model", "=", "account.move"),
            ("line_res_id", "=", self._origin.id or self.id),
        ]
        return domain

    @api.depends("invoice_line_ids.sale_line_ids.order_id")
    def _compute_prescription_count(self):
        Prescription = self.env["prescription.records"].sudo()
        for move in self:
            if not isinstance(move._origin.id, int):
                move.prescription_count = 0
                continue
            move.prescription_count = Prescription.search_count(
                move._get_prescription_domain()
            )

    def action_add_prescription(self):
        self.ensure_one()
        return {
            "name": _("Add Prescription For '%s'") % (self.name or "/"),
            "type": "ir.actions.act_window",
            "res_model": "prescription.records",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_partner_id": self.partner_id.id,
                "default_res_model": "account.move",
                "default_line_res_id": self.id,
                "default_invoice_id": self.id,
            },
        }

    def action_open_prescription(self):
        self.ensure_one()
        return {
            "name": _("Prescription Records"),
            "type": "ir.actions.act_window",
            "res_model": "prescription.records",
            "view_mode": "list,form",
            "domain": self._get_prescription_domain(),
            "context": {
                "default_partner_id": self.partner_id.id,
                "default_res_model": "account.move",
                "default_line_res_id": self.id,
            },
        }
