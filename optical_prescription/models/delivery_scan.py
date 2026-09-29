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


class DeliveryScanWizard(models.TransientModel):
    _name = "delivery.scan.wizard"
    _description = "Delivery Serial Scan Wizard - Lot Verification"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Delivery",
        required=True,
        readonly=True,
    )

    scan_barcode = fields.Char(
        string="Scan Barcode",
    )

    scan_line_ids = fields.One2many(
        "delivery.scan.wizard.line",
        "wizard_id",
        string="Serial Numbers",
    )

    # ✅ ONLY ONE DEFINITION (with full descriptors)
    all_scanned = fields.Boolean(
        compute="_compute_all_scanned",
        string="All Scanned",
    )

    scanned_count = fields.Integer(
        compute="_compute_scanned_count",
        string="Scanned Count",
    )

    total_count = fields.Integer(
        compute="_compute_total_count",
        string="Total Count",
    )

    @api.depends("scan_line_ids.scanned")
    def _compute_all_scanned(self):
        """Check if all lots are scanned."""
        for wizard in self:
            if not wizard.scan_line_ids:
                wizard.all_scanned = False
            else:
                missing = wizard.scan_line_ids.filtered(lambda l: not l.scanned)
                wizard.all_scanned = len(missing) == 0

    @api.depends("scan_line_ids.scanned")
    def _compute_scanned_count(self):
        """Count scanned lots."""
        for wizard in self:
            wizard.scanned_count = len(wizard.scan_line_ids.filtered(lambda l: l.scanned))

    @api.depends("scan_line_ids")
    def _compute_total_count(self):
        """Count total lots."""
        for wizard in self:
            wizard.total_count = len(wizard.scan_line_ids)

    @api.model_create_multi
    def create(self, vals_list):

        for vals in vals_list:

            picking_id = (
                    vals.get("picking_id")
                    or self.env.context.get("default_picking_id")
            )

            if not picking_id:
                continue

            picking = self.env["stock.picking"].browse(picking_id)

            _logger.warning(
                "📦 Creating scan wizard for picking: %s",
                picking.name,
            )

            scan_lines = []

            # Get only lot/serial tracked move lines
            move_lines = picking.move_line_ids.filtered(
                lambda ml: (
                        ml.lot_id
                        and ml.product_id.tracking in ("lot", "serial")
                )
            )

            _logger.warning(
                "📋 Found %s move lines with lots/serials",
                len(move_lines),
            )

            # IMPORTANT:
            # Do NOT sort by expiration date.
            # User can scan lots in ANY order.
            move_lines_sorted = move_lines

            for move_line in move_lines_sorted:
                product = move_line.product_id
                lot = move_line.lot_id

                barcode_value = str(lot.name) if lot.name else ""

                _logger.warning(
                    "  Product: %s | Lot: %s | Expiration: %s",
                    product.display_name,
                    barcode_value,
                    lot.expiration_date,
                )

                scan_lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "serial_id": lot.id,
                            "barcode": barcode_value,
                            "expiration_date": lot.expiration_date,

                            # No sequential scanning
                            "scanned": False,
                        },
                    )
                )

            _logger.warning(
                "✅ Created %s scan lines",
                len(scan_lines),
            )

            vals["scan_line_ids"] = scan_lines

        return super().create(vals_list)

    def action_scan(self):
        self.ensure_one()

        barcode = (self.scan_barcode or "").strip()

        if not barcode:
            raise UserError(_("Please scan a barcode."))

        _logger.warning("🔍 Scanning barcode: %s", barcode)

        # ---------------------------------------------------------
        # FIND MATCHING LOT/SERIAL
        # ANY ORDER IS ALLOWED
        # ---------------------------------------------------------
        line = self.scan_line_ids.filtered(
            lambda line: str(line.barcode or "") == barcode
        )[:1]

        if not line:
            raise UserError(
                _("Another lot expires earlier. Please select that lot.")

            )
        if line.expiration_date and line.expiration_date < fields.Date.today():
            raise UserError(
                _("Lot '%s' is expired. Please scan another lot.") % barcode
            )

        # ---------------------------------------------------------
        # DUPLICATE SCAN CHECK
        # ---------------------------------------------------------
        if line.scanned:
            raise UserError(
                _(
                    "⚠️ Lot/Serial '%s' has already been scanned."
                )
                % barcode
            )

        # ---------------------------------------------------------
        # MARK THIS LOT AS SCANNED
        # ---------------------------------------------------------
        line.write({
            "scanned": True,
        })

        _logger.warning(
            "✅ Successfully scanned: %s",
            barcode,
        )

        # ---------------------------------------------------------
        # CLEAR SCAN INPUT
        # ---------------------------------------------------------
        self.scan_barcode = False

        # ---------------------------------------------------------
        # DO NOT CLOSE WIZARD
        # ---------------------------------------------------------
        return {
            "type": "ir.actions.act_window",
            "name": _("FEFO Verification - Scan Delivery"),
            "res_model": "delivery.scan.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "context": dict(self.env.context),
        }

    def action_confirm(self):
        self.ensure_one()

        # ---------------------------------------------------------
        # FINAL SAFETY CHECK
        # ---------------------------------------------------------

        missing = self.scan_line_ids.filtered(
            lambda line: not line.scanned
        )

        if missing:
            raise UserError(
                _(
                    "⚠️ Not all lots/serials have been scanned yet!\n\n"
                    "Remaining: %s\n\n"
                    "Please complete all scans before confirming."
                )
                % len(missing)
            )

        _logger.warning(
            "✅ All %s lots/serials successfully scanned.",
            len(self.scan_line_ids),
        )
        self.picking_id.write({
            "scan_completed": True,
        })

        # ---------------------------------------------------------
        # CONTINUE ORIGINAL DELIVERY VALIDATION
        # ---------------------------------------------------------
        return {
            "type": "ir.actions.act_window_close"
        }
        # return self.picking_id.with_context(
        #     fefo_scan_passed=True
        # ).button_validate()


class DeliveryScanWizardLine(models.TransientModel):
    _name = "delivery.scan.wizard.line"
    _description = "Delivery Serial Scan Line"

    wizard_id = fields.Many2one(
        "delivery.scan.wizard",
        required=True,
        ondelete="cascade",
    )

    product_id = fields.Many2one(
        "product.product",
        string="Product",
        readonly=True,
    )

    serial_id = fields.Many2one(
        "stock.lot",
        string="Serial Number",
        readonly=True,
    )

    barcode = fields.Char(
        string="Barcode",
        readonly=True,
    )

    expiration_date = fields.Date(
        string="Expiration Date",
        readonly=True,
    )

    sequence = fields.Integer(
        string="Sequence",
        readonly=True,
    )

    scanned = fields.Boolean(
        string="Scanned",
        default=False,
    )

    is_current = fields.Boolean(
        string="Current",
        default=False,
    )

    status = fields.Selection(
        [
            ("pending", "⏳ Waiting"),
            ("current", "🔍 Scan Now"),
            ("scanned", "✓ Scanned"),
        ],
        compute="_compute_status",
        string="Status",
    )

    @api.depends("scanned", "is_current")
    def _compute_status(self):
        for line in self:
            if line.scanned:
                line.status = "scanned"
            elif line.is_current:
                line.status = "current"
            else:
                line.status = "pending"
