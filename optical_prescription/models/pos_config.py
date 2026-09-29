from odoo import models, fields, api, _


class PosConfig(models.Model):
    _inherit = "pos.config"

    @api.model
    def validate_pos_lot_scan(self, config_id, values):

        config = self.browse(config_id).exists()

        if not config:
            return {
                "valid": False,
                "message": _("POS configuration was not found."),
            }

        product_id = values.get("product_id")
        selected_lot = (values.get("selected_lot") or "").strip()
        scanned_lot = (values.get("scanned_lot") or "").strip()

        if not product_id:
            return {
                "valid": False,
                "message": _("Product information is missing."),
            }

        if not selected_lot:
            return {
                "valid": False,
                "message": _(
                    "No lot/serial number was selected for this product."
                ),
            }

        # ---------------------------------------------------------
        # SCANNED LOT MUST MATCH POS SELECTED LOT
        # ---------------------------------------------------------

        if scanned_lot != selected_lot:
            return {
                "valid": False,
                "message": _(
                    "Incorrect Lot/Serial!\n\n"
                    "Product: %s\n"
                    "POS Selected Lot: %s\n"
                    "Scanned Lot: %s\n\n"
                    "Please scan the lot that was selected in POS."
                ) % (
                               self.env["product.product"].browse(product_id).display_name,
                               selected_lot,
                               scanned_lot,
                           ),
            }

        product = self.env["product.product"].browse(product_id).exists()

        if not product:
            return {
                "valid": False,
                "message": _("Product was not found."),
            }

        # ---------------------------------------------------------
        # FIND LOT
        # ---------------------------------------------------------

        lot = self.env["stock.lot"].search(
            [
                ("name", "=", scanned_lot),
                ("product_id", "=", product.id),
            ],
            limit=1,
        )

        if not lot:
            return {
                "valid": False,
                "message": _(
                    "Lot/Serial '%s' does not exist for product '%s'."
                ) % (
                               scanned_lot,
                               product.display_name,
                           ),
            }

        # ---------------------------------------------------------
        # EXPIRATION CHECK
        # ---------------------------------------------------------

        if (
                lot.expiration_date
                and lot.expiration_date < fields.Datetime.now()
        ):
            return {
                "valid": False,
                "message": _(
                    "Lot '%s' is expired.\n\n"
                    "Expiration Date: %s\n\n"
                    "Please scan a valid lot."
                ) % (
                               lot.name,
                               lot.expiration_date.strftime("%d-%b-%Y"),
                           ),
            }

        # ---------------------------------------------------------
        # FIND POS SOURCE LOCATION
        # ---------------------------------------------------------

        picking_type = config.picking_type_id

        if not picking_type:
            return {
                "valid": False,
                "message": _(
                    "POS picking type is not configured."
                ),
            }

        source_location = picking_type.default_location_src_id

        if not source_location:
            return {
                "valid": False,
                "message": _(
                    "POS source stock location is not configured."
                ),
            }

        # ---------------------------------------------------------
        # FIND AVAILABLE LOTS
        #
        # Same principle as your delivery FEFO code:
        # earliest expiration date wins.
        # ---------------------------------------------------------

        quants = self.env["stock.quant"].search(
            [
                ("product_id", "=", product.id),
                (
                    "location_id",
                    "child_of",
                    source_location.id,
                ),
                ("lot_id", "!=", False),
                ("quantity", ">", 0),
            ]
        )

        available_lots = quants.mapped("lot_id")

        # Only lots with expiration dates participate in FEFO.
        lots_with_expiry = available_lots.filtered(
            lambda l: l.expiration_date
        )

        # ---------------------------------------------------------
        # NO FEFO LOTS
        #
        # If there is no expiration date, the selected lot is
        # still valid as long as it exists and is available.
        # ---------------------------------------------------------

        if not lots_with_expiry:

            selected_quant = quants.filtered(
                lambda q: q.lot_id == lot
            )

            if not selected_quant:
                return {
                    "valid": False,
                    "message": _(
                        "Lot '%s' is not available in POS stock."
                    ) % lot.name,
                }

            return {
                "valid": True,
                "message": _(
                    "✓ Lot '%s' verified successfully."
                ) % lot.name,
            }

        # ---------------------------------------------------------
        # LOT MUST BE AVAILABLE IN POS STOCK
        #
        # NOTE: we deliberately do NOT enforce "must be the single
        # earliest-expiring lot location-wide" here. An order can
        # legitimately span several lots (e.g. quantity 10 spread
        # across lot1 + lot2), and each of those was already selected
        # on the POS order line - the same trust model already used by
        # delivery.scan.wizard.action_scan, which only checks that the
        # scanned lot belongs to the picking's own reserved lots and is
        # not expired, without re-deriving a single "correct" lot.
        # ---------------------------------------------------------

        selected_quant = quants.filtered(
            lambda q: q.lot_id == lot
        )

        if not selected_quant:
            return {
                "valid": False,
                "message": _(
                    "Lot '%s' is not available in POS stock."
                ) % lot.name,
            }

        # ---------------------------------------------------------
        # SUCCESS
        # ---------------------------------------------------------

        return {
            "valid": True,
            "message": _(
                "✓ Lot '%s' verified successfully."
            ) % lot.name,
        }

class PosSession(models.Model):
    _inherit = "pos.session"

    def _loader_params_sale_order_line(self):
        result = super()._loader_params_sale_order_line()

        _logger.info("SALE ORDER LINE FIELDS = %s",
                     result["search_params"]["fields"])

        result["search_params"]["fields"].append("discount_amount")

        existing_domain = result["search_params"].get("domain", [])

        # A "parent" / master line (e.g. a Lab Order line) is just a
        # zero-priced container once it has components - it should never
        # be shown/loaded in the POS. Only lines WITHOUT components should
        # load: that covers normal (non-lab) products as well as the
        # component lines themselves (a component line can never have its
        # own child_component_ids).
        result["search_params"]["domain"] = existing_domain + [
            ('child_component_ids', '=', False),
            '|', '|',
            ('is_lab_order', '=', False),
            ('is_store_order', '=', False),
            ('parent_line_id', '!=', False),
        ]

        return result

class PosOrder(models.Model):

    _inherit = "pos.order"


