from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero
from odoo import models

from markupsafe import Markup



class IntercompanySudoSearchMixin(models.AbstractModel):
    _name = "intercompany.sudo.search.mixin"
    _description = "Search across all companies when flagged by context"

    def _ic_sudo_if_flagged(self):
        if self.env.context.get("intercompany_all_companies"):
            return self.sudo()
        return self

    @api.model
    def name_search(self, *args, **kwargs):
        return super(IntercompanySudoSearchMixin, self._ic_sudo_if_flagged()).name_search(*args, **kwargs)

    @api.model
    def web_name_search(self, *args, **kwargs):
        return super(IntercompanySudoSearchMixin, self._ic_sudo_if_flagged()).web_name_search(*args, **kwargs)

    @api.model
    def web_search_read(self, *args, **kwargs):
        return super(IntercompanySudoSearchMixin, self._ic_sudo_if_flagged()).web_search_read(*args, **kwargs)


class ResCompany(models.Model):
    _name = "res.company"
    _inherit = ["res.company", "intercompany.sudo.search.mixin"]


class IntercompanyStockTransfer(models.Model):
    _name = 'intercompany.stock.transfer'
    _description = 'Inter-Company Stock Transfer'
    _order = 'id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # ============================================================
    # POST STOCK ON HAND IN CHATTER
    # ============================================================

    def _post_stock_chatter(self, stage):
        Quant = self.env["stock.quant"].sudo()

        for rec in self:
            messages = []

            for line in rec.line_ids:
                source_qty = 0.0
                destination_qty = 0.0

                if rec.source_company_id and rec.source_location_id:
                    source_product = line.product_id.with_company(
                        rec.source_company_id
                    )

                    source_qty = Quant._get_available_quantity(
                        source_product,
                        rec.source_location_id,
                        strict=False,
                    )

                if rec.destination_company_id and rec.destination_location_id:
                    destination_product = line.product_id.with_company(
                        rec.destination_company_id
                    )

                    destination_qty = Quant._get_available_quantity(
                        destination_product,
                        rec.destination_location_id,
                        strict=False,
                    )

                messages.append(
                    Markup(
                        "<b>%s</b><br/>"
                        "Source On Hand: %s<br/>"
                        "Destination On Hand: %s"
                    )
                    % (
                        line.product_id.display_name,
                        source_qty,
                        destination_qty,
                    )
                )

            if messages:
                body = Markup(
                    "<b>%s</b><br/><br/>%s"
                ) % (
                           stage,
                           Markup("<br/><br/>").join(messages),
                       )

                rec.message_post(
                    body=body,
                    subtype_xmlid="mail.mt_note",
                )
    name = fields.Char(
        string="Transfer Number",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
        tracking=True,
    )

    # ============================================================
    # VIEW STOCK QUANTS
    # ============================================================

    def action_view_stock_quants(self):

        self.ensure_one()

        products = self.line_ids.mapped("product_id")

        if not products:
            raise UserError(
                _("No products are available in this transfer.")
            )

        # All companies currently enabled in the company switcher
        active_companies = self.env.companies

        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Quants"),
            "res_model": "stock.quant",
            "view_mode": "list,form",
            "domain": [
                ("product_id", "in", products.ids),
                ("company_id", "in", active_companies.ids),
                ("location_id.usage", "=", "internal"),
            ],
            "context": {
                "create": False,
                "edit": False,
                "delete": False},

        }


    source_company_id = fields.Many2one(
        "res.company",
        string="Source Company",
        required=True,
        check_company=True,
        tracking=True,
        default=lambda self: self.env.company,
    )

    destination_company_id = fields.Many2one(
        "res.company",
        string="Destination Company",
        required=True,
        check_company=True,
        tracking=True,
    )

    # ============================================================
    # SOURCE
    # ============================================================

    source_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Source Warehouse",
        required=True,
        check_company=True,
        tracking=True,
        domain="[('company_id', '=', source_company_id)]",
    )

    source_location_id = fields.Many2one(
        "stock.location",
        string="Source Location",
        required=True,
        check_company=True,
        tracking=True,
        domain="[('company_id', '=', source_company_id)]",
    )

    # ============================================================
    # DESTINATION
    # ============================================================

    # IMPORTANT:
    # Do NOT use check_company=True here.
    # This is a cross-company transfer.
    # The domain is controlled by destination_company_id instead.

    destination_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Destination Warehouse",
        required=True,
        tracking=True,
        domain="[('company_id', '=', destination_company_id)]",
    )

    destination_location_id = fields.Many2one(
        "stock.location",
        string="Destination Location",
        required=True,
        tracking=True,
        domain="[('company_id', '=', destination_company_id)]",
    )

    # ============================================================
    # TRANSIT
    # ============================================================

    transit_location_id = fields.Many2one(
        "stock.location",
        string="Transit Location",
        required=True,
        domain="[('usage', '=', 'transit'), ('company_id', '=', False)]",
        tracking=True,
    )

    # ============================================================
    # OTHER INFORMATION
    # ============================================================

    transfer_date = fields.Datetime(
        string="Transfer Date",
        default=fields.Datetime.now,
        required=True,
        tracking=True,
    )

    user_id = fields.Many2one(
        "res.users",
        string="Responsible",
        default=lambda self: self.env.user,
        required=True,
        tracking=True,
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("in_transit", "In Transit"),
            ("received", "Received"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="draft",
        required=True,
        copy=False,
        tracking=True,
    )

    notes = fields.Text(
        string="Remarks"
    )

    # ============================================================
    # PRODUCTS
    # ============================================================

    line_ids = fields.One2many(
        "intercompany.stock.transfer.line",
        "transfer_id",
        string="Products",
        copy=True,
    )

    # ============================================================
    # PICKINGS
    # ============================================================

    source_picking_id = fields.Many2one(
        "stock.picking",
        string="Source Picking",
        copy=False,
        readonly=True,
    )

    destination_picking_id = fields.Many2one(
        "stock.picking",
        string="Destination Picking",
        copy=False,
        readonly=True,
    )

    # ============================================================
    # STOCK MOVES
    # ============================================================

    source_move_ids = fields.One2many(
        "stock.move",
        "intercompany_source_transfer_id",
        string="Source Stock Moves",
        readonly=True,
    )

    destination_move_ids = fields.One2many(
        "stock.move",
        "intercompany_destination_transfer_id",
        string="Destination Stock Moves",
        readonly=True,
    )
    total_quantity = fields.Float(
        string="Total Quantity",
        digits="Product Unit",
        compute="_compute_total_quantity",
        store=True,
    )

    @api.depends("line_ids.quantity")
    def _compute_total_quantity(self):
        for rec in self:
            rec.total_quantity = sum(rec.line_ids.mapped("quantity"))

    # ============================================================
    # HELPERS (SUDO ACCESS TO OTHER COMPANIES)
    # ============================================================

    @api.model
    def _get_company_warehouse(self, company):
        """
        Return the first warehouse of the given company.
        Uses sudo() so it works even when the current user has no
        access to that company.
        """
        if not company:
            return self.env["stock.warehouse"]
        return self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", company.id)],
            limit=1,
        )

    # ============================================================
    # POST STOCK ON HAND IN CHATTER
    # ============================================================

    def _post_stock_chatter(self, stage):
        Quant = self.env["stock.quant"].sudo()

        for rec in self:
            messages = []

            for line in rec.line_ids:
                source_qty = 0.0
                destination_qty = 0.0
                product = line.product_id.sudo()

                if rec.source_company_id and rec.source_location_id:
                    source_qty = Quant._get_available_quantity(
                        product.with_company(rec.source_company_id),
                        rec.source_location_id.sudo(),
                        strict=False,
                    )

                if rec.destination_company_id and rec.destination_location_id:
                    destination_qty = Quant._get_available_quantity(
                        product.with_company(rec.destination_company_id),
                        rec.destination_location_id.sudo(),
                        strict=False,
                    )

                messages.append(
                    Markup(
                        "<b>%s</b><br/>"
                        "Source On Hand: %s<br/>"
                        "Destination On Hand: %s"
                    )
                    % (
                        line.product_id.display_name,
                        source_qty,
                        destination_qty,
                    )
                )

            if messages:
                body = Markup("<b>%s</b><br/><br/>%s") % (
                    stage,
                    Markup("<br/><br/>").join(messages),
                )
                rec.message_post(
                    body=body,
                    subtype_xmlid="mail.mt_note",
                )

    # ============================================================
    # VIEW STOCK QUANTS
    # ============================================================

    def action_view_stock_quants(self):
        self.ensure_one()

        products = self.line_ids.mapped("product_id")

        if not products:
            raise UserError(
                _("No products are available in this transfer.")
            )

        # All companies currently enabled in the company switcher
        active_companies = self.env.companies

        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Quants"),
            "res_model": "stock.quant",
            "view_mode": "list,form",
            "domain": [
                ("product_id", "in", products.ids),
                ("company_id", "in", active_companies.ids),
                ("location_id.usage", "=", "internal"),
            ],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
            },
        }

    # ============================================================
    # DEFAULT TRANSIT LOCATION
    # ============================================================

    def _default_transit_location_id(self):
        """
        Return the shared Inter-Company Transit location configured
        in Inventory Settings.
        """

        value = self.env["ir.config_parameter"].sudo().get_param(
            "intercompany_stock_transfer.transit_location_id"
        )

        if not value or not value.isdigit():
            return False

        location = self.env["stock.location"].sudo().browse(int(value))

        if not location.exists():
            return False

        if location.usage != "transit" or location.company_id:
            return False

        return location.id

    # ============================================================
    # DEFAULT GET
    # ============================================================
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)

        company = self.env.company

        # Source Company
        vals["source_company_id"] = company.id

        # Source Warehouse / Location
        warehouse = self._get_company_warehouse(company)

        if warehouse:
            vals["source_warehouse_id"] = warehouse.id

            # Source Location
            if warehouse.lot_stock_id:
                vals["source_location_id"] = warehouse.lot_stock_id.id

        # Transit Location
        transit_id = self._default_transit_location_id()

        if transit_id:
            vals["transit_location_id"] = transit_id

        return vals


    # ============================================================
    # CREATE
    # ============================================================

    @api.model_create_multi
    def create(self, vals_list):

        for vals in vals_list:

            # Transfer number
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = (
                        self.env["ir.sequence"].next_by_code(
                            "intercompany.stock.transfer"
                        )
                        or _("New")
                )

            # Responsible user
            vals.setdefault(
                "user_id",
                self.env.user.id
            )

            # Transit location
            if not vals.get("transit_location_id"):
                transit_id = self._default_transit_location_id()

                if transit_id:
                    vals["transit_location_id"] = transit_id

        return super().create(vals_list)

    # ============================================================
    # ONCHANGE - SOURCE COMPANY
    # ============================================================
    @api.onchange("source_company_id")
    def _onchange_source_company(self):
        for rec in self:
            rec.source_warehouse_id = False
            rec.source_location_id = False

            warehouse = self._get_company_warehouse(rec.source_company_id)

            if warehouse:
                rec.source_warehouse_id = warehouse
                rec.source_location_id = warehouse.lot_stock_id

    # ============================================================
    # ONCHANGE - DESTINATION COMPANY
    # ============================================================

    @api.onchange("destination_company_id")
    def _onchange_destination_company(self):
        for rec in self:
            # Clear previous values
            rec.destination_warehouse_id = False
            rec.destination_location_id = False

            warehouse = self._get_company_warehouse(
                rec.destination_company_id
            )

            if warehouse:
                rec.destination_warehouse_id = warehouse

                if warehouse.lot_stock_id:
                    rec.destination_location_id = warehouse.lot_stock_id

    # ============================================================
    # ONCHANGE - SOURCE WAREHOUSE
    # ============================================================

    @api.onchange("source_warehouse_id")
    def _onchange_source_warehouse(self):

        for rec in self:

            if rec.source_warehouse_id:
                rec.source_location_id = (
                    rec.source_warehouse_id.lot_stock_id
                )
            else:
                rec.source_location_id = False

    # ============================================================
    # ONCHANGE - DESTINATION WAREHOUSE
    # ============================================================

    @api.onchange("destination_warehouse_id")
    def _onchange_destination_warehouse(self):
        for rec in self:
            if rec.destination_warehouse_id:
                rec.destination_location_id = (
                    rec.destination_warehouse_id.sudo().lot_stock_id
                )
            else:
                rec.destination_location_id = False
    # ============================================================
    # ONCHANGE - TRANSIT
    # ============================================================

    @api.onchange("source_company_id", "destination_company_id")
    def _onchange_companies_transit(self):
        transit_id = self._default_transit_location_id()

        for rec in self:
            rec.transit_location_id = transit_id or False

    # ============================================================
    # GET CONFIGURED TRANSIT
    # ============================================================

    def _get_configured_transit(self):

        value = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("intercompany_stock_transfer.transit_location_id")
        )

        if not value or not value.isdigit():

            raise UserError(
                _(
                    "Configure an Inter-Company Transit Location "
                    "in Inventory → Configuration → Settings."
                )
            )

        location = self.env["stock.location"].sudo().browse(int(value))

        if not location.exists():

            raise UserError(
                _(
                    "The configured Inter-Company Transit Location "
                    "does not exist."
                )
            )

        if location.usage != "transit":

            raise UserError(
                _(
                    "The configured Inter-Company Transit Location "
                    "must be of type Transit."
                )
            )

        if location.company_id:

            raise UserError(
                _(
                    "The configured Inter-Company Transit Location "
                    "must be shared and have no company assigned."
                )
            )

        return location

    # ============================================================
    # VALIDATE HEADER
    # ============================================================

    def _validate_header(self):

        self.ensure_one()

        # Source and destination companies must differ
        if self.source_company_id == self.destination_company_id:
            raise ValidationError(
                _("Source and destination companies must be different.")
            )

        # Source warehouse
        if (
                not self.source_warehouse_id
                or self.source_warehouse_id.company_id != self.source_company_id
        ):
            raise ValidationError(
                _("The source warehouse must belong to the source company.")
            )

        # Destination warehouse (sudo: user may not access that company)
        destination_warehouse = self.destination_warehouse_id.sudo()
        if (
                not destination_warehouse
                or destination_warehouse.company_id
                != self.destination_company_id
        ):
            raise ValidationError(
                _(
                    "The destination warehouse must belong "
                    "to the destination company."
                )
            )

        # Source location
        if (
                not self.source_location_id
                or self.source_location_id.company_id != self.source_company_id
        ):
            raise ValidationError(
                _("The source location must belong to the source company.")
            )

        # Destination location (sudo: user may not access that company)
        destination_location = self.destination_location_id.sudo()
        if (
                not destination_location
                or destination_location.company_id
                != self.destination_company_id
        ):
            raise ValidationError(
                _(
                    "The destination location must belong "
                    "to the destination company."
                )
            )

        # Transit
        configured = self._get_configured_transit()

        if self.transit_location_id != configured:

            self.transit_location_id = configured

        if self.transit_location_id.company_id:

            raise ValidationError(
                _("The transit location must be shared (Company is empty).")
            )

        if self.transit_location_id.usage != "transit":

            raise ValidationError(
                _("The transit location must be of type Transit.")
            )

        # Products
        if not self.line_ids:
            raise ValidationError(_("Add at least one product line."))

        for line in self.line_ids:
            line._validate_line()

    # ============================================================
    # CHECK SOURCE STOCK
    # ============================================================

    def _check_source_stock(self):

        Quant = self.env["stock.quant"].sudo()

        for line in self.line_ids:
            product = line.product_id.sudo().with_company(
                self.source_company_id
            )

            available = Quant._get_available_quantity(
                product,
                self.source_location_id.sudo(),
                strict=False,
                allow_negative=False,
            )

            required = line.product_uom_id._compute_quantity(
                line.quantity,
                product.uom_id,
                round=False,
            )

            if product.uom_id.compare(available, required) < 0:
                raise ValidationError(
                    _(
                        "Insufficient stock for %(product)s.\n"
                        "Required Quantity: %(required)s\n"
                        "Available Quantity: %(available)s\n"
                        "Source Location: %(location)s"
                    )
                    % {
                        "product": product.display_name,
                        "required": required,
                        "available": available,
                        "location": (
                            self.source_location_id.display_name
                        ),
                    }
                )

    # ============================================================
    # SOURCE PICKING TYPE
    # ============================================================

    def _get_source_picking_type(self):

        picking_type = self.source_warehouse_id.out_type_id

        if (
                not picking_type
                or picking_type.company_id != self.source_company_id
        ):

            raise UserError(
                _("A source warehouse delivery operation type is required.")
            )

        return picking_type

    # ============================================================
    # DESTINATION PICKING TYPE
    # ============================================================

    def _get_destination_picking_type(self):
        # sudo: user may not have access to the destination company
        picking_type = self.destination_warehouse_id.sudo().in_type_id

        if (
                not picking_type
                or picking_type.company_id != self.destination_company_id
        ):

            raise UserError(
                _("A destination warehouse receipt operation type is required.")
            )

        return picking_type

    # ============================================================
    # PREPARE PICKING VALUES
    # ============================================================

    def _prepare_picking_vals(self, picking_type, origin):
        company = picking_type.sudo().company_id

        return {
            "picking_type_id": picking_type.id,
            "company_id": company.id,

            "location_id": (
                self.source_location_id.id
                if company == self.source_company_id
                else self.transit_location_id.id
            ),

            "location_dest_id": (
                self.transit_location_id.id
                if company == self.source_company_id
                else self.destination_location_id.id
            ),

            "origin": origin,
            "scheduled_date": self.transfer_date,
            "intercompany_transfer_id": self.id,
        }

    # ============================================================
    # CREATE SOURCE PICKING
    # ============================================================

    def _create_source_picking(self):

        Picking = self.env["stock.picking"]
        Move = self.env["stock.move"]

        picking_type = self._get_source_picking_type()

        picking = Picking.with_company(self.source_company_id).create(
            self._prepare_picking_vals(picking_type, self.name)
        )
        for line in self.line_ids:
            Move.with_company(self.source_company_id).create(
                {
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.product_uom_id._compute_quantity(
                        line.quantity,
                        line.product_id.uom_id,
                        round=False,
                    ),

                    "product_uom": line.product_id.uom_id.id,

                    "location_id": self.source_location_id.id,

                    "location_dest_id": self.transit_location_id.id,

                    "picking_id": picking.id,

                    "picking_type_id": picking_type.id,

                    "company_id": self.source_company_id.id,

                    "intercompany_source_transfer_id": self.id,

                    "intercompany_transfer_id": self.id,
                }
            )


        picking.action_confirm()
        picking.action_assign()

        # For untracked products, mark picked
        picking.move_ids.filtered(
            lambda m: m.product_id.tracking == "none"
        ).picked = True

        return picking

    # ============================================================
    # CREATE DESTINATION PICKING
    # ============================================================

    def _create_destination_picking(self):

        Picking = self.env["stock.picking"].sudo()
        Move = self.env["stock.move"].sudo()

        picking_type = self._get_destination_picking_type()

        picking = Picking.with_company(self.destination_company_id).create(
            self._prepare_picking_vals(picking_type, self.name)
        )

        for source_move in self.source_move_ids.sudo():
            Move.with_company(self.destination_company_id).create(
                {
                    "product_id": source_move.product_id.id,

                    "product_uom_qty": source_move.quantity,

                    "product_uom": source_move.product_uom.id,

                    "location_id": self.transit_location_id.id,

                    "location_dest_id": self.destination_location_id.id,

                    "picking_id": picking.id,

                    "picking_type_id": picking_type.id,

                    "company_id": self.destination_company_id.id,

                    "intercompany_destination_transfer_id": self.id,

                    "intercompany_transfer_id": self.id,
                }
            )
        picking.action_confirm()
        picking.action_assign()

        self._apply_source_tracking_to_destination(picking)

        return picking

    # ============================================================
    # APPLY LOT / SERIAL TRACKING
    # ============================================================

    def _apply_source_tracking_to_destination(self, picking):
        picking = picking.sudo()

        source_lines = self.source_move_ids.sudo().move_line_ids.filtered(
            lambda ml: ml.state == "done" and ml.quantity
        )

        if not source_lines:
            return

        for dest_move in picking.move_ids:

            src_lines = source_lines.filtered(
                lambda ml: ml.product_id == dest_move.product_id
            )

            if not src_lines:
                continue

            # Remove existing reservation
            dest_move._do_unreserve()

            commands = []

            for src_line in src_lines:

                commands.append(
                    (
                        0,
                        0,
                        {
                            "picking_id": picking.id,
                            "move_id": dest_move.id,
                            "product_id": dest_move.product_id.id,
                            "product_uom_id": dest_move.product_uom.id,
                            "location_id": self.transit_location_id.id,
                            "location_dest_id": (
                                self.destination_location_id.id
                            ),
                            "lot_id": src_line.lot_id.id,
                            "quantity": src_line.quantity,
                            "picked": True,
                            "company_id": self.destination_company_id.id,
                        },
                    )
                )

            if commands:
                dest_move.write({"move_line_ids": commands})

    # ============================================================
    # CONFIRM
    # ============================================================

    def action_confirm(self):
        # Run the whole workflow with sudo(): the destination company
        # may not be active in the company switcher (or accessible to
        # the user), and its records are hidden by multi-company rules.
        for rec in self.sudo():

            if rec.state != "draft":
                continue

            # Validate transfer
            rec._validate_header()

            # Check stock
            rec._check_source_stock()

            # Create source picking
            picking = rec._create_source_picking()

            rec.source_picking_id = picking.id

            rec.state = "confirmed"

            rec.message_post(
                body=_("Source picking %s created.") % picking.display_name
            )
            rec._post_stock_chatter(_("Transfer Confirmed"))

        return True

    # ============================================================
    # SEND TO TRANSIT
    # ============================================================

    def action_send_to_transit(self):
        # Run the whole workflow with sudo(): the destination company
        # may not be active in the company switcher (or accessible to
        # the user), and its records are hidden by multi-company rules.
        for rec in self.sudo():

            if rec.state != "confirmed":

                raise UserError(
                    _("Only confirmed transfers can be sent to transit.")
                )

            picking = rec.source_picking_id

            if not picking:

            if picking.state == "cancel":
                raise UserError(_("The source picking is cancelled."))

            if picking.state != "done":

                result = picking.button_validate()

                if isinstance(result, dict):

                    raise UserError(
                        _(
                            "Odoo returned a validation wizard "
                            "for source picking %s. "
                            "Open the picking and complete "
                            "the validation, then retry this button."
                        )
                        % picking.display_name
                    )

            # Make sure source move quantities are correct
            rec._ensure_source_move_quantities()

            # Create destination receipt
            destination = rec._create_destination_picking()

            rec.destination_picking_id = destination.id
            rec.state = "in_transit"

            rec.message_post(
                body=_(
                    "Source picking validated. "
                    "Goods are now in transit."
                )
            )
            rec._post_stock_chatter(_("Sent to Transit"))

        return True

    # ============================================================
    # ENSURE SOURCE MOVE QUANTITIES
    # ============================================================

    def _ensure_source_move_quantities(self):

        for move in self.source_move_ids.filtered(
                lambda m: m.state not in ("done", "cancel")
        ):

            if move.product_id.tracking == "serial":

                if not move.move_line_ids:

                    raise UserError(
                        _(
                            "Assign serial numbers before "
                            "validating the source picking."
                        )
                    )

            move.picked = True

    # ============================================================
    # RECEIVE
    # ============================================================

    def action_receive(self):
        # Run the whole workflow with sudo(): the destination company
        # may not be active in the company switcher (or accessible to
        # the user), and its records are hidden by multi-company rules.
        for rec in self.sudo():

            if rec.state != "in_transit":

                raise UserError(
                    _("Only transfers in transit can be received.")
                )

            picking = rec.destination_picking_id

            if not picking:

                raise UserError(
                    _("The destination picking does not exist.")
                )

            if picking.state != "done":

                result = picking.button_validate()

                if isinstance(result, dict):

                    raise UserError(
                        _(
                            "Odoo returned a validation wizard "
                            "for destination picking %s. "
                            "Open the picking and complete "
                            "the validation, then retry this button."
                        )
                        % picking.display_name
                    )

            if picking.state != "done":

                raise UserError(
                    _(
                        "The destination picking must be "
                        "validated before receiving."
                    )
                )

            rec._post_stock_chatter(_("Transfer Received"))

            rec.state = "received"

            rec.message_post(
                body=_(
                    "Destination picking validated. "
                    "Transfer received."
                )
            )

        return True

    # ============================================================
    # CANCEL
    # ============================================================

    def action_cancel(self):
        # Run the whole workflow with sudo(): the destination company
        # may not be active in the company switcher (or accessible to
        # the user), and its records are hidden by multi-company rules.
        for rec in self.sudo():

            if rec.state == "received":

                raise UserError(
                    _(
                        "A received transfer cannot be cancelled. "
                        "Use a reversal stock operation."
                    )
                )

            if rec.state == "in_transit":

                raise UserError(
                    _(
                        "Goods are already in transit. "
                        "Do not cancel completed stock moves directly; "
                        "create an appropriate reverse stock movement."
                    )
                )

            if rec.state == "confirmed":

                if (
                        rec.source_picking_id
                        and rec.source_picking_id.state not in ("done", "cancel")
                ):

                    rec.source_picking_id.action_cancel()

            rec.state = "cancelled"

            rec.message_post(body=_("Transfer cancelled."))

        return True

    # ============================================================
    # VIEW SOURCE PICKING
    # ============================================================

    def action_view_source_picking(self):

        self.ensure_one()
        return self._action_view_picking(self.source_picking_id)

    # ============================================================
    # VIEW DESTINATION PICKING
    # ============================================================

    def action_view_destination_picking(self):

        self.ensure_one()
        return self._action_view_picking(self.destination_picking_id)

    # ============================================================
    # VIEW STOCK MOVES
    # ============================================================

    def action_view_moves(self):

        self.ensure_one()

        moves = self.source_move_ids | self.destination_move_ids

        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Moves"),
            "res_model": "stock.move",
            "view_mode": "list,form",
            "domain": [("id", "in", moves.ids)],
        }

    # ============================================================
    # VIEW PICKING
    # ============================================================

    def _action_view_picking(self, picking):

        if not picking:
            raise UserError(_("No picking has been created yet."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Picking"),
            "res_model": "stock.picking",
            "view_mode": "form",
            "res_id": picking.id,
        }

