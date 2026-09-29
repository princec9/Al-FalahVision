# -*- coding: utf-8 -*-
import datetime
from email.policy import default
from html import escape

from markupsafe import Markup
import logging

_logger = logging.getLogger(__name__)
from odoo import api, fields, models, _
import datetime
from dateutil.relativedelta import relativedelta
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_compare

from odoo import models, fields


class StockPicking(models.Model):
    _inherit = "stock.picking"

    has_lot_lines = fields.Boolean(
        compute="_compute_has_lot_lines",
        string="Has Lot Lines",
        help="Check if picking has any move lines with lots/serials",
    )
    requires_scan = fields.Boolean(
        compute="_compute_requires_scan",
        string="Requires Scan",
        help="Scan is required before validation",
    )

    @api.depends("move_line_ids.lot_id", "move_line_ids.quantity")
    def _compute_has_lot_lines(self):
        """Check if picking has any tracked move lines with lots and quantity > 0."""
        for picking in self:
            picking.has_lot_lines = bool(
                picking.move_line_ids.filtered(
                    lambda ml: (
                            ml.lot_id
                            and ml.product_id.tracking in ("lot", "serial")
                            and ml.quantity > 0
                    )
                )
            )

    @api.depends("has_lot_lines", "scan_completed")
    def _compute_requires_scan(self):
        """Check if scan is required for this picking."""
        for picking in self:
            # Scan is required if there are lot lines and picking is outgoing
            picking.requires_scan = (
                    picking.has_lot_lines
                    and picking.picking_type_code == "outgoing"
            )

    def action_quality_checks(self):
        """Open the delivery scan wizard."""
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Scan Products'),
            'res_model': 'delivery.scan.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_id': self.id,
            },
        }

    def _create_product_followup_activities(self):
        """Create follow-up activities using the priority:
        Product Variant → Product Template → Product Category.
        """

        self.ensure_one()

        sale_order = self.sale_id
        if not sale_order:
            return

        # Use actual delivery validation date
        base_date = (
            self.date_done.date()
            if self.date_done
            else fields.Date.context_today(self)
        )

        # Activity responsible user
        user_id = sale_order.user_id.id or self.env.user.id

        for line in sale_order.order_line:

            product = line.product_id
            template = product.product_tmpl_id
            category = template.categ_id

            # =========================================================
            # FOLLOW-UP CONFIGURATION PRIORITY
            # 1. Product Variant
            # 2. Product Template
            # 3. Product Category
            # =========================================================

            # ---------------------------------------------------------
            # 1. PRODUCT VARIANT CONFIGURATION
            # ---------------------------------------------------------
            if product.followup:
                followup = True
                followup_month = product.followup_month
                activity_type = product.activity_type_id

            # ---------------------------------------------------------
            # 2. PRODUCT TEMPLATE CONFIGURATION
            # ---------------------------------------------------------
            elif template.followup:
                followup = True
                followup_month = template.followup_month
                activity_type = template.activity_type_id

            # ---------------------------------------------------------
            # 3. PRODUCT CATEGORY CONFIGURATION
            # ---------------------------------------------------------
            elif category.followup:
                followup = True
                followup_month = category.followup_month
                activity_type = category.activity_type_id

            # ---------------------------------------------------------
            # NOTHING CONFIGURED
            # ---------------------------------------------------------
            else:
                followup = False
                followup_month = False
                activity_type = False

            # No follow-up configured
            if not followup:
                continue

            # Month is required
            if not followup_month:
                continue

            followup_month = int(followup_month)

            if followup_month <= 0:
                continue

            # ---------------------------------------------------------
            # ACTIVITY TYPE FALLBACK
            # ---------------------------------------------------------
            if not activity_type:
                activity_type = self.env.ref(
                    "mail.mail_activity_data_call",
                    raise_if_not_found=False,
                )

            if not activity_type:
                continue

            # ---------------------------------------------------------
            # CALCULATE FOLLOW-UP DATE
            # ---------------------------------------------------------
            followup_date = base_date + relativedelta(
                months=followup_month
            )

            # ---------------------------------------------------------
            # PREVENT DUPLICATE ACTIVITY
            # ---------------------------------------------------------
            existing_activity = self.env["mail.activity"].search([
                ("activity_type_id", "=", activity_type.id),
                ("res_model", "=", "sale.order"),
                ("res_id", "=", sale_order.id),
                ("summary", "=", "Customer Follow Up"),
                ("date_deadline", "=", followup_date),
            ], limit=1)

            if existing_activity:
                continue

            # ---------------------------------------------------------
            # CREATE ACTIVITY ON SALE ORDER
            # ---------------------------------------------------------
            self.env["mail.activity"].create({
                "activity_type_id": activity_type.id,
                "summary": "Customer Follow Up",
                "note": (
                    f"Follow-up for product: {product.display_name}\n"
                    f"Sales Order: {sale_order.name}\n"
                    f"Follow-up after: {followup_month} months\n"
                    f"Delivery Done Date: {base_date}"
                ),
                "date_deadline": followup_date,
                "user_id": user_id,
                "res_model_id": self.env["ir.model"]._get_id(
                    "sale.order"
                ),
                "res_id": sale_order.id,
            })

    def button_validate(self):
        """Validate picking and create product follow-up activities."""

        self._check_lab_orders_completed()
        for picking in self:

            if picking.requires_scan and not picking.scan_completed:
                raise UserError(_(
                    "⚠️ Scan Verification Required!\n\n"
                    "This delivery contains lot-tracked products "
                    "that must be scanned before validation.\n\n"
                    "Please click the 'Scan' button to complete "
                    "the lot verification process."
                ))

        result = super().button_validate()
        for picking in self:
            if picking.state == "done":
                picking._create_product_followup_activities()

        return result

    scan_completed = fields.Boolean(
        string="Lot Scan Completed",
        default=False,
    )

    def check_quality(self):
        self._check_lab_orders_completed()
        return super().check_quality()

    def _check_lab_orders_completed(self):
        """Prevent delivery validation until all related Lab MOs are completed."""

        for picking in self:
            sale_lines = picking.move_ids.mapped("sale_line_id")

            lab_lines = sale_lines.filtered(
                lambda line: (
                        line.is_lab_order
                        and not line.parent_line_id
                        and line.production_id
                )
            )

            pending_lines = lab_lines.filtered(
                lambda line: line.production_id.state != "done"
            )

            if pending_lines:
                pending_details = []

                for line in pending_lines:
                    production = line.production_id

                    pending_details.append(
                        "%s - %s"
                        % (
                            production.name,
                            production.state,
                        )
                    )

                raise UserError(_(
                    "You cannot validate this Delivery Order yet.\n\n"
                    "The following Lab Order(s) are still not completed:\n\n"
                    "%s\n\n"
                    "Please complete the Lab Manufacturing Order(s) first."
                ) % "\n".join(
                    "• %s" % detail
                    for detail in pending_details
                ))

        return True

    # def button_validate(self):
    #     """Block delivery validation while related Lab Orders are pending."""
    #
    #     self._check_lab_orders_completed()
    #
    #     # return super().button_validate()
    #     if self.env.context.get("fefo_scan_passed"):
    #         return super().button_validate()
    #
    #     return {
    #         "type": "ir.actions.act_window",
    #         "name": _("FEFO Verification"),
    #         "res_model": "stock.fefo.scan.wizard",
    #         "view_mode": "form",
    #         "target": "new",
    #         "context": {
    #             "default_picking_id": self.id,
    #         },
    #     }

    def action_quality_checks(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Scan Products'),
            'res_model': 'delivery.scan.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_id': self.id,
            },
        }

    # def button_validate(self):
    #     """Open FEFO verification only for products whose category uses FEFO."""
    #
    #     self._check_lab_orders_completed()
    #
    #     for picking in self:
    #         if picking.picking_type_code != "outgoing":
    #             return super().button_validate()
    #
    #         moves = picking.move_ids.filtered(
    #             lambda m: (
    #                     m.state not in ("done", "cancel")
    #                     and m.product_id.tracking in ("lot", "serial")
    #             )
    #         )
    #
    #         fefo_product = moves.filtered(
    #             lambda m: (
    #                     m.product_id.categ_id.removal_strategy_id
    #                     and m.product_id.categ_id.removal_strategy_id.method == "fefo"
    #             )
    #         )
    #
    #         # No product/category using FEFO → normal validation
    #         if not fefo_product:
    #             return super().button_validate()
    #
    #         # FEFO already verified → continue validation
    #         if self.env.context.get("fefo_scan_passed"):
    #             return super().button_validate()
    #
    #         # Open FEFO popup
    #         return {
    #             "type": "ir.actions.act_window",
    #             "name": _("FEFO Verification"),
    #             "res_model": "stock.fefo.scan.wizard",
    #             "view_mode": "form",
    #             "target": "new",
    #             "context": {
    #                 "default_picking_id": picking.id,
    #             },
    #         }
    #
    #     return super().button_validate()
    def action_quality_checks(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Scan Products'),
            'res_model': 'delivery.scan.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_id': self.id,
            },
        }

    qc_done = fields.Boolean(string="QC Done", copy=False)
    qc_employee_id = fields.Many2one(
        "hr.employee",
        string="QC Assignee",
        copy=False,
        readonly=True,
    )
    qc_description = fields.Text(
        string="QC Description",
        copy=False,
        readonly=True,
    )

    def action_open_qc_wizard(self):
        self.ensure_one()

        if self.state != "done":
            raise UserError(_("Quality Check can be done only after validation."))

        return {
            "type": "ir.actions.act_window",
            "name": "Quality Check",
            "res_model": "stock.picking.qc.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_picking_id": self.id,
            },
        }

    def _check_fefo_lots(self):
        for picking in self:
            if picking.picking_type_code != "outgoing":
                continue

            for move in picking.move_ids.filtered(
                    lambda m: (
                            m.state not in ("done", "cancel")
                            and m.product_id.tracking in ("lot", "serial")
                    )
            ):
                product = move.product_id

                move_lines = move.move_line_ids.filtered(
                    lambda line: line.lot_id and line.quantity > 0
                )

                if not move_lines:
                    continue

                quants = self.env["stock.quant"].search([
                    ("product_id", "=", product.id),
                    ("location_id", "=", move.location_id.id),
                    ("lot_id", "!=", False),
                    ("quantity", ">", 0),
                ])

                lots = quants.mapped("lot_id").filtered(
                    lambda lot: lot.expiration_date
                )

                if not lots:
                    continue

                expected_lot = lots.sorted(
                    key=lambda lot: lot.expiration_date
                )[0]

                for line in move_lines:
                    if line.lot_id != expected_lot:
                        raise UserError(_(
                            "Incorrect Lot Selected!\n\n"
                            "Product: %s\n"
                            "Selected Lot: %s\n"
                            "Selected Expiry: %s\n\n"
                            "Please select the earliest-expiring lot:\n"
                            "Lot: %s\n"
                            "Expiry: %s"
                        ) % (
                                            product.display_name,
                                            line.lot_id.name,
                                            line.lot_id.expiration_date.strftime("%d-%b-%Y")
                                            if line.lot_id.expiration_date else "No Expiry",
                                            expected_lot.name,
                                            expected_lot.expiration_date.strftime("%d-%b-%Y"),
                                        ))


class StockPickingQCWizard(models.TransientModel):
    _name = "stock.picking.qc.wizard"
    _description = "Stock Picking QC Wizard"

    picking_id = fields.Many2one(
        "stock.picking",
        required=True,
        readonly=True,
    )

    employee_id = fields.Many2one(
        "hr.employee",
        string="Assignee",
        required=True,
    )

    description = fields.Text(
        string="Description",
        required=True,
    )

    def action_confirm(self):
        self.ensure_one()

        self.picking_id.write({
            "qc_done": True,
            "qc_employee_id": self.employee_id.id,
            "qc_description": self.description,
        })

        self.picking_id.message_post(
            body=_(
                "Quality Check Completed\n"
                "Assignee: %s\n"
                "Description: %s"
            ) % (
                     self.employee_id.name,
                     self.description or "",
                 ),
            subtype_xmlid="mail.mt_note",
        )

        return {"type": "ir.actions.act_window_close"}
