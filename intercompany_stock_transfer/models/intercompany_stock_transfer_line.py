from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class IntercompanyStockTransferLine(models.Model):
    _name = "intercompany.stock.transfer.line"
    _description = "Inter-Company Stock Transfer Line"
    _order = "id"

    sequence = fields.Integer(string="Sequence", default=10)
    serial_no = fields.Integer(
        string="S.No",
        compute="_compute_serial_no",
        readonly=True,
    )

    source_on_hand_qty = fields.Float(
        string="Source Qty",
        digits="Product Unit",
        compute="_compute_on_hand_qty",
        readonly=True,
    )

    destination_on_hand_qty = fields.Float(
        string="Dest. Qty",
        digits="Product Unit",
        compute="_compute_on_hand_qty",
        readonly=True,
    )
    transfer_id = fields.Many2one(
        "intercompany.stock.transfer",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        check_company=False,
        domain="[('is_storable', '=', True), ('active', '=', True)]",
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="UoM",
        required=True,
    )
    quantity = fields.Float(
        string="Quantity",
        required=True,
        digits="Product Unit",
    )

    @api.depends("sequence", "transfer_id.line_ids")
    def _compute_serial_no(self):
        for line in self:
            siblings = list(line.transfer_id.line_ids)
            line.serial_no = siblings.index(line) + 1 if line in siblings else 0

    @api.depends(
        "product_id",
        "transfer_id.source_company_id",
        "transfer_id.source_location_id",
        "transfer_id.destination_company_id",
        "transfer_id.destination_location_id",
    )
    def _compute_on_hand_qty(self):
        # sudo: the current user may have no access to the
        # destination company (or its locations / quants).
        Quant = self.env["stock.quant"].sudo()

        for line in self:
            line.source_on_hand_qty = 0.0
            line.destination_on_hand_qty = 0.0

            transfer = line.transfer_id

            if not line.product_id or not transfer:
                continue

            product = line.product_id.sudo()

            # ---------------- SOURCE ----------------
            if transfer.source_company_id and transfer.source_location_id:
                line.source_on_hand_qty = Quant._get_available_quantity(
                    product.with_company(transfer.source_company_id),
                    transfer.source_location_id.sudo(),
                    strict=False,
                )

            # ---------------- DESTINATION ----------------
            if (
                transfer.destination_company_id
                and transfer.destination_location_id
            ):
                line.destination_on_hand_qty = Quant._get_available_quantity(
                    product.with_company(transfer.destination_company_id),
                    transfer.destination_location_id.sudo(),
                    strict=False,
                )

    # ============================================================
    # ONCHANGE
    # ============================================================

    @api.onchange("product_id")
    def _onchange_product(self):
        for line in self:
            line.product_uom_id = (
                line.product_id.uom_id if line.product_id else False
            )

    def _validate_line(self):
        self.ensure_one()

        if not self.product_id:
            raise ValidationError(_("Product is required."))

        if not self.product_id.active:
            raise ValidationError(
                _("Product %s is inactive.") % self.product_id.display_name
            )

        if not self.product_id.is_storable:
            raise ValidationError(
                _("Product %s must be a storable product.")
                % self.product_id.display_name
            )

        if not self.product_uom_id:
            raise ValidationError(
                _("Unit of Measure is required for %s.")
                % self.product_id.display_name
            )

        # Odoo 19: validate UoM compatibility using UoM conversion
        # instead of category_id.
        try:
            self.product_uom_id._compute_quantity(
                1.0,
                self.product_id.uom_id,
                round=False,
            )
        except Exception:
            raise ValidationError(
                _(
                    "The Unit of Measure '%(uom)s' is not "
                    "compatible with the product '%(product)s'."
                )
                % {
                    "uom": self.product_uom_id.display_name,
                    "product": self.product_id.display_name,
                }
            )

        if self.quantity <= 0:
            raise ValidationError(
                _("Quantity must be greater than zero for %s.")
                % self.product_id.display_name
            )

    @api.constrains("quantity")
    def _check_quantity(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(
                    _("Quantity must be greater than zero.")
                )
