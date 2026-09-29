# -*- coding: utf-8 -*-

from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class SaleDiscountApprovalWizard(models.TransientModel):
    _name = "sale.discount.approval.wizard"
    _description = "Sale Discount Approval Wizard"

    sale_line_id = fields.Many2one(
        "sale.order.line",
        string="Sale Order Line",
        required=True,
        readonly=True,
    )

    order_id = fields.Many2one(
        "sale.order",
        string="Sale Order",
        related="sale_line_id.order_id",
        readonly=True,
    )

    salesperson_id = fields.Many2one(
        "res.users",
        string="Current Salesperson",
        related="sale_line_id.user_id",
        readonly=True,
    )

    current_discount = fields.Float(
        string="Current Discount (%)",
        readonly=True,
    )

    salesperson_limit = fields.Float(
        string="Salesperson Limit (%)",
        readonly=True,
    )

    additional_required = fields.Float(
        string="Additional Approval Required (%)",
        compute="_compute_additional_required",
        readonly=True,
    )

    approving_employee_id = fields.Many2one(
        "hr.employee",
        string="Approving Employee",
        required=True,
    )

    approver_limit = fields.Float(
        string="Approver Limit (%)",
        compute="_compute_approver_limit",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # The approver may REVISE the discount, not merely rubber-stamp it.
    # `approved_discount` is what will actually be written on the line.
    # It defaults to whatever the salesperson typed, and is capped by
    # `max_allowed_discount` (salesperson limit + approver limit).
    # ------------------------------------------------------------------
    approved_discount = fields.Float(
        string="Approved Discount (%)",
        compute="_compute_approved_discount",
        store=True,
        readonly=False,
        help="Discount that will be written on the line. The approver may "
             "raise or lower the value requested by the salesperson, up to "
             "their own authorisation limit.",
    )

    max_allowed_discount = fields.Float(
        string="Maximum You Can Approve (%)",
        compute="_compute_max_allowed_discount",
        readonly=True,
        help="Salesperson's own limit plus the selected approver's limit.",
    )

    over_limit = fields.Boolean(
        compute="_compute_max_allowed_discount",
        help="True when the entered discount exceeds what this approver may "
             "authorise.",
    )

    discount_secret_key = fields.Char(
        string="Secret Key",
        required=True,
    )

    @api.depends("current_discount")
    def _compute_approved_discount(self):
        """Seed the editable value with what the salesperson requested."""
        for wizard in self:
            wizard.approved_discount = wizard.current_discount or 0.0

    @api.depends("approved_discount", "salesperson_limit")
    def _compute_additional_required(self):
        """Approval is needed only for the slice ABOVE the salesperson's own
        limit, and it follows the value the approver actually enters."""
        for wizard in self:
            wizard.additional_required = max(
                (wizard.approved_discount or 0.0)
                - (wizard.salesperson_limit or 0.0),
                0.0,
            )

    @api.depends("salesperson_limit", "approver_limit", "additional_required")
    def _compute_max_allowed_discount(self):
        for wizard in self:
            wizard.max_allowed_discount = wizard.approver_limit or 0.0
            wizard.over_limit = float_compare(
                wizard.approver_limit or 0.0,
                wizard.additional_required or 0.0,
                precision_digits=2,
            ) < 0

    @api.depends("approving_employee_id")
    def _compute_approver_limit(self):
        # sudo(): a salesperson may not have read access on other employees,
        # but must still see whether the chosen approver is sufficient.
        for wizard in self:
            employee = wizard.approving_employee_id.sudo()
            wizard.approver_limit = (
                employee.sale_discount_limit if employee else 0.0
            )

    def action_approve_discount(self):
        self.ensure_one()

        line = self.sale_line_id
        # sudo(): discount_secret_key is restricted to sales managers, yet the
        # approval is performed by whoever is editing the order.
        employee = self.approving_employee_id.sudo()

        # -- Step 1: employee must exist -------------------------------
        if not employee:
            raise ValidationError(_("Please select an approving employee."))

        # -- Step 2: secret key must match exactly ---------------------
        stored_key = employee.discount_secret_key or ""
        if not stored_key:
            raise ValidationError(_(
                "No discount secret key is configured for %s. "
                "Ask a sales manager to set one on the employee record."
            ) % employee.name)

        if (self.discount_secret_key or "") != stored_key:
            raise ValidationError(_("Invalid secret key."))

        # -- Step 3: sanity-check the revised value --------------------
        approved = self.approved_discount or 0.0
        if float_compare(approved, 0.0, precision_digits=2) < 0:
            raise ValidationError(_("The discount cannot be negative."))
        if float_compare(approved, 100.0, precision_digits=2) > 0:
            raise ValidationError(_("The discount cannot exceed 100%."))

        # -- Step 4: approver must cover the additional percentage -----
        additional = self.additional_required
        if float_compare(
                approved,
                employee.sale_discount_limit,
                precision_digits=2
        ) > 0:
            raise ValidationError(_(
                "%(employee)s can approve a maximum discount of %(limit).2f%%.\n\n"
                "Requested Discount: %(requested).2f%%"
            ) % {
                                      "employee": employee.name,
                                      "limit": employee.sale_discount_limit,
                                      "requested": approved,
                                  })
        additional = max(
            approved - (self.salesperson_limit or 0.0),
            0.0,
        )
        # -- Record the approval ---------------------------------------
        # The approver's value is what lands on the line. When they leave it
        # untouched this is identical to the salesperson's request, so the
        # previous behaviour is preserved exactly.
        requested = self.current_discount or 0.0
        revised = float_compare(approved, requested, precision_digits=2) != 0

        line_vals = {
            "discount_approved_by_id": employee.id,
            "discount_approved_date": fields.Datetime.now(),
            "discount_approved_percentage": approved,
            "discount_approved_additional": additional,
        }
        if revised:
            line_vals["discount"] = approved

        # bypass_discount_lock: a previously approved line on a CONFIRMED
        # order is locked, and this is the one legitimate way to change it.
        # skip_discount_edit_log: the approval log below already records the
        # change in full, so the quotation edit journal must not double-post.
        line.with_context(
            bypass_discount_lock=True,
            skip_discount_edit_log=True,
        ).write(line_vals)

        self._post_approval_log(employee, additional, approved, revised)

        return {"type": "ir.actions.act_window_close"}

    def _post_approval_log(self, employee, additional, approved, revised):
        """Audit trail on the Sale Order chatter."""
        self.ensure_one()

        order = self.sale_line_id.order_id
        if not order:
            return

        product = self.sale_line_id.product_id.display_name or \
            self.sale_line_id.name or _("Line")

        # Floats are formatted up-front: Markup's % operator escapes every
        # substituted value, which would break a "%.2f" format spec.
        body = Markup(
            "<p><b>%(title)s</b></p>"
            "<ul>"
            "<li>%(l_product)s: %(product)s</li>"
            "<li>%(l_sp)s: %(sp)s</li>"
            "<li>%(l_limit)s: %(limit)s</li>"
            "<li>%(l_req)s: %(req)s</li>"
            "<li>%(l_disc)s: %(disc)s</li>"
            "<li>%(l_add)s: %(add)s</li>"
            "<li>%(l_by)s: %(by)s</li>"
            "<li>%(l_date)s: %(date)s</li>"
            "</ul>"
        ) % {
            "title": _("Discount Approval Completed"),
            "l_product": _("Product"),
            "product": product,
            "l_sp": _("Salesperson"),
            "sp": self.salesperson_id.name or _("Undefined"),
            "l_limit": _("Salesperson Limit"),
            "limit": "%.2f%%" % (self.salesperson_limit or 0.0),
            "l_req": _("Discount Requested"),
            "req": "%.2f%%" % (self.current_discount or 0.0),
            "l_disc": _("Discount Approved") if not revised
                      else _("Discount Approved (revised by approver)"),
            "disc": "%.2f%%" % (approved or 0.0),
            "l_add": _("Additional Approval"),
            "add": "%.2f%%" % additional,
            "l_by": _("Approved By"),
            "by": employee.name,
            "l_date": _("Approval Date"),
            "date": fields.Date.to_string(fields.Date.context_today(self)),
        }

        order.message_post(body=body)
